"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Strategy plotter registry — resolve plotter by strategy name.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, Literal

from .primitives import SessionRecord

_PLOTTERS: dict[str, type[StrategyPlotter]] = {}


def register_plotter(name: str):
    def decorator(cls: type[StrategyPlotter]) -> type[StrategyPlotter]:
        _PLOTTERS[name] = cls
        return cls

    return decorator


class StrategyPlotter(ABC):
    @abstractmethod
    def render(
        self,
        records: Sequence[SessionRecord],
        *,
        search_for: Literal["min", "max"],
    ) -> Any | None: ...


def render_session_plot(
    name: str,
    records: Sequence[SessionRecord],
    *,
    search_for: Literal["min", "max"],
) -> Any | None:
    plotter_cls = _PLOTTERS.get(name)
    if plotter_cls is None:
        return None
    return plotter_cls().render(records, search_for=search_for)
