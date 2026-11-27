# -*- coding: utf-8 -*-
"""Linux hidraw transport for Rapoo devices - pure Python stdlib.

No pip dependencies: talks to /dev/hidraw nodes directly with POSIX
write() for output reports and HIDIOCGFEATURE ioctl for feature reports.
The only requirement is a udev rule granting unprivileged write access
(install.sh does that).
"""
from __future__ import annotations

import glob
import os
import re
import select
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .protocol import (
    REPORT_ID_OUTPUT,
    REPORT_ID_FEATURE,
    ACK_BUSY,
    STATUS_REPORT_ID,
    response_status_index,
)

# ---------------------------------------------------------------------------
# hidraw ioctl constants (linux/hidraw.h)
# ---------------------------------------------------------------------------
_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS
_IOC_WRITE = 1
_IOC_READ = 2


def _IOC(direction: int, type_: str, nr: int, size: int) -> int:
    return (direction << _IOC_DIRSHIFT) | (ord(type_) << _IOC_TYPESHIFT) | \
        (nr << _IOC_NRSHIFT) | (size << _IOC_SIZESHIFT)


def HIDIOCGFEATURE(size: int) -> int:
    return _IOC(_IOC_WRITE | _IOC_READ, "H", 0x07, size)


def HIDIOCSFEATURE(size: int) -> int:
    return _IOC(_IOC_WRITE | _IOC_READ, "H", 0x06, size)


RAPOO_VENDOR_ID = 0x24AE


@dataclass
class DeviceEndpoint:
    """One hidraw node of a Rapoo device (one per HID interface)."""
    path: str            # /dev/hidrawN
    vendor_id: int
    product_id: int
    interface_num: int
    name: str = ""
    sysfs: str = ""
    vendor_page: bool = False  # descriptor declares usage page 0xFF00


class RapooProtocolError(RuntimeError):
    """Device returned an unexpected/unparseable response."""


class RapooTransport:
    """Frame-level transport over one hidraw node."""

    def __init__(self, hidraw_path: str):
        self.path = hidraw_path
        self._fd: Optional[int] = None

    # -- lifecycle ----------------------------------------------------------
    def open(self) -> None:
        if self._fd is not None:
            return
        self._fd = os.open(self.path, os.O_RDWR)

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def __enter__(self) -> "RapooTransport":
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # -- primitives ---------------------------------------------------------
    def send_output_report(self, payload: bytes) -> None:
        """Sends a 32-byte command frame as output report 6.

        hidraw requires the report ID as the first byte of write() when the
        device uses numbered reports.
        """
        if self._fd is None:
            raise RuntimeError("transport not open")
        os.write(self._fd, bytes([REPORT_ID_OUTPUT]) + payload)

    def get_feature_report(self, length: int = 33) -> bytes:
        """Reads a feature report (ID 8) via HIDIOCGFEATURE."""
        if self._fd is None:
            raise RuntimeError("transport not open")
        buf = bytearray(length)
        buf[0] = REPORT_ID_FEATURE
        try:
            fcntl_ioctl(self._fd, HIDIOCGFEATURE(length), buf)
        except OSError as exc:
            raise RapooProtocolError(f"HIDIOCGFEATURE failed on {self.path}: {exc}") from exc
        return bytes(buf)

    def read_input_report(self, timeout_s: float) -> Optional[bytes]:
        """Blocks up to timeout_s for one input report (status report 7)."""
        if self._fd is None:
            raise RuntimeError("transport not open")
        r, _, _ = select.select([self._fd], [], [], timeout_s)
        if not r:
            return None
        try:
            return os.read(self._fd, 512)
        except OSError:
            return None

    def wait_for_report(self, report_id: int, timeout_s: float) -> Optional[bytes]:
        """Waits for an input report with the given ID, discarding others.

        Deliberately does not pre-flush: status reports are rare (~3s
        heartbeat) and a queued one may be the only delivery.
        """
        deadline = time.time() + timeout_s
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            chunk = self.read_input_report(timeout_s=min(remaining, 0.5))
            if chunk is None:
                if time.time() >= deadline:
                    return None
                continue
            if chunk and chunk[0] == report_id:
                return chunk

    # -- request/response ----------------------------------------------------
    def request(self, frame: bytes, timeout_ms: int = 2000, max_retries: int = 6,
                retry_delay_s: float = 0.05, command_delay_s: float = 0.01) -> bytes:
        """Sends one command frame and fetches the feature-report response.

        Retries while the device answers ACK 0x02 (busy) - writes can be
        momentarily rejected while the firmware commits its EEPROM.
        """
        self.send_output_report(frame)
        start = time.time()
        while True:
            if (time.time() - start) * 1000 > timeout_ms:
                raise TimeoutError(
                    f"device did not answer in {timeout_ms}ms (frame {frame.hex(' ')})")
            time.sleep(command_delay_s)
            resp = self.get_feature_report()
            status = resp[response_status_index(resp)] if len(resp) > 1 else (resp[0] if resp else 0)
            if status != ACK_BUSY:
                return resp
            if max_retries <= 0:
                raise RapooProtocolError("device busy after max retries")
            max_retries -= 1
            time.sleep(retry_delay_s)


