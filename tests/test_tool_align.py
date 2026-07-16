"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from contextlib import ExitStack
from datetime import datetime
from unittest.mock import patch

import pytest
from fakes import (
    FakeGcmd,
    FakeKlipperConfig,
    FakePrinter,
    RecordingToolhead,
    as_config,
    as_tools,
    ok_seek_result,
)
from pytest import raises

from eddy_seek.common import Offset, Position
from eddy_seek.config import SeekConfig
from eddy_seek.kconsole import KConsole
from eddy_seek.session import ArtifactRunContext
from eddy_seek.tool_align import (
    align_tool_number,
    move_to_seek_start_pos,
)
from eddy_seek.tools.diy import DiyTool
from eddy_seek.tools.types import tool_align_from_config


class _FakeTools:
    def __init__(
        self, tools: list[DiyTool], printer: FakePrinter | None = None
    ) -> None:
        self.tool_count = len(tools)
        self.tools = tools
        self._printer = printer

    def get_tool(self, tool_number: int) -> DiyTool:
        return self.tools[tool_number]

    def apply_tool_offset(self, tool_number: int) -> DiyTool:
        if self._printer is None:
            raise AttributeError("printer")
        try:
            tool = self.get_tool(tool_number)
        except IndexError as exc:
            raise ValueError(str(exc)) from exc
        if not tool.is_calibrated:
            raise ValueError(
                f"Tool {tool_number} is not calibrated, and you are trying to apply an offset."
            )
        eff = tool.effective_offset
        gcode = self._printer.lookup_object("gcode")
        gcode.run_script_from_command(f"SET_GCODE_OFFSET {eff.to_gcode()}")
        return tool


def _assert_set_gcode_offset(script: str, *, x: float, y: float) -> None:
    vals = script.split(" ")
    assert vals[0] == "SET_GCODE_OFFSET"
    assert float(vals[1].split("=")[1]) == x
    assert float(vals[2].split("=")[1]) == y


def test_apply_tool_offset_sets_gcode_offset():
    printer = FakePrinter()
    tools = _FakeTools(
        [
            DiyTool(
                tool_number=0,
                offset=Offset(0.0, 0.0),
                manual_offset=Offset(0.0, 0.0),
                is_calibrated=True,
            ),
            DiyTool(
                tool_number=1,
                offset=Offset(1.5, -0.5),
                manual_offset=Offset(0.0, 0.0),
                is_calibrated=True,
            ),
        ],
        printer=printer,
    )
    tool = as_tools(tools).apply_tool_offset(1)
    assert tool.offset.x == 1.5
    _assert_set_gcode_offset(printer.gcode.scripts[0], x=1.5, y=-0.5)


def test_apply_tool_offset_includes_manual_adjust():
    printer = FakePrinter()
    tools = _FakeTools(
        [
            DiyTool(
                tool_number=0,
                offset=Offset(1.0, 2.0),
                manual_offset=Offset(0.1, -0.2),
                is_calibrated=True,
            ),
        ],
        printer=printer,
    )
    as_tools(tools).apply_tool_offset(0)
    _assert_set_gcode_offset(printer.gcode.scripts[0], x=1.1, y=1.8)


def test_apply_tool_offset_rejects_uncalibrated():
    printer = FakePrinter()
    tools = _FakeTools(
        [
            DiyTool(
                tool_number=0,
                offset=Offset(0.0, 0.0),
                manual_offset=Offset(0.0, 0.0),
                is_calibrated=False,
            ),
        ],
        printer=printer,
    )
    with raises(ValueError, match="not calibrated"):
        as_tools(tools).apply_tool_offset(0)


def _console(gcmd: FakeGcmd | None = None) -> KConsole:
    return KConsole(gcmd or FakeGcmd(), SeekConfig())


class _AlignTools:
    tool_count = 4

    def get_tool(self, tool_number: int) -> DiyTool:
        return DiyTool.create_default(tool_number)


class _FakeSeekHost:
    def __init__(
        self, printer: FakePrinter, seek_config: SeekConfig | None = None
    ) -> None:
        self.printer = printer
        self.seek_config = seek_config or SeekConfig()
        self.console: KConsole | None = None


