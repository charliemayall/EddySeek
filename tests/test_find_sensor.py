"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fakes import FakeGcmd, FakeGcode, FakePrinter, RecordingToolhead, ok_seek_result

from eddy_seek.common import Offset
from eddy_seek.config import SeekConfig
from eddy_seek.find_sensor import find_sensor_threshold, run_find_sensor
from eddy_seek.kconsole import KConsole
from eddy_seek.session import ArtifactRunContext, SeekSessionResult
from eddy_seek.strategy.base import SeekExitKind


def _console() -> KConsole:
    return KConsole(FakeGcmd(), SeekConfig())


class _FakeHost:
    def __init__(self, printer: FakePrinter) -> None:
        self.printer = printer
        self.seek_config = SeekConfig()


def test_find_sensor_threshold():
    assert find_sensor_threshold(0.05) == pytest.approx(0.4)
    assert find_sensor_threshold(0.1) == pytest.approx(0.5)
    assert find_sensor_threshold(0.2) == pytest.approx(0.5)


def test_run_find_sensor_stops_when_offset_below_threshold():
    host = _FakeHost(FakePrinter())
    console = _console()
    gcmd = FakeGcmd()
    artifact = ArtifactRunContext(run_label="start", write_at=datetime.now())
    strategy = MagicMock()
    strategy.name = "sweep_centroid"
    offsets = [Offset(1.0, 0.0), Offset(0.1, 0.0)]
    run_calls: list[str] = []

    def fake_run(
        _self,
        _gcmd,
        _strategy,
        *,
        boundaries=True,
        announce_plot=None,
        recover_max_passes=False,
    ):
        offset = offsets.pop(0)
        run_calls.append(_self.artifact_label)
        return ok_seek_result(offset=offset)

    with patch("eddy_seek.find_sensor.SeekSession.run", fake_run):
        result = run_find_sensor(
            host,  # ty: ignore[invalid-argument-type]
            gcmd,
            console=console,
            strategy=strategy,
            artifact=artifact,
            max_iters=10,
        )

    assert result is not None
    assert result.offset == Offset(0.1, 0.0)
    assert run_calls == ["start_f1", "start_f2"]


def test_run_find_sensor_fails_after_max_iters():
    host = _FakeHost(FakePrinter())
    console = _console()
    gcmd = FakeGcmd()
    artifact = ArtifactRunContext(run_label="start", write_at=datetime.now())
    strategy = MagicMock()
    strategy.name = "sweep_centroid"

    def fake_run(
        _self,
        _gcmd,
        _strategy,
        *,
        boundaries=True,
        announce_plot=None,
        recover_max_passes=False,
    ):
        return ok_seek_result(offset=Offset(1.0, 0.0))

    with patch("eddy_seek.find_sensor.SeekSession.run", fake_run):
        result = run_find_sensor(
            host,  # ty: ignore[invalid-argument-type]
            gcmd,
            console=console,
            strategy=strategy,
            artifact=artifact,
            max_iters=3,
        )

    assert result is None


def test_run_find_sensor_continues_after_max_passes():
    host = _FakeHost(FakePrinter())
    console = _console()
    gcmd = FakeGcmd()
    artifact = ArtifactRunContext(run_label="start", write_at=datetime.now())
    strategy = MagicMock()
    strategy.name = "sweep_centroid"
    results = [
        SeekSessionResult(
            session_id="s1",
            start_time=0.0,
            end_time=1.0,
            status="ok",
            offset=Offset(1.0, 0.0),
            passes_run=6,
            error_message=None,
            exit_kind=SeekExitKind.MAX_PASSES,
        ),
        ok_seek_result(offset=Offset(0.1, 0.0)),
    ]
    run_calls = 0

    def fake_run(
        _self,
        _gcmd,
        _strategy,
        *,
        boundaries=True,
        announce_plot=None,
        recover_max_passes=False,
    ):
        nonlocal run_calls
        run_calls += 1
        return results.pop(0)

    with patch("eddy_seek.find_sensor.SeekSession.run", fake_run):
        result = run_find_sensor(
            host,  # ty: ignore[invalid-argument-type]
            gcmd,
            console=console,
            strategy=strategy,
            artifact=artifact,
            max_iters=10,
        )

    assert result is not None
    assert result.offset == Offset(0.1, 0.0)
    assert run_calls == 2


