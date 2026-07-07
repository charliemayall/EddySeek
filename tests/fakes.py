"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.


Shared Klipper test doubles for EddySeek tests.

Behold, a circus of pyright hoop jumpings
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

from _eddy_seek.common import Offset
from _eddy_seek.session import SeekSessionResult

PLOT_SESSION_ID = "abcd1234"
PLOT_WRITE_AT = datetime(2026, 7, 2, 14, 30)
PLOT_RUN_DIR = "2026-07-02_14-30-00_run"
PLOT_HTML_SUFFIX = f"{PLOT_RUN_DIR}/session.html"


_MISSING = object()


class CommandError(Exception):
    """Matches ``klippy.gcode.CommandError`` for test doubles."""


if TYPE_CHECKING:
    from klippy.extras.configfile import ConfigWrapper
    from klippy.gcode import CommandError as KlipperCommandError
    from klippy.gcode import GCodeCommand, GCodeDispatch
    from klippy.klippy import Printer

    from _eddy_seek.tools import ToolAlignConfig

    _GCodeCommandBase = GCodeCommand
    _GCodeDispatchBase = GCodeDispatch
    _PrinterBase = Printer
    _CmdError = KlipperCommandError
else:
    _GCodeCommandBase = object
    _GCodeDispatchBase = object
    _PrinterBase = object
    _CmdError = CommandError


class FakeGcmd(_GCodeCommandBase):
    error = _CmdError

    def __init__(
        self,
        params: dict[str, str] | None = None,
        **kwargs: str,
    ) -> None:
        merged = {**(params or {}), **kwargs}
        self._params = {k.upper(): str(v) for k, v in merged.items()}
        self.raw: list[str] = []

    def get_command(self) -> str:
        return "FAKE"

    def get_commandline(self) -> str:
        return "FAKE"

    def get_command_parameters(self) -> dict[str, str]:
        return dict(self._params)

    def get_raw_command_parameters(self) -> str:
        return " ".join(f"{k}={v}" for k, v in self._params.items())

    def respond_info(self, msg: str, log: bool = True) -> None:
        pass

    def respond_raw(self, msg: str) -> None:
        self.raw.append(msg)

    def ack(self, msg: str | None = None) -> bool:
        return True

    def get(
        self,
        name: str,
        default: Any = "",
        parser: Callable[[str], Any] = str,
        minval: float | None = None,
        maxval: float | None = None,
        above: float | None = None,
        below: float | None = None,
    ) -> Any:
        return self._params.get(name.upper(), default)

    def get_int(
        self,
        name: str,
        default: Any = "",
        minval: int | None = None,
        maxval: int | None = None,
    ) -> int:
        return int(self.get(name, default))

    def get_float(
        self,
        name: str,
        default: Any = "",
        minval: float | None = None,
        maxval: float | None = None,
        above: float | None = None,
        below: float | None = None,
    ) -> float:
        return float(self.get(name, default))


class FakeGcode(_GCodeDispatchBase):
    error = _CmdError

    def __init__(self) -> None:
        self.scripts: list[str] = []

    def respond_raw(self, msg: str) -> None:
        pass

    def respond_info(self, msg: str, log: bool = True) -> None:
        pass

    def register_command(
        self,
        cmd: str,
        func: Callable[..., object] | None,
        when_not_ready: bool = False,
        desc: str | None = None,
    ) -> Callable[..., object] | None:
        return func

    def run_script_from_command(self, script: str) -> None:
        self.scripts.append(script)

    def request_restart(self, result: str) -> None:
        pass

    def create_gcode_command(
        self, command: str, commandline: str, params: dict[str, str]
    ) -> FakeGcmd:
        return FakeGcmd(params)


class FakeKlipperConfig:
    error = ValueError

    def __init__(
        self,
        *,
        get_printer: Any | None = None,
        **options: Any,
    ) -> None:
        self._options = options
        self._get_printer = get_printer

    def get_printer(self) -> Printer:
        if self._get_printer is None:
            raise AttributeError("get_printer")
        return self._get_printer()

    def get(self, key: str, default: str = "") -> str:
        if key not in self._options:
            return default
        return str(self._options[key])

    def getint(self, key: str, default: int, **kwargs: Any) -> int:
        return int(self._options.get(key, default))

    def getfloat(self, key: str, default: float | None = None, **kwargs: Any) -> float:
        if key in self._options:
            value = self._options[key]
            return float(value) if not isinstance(value, (int, float)) else value
        if default is not None:
            return default
        raise self.error(f"Option '{key}' is required")

    def getboolean(self, key: str, default: bool = False, **kwargs: Any) -> bool:
        if key not in self._options:
            return default
        return str(self._options[key]).lower() in ("true", "1", "yes", "on")


def as_config(fake: FakeKlipperConfig) -> ConfigWrapper:
    return cast("ConfigWrapper", fake)


def as_printer(fake: FakePrinter) -> Printer:
    return cast("Printer", fake)


def as_tools(fake: Any) -> ToolAlignConfig:
    return cast("ToolAlignConfig", fake)


class RecordingToolhead:
    def __init__(self, start: tuple[float, float] = (0.0, 0.0)) -> None:
        self.pos = [start[0], start[1], 0.0, 0.0]
        self.moves: list[list[float | None]] = []

    def manual_move(self, coord, speed) -> None:
        self.moves.append(coord)
        for i, value in enumerate(coord):
            if value is not None:
                self.pos[i] = value

    def wait_moves(self) -> None:
        pass

    def get_position(self) -> list[float]:
        return list(self.pos)


class FakePrinter(_PrinterBase):
    config_error = Exception
    command_error = Exception
    start_args: dict[str, Any] = {}  # noqa:RUF012

    def __init__(self, **objects: Any) -> None:
        self.gcode = objects.get("gcode", FakeGcode())
        self._objects = objects

    def get_reactor(self) -> Any:
        reactor = MagicMock()
        reactor.monotonic.return_value = 0.0
        return reactor

    def get_start_args(self) -> dict[str, Any]:
        return dict(self.start_args)

    def add_object(self, name: str, obj: object) -> None:
        self._objects[name] = obj

    def lookup_object(self, name: str, default: Any = _MISSING) -> Any:
        if name in self._objects:
            return self._objects[name]
        if name == "gcode":
            return self.gcode
        if default is not _MISSING:
            return default
        raise KeyError(name)

    def lookup_objects(self, module: str | None = None) -> list[tuple[str, object]]:
        return list(self._objects.items())

    def register_event_handler(self, event: str, callback: Callable[..., Any]) -> None:
        pass

    def set_rollover_info(self, name: str, info: str, log: bool = True) -> None:
        pass


def fake_motion_printer(toolhead: MagicMock | None = None) -> tuple[Any, MagicMock]:
    toolhead = toolhead or MagicMock()
    printer = MagicMock()
    printer.lookup_object.return_value = toolhead
    return printer, toolhead


def ok_seek_result(
    offset: Offset | None = Offset(0.1, -0.2),
    **kwargs: object,
) -> SeekSessionResult:
    defaults: dict[str, object] = {
        "session_id": "s",
        "start_time": 0.0,
        "end_time": 1.0,
        "status": "ok",
        "offset": offset,
        "passes_run": 1,
        "error_message": None,
    }
    defaults.update(kwargs)
    return SeekSessionResult(**defaults)  # pyright: ignore[reportArgumentType]