def test_move_to_seek_start_pos_uses_current_xy():
    toolhead = RecordingToolhead(start=(1.0, 2.0))
    host = _FakeSeekHost(FakePrinter(toolhead=toolhead))
    start = move_to_seek_start_pos(host)  # ty: ignore[invalid-argument-type]
    assert start == Position(1.0, 2.0)
    assert toolhead.moves == []


class _ConfigfileMain:
    def has_section(self, section: str) -> bool:
        return False


class _Configfile:
    def read_main_config(self) -> _ConfigfileMain:
        return _ConfigfileMain()


class _ConfigfilePrinter:
    def lookup_object(self, name: str):
        if name == "configfile":
            return _Configfile()
        raise KeyError(name)


def test_sensor_z_is_optional():
    def configfile_printer():
        return _ConfigfilePrinter()

    cfg = tool_align_from_config(
        as_config(FakeKlipperConfig(get_printer=configfile_printer))
    )
    assert cfg.sensor_z is None

    cfg = tool_align_from_config(
        as_config(FakeKlipperConfig(sensor_z=5.0, get_printer=configfile_printer))
    )
    assert cfg.sensor_z == 5.0


def test_align_tool0_seeks_from_current_xy():
    tools = _AlignTools()
    tools.tool_count = 1
    toolhead = RecordingToolhead(start=(5.0, 10.0))
    host = _FakeSeekHost(FakePrinter(toolhead=toolhead))
    gcmd = FakeGcmd()
    offset = Offset(0.1, -0.2)

    with patch(
        "eddy_seek.tool_align.align_tool",
        return_value=ok_seek_result(offset=offset),
    ):
        tool, center, error = align_tool_number(
            host,  # ty: ignore[invalid-argument-type]
            tools,  # ty: ignore[invalid-argument-type]
            gcmd,
            0,
            None,
            console=_console(gcmd),
        )

    assert error is None
    assert tool is not None
    assert center == Position(5.1, 9.8)
    assert toolhead.moves == []


def test_align_tool_number_approaches_x_then_y():
    tools = _AlignTools()
    toolhead = RecordingToolhead(start=(5.0, 10.0))
    host = _FakeSeekHost(FakePrinter(toolhead=toolhead))
    tool0_center = Position(20.0, 30.0)
    ok = ok_seek_result()

    with patch("eddy_seek.tool_align.align_tool", return_value=ok):
        align_tool_number(
            host,  # ty: ignore[invalid-argument-type]
            tools,  # ty: ignore[invalid-argument-type]
            FakeGcmd(),
            1,
            tool0_center,
            console=_console(),
        )

    assert toolhead.moves == [[20.0, 10.0], [20.0, 30.0]]


def test_align_tool_number_requires_tool0_after_restart():
    tools = _AlignTools()
    host = _FakeSeekHost(FakePrinter(toolhead=RecordingToolhead()))

    _, _, error = align_tool_number(
        host,  # ty: ignore[invalid-argument-type]
        tools,  # ty: ignore[invalid-argument-type]
        FakeGcmd(),
        1,
        None,
        console=_console(),
    )

    assert error is not None
    assert "Klipper restart clears the reference" in error
    assert "EDDY_SEEK_TOOL TOOL=0" in error


def test_align_tool_number_passes_strategy_override():
    tools = _AlignTools()
    host = _FakeSeekHost(FakePrinter(toolhead=RecordingToolhead()))
    center = Position(10.0, 20.0)
    ok = ok_seek_result()
    seen: list[str] = []

    def capture_strategy(*_args, strategy=None, **_kwargs):
        seen.append(strategy or "")
        return ok

    with patch("eddy_seek.tool_align.align_tool", side_effect=capture_strategy):
        align_tool_number(
            host,  # ty: ignore[invalid-argument-type]
            tools,  # ty: ignore[invalid-argument-type]
            FakeGcmd(),
            1,
            center,
            console=_console(),
            strategy="centroid",
        )

    assert seen == ["centroid"]


