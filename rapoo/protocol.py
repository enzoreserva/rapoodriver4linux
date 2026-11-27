# -*- coding: utf-8 -*-
"""Rapoo HID protocol constants and packet framing.

Reverse-engineered from the official "A HUB" driver v1.0.19 (Windows/macOS)
and the official Rapoo Web Driver. Protocol knowledge documented by the
community project pedro3z0/rapoo-software-linux (MIT).

Layer summary:
- Interface 1 (vendor usage page 0xFF00):
  - Output Report ID 6 (32 bytes): host -> device commands
  - Feature Report ID 8 (32 bytes): device -> host responses
  - Input Report ID 7 (~3s heartbeat): live status/battery
- Memory is a virtual EEPROM of up to 8 profiles (1024 bytes each).
"""
from __future__ import annotations

import struct
from typing import List, Tuple

# ---------------------------------------------------------------------------
# HID layer
# ---------------------------------------------------------------------------
REPORT_ID_OUTPUT = 6    # host -> device command frames (32 bytes)
REPORT_ID_FEATURE = 8   # device -> host response frames (32 bytes)
STATUS_REPORT_ID = 7    # periodic status/battery input report
FRAME_SIZE = 32         # payload size of reports 6/8 (without report ID)
MAX_PAYLOAD = FRAME_SIZE - 7  # data bytes that fit in one write frame (25)

ACK_OK = 0x01
ACK_BUSY = 0x02

CMD_READ_EEPROM = 0xA4
CMD_WRITE_EEPROM = 0xA5
CMD_GET_FIRMWARE = 0xB0        # documented in driver, not validated live yet
CMD_RETURN_FACTORY = 0xAD      # factory reset (untested - use with care)

CONN_WIRED = 0xFF     # connection-type byte when talking over the USB cable
CONN_WIRELESS = 0xA5  # connection-type byte when talking through the 2.4G dongle

# ---------------------------------------------------------------------------
# Memory map (profile-relative unless noted)
# ---------------------------------------------------------------------------
# Profile 0 base = 1536; each profile adds 1024 bytes.
PROFILE_BASES = [1536 + 1024 * i for i in range(8)]

OFFSET_MOUSE_LEFT = 0       # button mapping blocks, 4 bytes each
OFFSET_MOUSE_MID = 4
OFFSET_MOUSE_RIGHT = 8
OFFSET_MOUSE_CPIADD = 12
OFFSET_MOUSE_CPIREDUCE = 16
OFFSET_MOUSE_FORWARD = 20
OFFSET_MOUSE_BACK = 24

OFFSET_MOUSE_REPORT = 640        # polling rate code
OFFSET_MOUSE_SCAN_RATE = 641     # key scan-rate index
OFFSET_MOUSE_SLIGHT = 644        # lift-off distance (1-based scale index)
OFFSET_MOUSE_MOTION = 645        # motion sync switch (0/1)
OFFSET_MOUSE_DPI_X_LIST = 648    # 7 stages * u16 LE = 14 bytes
OFFSET_MOUSE_DPI_ENABLE_GEAR = 662  # enabled stage count - 1
OFFSET_MOUSE_DPI_CUR = 664       # active stage index (0..6)
OFFSET_MOUSE_LIGHTMOD = 665      # lighting mode (where present)
OFFSET_MOUSE_LIGHTRGB = 696      # lighting RGB (where present)
OFFSET_MOUSE_DOWNDELAY = 704     # press debounce (ms)
OFFSET_MOUSE_LIFTDELAY = 705     # release debounce (ms)
OFFSET_MOUSE_SLEEPTIME = 706     # wireless sleep timeout (minutes)
OFFSET_MOUSE_LINEAR_RIPPLE = 707 # bit0 angle snapping, bit1 ripple correction
OFFSET_MOUSE_SENSORANGLE = 708   # signed sensor angle (-30..+30 deg)
OFFSET_MOUSE_GLASS = 709         # glass tracking switch (0/1)
OFFSET_MOUSE_LOWPOWER = 710      # low battery warning threshold
OFFSET_MOUSE_DPI_Y_LIST = 712    # 7 stages * u16 LE = 14 bytes

OFFSET_RF_BOOST_SWITCH = 728           # 0=Adaptive RF, 1=Maximum RF
OFFSET_LOW_BATTERY_LIGHT_SWITCH = 729  # low-battery LED flash alert (0/1)
OFFSET_DC_SWITCH = 730                 # undocumented switch (0/1)
SENSOR_MODE_OFFSET = 732               # 7 slots, one preset per polling rate

