"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

import json
import typing
from datetime import datetime
from pathlib import Path

from _eddy_seek.common import Offset
from _eddy_seek.config import SeekConfig
from _eddy_seek.plotting.primitives import ProbeRecord
from _eddy_seek.plotting.recorder import SessionRecorder
from _eddy_seek.session import SeekSessionResult, _write_seek_trace


class _TraceSensor:
    seek_config = SeekConfig(save_session_trace=True)

    def session_trace_config(self) -> dict:
        return {"seek": self.seek_config.to_dict()}


def test_write_seek_trace(tmp_path):
    host = _TraceSensor()
    host.seek_config = SeekConfig(save_session_trace=True, result_folder=str(tmp_path))
    result = SeekSessionResult(
        session_id="test-session",
        start_time=1.0,
        end_time=2.0,
        status="ok",
        offset=Offset(0.1, -0.2),
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

    write_at = datetime(2026, 7, 2, 14, 30)
    path = _write_seek_trace(host, result, probes, write_at=write_at)

    assert path is not None
    assert Path(path).is_file()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    assert payload["metadata"]["session_id"] == "test-session"
    assert payload["metadata"]["config"]["seek"]["strategy"] == "sweep_centroid"
    assert payload["metadata"]["config"]["seek"]["save_session_trace"] is True
    assert payload["probes"][0]["samples_hz"] == [12340.0, 12350.0, 12346.0]
    assert _write_seek_trace(host, result, probes, write_at=write_at) == path


def test_probe_record_round_trip():
    probe = ProbeRecord(Offset(1.0, 2.0), 100.0, (99.0, 101.0))
    payload = probe.to_dict()
    assert payload["at"] == {"x": 1.0, "y": 2.0}
    assert payload["mean_hz"] == 100.0
    assert payload["samples_hz"] == [99.0, 101.0]
    recorder = SessionRecorder(trace=True, plots=False)
    recorder.record(probe)
    assert recorder.to_probe_dicts() == [probe.to_dict()]


def test_write_seek_trace_labeled_filename(tmp_path):
    host = _TraceSensor()
    host.seek_config = SeekConfig(save_session_trace=True, result_folder=str(tmp_path))
    result = SeekSessionResult(
        session_id="test-session",
        start_time=1.0,
        end_time=2.0,
        status="ok",
        offset=Offset(0.1, -0.2),
        passes_run=3,
        error_message=None,
    )
    write_at = datetime(2026, 7, 2, 14, 30)
    path = _write_seek_trace(
        host,
        result,
        [],
        run_id="batch123",
        suffix="tools_t0_ternary",
        write_at=write_at,
    )
    assert path is not None
    assert path.endswith("14_30_02_07_26_batch123/tools_t0_ternary.json")


def test_seek_session_collects_probes_when_enabled():
    class _Sensor:
        seek_config = SeekConfig(save_session_trace=True)
        _buf: typing.ClassVar[list[float]] = [100.0, 101.0, 102.0]

        def session_trace_config(self) -> dict:
            return {"seek": {}}

        def peek_capture_samples(self) -> list[float]:
            return list(self._buf)

        def get_capture_mean(self, min_samples: int = 5) -> float:
            return sum(self._buf) / len(self._buf)

    recorder = SessionRecorder(trace=True, plots=False)
    recorder.record(
        ProbeRecord(
            at=Offset(1.0, 2.0),
            mean_hz=_Sensor().get_capture_mean(),
            samples_hz=tuple(_Sensor().peek_capture_samples()),
        )
    )
    probes = recorder.to_probe_dicts()
    assert probes[0]["mean_hz"] == 101.0
    assert len(probes[0]["samples_hz"]) == 3
