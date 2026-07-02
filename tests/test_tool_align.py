"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""

from _eddy_seek.config import SeekConfig
from _eddy_seek.common import Position
from _eddy_seek.tools import (
    Tool,
    ToolAlignConfig,
    apply_tool_offset,
)
from _eddy_seek.tool_align import (
    align_tool_number,
    move_to_seek_start_pos,
)
from _eddy_seek.session import SeekSessionResult


from pytest import raises
from unittest.mock import patch


class _FakeGcode:
    def __init__(self) -> None:
        self.scripts: list[str] = []

    def run_script_from_command(self, script: str) -> None:
        self.scripts.append(script)


class _FakePrinter:
    def __init__(self) -> None:
        self.gcode = _FakeGcode()

    def lookup_object(self, name: str):
        if name == "gcode":
            return self.gcode
        raise KeyError(name)


class _FakeTools:
    def __init__(self, tools: list[Tool]) -> None:
        self.tool_count = len(tools)
        self.tools = tools

    def get_tool(self, tool_number: int) -> Tool:
        return self.tools[tool_number]


def test_position_offset_applies():
    center = Position(10.0, 20.0) + Position(1.5, -0.5)
    assert center == Position(11.5, 19.5)


def test_apply_tool_offset_sets_gcode_offset():
    printer = _FakePrinter()
    tools = _FakeTools(
        [
            Tool(
                tool_number=0,
                offset=Position(0.0, 0.0),
                manual_offset=Position(0.0, 0.0),
                is_calibrated=True,
            ),
            Tool(
                tool_number=1,
                offset=Position(1.5, -0.5),
                manual_offset=Position(0.0, 0.0),
                is_calibrated=True,
            ),
        ]
    )
    tool = apply_tool_offset(tools, printer, 1)
    assert tool.offset.x == 1.5
    assert printer.gcode.scripts == ["SET_GCODE_OFFSET X=1.500000 Y=-0.500000"]


def test_apply_tool_offset_includes_manual_adjust():
    printer = _FakePrinter()
    tools = _FakeTools(
        [
            Tool(
                tool_number=0,
                offset=Position(1.0, 2.0),
                manual_offset=Position(0.1, -0.2),
                is_calibrated=True,
            ),
        ]
    )
    apply_tool_offset(tools, printer, 0)
    assert printer.gcode.scripts == ["SET_GCODE_OFFSET X=1.100000 Y=1.800000"]


def test_apply_tool_offset_rejects_uncalibrated():
    printer = _FakePrinter()
    tools = _FakeTools(
        [
            Tool(
                tool_number=0,
                offset=Position(0.0, 0.0),
                manual_offset=Position(0.0, 0.0),
                is_calibrated=False,
            ),
        ]
    )
    with raises(ValueError, match="not calibrated"):
        apply_tool_offset(tools, printer, 0)


class _RecordingToolhead:
    def __init__(self, start: tuple[float, float]) -> None:
        self.pos = [start[0], start[1], 0.0, 0.0]
        self.moves: list[list[float | None]] = []

    def manual_move(self, coord, speed) -> None:
        self.moves.append(coord)
        for i, v in enumerate(coord):
            if v is not None:
                self.pos[i] = v

    def wait_moves(self) -> None:
        pass

    def get_position(self) -> list[float]:
        return list(self.pos)


class _MovePrinter:
    def __init__(self, toolhead: _RecordingToolhead) -> None:
        self._toolhead = toolhead
        self.gcode = _FakeGcode()

    def lookup_object(self, name: str):
        if name == "toolhead":
            return self._toolhead
        if name == "gcode":
            return self.gcode
        raise KeyError(name)


class _FakeGcmd:
    def respond_info(self, msg: str) -> None:
        pass


class _SensorTools:
    def __init__(self, sensor_position: Position) -> None:
        self._sensor_position = sensor_position

    def sensor_position(self) -> Position:
        return self._sensor_position


class _FakeSeekHost:
    def __init__(
        self, printer: _MovePrinter, seek_config: SeekConfig | None = None
    ) -> None:
        self.printer = printer
        self.seek_config = seek_config or SeekConfig()


