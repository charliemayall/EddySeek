"""
Typings for Klipper extras/gcode_move.py
https://github.com/Klipper3d/klipper/blob/master/klippy/extras/gcode_move.py
"""

from typing import Any

class GCodeMove:
    homing_position: list[float]
    base_position: list[float]
    last_position: list[float]
    def get_status(self, eventtime: float) -> dict[str, Any]: ...
