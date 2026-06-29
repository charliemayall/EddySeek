"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""

from _eddy_seek.config import SeekConfig
from _eddy_seek.printer_handler import Tool, ToolAlignConfig
from _eddy_seek.tool_align import (
    apply_tool_offset,
    resolve_tool0_start,
    tool0_center_xy,
)
from _eddy_seek.session import Position


from pytest import raises


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


def test_tool0_center_xy_offset_applies():
    center = tool0_center_xy(10.0, 20.0, Position(1.5, -0.5))
    assert center == Position(11.5, 19.5)


def test_apply_tool_offset_sets_gcode_offset():
    printer = _FakePrinter()
    tools = _FakeTools(
        [
            Tool(
                tool_number=0,
                offset_x=0.0,
                offset_y=0.0,
                is_calibrated=True,
            ),
            Tool(
                tool_number=1,
                offset_x=1.5,
                offset_y=-0.5,
                is_calibrated=True,
            ),
        ]
    )
    tool = apply_tool_offset(tools, printer, 1)  # type: ignore[arg-type]
    assert tool["offset_x"] == 1.5
    assert printer.gcode.scripts == ["SET_GCODE_OFFSET X=1.500000 Y=-0.500000"]


def test_apply_tool_offset_rejects_uncalibrated():
    printer = _FakePrinter()
    tools = _FakeTools(
        [
            Tool(
                tool_number=0,
                offset_x=0.0,
                offset_y=0.0,
                is_calibrated=False,
            ),
        ]
    )
    with raises(ValueError, match="not calibrated"):
        apply_tool_offset(tools, printer, 0)  # type: ignore[arg-type]


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

    def lookup_object(self, name: str):
        if name == "toolhead":
            return self._toolhead
        raise KeyError(name)


class _Host:
    def __init__(self, printer: _MovePrinter) -> None:
        self.printer = printer


class _FakeGcmd:
    def respond_info(self, msg: str) -> None:
        pass


class _SensorTools:
    def __init__(self, sensor_xy: tuple[float, float] | None) -> None:
        self._sensor_xy = sensor_xy

    def sensor_position(self) -> tuple[float, float] | None:
        return self._sensor_xy


def test_resolve_tool0_start_moves_to_sensor_position():
    toolhead = _RecordingToolhead(start=(1.0, 2.0))
    host = _Host(_MovePrinter(toolhead))
    start = resolve_tool0_start(
        host,  # type: ignore[arg-type]
        SeekConfig(),
        _SensorTools((10.0, 20.0)),  # type: ignore[arg-type]
        _FakeGcmd(),
        label="EDDY_SEEK_TOOLS",
    )
    assert start == (10.0, 20.0)
    assert toolhead.moves == [[10.0, 20.0]]


def test_resolve_tool0_start_without_sensor_position_uses_current_xy():
    toolhead = _RecordingToolhead(start=(3.0, 4.0))
    host = _Host(_MovePrinter(toolhead))
    start = resolve_tool0_start(
        host,  # type: ignore[arg-type]
        SeekConfig(),
        _SensorTools(None),  # type: ignore[arg-type]
        _FakeGcmd(),
        label="EDDY_SEEK_TOOLS",
    )
    assert start == (3.0, 4.0)
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


class _ToolConfig:
    def __init__(self, **opts) -> None:
        self._opts = opts

    def get_printer(self) -> _ConfigfilePrinter:
        return _ConfigfilePrinter()

    def getint(self, key: str, default: int, **kwargs) -> int:
        return int(self._opts.get(key, default))

    def get(self, key: str, default: str = "") -> str:
        return self._opts.get(key, default)

    def getfloat(self, key: str, default=None, **kwargs):
        return self._opts.get(key, default)

    def error(self, msg: str) -> ValueError:
        return ValueError(msg)


def test_sensor_position_both_or_neither():
    none_set = ToolAlignConfig(_ToolConfig())  # type: ignore[arg-type]
    assert none_set.sensor_position() is None

    both_set = ToolAlignConfig(_ToolConfig(sensor_x=10.0, sensor_y=20.0))  # type: ignore[arg-type]
    assert both_set.sensor_position() == (10.0, 20.0)

    with raises(ValueError, match="both sensor_x and sensor_y"):
        ToolAlignConfig(_ToolConfig(sensor_x=10.0))  # type: ignore[arg-type]
