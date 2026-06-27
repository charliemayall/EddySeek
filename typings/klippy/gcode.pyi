"""
Typings for Klipper gcode.py
https://github.com/Klipper3d/klipper/blob/master/klippy/gcode.py
"""

from collections.abc import Callable

class GCodeDispatch:
    def register_command(
        self,
        cmd: str,
        func: Callable[..., object] | None,
        when_not_ready: bool = False,
        desc: str | None = None,
    ) -> Callable[..., object] | None: ...
    def run_script_from_command(self, script: str) -> None: ...
    def request_restart(self, result: str) -> None: ...
