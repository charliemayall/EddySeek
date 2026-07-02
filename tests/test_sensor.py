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

    def error(self, msg: str) -> ValueError:
        return ValueError(msg)


class _FakeSensor:
    clients: list = []
    name = "eddy_seek"

    def add_client(self, cb) -> None:
        _FakeSensor.clients.append(cb)


class _StreamHost(EddySeek):
    def __init__(self) -> None:
        self._sensor = _FakeSensor()
        self._stream_refs = 0
        self._stream_active = False
        self._batch_client_added = False


def test_load_sensor_requires_ldc1612():
    with raises(ValueError, match="sensor_type"):
        EddySeek._load_ldc1612(_FakeConfig("bogus"))  # type: ignore[arg-type]


def test_sensor_stream_only_while_referenced():
    _FakeSensor.clients = []
    host = _StreamHost()
    assert host._stream_refs == 0
    assert not host._batch_client_added

    host.acquire_sensor_stream()
    assert host._stream_refs == 1
    assert host._batch_client_added
    assert len(_FakeSensor.clients) == 1

    host.acquire_sensor_stream()
    assert host._stream_refs == 2
    assert len(_FakeSensor.clients) == 1

    host.release_sensor_stream()
    assert host._stream_refs == 1
    assert host._stream_active

    host.release_sensor_stream()
    assert host._stream_refs == 0
    assert not host._stream_active
    assert host._handle_batch({"data": []}) is False
    assert not host._batch_client_added
