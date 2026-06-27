"""

Typings for Klipper ldc1612.py (https://github.com/Klipper3d/klipper/blob/master/klippy/extras/ldc1612.py)

"""

from collections.abc import Callable

from klippy.extras.configfile import ConfigWrapper

class LDC1612:
    name: str
    def __init__(self, config: ConfigWrapper) -> None: ...
    def add_client(self, callback: Callable[[dict], bool]) -> None: ...
