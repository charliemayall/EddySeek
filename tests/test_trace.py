"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""

import json

from _eddy_seek.config import SeekConfig
from _eddy_seek.common import Position
from _eddy_seek.session import (
    SeekSession,
    SeekSessionResult,
    _write_seek_trace,
)


class _TraceSensor:
    seek_config = SeekConfig(save_session_trace=True)

    def session_trace_config(self) -> dict:
        return {"seek": self.seek_config.to_dict()}


def test_write_seek_trace(tmp_path, monkeypatch):
    monkeypatch.setattr("_eddy_seek.session.tempfile.gettempdir", lambda: str(tmp_path))
    result = SeekSessionResult(
        session_id="test-session",
        start_time=1.0,
        end_time=2.0,
        status="ok",
        offset=Position(0.1, -0.2),
        passes_run=3,
        error_message=None,
    )
    probes = [
        {
            "x": 0.0,
            "y": 0.0,
            "mean_hz": 12345.6,
            "samples_hz": [12340.0, 12350.0, 12346.0],
        }
    ]

    _write_seek_trace(_TraceSensor(), result, probes, [])

    path = tmp_path / "seek_trace.json"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["metadata"]["session_id"] == "test-session"
    assert payload["metadata"]["config"]["seek"]["window_size"] == 20
    assert payload["metadata"]["config"]["seek"]["strategy"] == "sweep_centroid"
    assert payload["metadata"]["config"]["seek"]["save_session_trace"] is True
    assert payload["probes"][0]["samples_hz"] == [12340.0, 12350.0, 12346.0]
    assert _write_seek_trace(_TraceSensor(), result, probes, []) == str(path)


def test_seek_session_collects_probes_when_enabled():
    class _Sensor:
        seek_config = SeekConfig(save_session_trace=True)
        _buf = [100.0, 101.0, 102.0]

        def session_trace_config(self) -> dict:
            return {"seek": {}}

        def peek_capture_samples(self) -> list[float]:
            return list(self._buf)

        def get_capture_mean(self, min_samples: int = 5) -> float:
            return sum(self._buf) / len(self._buf)

    session = SeekSession.__new__(SeekSession)
    session._host = _Sensor()
    session.config = SeekConfig()
    session._save_trace = True
    session._probes = []
    session._plot_traces = []

    session._probes.append(
        {
            "x": 1.0,
            "y": 2.0,
            "mean_hz": _Sensor().get_capture_mean(),
            "samples_hz": _Sensor().peek_capture_samples(),
        }
    )
    assert session._probes[0]["mean_hz"] == 101.0
    assert len(session._probes[0]["samples_hz"]) == 3
