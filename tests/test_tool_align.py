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

from _eddy_seek.common import Offset, Position
from _eddy_seek.config import SeekConfig
from _eddy_seek.kconsole import KConsole
from _eddy_seek.tool_align import (
    _TARGET_SENSOR_OFFSET_FROM_REF,
    align_all_tools,
    align_tool_number,
    move_to_seek_start_pos,
)
from _eddy_seek.tools import Tool, ToolAlignConfig


class _FakeTools:
    def __init__(self, tools: list[Tool], printer: FakePrinter | None = None) -> None:
        self.tool_count = len(tools)
        self.tools = tools
        self._printer = printer

    def get_tool(self, tool_number: int) -> Tool:
        return self.tools[tool_number]

    apply_tool_offset = ToolAlignConfig.apply_tool_offset


def _assert_set_gcode_offset(script: str, *, x: float, y: float) -> None:
    vals = script.split(" ")
    assert vals[0] == "SET_GCODE_OFFSET"
    assert float(vals[1].split("=")[1]) == x
    assert float(vals[2].split("=")[1]) == y


def test_apply_tool_offset_sets_gcode_offset():
    printer = FakePrinter()
    tools = _FakeTools(
        [
            Tool(
                tool_number=0,
                offset=Offset(0.0, 0.0),
                manual_offset=Offset(0.0, 0.0),
                is_calibrated=True,
            ),
            Tool(
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
            Tool(
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
            Tool(
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


class _SensorTools:
    tool_count = 1

    def __init__(self, sensor_position: Position) -> None:
        self._sensor_position = sensor_position
        self.sensor_x = sensor_position.x
        self.sensor_y = sensor_position.y

    def sensor_position(self) -> Position:
        return self._sensor_position

    def get_tool(self, tool_number: int) -> Tool:
        return Tool.create_default(tool_number)


class _FakeSeekHost:
    def __init__(
        self, printer: FakePrinter, seek_config: SeekConfig | None = None
    ) -> None:
        self.printer = printer
        self.seek_config = seek_config or SeekConfig()
        self.console: KConsole | None = None


def test_move_to_seek_start_pos_moves_to_sensor_position():
    toolhead = RecordingToolhead(start=(1.0, 2.0))
    host = _FakeSeekHost(FakePrinter(toolhead=toolhead))
    start = move_to_seek_start_pos(
        host,  # ty: ignore[invalid-argument-type]
        _SensorTools(Position(10.0, 20.0)),  # ty: ignore[invalid-argument-type]
    )
    assert start == Position(10.0, 20.0)
    assert toolhead.moves == [[10.0, 20.0]]


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


def test_sensor_position_is_required():
    def configfile_printer():
        return _ConfigfilePrinter()

    with raises(ValueError, match="sensor_x"):
        ToolAlignConfig(as_config(FakeKlipperConfig(get_printer=configfile_printer)))

    with raises(ValueError, match="sensor_y"):
        ToolAlignConfig(
            as_config(FakeKlipperConfig(sensor_x=10.0, get_printer=configfile_printer))
        )

    cfg = ToolAlignConfig(
        as_config(
            FakeKlipperConfig(
                sensor_x=10.0, sensor_y=20.0, get_printer=configfile_printer
            )
        )
    )
    assert cfg.sensor_position() == Position(10.0, 20.0)


class _LoadMacroTools:
    tool_count = 4
    sensor_x = 10.0
    sensor_y = 20.0

    def __init__(self) -> None:
        self.load_calls: list[int] = []

    def format_load_macro(self, tool_number: int) -> str:
        return f"T{tool_number}"

    def run_load_macro(self, tool_number: int) -> None:
        self.load_calls.append(tool_number)

    def get_tool(self, tool_number: int) -> Tool:
        return Tool.create_default(tool_number)

    def update_tool(self, tool: Tool) -> None:
        pass

    def sensor_position(self) -> Position:
        return Position(self.sensor_x, self.sensor_y)


def test_align_tool_number_load_macro_only_when_requested():
    tools = _LoadMacroTools()
    host = _FakeSeekHost(FakePrinter(toolhead=RecordingToolhead()))
    center = Position(10.0, 20.0)
    ok = ok_seek_result()

    with patch("_eddy_seek.tool_align.align_tool", return_value=ok):
        align_tool_number(
            host,  # ty: ignore[invalid-argument-type]
            tools,  # ty: ignore[invalid-argument-type]
            FakeGcmd(),
            1,
            center,
            console=_console(),
            load_tool=False,
        )
        assert tools.load_calls == []

        align_tool_number(
            host,  # ty: ignore[invalid-argument-type]
            tools,  # ty: ignore[invalid-argument-type]
            FakeGcmd(),
            1,
            center,
            console=_console(),
            load_tool=True,
        )
        assert tools.load_calls == [1]


def test_align_tool_number_requires_tool0_after_restart():
    tools = _LoadMacroTools()
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
    tools = _LoadMacroTools()
    host = _FakeSeekHost(FakePrinter(toolhead=RecordingToolhead()))
    center = Position(10.0, 20.0)
    ok = ok_seek_result()
    seen: list[str] = []

    def capture_strategy(*_args, strategy=None, **_kwargs):
        seen.append(strategy or "")
        return ok

    with patch("_eddy_seek.tool_align.align_tool", side_effect=capture_strategy):
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


def test_align_all_tools_milestone_console_messages():
    tools = _LoadMacroTools()
    host = _FakeSeekHost(FakePrinter(toolhead=RecordingToolhead()))
    gcmd = FakeGcmd()
    ok = ok_seek_result()

    with (
        patch(
            "_eddy_seek.tool_align.move_to_seek_start_pos",
            return_value=Position(10.0, 20.0),
        ),
        patch("_eddy_seek.tool_align.align_tool", return_value=ok),
    ):
        result = align_all_tools(host, tools, gcmd, tool_count=2)  # ty: ignore[invalid-argument-type]

    assert result.status == "ok"
    assert gcmd.raw == [
        "echo: ES: Aligning tool 1 of 2…",
        "echo: Tool 0 reference - X=+10.10 Y=+19.80 mm",
        "echo: Aligning tool 2 of 2…",
        "echo: Tool 1 offset - X=0.1 Y=-0.2 mm",
        "echo: ES: 2 tools aligned - run SAVE_CONFIG to persist",
    ]


def test_align_all_tools_shares_artifact_run_context():
    tools = _LoadMacroTools()
    host = _FakeSeekHost(FakePrinter(toolhead=RecordingToolhead()))
    gcmd = FakeGcmd()
    ok = ok_seek_result()
    captured: list[dict] = []

    def capture_align_tool(host, gcmd, **kwargs):
        captured.append(kwargs)
        return ok

    with (
        patch(
            "_eddy_seek.tool_align.move_to_seek_start_pos",
            return_value=Position(10.0, 20.0),
        ),
        patch("_eddy_seek.tool_align.align_tool", side_effect=capture_align_tool),
    ):
        result = align_all_tools(host, tools, gcmd, tool_count=2)  # ty: ignore[invalid-argument-type]

    assert result.status == "ok"
    assert len(captured) == 2
    assert captured[0]["run_id"] == captured[1]["run_id"]
    assert captured[0]["artifact_write_at"] == captured[1]["artifact_write_at"]
    assert captured[0]["artifact_label"] == "tools_t0"
    assert captured[1]["artifact_label"] == "tools_t1"


def test_align_all_tools_clears_gcode_offset_on_exit():
    tools = _LoadMacroTools()
    printer = FakePrinter(toolhead=RecordingToolhead())
    host = _FakeSeekHost(printer)
    gcmd = FakeGcmd()
    ok = ok_seek_result()

    with (
        patch(
            "_eddy_seek.tool_align.move_to_seek_start_pos",
            return_value=Position(10.0, 20.0),
        ),
        patch("_eddy_seek.tool_align.align_tool", return_value=ok),
    ):
        align_all_tools(host, tools, gcmd, tool_count=1)  # ty: ignore[invalid-argument-type]

    _assert_set_gcode_offset(printer.gcode.scripts[-1], x=0.0, y=0.0)


def test_align_tool_number_clears_gcode_offset_after_load():
    tools = _LoadMacroTools()
    printer = FakePrinter(toolhead=RecordingToolhead())
    host = _FakeSeekHost(printer)
    center = Position(10.0, 20.0)

    with patch("_eddy_seek.tool_align.align_tool", return_value=ok_seek_result()):
        align_tool_number(
            host,  # ty: ignore[invalid-argument-type]
            tools,  # ty: ignore[invalid-argument-type]
            FakeGcmd(),
            1,
            center,
            console=_console(),
            load_tool=True,
        )
    _assert_set_gcode_offset(printer.gcode.scripts[0], x=0.0, y=0.0)


def test_align_tool0_warns_when_seek_offset_exceeds_sensor_threshold():
    tools = _SensorTools(Position(150.0, 150.0))
    host = _FakeSeekHost(FakePrinter(toolhead=RecordingToolhead()))
    gcmd = FakeGcmd()
    large_offset = Offset(1.5, 0.0)
    assert (
        max(abs(large_offset.x), abs(large_offset.y)) > _TARGET_SENSOR_OFFSET_FROM_REF
    )

    with patch(
        "_eddy_seek.tool_align.align_tool",
        return_value=ok_seek_result(offset=large_offset),
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
    assert center == Position(151.5, 150.0)
    assert tool is not None
    assert any("WARNING" in line and "sensor_x" in line for line in gcmd.raw)


def test_align_tool0_warns_for_reported_centre_vs_sensor_position():
    """Regression: ~1 mm Y miss (150,150 -> 150.44, 149.04) must warn."""
    tools = _SensorTools(Position(150.0, 150.0))
    host = _FakeSeekHost(FakePrinter(toolhead=RecordingToolhead()))
    gcmd = FakeGcmd()
    offset = Offset(0.4356, -0.9580)

    with patch(
        "_eddy_seek.tool_align.align_tool",
        return_value=ok_seek_result(offset=offset),
    ):
        _, center, error = align_tool_number(
            host,  # ty: ignore[invalid-argument-type]
            tools,  # ty: ignore[invalid-argument-type]
            gcmd,
            0,
            None,
            console=_console(gcmd),
        )

    assert error is None
    assert center == Position(150.4356, 149.0420)
    assert any("WARNING" in line and "X=+0.44 Y=-0.96 mm" in line for line in gcmd.raw)


@pytest.mark.parametrize(
    "tool_number,tools,center,offsets,expect_center,expect_tool_offset,check_stats",
    [
        pytest.param(
            0,
            lambda: _SensorTools(Position(150.0, 150.0)),
            None,
            [Offset(0.0, 0.0), Offset(0.2, 0.0), Offset(0.4, 0.0)],
            Position(150.2, 150.0),
            None,
            True,
            id="tool0",
        ),
        pytest.param(
            1,
            _LoadMacroTools,
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
        if tool_number == 0:
            stack.enter_context(
                patch(
                    "_eddy_seek.tool_align.move_to_seek_start_pos",
                    return_value=Position(150.0, 150.0),
                )
            )
        if check_stats:
            stack.enter_context(
                patch(
                    "_eddy_seek.tool_align.finalize_repeat_seek",
                    side_effect=capture_finalize,
                )
            )
        stack.enter_context(
            patch("_eddy_seek.tool_align.align_tool", side_effect=seek_results)
        )
        tool, got_center, error = align_tool_number(
            host,  # ty: ignore[invalid-argument-type]
            tools_obj,
            gcmd,
            tool_number,
            center,
            console=_console(gcmd if tool_number == 0 else None),
            run_id="abc",
            artifact_write_at=datetime.now(),
            repeats=3,
        )

    assert error is None
    assert tool is not None
    if expect_center is not None:
        assert got_center == expect_center
    if expect_tool_offset is not None:
        assert tool.offset.x == pytest.approx(expect_tool_offset.x)
        assert tool.offset.y == pytest.approx(expect_tool_offset.y)
    if check_stats:
        assert stats_called


def test_align_tool_number_fails_on_repeat_failure():
    tools = _SensorTools(Position(150.0, 150.0))
    host = _FakeSeekHost(FakePrinter(toolhead=RecordingToolhead()))
    gcmd = FakeGcmd()
    results = [
        ok_seek_result(offset=Offset(0.1, 0.0)),
        ok_seek_result(status="failed", offset=None, error_message="seek failed"),
    ]

    with (
        patch(
            "_eddy_seek.tool_align.move_to_seek_start_pos",
            return_value=Position(150.0, 150.0),
        ),
        patch("_eddy_seek.tool_align.align_tool", side_effect=results),
    ):
        tool, center, error = align_tool_number(
            host,  # ty: ignore[invalid-argument-type]
            tools,  # ty: ignore[invalid-argument-type]
            gcmd,
            0,
            None,
            console=_console(gcmd),
            run_id="abc",
            artifact_write_at=datetime.now(),
            repeats=2,
        )

    assert tool is None
    assert center is None
    assert error == "tool 0 alignment failed"


def test_align_tool0_no_sensor_warning_when_offset_within_threshold():
    tools = _SensorTools(Position(150.0, 150.0))
    host = _FakeSeekHost(FakePrinter(toolhead=RecordingToolhead()))
    gcmd = FakeGcmd()
    small_offset = Offset(0.5, 0.3)
    assert (
        max(abs(small_offset.x), abs(small_offset.y)) <= _TARGET_SENSOR_OFFSET_FROM_REF
    )

    with patch(
        "_eddy_seek.tool_align.align_tool",
        return_value=ok_seek_result(offset=small_offset),
    ):
        _, _, error = align_tool_number(
            host,  # ty: ignore[invalid-argument-type]
            tools,  # ty: ignore[invalid-argument-type]
            gcmd,
            0,
            None,
            console=_console(gcmd),
        )

    assert error is None
    assert not any("sensor_x" in line for line in gcmd.raw)
