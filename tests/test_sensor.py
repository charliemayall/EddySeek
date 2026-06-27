"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""

from pytest import raises

from eddy_seek import EddySeek


class _FakeConfig:
    def __init__(self, sensor_type: str = "bogus") -> None:
        self._sensor_type = sensor_type

    def get(self, key: str, default: str = "") -> str:
        if key == "sensor_type":
            return self._sensor_type
        return default

    def error(self, msg: str) -> None:
        raise ValueError(msg)


def test_load_sensor_requires_ldc1612():
    with raises(ValueError, match="sensor_type"):
        EddySeek._load_ldc1612(_FakeConfig("bogus"))  # type: ignore[arg-type]
