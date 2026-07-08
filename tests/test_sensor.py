"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import ClassVar

import pytest
from pytest import raises

ROOT = Path(__file__).resolve().parents[1]

LDC1612_STUB = (
    "class LDC1612:\n"
    "    def __init__(self, config): pass\n"
    "    def add_client(self, cb): pass\n"
)


def _purge_eddy_seek_modules() -> None:
    for name in list(sys.modules):
        if name == "extras.eddy_seek" or name.startswith("extras._eddy_seek"):
            del sys.modules[name]


@pytest.fixture
def eddy_seek_mod(tmp_path):
    klippy_root = tmp_path / "klippy"
    klippy_pkg = klippy_root / "klippy"
    klippy_pkg.mkdir(parents=True)
    (klippy_pkg / "__init__.py").write_text("")
    (klippy_pkg / "gcode.py").write_text(
        "class CommandError(Exception):\n    pass\n\n"
        "class GCodeCommand:\n    error = CommandError\n"
    )

    extras = klippy_root / "extras"
    extras.mkdir(parents=True)
    (extras / "ldc1612.py").write_text(LDC1612_STUB)
    (extras / "eddy_seek.py").symlink_to(ROOT / "src" / "eddy_seek.py")
    (extras / "_eddy_seek").symlink_to(ROOT / "src" / "_eddy_seek")

    sys.path.insert(0, str(klippy_root))
    try:
        yield importlib.import_module("extras.eddy_seek")
    finally:
        sys.path.pop(0)
        _purge_eddy_seek_modules()


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
    clients: ClassVar[list] = []
    name = "eddy_seek"

    def add_client(self, cb) -> None:
        _FakeSensor.clients.append(cb)


def _stream_host(eddy_seek_mod):
    class _StreamHost(eddy_seek_mod.EddySeek):
        def __init__(self) -> None:
            self._sensor = _FakeSensor()
            self._stream_refs = 0
            self._batch_client_added = False
            self._capturing = False
            self._total_samples = 0
            self._last_freq = 0.0
            self._status_samples: list[float] = []
            self._capture_buf: list[float] = []
            self._capture_count = 0
            self._sample_rate_hz: float | None = None

    return _StreamHost()


def test_load_sensor_requires_ldc1612(eddy_seek_mod):
    with raises(ValueError, match="sensor_type"):
        eddy_seek_mod.EddySeek._load_ldc1612(_FakeConfig("bogus"))


def test_sensor_stream_only_while_referenced(eddy_seek_mod):
    _FakeSensor.clients = []
    host = _stream_host(eddy_seek_mod)
    assert host._stream_refs == 0
    assert not host._batch_client_added

    with host.acquire_sensor_stream():
        assert host._stream_refs == 1
        assert host._batch_client_added
        assert len(_FakeSensor.clients) == 1

        with host.acquire_sensor_stream():
            assert host._stream_refs == 2
            assert len(_FakeSensor.clients) == 1

        assert host._stream_refs == 1

    assert host._stream_refs == 0
    assert not host._batch_client_added


def test_disconnect_stops_sensor_stream(eddy_seek_mod):
    _FakeSensor.clients = []
    host = _stream_host(eddy_seek_mod)
    with host.acquire_sensor_stream():
        host._capturing = True
        assert host._batch_client_added

    host._on_disconnect()
    assert host._stream_refs == 0
    assert not host._batch_client_added
    assert not host._capturing


def test_sample_rate_from_count(eddy_seek_mod):
    from eddy_seek import (
        _sample_rate_from_count,
    )

    assert _sample_rate_from_count(count=0, duration_s=0.2) is None
    assert _sample_rate_from_count(count=80, duration_s=0.2) == pytest.approx(400.0)


def test_handle_batch_capture_only_when_capturing(eddy_seek_mod):
    host = _stream_host(eddy_seek_mod)
    host._stream_refs = 1
    host._handle_batch({"data": [[0, 100.0], [0, 200.0]]})
    assert host._total_samples == 2
    assert host._capture_count == 0

    host.reset_capture()
    host._handle_batch({"data": [[0, 300.0], [0, 400.0]]})
    assert host._capture_count == 2
    assert host._capture_buf == [300.0, 400.0]