def fcntl_ioctl(fd: int, request: int, buf: bytearray) -> int:
    # Local import: fcntl only exists on Linux; keeps the module importable
    # (and unit-testable) on any OS.
    import fcntl
    # fcntl.ioctl takes only positional args (mutate_flag must mutate our
    # bytearray so the kernel's feature-report reply lands in `buf`).
    return fcntl.ioctl(fd, request, buf, True)


# ---------------------------------------------------------------------------
# Device enumeration (Linux sysfs)
# ---------------------------------------------------------------------------
def _parse_report_descriptor(data: bytes) -> dict:
    """Minimal HID report-descriptor walk.

    Returns {"vendor_page": bool} - True when a collection declares the
    Rapoo vendor usage page 0xFF00 with usage 0x02 or 0x0E (the config
    interface). Enough of the item grammar to be reliable here.
    """
    vendor = False
    page = 0
    usage_stack: list = []
    usage = 0
    i = 0
    while i < len(data):
        head = data[i]
        i += 1
        if head == 0xFE:  # long item
            if i + 2 > len(data):
                break
            size = data[i] | (data[i + 1] << 8)
            i += 2 + size
            continue
        size = head & 0x03
        if size == 3:
            size = 4
        item_type = (head >> 2) & 0x03
        if i + size > len(data):
            break
        value = 0
        for k in range(size):
            value |= data[i + k] << (8 * k)
        i += size
        tag = head & 0xFC
        if item_type == 1:  # global
            if tag == 0x04:      # usage page
                page = value
        elif item_type == 2:  # local
            if tag in (0x08, 0x18, 0x28) or (0x38 <= tag <= 0x78 and (tag & 0x07) == 0x08):
                usage = value  # usage / usage min (approximation)
        elif item_type == 0:  # main
            if tag == 0xA0 or tag == 0xA1:  # physical/general collection
                usage_stack.append((page, usage))
                if page == 0xFF00 and usage in (0x02, 0x0E):
                    vendor = True
            elif tag == 0xC0:  # end collection
                if usage_stack:
                    usage_stack.pop()
    return {"vendor_page": vendor}


def _interface_number(sysfs_dev_dir: str) -> int:
    """USB interface number from a sysfs path like .../1-7:1.1/."""
    m = re.search(r"/\d+-\d+(?:\.\d+)*:\d+\.(\d+)/", sysfs_dev_dir.replace("\\", "/"))
    return int(m.group(1)) if m else 0


def enumerate_rapoo_endpoints() -> List[DeviceEndpoint]:
    """Finds all Rapoo hidraw nodes currently present on the system."""
    endpoints: List[DeviceEndpoint] = []
    for uevent_path in glob.glob("/sys/class/hidraw/hidraw*/device/uevent"):
        try:
            props = {}
            with open(uevent_path, "r", encoding="utf-8") as f:
                for line in f.read().splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        props[k.strip()] = v.strip()
            hid_id = props.get("HID_ID", "")
            parts = hid_id.split(":")
            if len(parts) < 3:
                continue
            bus = int(parts[0], 16)
            vid = int(parts[1], 16)
            pid = int(parts[2], 16)
            if vid != RAPOO_VENDOR_ID or bus not in (0x03, 0x05):
                continue
            dev_dir = str(Path(uevent_path).parent)
            node = "/dev/" + Path(dev_dir).parent.name
            desc_path = os.path.join(dev_dir, "report_descriptor")
            vendor_page = False
            if os.path.exists(desc_path):
                with open(desc_path, "rb") as f:
                    vendor_page = _parse_report_descriptor(f.read())["vendor_page"]
            endpoints.append(DeviceEndpoint(
                path=node,
                vendor_id=vid,
                product_id=pid,
                interface_num=_interface_number(os.path.realpath(dev_dir)),
                name=props.get("HID_NAME", "Rapoo Device"),
                sysfs=dev_dir,
                vendor_page=vendor_page,
            ))
        except (OSError, ValueError):
            continue
    endpoints.sort(key=lambda e: (e.vendor_page is False, e.interface_num))
    return endpoints


def pick_config_endpoint(endpoints: List[DeviceEndpoint]) -> Optional[DeviceEndpoint]:
    """Chooses the hidraw node that carries the configuration channel.

    Preference order: descriptor-declared vendor page -> interface 1 ->
    first Rapoo node. Vendor-page detection is the reliable path; the
    interface-1 fallback matches what the official driver uses.
    """
    if not endpoints:
        return None
    for ep in endpoints:
        if ep.vendor_page:
            return ep
    for ep in endpoints:
        if ep.interface_num == 1:
            return ep
    return endpoints[0]
