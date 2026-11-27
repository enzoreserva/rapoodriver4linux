# -*- coding: utf-8 -*-
"""High-level Rapoo mouse control - the "driver" logic.

Robustness rules (the "works 100% of the time" part):
- Response layout auto-calibration (kernel-dependent feature report layout).
- Connection type auto-detection (wired vs 2.4G dongle) with manual override.
- Every write is verified by a read-back; mismatches are retried before
  surfacing an error - settings never silently fail.
- Large transfers are chunked into 25-byte frames transparently.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .protocol import (
    ACK_OK,
    CONN_WIRED,
    CONN_WIRELESS,
    KEY_SCAN_RATE_ALLOWED,
    KEY_SCAN_RATE_VALUES,
    LABEL_TO_POLLING_RATE,
    MAX_PAYLOAD,
    NAME_TO_SENSOR_MODE,
    OFFSET_LOW_BATTERY_LIGHT_SWITCH,
    OFFSET_MOUSE_DPI_CUR,
    OFFSET_MOUSE_DPI_ENABLE_GEAR,
    OFFSET_MOUSE_DPI_X_LIST,
    OFFSET_MOUSE_DPI_Y_LIST,
    OFFSET_MOUSE_DOWNDELAY,
    OFFSET_MOUSE_GLASS,
    OFFSET_MOUSE_LINEAR_RIPPLE,
    OFFSET_MOUSE_MOTION,
    OFFSET_MOUSE_REPORT,
    OFFSET_MOUSE_SCAN_RATE,
    OFFSET_MOUSE_SENSORANGLE,
    OFFSET_MOUSE_SLEEPTIME,
    OFFSET_MOUSE_SLIGHT,
    OFFSET_RF_BOOST_SWITCH,
    OFFSET_RF_PROTOCOL_SETTING,
    POLLING_RATE_LABELS,
    POLLING_RATE_SLOTS,
    POLLING_SLOTS_FOR_SENSOR_MODE,
    PROFILE_BASES,
    SENSOR_MODE_OFFSET,
    SENSOR_MODE_VALUES,
    RF_BOOST_FULL,
    RF_BOOST_SMART,
    RF_PROTOCOL_INITIAL,
    RF_PROTOCOL_TURBO,
    STATUS_REPORT_ID,
    DPI_STAGES,
    build_read_packet,
    build_write_packet,
    decode_dpi_list,
    encode_dpi_list,
    encode_dpi_value,
    get_absolute_address,
    get_address,
    response_status,
)
from .transport import (
    DeviceEndpoint,
    RapooProtocolError,
    RapooTransport,
    enumerate_rapoo_endpoints,
    pick_config_endpoint,
)
from .devices import registry, DeviceModel


@dataclass
class MouseSettings:
    profile: int
    dpi_stage: int
    dpi_stage_count: int
    dpi_stages_x: List[int]
    dpi_stages_y: List[int]
    polling_raw: int
    polling_rate: str
    key_scan_index: int
    key_scan_rate: str
    lod_index: int
    lod_label: str
    motion_sync: bool
    glass_tracking: bool
    linear_correction: bool
    ripple_correction: bool
    debounce_press_ms: int
    debounce_release_ms: int
    sleep_minutes: int
    sensor_angle: int
    sensor_mode_slots: List[int]
    sensor_mode_preset: str
    rf_protocol: str
    rf_boost: str
    low_battery_light: bool
    raw_bytes: Dict[str, str] = field(default_factory=dict)


class RapooMouse:
    def __init__(self, endpoint: Optional[DeviceEndpoint] = None,
                 connection: Optional[int] = None,
                 payload_offset: Optional[int] = None):
        if endpoint is None:
            endpoints = enumerate_rapoo_endpoints()
            endpoint = pick_config_endpoint(endpoints)
            if endpoint is None:
                raise RuntimeError(
                    "Nenhum dispositivo Rapoo encontrado (VID 0x24AE). "
                    "Conecte o mouse (cabo) ou o dongle 2.4GHz e instale a regra udev: "
                    "sudo cp udev/99-rapoo.rules /etc/udev/rules.d/ && "
                    "sudo udevadm control --reload-rules && sudo udevadm trigger")
        self.endpoint = endpoint
        self.transport = RapooTransport(endpoint.path)
        self.model: Optional[DeviceModel] = registry.find_by_pid(endpoint.product_id)

        # Connection type: prefer the registry role of the PID, then a CLI
        # override, then a heuristics guess refined by the calibration probe.
        self.connection_type: Optional[int] = connection
        if self.connection_type is None:
            self.connection_type = self._guess_connection_type(endpoint)

        env_offset = os.environ.get("RAPOO_DATA_OFFSET")
        self.payload_offset: Optional[int] = payload_offset or (
            int(env_offset) if env_offset else None)
        self._calibrated = self.payload_offset is not None

    # ------------------------------------------------------------------
    # detection / calibration
    # ------------------------------------------------------------------
    def _guess_connection_type(self, endpoint: DeviceEndpoint) -> Optional[int]:
        if self.model and len(self.model.pids) >= 2:
            if endpoint.product_id == self.model.pids[0]:
                return CONN_WIRED
            if endpoint.product_id == self.model.pids[1]:
                return CONN_WIRELESS
        return CONN_WIRED if endpoint.product_id > 10000 else CONN_WIRELESS

    def _probe_payload_offset(self, offset: int) -> bool:
        """True when slicing at `offset` yields known-sane register values."""
        try:
            resp = self._raw_request(build_read_packet(
                self.connection_type, get_address(OFFSET_MOUSE_REPORT, 0), 8))
            perf = resp[offset:offset + 8]
            if len(perf) < 6:
                return False
            if perf[0] not in POLLING_RATE_LABELS:
                return False
            if perf[1] > 6:
                return False
            if not (1 <= perf[4] <= 12):
                return False
            if perf[5] not in (0, 1):
                return False
            resp2 = self._raw_request(build_read_packet(
                self.connection_type, get_address(OFFSET_MOUSE_DPI_CUR, 0), 1))
            return 0 <= resp2[offset] <= 6
        except (TimeoutError, RapooProtocolError, OSError):
            return False

    def calibrate(self, allow_conn_fallback: bool = True) -> int:
        """Determines response payload offset (and connection type if unsure)."""
        if self._calibrated and self.payload_offset is not None:
            return self.payload_offset
        tried_conns: List[Optional[int]] = [self.connection_type]
        if allow_conn_fallback:
            other = (CONN_WIRELESS if self.connection_type == CONN_WIRED
                     else CONN_WIRED if self.connection_type == CONN_WIRELESS else None)
            if other is not None:
                tried_conns.append(other)
        for conn in tried_conns:
            if conn is None:
                continue
            self.connection_type = conn
            for cand in (5, 4, 6):
                if self._probe_payload_offset(cand):
                    self.payload_offset = cand
                    self._calibrated = True
                    return cand
        raise RapooProtocolError(
            "Não foi possível calibrar a resposta do dispositivo (payload offset). "
            "Verifique a conexão (cabo/dongle), reacesse o mouse, ou force com "
            "RAPOO_DATA_OFFSET=4|5|6 / --conn wired|wireless.")

    def _slice(self, resp: bytes) -> bytes:
        status = response_status(resp)
        if status != ACK_OK:
            raise RapooProtocolError(
                f"ACK inesperado 0x{status:02X} do dispositivo; resposta="
                + resp[:8].hex(" "))
        return resp[self.calibrate():]

    def _raw_request(self, frame: bytes) -> bytes:
        return self.transport.request(frame)

    # ------------------------------------------------------------------
    # memory access (chunked, verified)
    # ------------------------------------------------------------------
    def read_memory(self, offset: int, length: int, profile: int = 0) -> bytes:
        out = bytearray()
        while len(out) < length:
            chunk_off = offset + len(out)
            n = min(MAX_PAYLOAD, length - len(out))
            resp = self._raw_request(build_read_packet(
                self.connection_type, get_address(chunk_off, profile), n))
            out += self._slice(resp)[:n]
        return bytes(out[:length])

    def read_absolute_memory(self, offset: int, length: int) -> bytes:
        out = bytearray()
        while len(out) < length:
            chunk_off = offset + len(out)
            n = min(MAX_PAYLOAD, length - len(out))
            resp = self._raw_request(build_read_packet(
                self.connection_type, get_absolute_address(chunk_off), n))
            out += self._slice(resp)[:n]
        return bytes(out[:length])

    def write_memory(self, offset: int, data: bytes, profile: int = 0,
                     verify: bool = True, retries: int = 3) -> None:
        """Writes bytes, verifying each chunk by read-back."""
        data = bytes(data)
        written = 0
        while written < len(data):
            chunk = data[written:written + MAX_PAYLOAD]
            chunk_off = offset + written
            frame = build_write_packet(self.connection_type,
                                       get_address(chunk_off, profile), chunk)
            for attempt in range(retries + 1):
                resp = self._raw_request(frame)
                response_status(resp)  # raises on NACK
                if not verify:
                    break
                back = self.read_memory(chunk_off, len(chunk), profile)
                if back == chunk:
                    break
                if attempt == retries:
                    raise RapooProtocolError(
                        f"Falha ao gravar em offset {chunk_off}: escrita não "
                        f"confirmada (esperado {chunk.hex(' ')}, lido {back.hex(' ')})")
                time.sleep(0.05)
            written += len(chunk)

    def write_absolute_memory(self, offset: int, data: bytes, verify: bool = True,
                              retries: int = 3) -> None:
        data = bytes(data)
        written = 0
        while written < len(data):
            chunk = data[written:written + MAX_PAYLOAD]
            chunk_off = offset + written
            frame = build_write_packet(self.connection_type,
                                       get_absolute_address(chunk_off), chunk)
            for attempt in range(retries + 1):
                resp = self._raw_request(frame)
                response_status(resp)
                if not verify:
                    break
                back = self.read_absolute_memory(chunk_off, len(chunk))
                if back == chunk:
                    break
                if attempt == retries:
                    raise RapooProtocolError(
                        f"Falha ao gravar em offset global {chunk_off}")
                time.sleep(0.05)
            written += len(chunk)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def open(self):
        self.transport.open()
        self.calibrate()

    def close(self):
        self.transport.close()

    def __enter__(self) -> "RapooMouse":
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # settings: read
    # ------------------------------------------------------------------
    def get_settings(self, profile: int = 0) -> MouseSettings:
        cur = self.read_memory(OFFSET_MOUSE_DPI_CUR, 1, profile)
        stage = cur[0] if cur else 0
        count_raw = self.read_memory(OFFSET_MOUSE_DPI_ENABLE_GEAR, 1, profile)
        count = (count_raw[0] + 1) if count_raw else 1
        dpi_x = decode_dpi_list(self.read_memory(OFFSET_MOUSE_DPI_X_LIST, 14, profile))
        dpi_y = decode_dpi_list(self.read_memory(OFFSET_MOUSE_DPI_Y_LIST, 14, profile))
        perf = self.read_memory(OFFSET_MOUSE_REPORT, 8, profile)
        perf = perf.ljust(8, b"\x00")
        poll_raw = perf[0]
        scan_idx = perf[1]
        lod_idx = perf[4]
        motion = bool(perf[5])
        blk = self.read_memory(OFFSET_MOUSE_DOWNDELAY, 6, profile).ljust(6, b"\x00")
        press_ms, release_ms, sleep_min = blk[0], blk[1], blk[2]
        linear = bool(blk[3] & 0x01)
        ripple = bool((blk[3] >> 1) & 0x01)
        angle = int.from_bytes(bytes([blk[4]]), "little", signed=True)
        glass = bool(blk[5])
        rf = self.read_memory(OFFSET_RF_BOOST_SWITCH, 3, profile).ljust(3, b"\x00")
        proto_raw = self.read_absolute_memory(OFFSET_RF_PROTOCOL_SETTING, 1)
        proto_val = proto_raw[0] if proto_raw else RF_PROTOCOL_TURBO
        slots_raw = self.read_memory(SENSOR_MODE_OFFSET, 7, profile)
        slots = list(slots_raw[:7]) if len(slots_raw) >= 7 else []
        try:
            slot_idx = POLLING_RATE_SLOTS.index(poll_raw)
        except ValueError:
            slot_idx = 3
        preset_val = slots[slot_idx] if slot_idx < len(slots) else None
        scale = self.model.lod_scale if self.model else None
        from .protocol import LOD_SCALE_LV
        scale = scale or LOD_SCALE_LV
        lod_label = (f"{scale[lod_idx - 1]:.1f} mm"
                     if 1 <= lod_idx <= len(scale) else f"raw {lod_idx}")
        return MouseSettings(
            profile=profile,
            dpi_stage=stage,
            dpi_stage_count=count,
            dpi_stages_x=dpi_x,
            dpi_stages_y=dpi_y,
            polling_raw=poll_raw,
            polling_rate=POLLING_RATE_LABELS.get(poll_raw, f"raw {poll_raw}"),
            key_scan_index=scan_idx,
            key_scan_rate=f"{KEY_SCAN_RATE_VALUES.get(scan_idx, '?')}Hz",
            lod_index=lod_idx,
            lod_label=lod_label,
            motion_sync=motion,
            glass_tracking=glass,
            linear_correction=linear,
            ripple_correction=ripple,
            debounce_press_ms=press_ms,
            debounce_release_ms=release_ms,
            sleep_minutes=sleep_min,
            sensor_angle=angle,
            sensor_mode_slots=slots,
            sensor_mode_preset=_preset_name(preset_val),
            rf_protocol={RF_PROTOCOL_TURBO: "turbo", RF_PROTOCOL_INITIAL: "initial"}.get(
                proto_val, f"raw {proto_val}"),
            rf_boost={RF_BOOST_FULL: "maximum", RF_BOOST_SMART: "adaptive"}.get(
                rf[0], f"raw {rf[0]}"),
            low_battery_light=bool(rf[1]),
            raw_bytes={
                "perf": perf.hex(" "),
                "settings": blk.hex(" "),
                "rf": rf.hex(" "),
                "sensor_mode": slots_raw.hex(" ") if slots_raw else "",
            },
        )

    # ------------------------------------------------------------------
    # settings: write
    # ------------------------------------------------------------------
    def set_dpi_stage_value(self, stage: int, dpi: Optional[int] = None,
                            dpi_y: Optional[int] = None, profile: int = 0) -> None:
        """Sets DPI for one stage (X and optionally Y independently)."""
        if not 0 <= stage < DPI_STAGES:
            raise ValueError(f"Estágio DPI deve ser 0-{DPI_STAGES - 1}, recebido {stage}")
        lo, hi = self.dpi_range()
        if dpi is not None:
            _check_dpi(dpi, lo, hi)
            self.write_memory(OFFSET_MOUSE_DPI_X_LIST + stage * 2,
                              encode_dpi_value(dpi), profile)
        if dpi_y is not None:
            _check_dpi(dpi_y, lo, hi)
            self.write_memory(OFFSET_MOUSE_DPI_Y_LIST + stage * 2,
                              encode_dpi_value(dpi_y), profile)

    def set_dpi_list(self, values_x: List[int], values_y: Optional[List[int]] = None,
                     profile: int = 0) -> None:
        """Sets the whole 7-stage DPI list (values 0 disables a stage slot)."""
        if len(values_x) != DPI_STAGES:
            raise ValueError(f"Lista X precisa de {DPI_STAGES} valores")
        lo, hi = self.dpi_range()
        for v in values_x:
            if v:
                _check_dpi(v, lo, hi)
        self.write_memory(OFFSET_MOUSE_DPI_X_LIST, encode_dpi_list(values_x), profile)
        if values_y is not None:
            if len(values_y) != DPI_STAGES:
                raise ValueError(f"Lista Y precisa de {DPI_STAGES} valores")
            for v in values_y:
                if v:
                    _check_dpi(v, lo, hi)
            self.write_memory(OFFSET_MOUSE_DPI_Y_LIST, encode_dpi_list(values_y), profile)

    def set_dpi_stage_count(self, count: int, profile: int = 0) -> None:
        if not 1 <= count <= DPI_STAGES:
            raise ValueError(f"Contagem de estágios deve ser 1-{DPI_STAGES}, recebido {count}")
        self.write_memory(OFFSET_MOUSE_DPI_ENABLE_GEAR, [count - 1], profile)

    def set_dpi_stage(self, stage: int, profile: int = 0) -> None:
        """Selects the active DPI stage."""
        if not 0 <= stage < DPI_STAGES:
            raise ValueError(f"Estágio DPI deve ser 0-{DPI_STAGES - 1}, recebido {stage}")
        self.write_memory(OFFSET_MOUSE_DPI_CUR, [stage], profile)

    def set_polling_rate(self, rate: int | str, profile: int = 0) -> None:
        raw = _resolve_polling(rate)
        self.write_memory(OFFSET_MOUSE_REPORT, [raw], profile)

    def set_key_scan_rate(self, rate: int | str, profile: int = 0) -> None:
        raw = _resolve_key_scan(rate)
        self.write_memory(OFFSET_MOUSE_SCAN_RATE, [raw], profile)

    def set_lod(self, value: int | float, profile: int = 0) -> None:
        scale = self.model.lod_scale if self.model else None
        from .protocol import LOD_SCALE_LV
        scale = scale or LOD_SCALE_LV
        if isinstance(value, int) and not isinstance(value, bool):
            if not 1 <= value <= len(scale):
                raise ValueError(f"Índice LOD deve ser 1-{len(scale)}")
            idx = value
        else:
            text = str(value).strip()
            mm = float(text)
            matches = [i for i, v in enumerate(scale, 1) if abs(v - mm) < 1e-6]
            if matches:
                idx = matches[0]
            elif text.isdigit() and 1 <= int(text) <= len(scale):
                # CLI always passes strings; a bare integer that is not on the
                # mm scale is accepted as a 1-based scale index (--lod 5).
                idx = int(text)
            else:
                raise ValueError(
                    f"LOD {mm} mm fora da escala do modelo "
                    f"({scale[0]:.1f}-{scale[-1]:.1f} mm). Use um valor da "
                    f"escala em mm ou um índice inteiro 1-{len(scale)}")
        self.write_memory(OFFSET_MOUSE_SLIGHT, [idx], profile)

    def set_motion_sync(self, enabled: bool, profile: int = 0) -> None:
        self.write_memory(OFFSET_MOUSE_MOTION, [1 if enabled else 0], profile)

    def set_glass_tracking(self, enabled: bool, profile: int = 0) -> None:
        self.write_memory(OFFSET_MOUSE_GLASS, [1 if enabled else 0], profile)

    def set_angle(self, angle: int, profile: int = 0) -> None:
        if not -30 <= angle <= 30:
            raise ValueError("Ângulo deve estar entre -30 e +30 graus")
        self.write_memory(OFFSET_MOUSE_SENSORANGLE,
                          angle.to_bytes(1, "little", signed=True), profile)

    def set_corrections(self, linear: Optional[bool] = None,
                        ripple: Optional[bool] = None, profile: int = 0) -> None:
        if linear is None or ripple is None:
            cur = self.read_memory(OFFSET_MOUSE_LINEAR_RIPPLE, 1, profile)[0]
            if linear is None:
                linear = bool(cur & 0x01)
            if ripple is None:
                ripple = bool(cur & 0x02)
        self.write_memory(OFFSET_MOUSE_LINEAR_RIPPLE,
                          [(1 if linear else 0) | ((1 if ripple else 0) << 1)], profile)

    def set_debounce(self, press: Optional[int] = None,
                     release: Optional[int] = None, profile: int = 0) -> None:
        for name, value in (("press", press), ("release", release)):
            if value is not None and not 0 <= value <= 32:
                raise ValueError(f"Debounce {name} deve ser 0-32 ms")
        if press is None or release is None:
            blk = self.read_memory(OFFSET_MOUSE_DOWNDELAY, 2, profile).ljust(2, b"\x00")
            press = blk[0] if press is None else press
            release = blk[1] if release is None else release
        self.write_memory(OFFSET_MOUSE_DOWNDELAY, [press, release], profile)

    def set_sleep(self, minutes: int, profile: int = 0) -> None:
        if not 1 <= minutes <= 60:
            raise ValueError("Sleep deve ser 1-60 minutos")
        self.write_memory(OFFSET_MOUSE_SLEEPTIME, [minutes], profile)

    def set_sensor_mode(self, preset: int | str, polling_rate: Optional[int | str] = None,
                        profile: int = 0) -> None:
        value = _resolve_sensor_mode(preset)
        poll_raw = (_resolve_polling(polling_rate)
                    if polling_rate is not None
                    else self.read_memory(OFFSET_MOUSE_REPORT, 1, profile)[0])
        try:
            slot = POLLING_RATE_SLOTS.index(poll_raw)
        except ValueError:
            raise ValueError(f"Código de polling {poll_raw} não mapeia para um slot")
        allowed = POLLING_SLOTS_FOR_SENSOR_MODE.get(poll_raw)
        if allowed and value not in allowed:
            names = [POLLING_RATE_LABELS.get(poll_raw, str(poll_raw))]
            raise ValueError(
                f"Preset '{POLLING_RATE_LABELS.get(value, value)}' não é válido em "
                f"{names[0]} (permitidos: "
                f"{', '.join(_preset_name(v) for v in allowed if v in SENSOR_MODE_VALUES)})")
        self.write_memory(SENSOR_MODE_OFFSET + slot, [value], profile)

    def set_sensor_mode_slots(self, slots: List[int], profile: int = 0) -> None:
        if len(slots) != 7:
            raise ValueError("Lista de presets precisa de 7 valores")
        self.write_memory(SENSOR_MODE_OFFSET, list(slots), profile)

    def set_rf_protocol(self, protocol: int | str) -> None:
        if isinstance(protocol, str):
            table = {"turbo": RF_PROTOCOL_TURBO, "initial": RF_PROTOCOL_INITIAL}
            key = protocol.strip().lower()
            if key not in table:
                raise ValueError("Protocolo RF: 'turbo' ou 'initial'")
            raw = table[key]
        else:
            raw = protocol
        if raw not in (RF_PROTOCOL_TURBO, RF_PROTOCOL_INITIAL):
            raise ValueError("Protocolo RF deve ser 0 (turbo) ou 2 (initial)")
        self.write_absolute_memory(OFFSET_RF_PROTOCOL_SETTING, [raw])

    def set_rf_boost(self, mode: int | str, profile: int = 0) -> None:
        if isinstance(mode, str):
            table = {"adaptive": RF_BOOST_SMART, "maximum": RF_BOOST_FULL}
            key = mode.strip().lower()
            if key not in table:
                raise ValueError("RF boost: 'adaptive' ou 'maximum'")
            raw = table[key]
        else:
            raw = int(mode)
        if raw not in (RF_BOOST_SMART, RF_BOOST_FULL):
            raise ValueError("RF boost deve ser 0 (adaptive) ou 1 (maximum)")
        self.write_memory(OFFSET_RF_BOOST_SWITCH, [raw], profile)

    def set_low_battery_light(self, enabled: bool, profile: int = 0) -> None:
        self.write_memory(OFFSET_LOW_BATTERY_LIGHT_SWITCH, [1 if enabled else 0], profile)

    def set_profile(self, index: int) -> None:
        # The active profile lives in the status report; per-profile settings
        # are selected by reading/writing each profile's memory block. Switching
        # profiles is done by the mouse's DPI-cycle button or the official
        # driver; expose raw access instead of guessing an unverified command.
        raise NotImplementedError(
            "Troca de perfil via software ainda não validada no hardware; "
            "use o botão DPI do mouse ou o web driver oficial.")

    # ------------------------------------------------------------------
    # live status
    # ------------------------------------------------------------------
    def read_status(self, timeout_s: float = 30.0,
                    trigger_every_s: float = 2.0) -> Optional[dict]:
        """Waits for one status input report (ID 7) and parses it.

        Timing measured live on Gen2 (VT3S MAX, nRF54L, 2.4G dongle): report
        7 is NOT a fixed heartbeat - it flows in ~2s bursts (9 reports in 40s
        observed) and then goes silent for minutes, even while the mouse
        moves and the host sends commands. Strategy: passive select wait;
        every trigger_every_s send a harmless 1-byte EEPROM read (free to
        send, sometimes coincides with status resuming); return the first
        report that arrives, or None after timeout_s.
        """
        from .protocol import parse_status_report
        deadline = time.time() + timeout_s
        next_trigger = time.time() + trigger_every_s
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            raw = self.transport.wait_for_report(
                STATUS_REPORT_ID, timeout_s=min(remaining, trigger_every_s))
            if raw is not None:
                try:
                    return parse_status_report(raw)
                except ValueError:
                    return None
            if time.time() >= deadline:
                return None
            if time.time() >= next_trigger:
                try:
                    self.read_memory(OFFSET_MOUSE_DPI_CUR, 1, 0)
                except Exception:
                    pass
                next_trigger = time.time() + trigger_every_s

    def watch(self, poll_s: float = 1.0, once: bool = False) -> None:
        """Prints live status, deduplicating unchanged reports.

        Gen2 status arrives in irregular bursts (see read_status); each wait
        window gives the device a fair chance to report while the loop keeps
        printing on every change.
        """
        last_key = None
        while True:
            st = self.read_status(timeout_s=max(8.0, poll_s * 5))
            if st:
                key = (st.get("battery_percent"), st.get("dpi_stage"),
                       st.get("polling_raw"), st.get("profile"),
                       st.get("connection_nibble"))
                if key != last_key or once:
                    print(_format_status(st))
                    last_key = key
            if once:
                break
            time.sleep(poll_s)

    # ------------------------------------------------------------------
    # backup / restore (full raw profile block)
    # ------------------------------------------------------------------
    def backup_profile(self, filepath: Path, profile: int = 0) -> dict:
        base = PROFILE_BASES[profile % len(PROFILE_BASES)]
        blob = self.read_absolute_memory(base, 1024)
        payload = {
            "created": datetime.now().isoformat(timespec="seconds"),
            "tool": "rapoodriver4linux",
            "model": self.model.model if self.model else "unknown",
            "usb_pid": self.endpoint.product_id,
            "profile": profile,
            "base_address": base,
            "data_hex": blob.hex(),
        }
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return payload

    def restore_profile(self, filepath: Path, profile: int = 0,
                        verify: bool = True) -> None:
        with open(filepath, "r", encoding="utf-8") as f:
            payload = json.load(f)
        blob = bytes.fromhex(payload["data_hex"])
        if len(blob) != 1024:
            raise ValueError(f"Backup inválido: {len(blob)} bytes (esperado 1024)")
        base = PROFILE_BASES[profile % len(PROFILE_BASES)]
        self.write_absolute_memory(base, blob, verify=verify)

    # ------------------------------------------------------------------
    def dpi_range(self) -> Tuple[int, int]:
        if self.model:
            return self.model.dpi_range
        from .protocol import DPI_LIMIT_DEFAULT
        return DPI_LIMIT_DEFAULT


def _check_dpi(dpi: int, lo: int, hi: int) -> None:
    if not lo <= dpi <= hi:
        raise ValueError(f"DPI deve estar entre {lo} e {hi} para este sensor, recebido {dpi}")


_POLLING_HZ_TO_CODE = {int(v.replace("Hz", "")): k
                       for k, v in POLLING_RATE_LABELS.items()}


def _resolve_polling(rate: int | str) -> int:
    if isinstance(rate, str):
        label = rate.strip().lower().replace(" ", "")
        table = {k.lower(): v for k, v in LABEL_TO_POLLING_RATE.items()}
        if label in table:
            return table[label]
        raise ValueError(f"Polling rate inválido '{rate}'. Opções: "
                         f"{', '.join(sorted(POLLING_RATE_LABELS.values()))}")
    if rate in POLLING_RATE_LABELS:      # raw hardware code
        return rate
    if rate in _POLLING_HZ_TO_CODE:      # plain Hz number (125, 500, 8000...)
        return _POLLING_HZ_TO_CODE[rate]
    raise ValueError(
        f"Polling rate desconhecido {rate}. Use 125/250/500/1000/2000/4000/8000 "
        f"ou os códigos {sorted(POLLING_RATE_LABELS)}")


_KEY_SCAN_HZ_TO_INDEX = {hz: idx for idx, hz in KEY_SCAN_RATE_VALUES.items()}


def _resolve_key_scan(rate: int | str) -> int:
    if isinstance(rate, str):
        label = rate.strip().lower().replace(" ", "")
        table = {f"{hz}hz": idx for idx, hz in KEY_SCAN_RATE_VALUES.items()}
        if label not in table:
            raise ValueError(f"Key scan rate inválido '{rate}'. "
                             f"Opções: {', '.join(f'{KEY_SCAN_RATE_VALUES[i]}Hz' for i in KEY_SCAN_RATE_ALLOWED)}")
        raw = table[label]
    else:
        raw = _KEY_SCAN_HZ_TO_INDEX.get(rate, rate)  # accept Hz ints too
    if raw not in KEY_SCAN_RATE_ALLOWED:
        raise ValueError("Key scan rate aceito: 1000Hz, 2000Hz, 4000Hz ou 8000Hz")
    return raw


def _resolve_sensor_mode(preset: int | str) -> int:
    if isinstance(preset, str):
        key = preset.strip().lower()
        if key not in NAME_TO_SENSOR_MODE:
            raise ValueError(f"Preset inválido '{preset}'. Opções: "
                             f"{', '.join(sorted(set(SENSOR_MODE_NAMES)))}")
        return NAME_TO_SENSOR_MODE[key]
    if preset not in SENSOR_MODE_NAMES:
        raise ValueError(f"Preset inválido {preset}")
    return preset


SENSOR_MODE_NAMES = {v: k for k, v in NAME_TO_SENSOR_MODE.items()}


def _preset_name(value: Optional[int]) -> str:
    if value is None:
        return "unknown"
    from .protocol import SENSOR_MODE_VALUES
    name = SENSOR_MODE_VALUES.get(value)
    return name if name is not None else f"raw {value}"


def _format_status(st: dict) -> str:
    pct = st.get("battery_percent")
    # nRF54L Gen2 heartbeats carry flag 0x01 with a live level (Telink uses
    # 0 = valid); trust the percent whenever it is a plausible 0-100 value.
    bat = (f"{pct}%" if pct is not None and 0 <= pct <= 100
           else f"{pct}% (?)")
    return (f"[{st.get('connection', '?')}] bateria {bat} | "
            f"DPI {st.get('dpi_x')}x{st.get('dpi_y')} (estágio {st.get('dpi_stage')}) | "
            f"polling {st.get('polling_rate')} | perfil {st.get('profile', '?')}"
            + (f" | RF {'ok' if st.get('rf_connected') else 'off'} "
               f"RSSI {st.get('rf_rssi')}" if "rf_connected" in st else ""))
