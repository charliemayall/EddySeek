"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Append-only session record buffer for trace JSON and plot rendering.
"""

from __future__ import annotations

from typing import Any

from ..records import (
    PlotArtifactRecord,
    SessionRecord,
    record_pass_num,
)


class SessionRecorder:
    def __init__(self, *, trace: bool, plots: bool) -> None:
        self._trace = trace
        self._plots = plots
        self._records: list[SessionRecord] = []

    @property
    def trace(self) -> bool:
        return self._trace

    @property
    def plots(self) -> bool:
        return self._plots

    @property
    def active(self) -> bool:
        return self._trace or self._plots

    def record(self, entry: SessionRecord) -> None:
        if not self.active:
            return
        self._records.append(entry)

    def records(self) -> tuple[SessionRecord, ...]:
        return tuple(self._records)

    def to_probe_dicts(self) -> list[dict[str, Any]]:
        if not self._trace:
            return []
        return [
            record.to_trace_dict()
            for record in self._records
            if not isinstance(record, PlotArtifactRecord)
        ]

    def pass_count(self) -> int:
        best = 0
        for record in self._records:
            pass_num = record_pass_num(record)
            if pass_num is not None:
                best = max(best, pass_num)
        return best