def test_run_find_sensor_fails_fast_on_seek_failure():
    host = _FakeHost(FakePrinter())
    console = _console()
    gcmd = FakeGcmd()
    artifact = ArtifactRunContext(run_label="start", write_at=datetime.now())
    strategy = MagicMock()
    strategy.name = "sweep_centroid"
    call_count = 0

    def fake_run(
        _self,
        _gcmd,
        _strategy,
        *,
        boundaries=True,
        announce_plot=None,
        recover_max_passes=False,
    ):
        nonlocal call_count
        call_count += 1
        return SeekSessionResult(
            session_id="s",
            start_time=0.0,
            end_time=1.0,
            status="failed",
            offset=None,
            passes_run=0,
            error_message="diverged",
        )

    with patch("eddy_seek.find_sensor.SeekSession.run", fake_run):
        result = run_find_sensor(
            host,  # ty: ignore[invalid-argument-type]
            gcmd,
            console=console,
            strategy=strategy,
            artifact=artifact,
            max_iters=10,
        )

    assert result is None
    assert call_count == 1


def test_run_find_sensor_does_not_restore_start_between_seeks():
    """Unlike REPEATS, walk-in leaves nozzle at finish and seeks again."""
    host = _FakeHost(FakePrinter())
    console = _console()
    gcmd = FakeGcmd()
    artifact = ArtifactRunContext(run_label="start", write_at=datetime.now())
    strategy = MagicMock()
    strategy.name = "sweep_centroid"
    offsets = [Offset(1.0, 0.0), Offset(0.1, 0.0)]
    run_calls: list[bool] = []

    def fake_run(
        _self,
        _gcmd,
        _strategy,
        *,
        boundaries=True,
        announce_plot=None,
        recover_max_passes=False,
    ):
        run_calls.append(recover_max_passes)
        return ok_seek_result(offset=offsets.pop(0))

    with patch("eddy_seek.find_sensor.SeekSession.run", fake_run):
        run_find_sensor(
            host,  # ty: ignore[invalid-argument-type]
            gcmd,
            console=console,
            strategy=strategy,
            artifact=artifact,
        )

    assert run_calls == [True, True]


def test_eddy_seek_start_find_zero_uses_single_seek():
    from eddy_seek.host import EddySeek

    tools = MagicMock()
    tools.sensor_z = None
    config = MagicMock()
    config.get_printer.return_value = FakePrinter(
        toolhead=RecordingToolhead(),
        gcode=FakeGcode(),
    )
    with (
        patch.object(EddySeek, "_load_ldc1612", return_value=MagicMock(name="ldc")),
        patch("eddy_seek.host.SeekSession") as session_cls,
        patch("eddy_seek.host.run_find_sensor") as find_mock,
        patch("eddy_seek.host.load_seek_config", return_value=SeekConfig()),
        patch("eddy_seek.host.tool_align_from_config", return_value=tools),
    ):
        eddy = EddySeek(config)
        gcmd = FakeGcmd(FIND="0")
        eddy.cmd_EDDY_SEEK_START(gcmd)

    session_cls.assert_called_once()
    find_mock.assert_not_called()


def test_eddy_seek_start_find_one_uses_walk_in():
    from eddy_seek.host import EddySeek

    tools = MagicMock()
    tools.sensor_z = None
    config = MagicMock()
    config.get_printer.return_value = FakePrinter(
        toolhead=RecordingToolhead(),
        gcode=FakeGcode(),
    )
    with (
        patch.object(EddySeek, "_load_ldc1612", return_value=MagicMock(name="ldc")),
        patch("eddy_seek.host.SeekSession") as session_cls,
        patch("eddy_seek.host.run_find_sensor") as find_mock,
        patch("eddy_seek.host.load_seek_config", return_value=SeekConfig()),
        patch("eddy_seek.host.tool_align_from_config", return_value=tools),
    ):
        eddy = EddySeek(config)
        gcmd = FakeGcmd(FIND="1")
        eddy.cmd_EDDY_SEEK_START(gcmd)

    find_mock.assert_called_once()
    session_cls.assert_not_called()