def test_move_to_seek_start_pos_moves_to_sensor_position():
    toolhead = _RecordingToolhead(start=(1.0, 2.0))
    host = _FakeSeekHost(_MovePrinter(toolhead))  # type: ignore[arg-type]
    start = move_to_seek_start_pos(
        host,  # type: ignore[arg-type]
        _SensorTools(Position(10.0, 20.0)),  # type: ignore[arg-type]
        _FakeGcmd(),
        label="EDDY_SEEK_TOOLS",
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


class _ToolConfig:
    def __init__(self, **opts) -> None:
        self._opts = opts

    def get_printer(self) -> _ConfigfilePrinter:
        return _ConfigfilePrinter()

    def getint(self, key: str, default: int, **kwargs) -> int:
        return int(self._opts.get(key, default))

    def get(self, key: str, default: str = "") -> str:
        return self._opts.get(key, default)

    def getfloat(self, key: str, default: float | None = None, **kwargs):
        if key in self._opts:
            return self._opts[key]
        if default is not None:
            return default
        raise self.error(f"Option '{key}' is required")

    def error(self, msg: str) -> ValueError:
        return ValueError(msg)


def test_sensor_position_is_required():
    with raises(ValueError, match="sensor_x"):
        ToolAlignConfig(_ToolConfig())  # type: ignore[arg-type]

    with raises(ValueError, match="sensor_y"):
        ToolAlignConfig(_ToolConfig(sensor_x=10.0))  # type: ignore[arg-type]

    cfg = ToolAlignConfig(_ToolConfig(sensor_x=10.0, sensor_y=20.0))  # type: ignore[arg-type]
    assert cfg.sensor_position() == Position(10.0, 20.0)


class _LoadMacroTools:
    tool_count = 4
    load_calls: list[int] = []

    def format_load_macro(self, tool_number: int) -> str:
        return f"T{tool_number}"

    def run_load_macro(self, tool_number: int) -> None:
        self.load_calls.append(tool_number)

    def get_tool(self, tool_number: int) -> Tool:
        return Tool.create_default(tool_number)


class _OffsetClearPrinter(_FakePrinter):
    def __init__(self) -> None:
        super().__init__()
        self._toolhead = _RecordingToolhead(start=(0.0, 0.0))

    def lookup_object(self, name: str):
        if name == "toolhead":
            return self._toolhead
        return super().lookup_object(name)


def test_align_tool_number_load_macro_only_when_requested():
    tools = _LoadMacroTools()
    host = _FakeSeekHost(_MovePrinter(_RecordingToolhead(start=(0.0, 0.0))))  # type: ignore[arg-type]
    center = Position(10.0, 20.0)
    ok = SeekSessionResult("s", 0.0, 1.0, "ok", Position(0.1, -0.2), 1, None)

    with patch("_eddy_seek.tool_align.align_tool_at", return_value=ok):
        align_tool_number(
            host,  # type: ignore[arg-type]
            tools,  # type: ignore[arg-type]
            _FakeGcmd(),
            1,
            center,
            load_tool=False,
        )
        assert tools.load_calls == []

        align_tool_number(
            host,  # type: ignore[arg-type]
            tools,  # type: ignore[arg-type]
            _FakeGcmd(),
            1,
            center,
            load_tool=True,
        )
        assert tools.load_calls == [1]


def test_align_tool_number_clears_gcode_offset_after_load():
    tools = _LoadMacroTools()
    printer = _OffsetClearPrinter()
    host = _FakeSeekHost(printer)  # type: ignore[arg-type]
    center = Position(10.0, 20.0)
    ok = SeekSessionResult("s", 0.0, 1.0, "ok", Position(0.1, -0.2), 1, None)

    with patch("_eddy_seek.tool_align.align_tool_at", return_value=ok):
        align_tool_number(
            host,  # type: ignore[arg-type]
            tools,  # type: ignore[arg-type]
            _FakeGcmd(),
            1,
            center,
            load_tool=True,
        )
    assert printer.gcode.scripts == ["SET_GCODE_OFFSET X=0.000000 Y=0.000000"]
