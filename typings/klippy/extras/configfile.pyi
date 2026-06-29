"""
Typings for Klipper configfile.py
https://github.com/Klipper3d/klipper/blob/master/klippy/extras/configfile.py
"""

from typing import TypeVar, overload

from klippy.klippy import Printer

class sentinel:
    pass

T = TypeVar("T")

class ConfigWrapper:
    error: type[Exception]
    printer: Printer
    section: str

    def get_printer(self) -> Printer: ...
    def get_name(self) -> str: ...
    @overload
    def get(self, option: str, *, note_valid: bool = True) -> str: ...
    @overload
    def get(self, option: str, default: T, *, note_valid: bool = True) -> str | T: ...
    @overload
    def getint(
        self,
        option: str,
        *,
        minval: int | None = None,
        maxval: int | None = None,
        note_valid: bool = True,
    ) -> int: ...
    @overload
    def getint(
        self,
        option: str,
        default: T,
        *,
        minval: int | None = None,
        maxval: int | None = None,
        note_valid: bool = True,
    ) -> int | T: ...
    @overload
    def getfloat(
        self,
        option: str,
        *,
        minval: float | None = None,
        maxval: float | None = None,
        above: float | None = None,
        below: float | None = None,
        note_valid: bool = True,
    ) -> float: ...
    @overload
    def getfloat(
        self,
        option: str,
        default: T,
        *,
        minval: float | None = None,
        maxval: float | None = None,
        above: float | None = None,
        below: float | None = None,
        note_valid: bool = True,
    ) -> float | T: ...
    @overload
    def getboolean(self, option: str, *, note_valid: bool = True) -> bool: ...
    @overload
    def getboolean(
        self, option: str, default: T, *, note_valid: bool = True
    ) -> bool | T: ...
    def has_section(self, section: str) -> bool: ...
    def getsection(self, section: str) -> ConfigWrapper: ...

class PrinterConfig:
    printer: Printer

    def get_printer(self) -> Printer: ...
    def read_main_config(self) -> ConfigWrapper: ...
    def set(self, section: str, option: str, value: object) -> None: ...
    def remove_section(self, section: str) -> None: ...
