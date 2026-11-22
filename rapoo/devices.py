# -*- coding: utf-8 -*-
"""Rapoo device registry - 102 models extracted from the official web driver.

Manifest (data/rapoo_models.json) comes from the official WebHID driver
device table, extracted by pedro3z0/rapoo-software-linux (MIT).

pid convention: list index 0 = wired mouse PID (USB cable),
index 1 = dongle/receiver PID (2.4GHz). Single-entry models are cable-only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .protocol import dpi_limits

_DATA = Path(__file__).resolve().parent.parent / "data" / "rapoo_models.json"

# Sensor engine scan-rate class per PID, from the official driver device
# table (drives the ">=NKFPS engine scan rate" preset copy).
_SENSOR_SCAN_LABELS: Dict[int, str] = {}
for _pid in [5136, 5137, 5138, 5139, 5140, 5141, 5154, 5188, 5201, 17936, 17937,
             17938, 17939, 17940, 17941, 17954, 17969, 17970, 17971, 17973,
             17988, 18001]:
    _SENSOR_SCAN_LABELS[_pid] = "10K"
for _pid in [5142, 5143, 5144, 5152, 5153, 5155, 5194, 5195, 5202, 5203, 5209,
             5211, 5213, 5216, 5218, 5220, 5222, 5224, 5226, 5228, 5229, 5230,
             5232, 5233, 5234, 5235, 5236, 5237, 5238, 5239, 5240, 5281, 5282,
             5284, 5285, 5286, 5287, 5288, 5289, 5290, 6913, 6914, 6915, 6916,
             6917, 6918, 6919, 6920, 6921, 6922, 6923, 6924, 6925, 6926, 6927,
             6928, 6929, 6930, 6931, 6932, 6933, 6944, 6945, 17942, 17943,
             17944, 17952, 17953, 17955, 17975, 17984, 17994, 17995, 17997,
             18002, 18003, 18009, 18011, 18013, 18016, 18018, 18020, 18022,
             18024, 18026, 18028, 18029, 18030, 18032, 18033, 18034, 18035,
             18036, 18037, 18038, 18039, 18040, 18049, 18081, 18082, 18084,
             18085, 18086, 18087, 18088, 18089, 18090, 19201, 19202, 19203,
             19204, 19205, 19206, 19207, 19208, 19209, 19210, 19211, 19212,
             19213, 19214, 19215, 19216, 19217, 19218, 19219, 19220, 19221,
             19232, 19233]:
    _SENSOR_SCAN_LABELS[_pid] = "13K"

# Minimal fallback when the manifest file is missing.
_FALLBACK = [
    {"model": "VT3S MAX", "pids": [17953, 5153], "mcu": "N54L15",
     "sensor": "PAW3950U", "driver": "VT_nrf54L", "dongle": "Dongle_NordicL5",
     "battery": 800},
    {"model": "VT3 Air / VT0 Air MAX", "pids": [18081, 5281], "mcu": "N54L15",
     "sensor": "PAW3950U", "driver": "VT_nrf54L", "dongle": "Dongle_NordicL5",
     "battery": 300},
]


@dataclass
class DeviceModel:
    model: str
    pids: List[int]                       # [wired_pid, dongle_pid, ...]
    mcu: str = ""
    sensor: str = ""
    driver: str = ""
    dongle: str = ""
    battery: int = 0
    sensor_scan_label: str = ""
    capabilities: Dict[str, Any] = field(default_factory=dict)

    def matches_pid(self, pid: int) -> bool:
        return pid in self.pids

    @property
    def wired_pid(self) -> Optional[int]:
        return self.pids[0] if len(self.pids) > 1 else None

    @property
    def dongle_pid(self) -> Optional[int]:
        return self.pids[1] if len(self.pids) > 1 else None

    @property
    def dpi_range(self) -> "tuple[int, int]":
        return dpi_limits(self.sensor)

    @property
    def lod_scale(self) -> List[float]:
        from .protocol import LOD_SCALE_LV, LOD_SCALE_MV
        return LOD_SCALE_MV if self.sensor == "PAW3398" else LOD_SCALE_LV


class DeviceRegistry:
    def __init__(self) -> None:
        self._models: List[DeviceModel] = []
        self._load()

    def _load(self) -> None:
        try:
            with open(_DATA, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = _FALLBACK
        for item in data:
            pids = [int(p) for p in item.get("pids", [])]
            self._models.append(DeviceModel(
                model=item.get("model", ""),
                pids=pids,
                mcu=item.get("mcu", ""),
                sensor=item.get("sensor", ""),
                driver=item.get("driver", ""),
                dongle=item.get("dongle", ""),
                battery=int(item.get("battery", 0) or 0),
                sensor_scan_label=next(
                    (_SENSOR_SCAN_LABELS[p] for p in pids if p in _SENSOR_SCAN_LABELS), ""),
                capabilities=item.get("capabilities", {}),
            ))

    def find_by_pid(self, pid: int) -> Optional[DeviceModel]:
        for m in self._models:
            if m.matches_pid(pid):
                return m
        return None

    def all_models(self) -> List[DeviceModel]:
        return list(self._models)

    def find(self, name_substring: str) -> List[DeviceModel]:
        needle = name_substring.strip().lower()
        return [m for m in self._models if needle in m.model.lower()]


registry = DeviceRegistry()
