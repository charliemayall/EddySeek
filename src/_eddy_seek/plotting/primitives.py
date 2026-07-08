"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Shared session/plot record primitives - JSON-serializable, consumed by recorder and plotters.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from typing import Any, ClassVar, TypeAlias

from ..common import Offset
from ..movement.handler import MotionSample
from ..movement.sweep import SweepGridTraceRecord, SweepTraceRecord

PASS_COLORS = (
    "#636EFA",
    "#EF553B",
    "#00CC96",
    "#AB63FA",
    "#FFA15A",
    "#19D3F3",
    "#FF6692",
    "#B6E880",
    "#FF97FF",
    "#FECB52",
)


def pass_color(pass_num: int) -> str:
    return PASS_COLORS[(pass_num - 1) % len(PASS_COLORS)]


@dataclass(frozen=True, slots=True)
class Bounds:
    lo: Offset
    hi: Offset

    @classmethod
    def from_box(cls, box: tuple[float, float, float, float]) -> Bounds:
        x_lo, x_hi, y_lo, y_hi = box
        return cls(Offset(x_lo, y_lo), Offset(x_hi, y_hi))

    def as_box(self) -> tuple[float, float, float, float]:
        return self.lo.x, self.hi.x, self.lo.y, self.hi.y


@dataclass(frozen=True, slots=True)
class PassMove:
    center: Offset
    result: Offset
    moved: Offset

    @classmethod
    def compute(cls, center: Offset, result: Offset) -> PassMove:
        return cls(center, result, (result - center).abs_components())


@dataclass(frozen=True, slots=True)
class XYCloud:
    xs: tuple[float, ...]
    ys: tuple[float, ...]
    freqs: tuple[float, ...] | None = None

    @classmethod
    def from_samples(cls, samples: Sequence[MotionSample]) -> XYCloud:
        return cls(
            tuple(sample.offset.x for sample in samples),
            tuple(sample.offset.y for sample in samples),
            tuple(sample.freq for sample in samples),
        )

    @classmethod
    def from_probes(
        cls,
        probes: Sequence[tuple[Offset, float]],
        *,
        freqs: bool = True,
    ) -> XYCloud:
        xs = tuple(position.x for position, _ in probes)
        ys = tuple(position.y for position, _ in probes)
        if freqs:
            return cls(xs, ys, tuple(freq for _, freq in probes))
        return cls(xs, ys)


@dataclass(frozen=True, slots=True)
class _Record:
    _KIND: ClassVar[str] = ""

    def to_dict(self) -> dict[str, Any]:
        return _record_to_dict(self)

    def to_trace_dict(self) -> dict[str, Any]:
        return self.to_dict()


def record_pass_num(record: _Record) -> int | None:
    pass_num = getattr(record, "pass_num", None)
    if isinstance(pass_num, int):
        return pass_num
    repeat = getattr(record, "repeat_num", None)
    return repeat if isinstance(repeat, int) else None


@dataclass(frozen=True, slots=True)
class ProbeRecord(_Record):
    _KIND = "probe"

    at: Offset
    mean_hz: float
    samples_hz: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class HeatmapRecord(_Record):
    _KIND = "heatmap"

    move: PassMove
    bounds: Bounds
    z: tuple[tuple[float | None, ...], ...]
    x_centers: tuple[float, ...]
    y_centers: tuple[float, ...]
    samples: XYCloud

    def to_trace_dict(self) -> dict[str, Any]:
        return {
            "type": "debug_scan",
            "move": _json_value(asdict(self.move)),
            "sample_count": len(self.samples.xs),
        }


@dataclass(frozen=True, slots=True)
class PlotArtifactRecord(_Record):
    _KIND = "plot"

    strategy: str
    passes: int
    path: str


@dataclass(frozen=True, slots=True)
class SweepCentroidPassRecord(_Record):
    _KIND = "sweep_centroid"

    pass_num: int
    phase: str
    move: PassMove
    bounds: Bounds
    samples: XYCloud

    def to_trace_dict(self) -> dict[str, Any]:
        return {
            "type": self._KIND,
            "pass_num": self.pass_num,
            "phase": self.phase,
            "move": _json_value(asdict(self.move)),
            "sample_count": len(self.samples.xs),
        }


@dataclass(frozen=True, slots=True)
class CentroidPassRecord(_Record):
    _KIND = "centroid_pass"

    pass_num: int
    move: PassMove
    probes: XYCloud

    def to_trace_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "type": self._KIND,
            "pass_num": self.pass_num,
            "result": _json_value(asdict(self.move.result)),
            "sample_count": len(self.probes.xs),
        }
        if self.probes.freqs:
            freqs = self.probes.freqs
            out["freq_range"] = [min(freqs), max(freqs)]
        return out


@dataclass(frozen=True, slots=True)
class AccuracyRepeatRecord(_Record):
    _KIND = "accuracy_repeat"

    repeat_num: int
    offset: Offset
    session_plot_path: str | None = None


SessionRecord: TypeAlias = (
    ProbeRecord
    | HeatmapRecord
    | PlotArtifactRecord
    | SweepTraceRecord
    | SweepGridTraceRecord
    | SweepCentroidPassRecord
    | CentroidPassRecord
    | AccuracyRepeatRecord
)


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def _record_to_dict(record: Any) -> dict[str, Any]:
    out = _json_value(asdict(record))
    if record._KIND:
        out["type"] = record._KIND
    return out
