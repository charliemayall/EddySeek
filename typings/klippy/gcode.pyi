"""
Typings for Klipper gcode.py
https://github.com/Klipper3d/klipper/blob/master/klippy/gcode.py
"""

from collections.abc import Callable
from typing import Any, Type

class CommandError(Exception):
    pass

class GCodeCommand:
    error: Type[CommandError]
    def get_command(self) -> str: ...
    def get_commandline(self) -> str: ...
    def get_command_parameters(self) -> dict[str, str]: ...
    def get_raw_command_parameters(self) -> str: ...
    def respond_info(self, msg: str, log: bool = True) -> None: ...
    def respond_raw(self, msg: str) -> None: ...
    def ack(self, msg: str | None = None) -> bool: ...
    def get(
        self,
        name: str,
        default: Any = ...,
        parser: Callable[[str], Any] = str,
        minval: float | None = None,
        maxval: float | None = None,
        above: float | None = None,
        below: float | None = None,
    ) -> Any: ...
    def get_int(
        self,
        name: str,
        default: Any = ...,
        minval: int | None = None,
        maxval: int | None = None,
    ) -> int: ...
    def get_float(
        self,
        name: str,
        default: Any = ...,
        minval: float | None = None,
        maxval: float | None = None,
        above: float | None = None,
        below: float | None = None,
    ) -> float: ...

class GCodeDispatch:
    error: Type[CommandError]
    def register_command(
        self,
        cmd: str,
        func: Callable[..., object] | None,
        when_not_ready: bool = False,
        desc: str | None = None,
    ) -> Callable[..., object] | None: ...
    def run_script_from_command(self, script: str) -> None: ...
    def request_restart(self, result: str) -> None: ...
    def create_gcode_command(
        self, command: str, commandline: str, params: dict[str, str]
    ) -> GCodeCommand: ...