# Global (non-profile) EEPROM space
OFFSET_RF_PROTOCOL_SETTING = 96   # 0=turbo (default), 2=initial
RF_PROTOCOL_TURBO = 0
RF_PROTOCOL_INITIAL = 2

RF_BOOST_SMART = 0
RF_BOOST_FULL = 1

# ---------------------------------------------------------------------------
# Polling rate codes (nRF54L retail tier, offset 640)
# ---------------------------------------------------------------------------
POLLING_RATE_LABELS = {
    8: "125Hz",
    4: "250Hz",
    2: "500Hz",
    1: "1000Hz",
    132: "2000Hz",
    130: "4000Hz",
    129: "8000Hz",
}
LABEL_TO_POLLING_RATE = {v: k for k, v in POLLING_RATE_LABELS.items()}
POLLING_RATE_SLOTS = [8, 4, 2, 1, 132, 130, 129]  # slot order 125..8000Hz

# ---------------------------------------------------------------------------
# Key scan rate (offset 641): plain index into official label array
# ---------------------------------------------------------------------------
KEY_SCAN_RATE_VALUES = {0: 125, 1: 250, 2: 500, 3: 1000, 4: 2000, 5: 4000, 6: 8000}
KEY_SCAN_RATE_ALLOWED = (3, 4, 5, 6)  # retail devices accept 1000..8000Hz only
KEY_SCAN_RATE_DEFAULT = 3

# ---------------------------------------------------------------------------
# Performance presets (profile+732, 7 slots; values 0..5)
# ---------------------------------------------------------------------------
SENSOR_MODE_VALUES = {
    5: "frenzy",
    4: "competitive",
    3: "hyperCore",
    2: "firepower",
    1: "balanced",
    0: "office",
}
NAME_TO_SENSOR_MODE = {n.lower(): v for v, n in SENSOR_MODE_VALUES.items()}

# Which preset values are legal per polling slot (official VV table)
POLLING_SLOTS_FOR_SENSOR_MODE = {
    8: [1, 0],
    4: [1, 0],
    2: [2, 1, 0],
    1: [5, 4, 3, 2, 1],
    132: [5, 4, 3, 2],
    130: [5, 4, 3],
    129: [5, 4, 3],
}

# ---------------------------------------------------------------------------
# Lift-off distance scales (offset 644, 1-based index)
# ---------------------------------------------------------------------------
LOD_SCALE_LV = [0.7 + 0.1 * i for i in range(11)]  # 0.7..1.7 mm (PAW3950U/3955U)
LOD_SCALE_MV = [1.0 + 0.1 * i for i in range(11)]  # 1.0..2.0 mm (PAW3398 legacy)

# DPI limits by sensor tier (official capability tables)
DPI_LIMITS = {
    "PAW3950U": (10, 45000),   # VT3S MAX / VT3 Air / VT0 Air MAX family
    "PAW3955U": (10, 45000),
    "PAW3398": (50, 26000),
}
DPI_LIMIT_DEFAULT = (50, 30000)

DPI_STAGES = 7  # 7 configurable stages, indexes 0..6


def dpi_limits(sensor: str) -> Tuple[int, int]:
    return DPI_LIMITS.get(sensor, DPI_LIMIT_DEFAULT)


# ---------------------------------------------------------------------------
# Packet framing
# ---------------------------------------------------------------------------
def get_address(offset: int, profile: int = 0) -> List[int]:
    """4-byte little-endian address of a profile-relative offset."""
    addr = PROFILE_BASES[profile % len(PROFILE_BASES)] + offset
    return [addr & 0xFF, (addr >> 8) & 0xFF, (addr >> 16) & 0xFF, (addr >> 24) & 0xFF]


def get_absolute_address(offset: int) -> List[int]:
    """4-byte little-endian address in the global EEPROM space."""
    return [offset & 0xFF, (offset >> 8) & 0xFF, (offset >> 16) & 0xFF, (offset >> 24) & 0xFF]


def build_read_packet(connection_type: int, address: List[int], length: int) -> bytes:
    """32-byte output-report frame asking the device for `length` bytes."""
    if not 1 <= length <= MAX_PAYLOAD:
        raise ValueError(f"read length must be 1..{MAX_PAYLOAD}, got {length}")
    pkt = bytearray(FRAME_SIZE)
    pkt[0] = connection_type & 0xFF
    pkt[1] = CMD_READ_EEPROM
    pkt[2] = length
    pkt[3:7] = bytes(address[:4])
    return bytes(pkt)