def test_align_tool_number_clears_gcode_offset():
    tools = _AlignTools()
    printer = FakePrinter(toolhead=RecordingToolhead())
    host = _FakeSeekHost(printer)
    center = Position(10.0, 20.0)

    with patch("eddy_seek.tool_align.align_tool", return_value=ok_seek_result()):
        align_tool_number(
            host,  # ty: ignore[invalid-argument-type]
            tools,  # ty: ignore[invalid-argument-type]
            FakeGcmd(),
            1,
            center,
            console=_console(),
        )
    _assert_set_gcode_offset(printer.gcode.scripts[0], x=0.0, y=0.0)


@pytest.mark.parametrize(
    "tool_number,tools,center,offsets,expect_center,expect_tool_offset,check_stats",
    [
        pytest.param(
            0,
            _AlignTools,
            None,
            [Offset(0.0, 0.0), Offset(0.2, 0.0), Offset(0.4, 0.0)],
            Position(0.2, 0.0),
            None,
            True,
            id="tool0",
        ),
        pytest.param(
            1,
            _AlignTools,
            Position(10.0, 20.0),
            [Offset(1.0, 0.0), Offset(1.2, 0.0), Offset(1.4, 0.0)],
            None,
            Offset(1.2, 0.0),
            False,
            id="tool1",
        ),
    ],
)
def test_align_tool_number_averages_repeats(
    tool_number,
    tools,
    center,
    offsets,
    expect_center,
    expect_tool_offset,
    check_stats,
):
    tools_obj = tools() if callable(tools) else tools
    if tool_number == 0:
        tools_obj.tool_count = 1
    host = _FakeSeekHost(FakePrinter(toolhead=RecordingToolhead()))
    gcmd = FakeGcmd()
    seek_results = [ok_seek_result(offset=offset) for offset in offsets]
    stats_called = False

    def capture_finalize(_host, _console, repeated, **kwargs):
        nonlocal stats_called
        stats_called = True
        recorded = list(repeated.offsets)
        assert len(recorded) == 3
        assert recorded[0].x == pytest.approx(offsets[0].x)
        assert recorded[2].x == pytest.approx(offsets[2].x)

    with ExitStack() as stack:
        if check_stats:
            stack.enter_context(
                patch(
                    "eddy_seek.tool_align.finalize_repeat_seek",
                    side_effect=capture_finalize,
                )
            )
        stack.enter_context(
            patch("eddy_seek.tool_align.align_tool", side_effect=seek_results)
        )
        tool, got_center, error = align_tool_number(
            host,  # ty: ignore[invalid-argument-type]
            tools_obj,
            gcmd,
            tool_number,
            center,
            console=_console(gcmd if tool_number == 0 else None),
            artifact=ArtifactRunContext(run_label="run", write_at=datetime.now()),
            repeats=3,
        )

    assert error is None
    assert tool is not None
    if expect_center is not None:
        assert got_center.x == pytest.approx(expect_center.x)
        assert got_center.y == pytest.approx(expect_center.y)
    if expect_tool_offset is not None:
        assert tool.offset.x == pytest.approx(expect_tool_offset.x)
        assert tool.offset.y == pytest.approx(expect_tool_offset.y)
    if check_stats:
        assert stats_called


def test_align_tool_number_fails_on_repeat_failure():
    tools = _AlignTools()
    tools.tool_count = 1
    host = _FakeSeekHost(FakePrinter(toolhead=RecordingToolhead()))
    gcmd = FakeGcmd()
    results = [
        ok_seek_result(offset=Offset(0.1, 0.0)),
        ok_seek_result(status="failed", offset=None, error_message="seek failed"),
    ]

    with patch("eddy_seek.tool_align.align_tool", side_effect=results):
        tool, center, error = align_tool_number(
            host,  # ty: ignore[invalid-argument-type]
            tools,  # ty: ignore[invalid-argument-type]
            gcmd,
            0,
            None,
            console=_console(gcmd),
            artifact=ArtifactRunContext(run_label="run", write_at=datetime.now()),
            repeats=2,
        )

    assert tool is None
    assert center is None
    assert error == "tool 0 alignment failed"
