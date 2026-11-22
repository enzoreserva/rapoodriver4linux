# -*- coding: utf-8 -*-
"""Unit tests that run WITHOUT hardware.

Protocol framing, memory map codecs, response layout parsing, device
registry and the high-level mouse logic are exercised against a fake
transport that simulates the device EEPROM.

Run:  python3 -m pytest tests/   ou   python3 -m unittest discover tests
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rapoo import protocol as P
from rapoo.devices import registry
from rapoo.mouse import RapooMouse, MouseSettings
from rapoo.transport import DeviceEndpoint, _parse_report_descriptor


# ---------------------------------------------------------------------------
# framing / codecs
# ---------------------------------------------------------------------------
class ProtocolTests(unittest.TestCase):
    def test_read_packet_layout(self):
        pkt = P.build_read_packet(P.CONN_WIRELESS, P.get_address(640, 0), 8)
        self.assertEqual(len(pkt), 32)
        self.assertEqual(pkt[0], 0xA5)
        self.assertEqual(pkt[1], 0xA4)
        self.assertEqual(pkt[2], 8)
        addr = 1536 + 640  # profile 0 base + offset
        self.assertEqual(list(pkt[3:7]), [addr & 0xFF, (addr >> 8) & 0xFF,
                                          (addr >> 16) & 0xFF, (addr >> 24) & 0xFF])
        self.assertEqual(pkt[7:], bytes(25))

    def test_write_packet_layout(self):
        pkt = P.build_write_packet(P.CONN_WIRED, P.get_absolute_address(96),
                                   bytes([0x00]))
        self.assertEqual(pkt[0], 0xFF)
        self.assertEqual(pkt[1], 0xA5)
        self.assertEqual(pkt[2], 1)
        self.assertEqual(list(pkt[3:7]), [96, 0, 0, 0])
        self.assertEqual(pkt[7], 0x00)

    def test_payload_limits(self):
        with self.assertRaises(ValueError):
            P.build_read_packet(P.CONN_WIRED, P.get_address(0), P.MAX_PAYLOAD + 1)
        with self.assertRaises(ValueError):
            P.build_write_packet(P.CONN_WIRED, P.get_address(0),
                                 bytes(P.MAX_PAYLOAD + 1))

    def test_profile_bases(self):
        self.assertEqual(P.get_address(0, 0), [0x00, 0x06, 0x00, 0x00])  # 1536 = 0x0600
        self.assertEqual(P.get_address(0, 1), [0x00, 0x0A, 0x00, 0x00])  # 2560 = 0x0A00
        self.assertEqual(P.get_address(0, 7), [0x00, 0x22, 0x00, 0x00])  # 8704 = 0x2200

    def test_polling_codes(self):
        for code, label in P.POLLING_RATE_LABELS.items():
            self.assertIn(label, P.LABEL_TO_POLLING_RATE)
            self.assertEqual(P.LABEL_TO_POLLING_RATE[label], code)
        self.assertEqual(P.POLLING_RATE_LABELS[129], "8000Hz")
        self.assertEqual(P.POLLING_RATE_LABELS[8], "125Hz")

    def test_dpi_codecs(self):
        raw = P.encode_dpi_list([800, 1600, 3200, 5000, 0, 0, 0])
        self.assertEqual(len(raw), 14)
        self.assertEqual(P.decode_dpi_list(raw)[2], 3200)
        self.assertEqual(P.encode_dpi_value(45000), bytes([0xC8, 0xAF]))  # 45000 = 0xAFC8 LE

    def test_response_layout_a(self):
        # Layout A: status byte first (report id stripped by kernel)
        resp = bytes([0x01, 0x08, 0x00, 0x90, 0xC8, 0x88, 0xAF] + [0x00] * 26)
        status, payload = P.parse_feature_response(resp)
        self.assertEqual(status, 0x01)
        self.assertEqual(payload[0], 0xC8)
        self.assertEqual(payload[1:3], b"\x88\xAF")

    def test_response_layout_b(self):
        # Layout B: report ID 0x08 first (typical Linux HIDIOCGFEATURE)
        resp = bytes([0x08, 0x01, 0x08, 0x00, 0x90, 0xC8, 0x88, 0xAF] + [0x00] * 25)
        status, payload = P.parse_feature_response(resp)
        self.assertEqual(status, 0x01)
        self.assertEqual(payload[0], 0xC8)

    def test_status_report_parse(self):
        # Sample captured by the community project: 07 20 01 40 06 40 06 01 5D ...
        raw = bytes([0x07, 0x20, 0x01, 0x40, 0x06, 0x40, 0x06, 0x01, 0x5D,
                     0x00, 0x03, 0x01, 0x00, 0x21, 0x46])
        st = P.parse_status_report(raw)
        self.assertEqual(st["dpi_stage"], 1)
        self.assertEqual(st["dpi_x"], 0x0640)  # 1600
        self.assertEqual(st["dpi_y"], 0x0640)
        self.assertEqual(st["battery_percent"], 0x5D)  # 93
        self.assertEqual(st["polling_rate"], "1000Hz")
        self.assertEqual(st["usb_pid"], 0x4621)  # VT3S MAX wired PID
        self.assertEqual(st["connection"], "wireless-2.4G")  # low nibble 0 = 2.4G link


# ---------------------------------------------------------------------------
# device registry
# ---------------------------------------------------------------------------
class DeviceRegistryTests(unittest.TestCase):
    def test_vt3s_max_present(self):
        model = registry.find_by_pid(0x1421)
        self.assertIsNotNone(model)
        self.assertEqual(model.model, "VT3S MAX")
        self.assertEqual(model.wired_pid, 0x4621)
        self.assertEqual(model.sensor, "PAW3950U")

    def test_vt3s_max_dpi_limits(self):
        model = registry.find_by_pid(0x1421)
        lo, hi = model.dpi_range
        self.assertEqual((lo, hi), (10, 45000))

    def test_manifest_loaded(self):
        self.assertGreaterEqual(len(registry.all_models()), 100)

    def test_unknown_pid_dpi_limits(self):
        lo, hi = P.dpi_limits("PAW3398")
        self.assertEqual((lo, hi), (50, 26000))


# ---------------------------------------------------------------------------
# report descriptor parser (vendor page detection)
# ---------------------------------------------------------------------------
class DescriptorParserTests(unittest.TestCase):
    def test_vendor_page_detected(self):
        # usage page 0xFF00 (06 00 FF), usage 0x02 (09 02), collection
        desc = bytes([0x06, 0x00, 0xFF, 0x09, 0x02, 0xA1, 0x01,
                      0x09, 0x06, 0x15, 0x00, 0x26, 0xFF, 0x00, 0x75, 0x08,
                      0x95, 0x20, 0x91, 0x02, 0xC0])
        self.assertTrue(_parse_report_descriptor(desc)["vendor_page"])

    def test_generic_mouse_not_vendor(self):
        desc = bytes([0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x09, 0x01,
                      0xA1, 0x00, 0x05, 0x09, 0x19, 0x01, 0x29, 0x03,
                      0x15, 0x00, 0x25, 0x01, 0x95, 0x03, 0x75, 0x01,
                      0x81, 0x02, 0xC0, 0xC0])
        self.assertFalse(_parse_report_descriptor(desc)["vendor_page"])


# ---------------------------------------------------------------------------
# high-level mouse logic against a fake device
# ---------------------------------------------------------------------------
class FakeEEPROMTransport:
    """Simulates the device: reports 6/8 protocol + virtual EEPROM."""

    def __init__(self, eeprom=None, layout="B", fail_writes=0):
        self.mem = bytearray(16384) if eeprom is None else eeprom
        self.layout = layout
        self.fail_writes = fail_writes
        self.opened = False
        self.frames = []

    def open(self):
        self.opened = True

    def close(self):
        self.opened = False

    def send_output_report(self, frame):
        self.frames.append(frame)
        # protocol: byte1 0xA4 read / 0xA5 write
        addr = frame[3] | (frame[4] << 8) | (frame[5] << 16) | (frame[6] << 24)
        length = frame[2]
        if frame[1] == 0xA5:
            if self.fail_writes > 0:
                self.fail_writes -= 1  # pretend the write never landed
            else:
                self.mem[addr:addr + length] = frame[7:7 + length]
        self._last_read = addr
        self._last_len = length

    def get_feature_report(self, length=33):
        data = bytes(self.mem[self._last_read:self._last_read + self._last_len])
        if self.layout == "B":
            resp = bytearray([0x08, 0x01, 0x08, 0x00, 0x90]) + data
            resp += bytes(33 - len(resp))
            return bytes(resp)
        resp = bytearray([0x01, 0x08, 0x00, 0x90]) + data
        resp += bytes(33 - len(resp))
        return bytes(resp)

    def read_input_report(self, timeout_s):
        return None

    def wait_for_report(self, report_id, timeout_s):
        return None

    def request(self, frame, timeout_ms=2000, max_retries=6,
                retry_delay_s=0.0, command_delay_s=0.0):
        self.send_output_report(frame)
        return self.get_feature_report()


def _make_mouse(layout="B") -> RapooMouse:
    ep = DeviceEndpoint(path="/dev/hidrawX", vendor_id=0x24AE,
                        product_id=0x1421, interface_num=1,
                        name="Rapoo VT3S MAX")
    mouse = RapooMouse(endpoint=ep, connection=P.CONN_WIRELESS)
    fake = FakeEEPROMTransport(layout=layout)
    mouse.transport = fake  # type: ignore[assignment]
    mouse.payload_offset = 5 if layout == "B" else 4
    mouse._calibrated = True
    return mouse


class MouseLogicTests(unittest.TestCase):
    def test_defaults_then_get(self):
        mouse = _make_mouse()
        # seed factory-ish values
        mouse.transport.mem[1536 + 640] = 1           # 1000Hz
        mouse.transport.mem[1536 + 641] = 3           # key scan 1000Hz
        mouse.transport.mem[1536 + 644] = 4           # LOD index 4
        mouse.transport.mem[1536 + 645] = 1           # motion sync on
        mouse.transport.mem[1536 + 648:1536 + 662] = P.encode_dpi_list(
            [800, 1600, 3200, 5000, 10000, 20000, 30000])
        mouse.transport.mem[1536 + 662] = 4           # 5 stages enabled
        mouse.transport.mem[1536 + 664] = 2           # active stage 2
        s = mouse.get_settings(0)
        self.assertEqual(s.polling_rate, "1000Hz")
        self.assertEqual(s.key_scan_rate, "1000Hz")
        self.assertEqual(s.dpi_stage, 2)
        self.assertEqual(s.dpi_stage_count, 5)
        self.assertEqual(s.dpi_stages_x[2], 3200)
        self.assertTrue(s.motion_sync)

    def test_set_dpi_roundtrip_layout_b(self):
        mouse = _make_mouse("B")
        mouse.set_dpi_stage_value(1, dpi=1600)
        s = mouse.get_settings(0)
        self.assertEqual(s.dpi_stages_x[1], 1600)

    def test_set_dpi_roundtrip_layout_a(self):
        mouse = _make_mouse("A")
        mouse.set_dpi_stage_value(0, dpi=800)
        s = mouse.get_settings(0)
        self.assertEqual(s.dpi_stages_x[0], 800)

    def test_set_polling(self):
        mouse = _make_mouse()
        mouse.set_polling_rate("8000Hz")
        self.assertEqual(mouse.transport.mem[1536 + 640], 129)
        mouse.set_polling_rate(2000)
        self.assertEqual(mouse.transport.mem[1536 + 640], 132)
        with self.assertRaises(ValueError):
            mouse.set_polling_rate("777Hz")

    def test_dpi_validation(self):
        mouse = _make_mouse()
        with self.assertRaises(ValueError):
            mouse.set_dpi_stage_value(0, dpi=45001)   # above PAW3950U limit
        with self.assertRaises(ValueError):
            mouse.set_dpi_stage_value(0, dpi=9)       # below limit
        with self.assertRaises(ValueError):
            mouse.set_dpi_stage_value(7, dpi=1600)    # stage out of range

    def test_stage_count(self):
        mouse = _make_mouse()
        mouse.set_dpi_stage_count(7)
        self.assertEqual(mouse.transport.mem[1536 + 662], 6)

    def test_write_verified_retries(self):
        mouse = _make_mouse()
        mouse.transport.fail_writes = 2  # fail twice, succeed on 3rd try
        mouse.set_polling_rate(500)      # verify-after-write triggers retries
        self.assertEqual(mouse.transport.mem[1536 + 640], 2)

    def test_chunked_full_profile_backup_restore(self):
        mouse = _make_mouse()
        # scramble the whole profile block
        for i in range(1024):
            mouse.transport.mem[1536 + i] = (i * 7 + 3) & 0xFF
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "prof0.rapoo.json"
            mouse.backup_profile(out, 0)
            mouse.transport.mem[1536:1536 + 1024] = bytes(1024)  # wipe
            mouse.restore_profile(out, 0)
            self.assertEqual(bytes(mouse.transport.mem[1536:1536 + 1024]),
                             bytes((i * 7 + 3) & 0xFF for i in range(1024)))

    def test_keyscan_validation(self):
        mouse = _make_mouse()
        mouse.set_key_scan_rate("8000Hz")
        self.assertEqual(mouse.transport.mem[1536 + 641], 6)
        with self.assertRaises(ValueError):
            mouse.set_key_scan_rate("125Hz")  # retail accepts 1000..8000 only

    def test_sensor_mode_slot_validation(self):
        mouse = _make_mouse()
        mouse.transport.mem[1536 + 640] = 129  # 8000Hz slot (index 6)
        mouse.set_sensor_mode("frenzy")        # legal at 8000
        self.assertEqual(mouse.transport.mem[1536 + 732 + 6], 5)
        with self.assertRaises(ValueError):
            mouse.set_sensor_mode("office")    # office not legal at 8000Hz

    def test_status_watch_once(self):
        mouse = _make_mouse()
        self.assertIsNone(mouse.read_status(timeout_s=0.2))


if __name__ == "__main__":
    unittest.main()