def build_write_packet(connection_type: int, address: List[int], data: bytes) -> bytes:
    """32-byte output-report frame writing up to MAX_PAYLOAD bytes."""
    if not 1 <= len(data) <= MAX_PAYLOAD:
        raise ValueError(f"write length must be 1..{MAX_PAYLOAD}, got {len(data)}")
    pkt = bytearray(FRAME_SIZE)
    pkt[0] = connection_type & 0xFF
    pkt[1] = CMD_WRITE_EEPROM
    pkt[2] = len(data)
    pkt[3:7] = bytes(address[:4])
    pkt[7:7 + len(data)] = data
    return bytes(pkt)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------
# A feature-report response buffer (33 bytes with report ID) can arrive in
# two layouts depending on the kernel:
#   Layout A (report ID stripped): [status, echo, echo, echo, data...]
#   Layout B (report ID present):  [0x08, status, echo, echo, echo, data...]
PAYLOAD_OFFSET_CANDIDATES = (5, 4, 6)


def response_status_index(resp: bytes) -> int:
    """Index of the ACK status byte in a raw response buffer."""
    if resp and resp[0] in (ACK_OK, ACK_BUSY):
        return 0
    return 1


def response_status(resp: bytes) -> int:
    if not resp:
        raise ValueError("empty response")
    if len(resp) == 1:
        return resp[0]
    return resp[response_status_index(resp)]


def parse_feature_response(resp: bytes, payload_offset: int | None = None) -> Tuple[int, bytes]:
    """Return (ack_status, payload) honouring a fixed or guessed layout."""
    status = response_status(resp)
    if payload_offset is None:
        payload_offset = 4 if response_status_index(resp) == 0 else 5
    return status, resp[payload_offset:]


# ---------------------------------------------------------------------------
# Value codecs
# ---------------------------------------------------------------------------
def decode_dpi_list(raw: bytes) -> List[int]:
    """14-byte block -> list of 7 DPI values (u16 LE)."""
    return [raw[i] | (raw[i + 1] << 8) for i in range(0, len(raw) - 1, 2)]


def encode_dpi_value(dpi: int) -> bytes:
    return bytes([dpi & 0xFF, (dpi >> 8) & 0xFF])


def encode_dpi_list(values: List[int]) -> bytes:
    out = bytearray()
    for v in values:
        out += encode_dpi_value(v)
    return bytes(out)


def parse_status_report(raw: bytes) -> dict:
    """Parse the heartbeat input report (ID 7) as delivered by hidraw.

    Byte 0 is the report ID in the hidraw view; official WebHID sees the
    payload after it. Field meanings (payload-relative):
      0: low nibble connection (0=wireless 2.4G, 2=wired) | high nibble sensor class
      1: active DPI stage; 2-3 DPI X (LE); 4-5 DPI Y (LE)
      6: battery validity flag (Telink: 0 = level valid; nRF54L Gen2 sends
         0x01 with a plausible live level - treated as valid in the UI); 7: battery percent
      8: backlight mode; 9: key scan index; 10: polling raw code
      11: active profile; 12-13: USB PID (LE); 17: bit7 RF linked, bits0-6 RSSI
    """
    if not raw or raw[0] != STATUS_REPORT_ID:
        raise ValueError("not a status report (report id 7)")
    o = raw[1:]
    out: dict = {"report_id": STATUS_REPORT_ID, "raw": raw.hex(" ")}
    if len(o) < 11:
        return out
    conn_nibble = o[0] & 0x0F
    out["connection_nibble"] = conn_nibble
    out["connection"] = {0: "wireless-2.4G", 2: "wired-usb"}.get(conn_nibble, f"unknown({conn_nibble})")
    out["sensor_class"] = (o[0] >> 4) & 0x0F
    out["dpi_stage"] = o[1]
    out["dpi_x"] = o[2] | (o[3] << 8)
    out["dpi_y"] = o[4] | (o[5] << 8)
    out["battery_flag_raw"] = o[6]
    out["battery_valid"] = o[6] == 0
    out["battery_percent"] = o[7]
    out["backlight_mode"] = o[8]
    out["key_scan_index"] = o[9]
    poll = o[10]
    out["polling_raw"] = poll
    out["polling_rate"] = POLLING_RATE_LABELS.get(poll, f"raw {poll}")
    if len(o) > 11:
        out["profile"] = o[11]
    if len(o) > 13:
        out["usb_pid"] = o[12] | (o[13] << 8)
    if len(o) > 17:
        out["rf_connected"] = bool((o[17] >> 7) & 1)
        out["rf_rssi"] = o[17] & 0x7F
    return out
