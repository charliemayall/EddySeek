"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Shared session/plot record primitives — JSON-serializable, consumed by recorder and plotters.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from typing import Any, TypeAlias

from ..common import Axis, Offset
from ..harmonic import HarmonicFit

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


class RecordType(str, Enum):
    SCATTER = "scatter"
    MARKER = "marker"
    BOX = "box"
    STATS = "stats"
    PROBE = "probe"
    SERIES = "series"
    HEATMAP = "heatmap"
    PLOT = "plot"
    SWEEP = "sweep"
    SWEEP_GRID = "sweep_grid"
    SWEEP_CENTROID = "sweep_centroid"
    TERNARY_STEP = "ternary_step"
    DEBUG_SCAN = "debug_scan"
    CIRCLE_BOOTSTRAP_PASS = "circle_bootstrap_pass"
    CIRCLE_PASS = "circle_pass"
    ACCURACY_REPEAT = "accuracy_repeat"


class ScatterMode(str, Enum):
    MARKERS = "markers"
    MARKERS_LINES = "markers+lines"


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


@dataclass(frozen=True, slots=True)
class AxisSpan:
    axis: Axis
    lo: float
    hi: float


@dataclass(frozen=True, slots=True)
class TernaryStep:
    axis: Axis
    iteration: int
    lo: float
    hi: float
    m1: float
    m2: float
    f1: float
    f2: float

    @property
    def span(self) -> AxisSpan:
        return AxisSpan(self.axis, self.lo, self.hi)


@dataclass(frozen=True, slots=True)
class BinnedProfile:
    thetas: tuple[float, ...]
    freqs: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class _Record:
    def to_dict(self) -> dict[str, Any]:
        return _record_to_dict(self)


@dataclass(frozen=True, slots=True)
class ScatterRecord(_Record):
    pass_num: int
    label: str
    cloud: XYCloud
    mode: ScatterMode = ScatterMode.MARKERS
    type: RecordType = RecordType.SCATTER


@dataclass(frozen=True, slots=True)
class MarkerRecord(_Record):
    pass_num: int
    label: str
    at: Offset
    symbol: str
    type: RecordType = RecordType.MARKER


@dataclass(frozen=True, slots=True)
class BoxRecord(_Record):
    pass_num: int
    bounds: Bounds
    type: RecordType = RecordType.BOX


@dataclass(frozen=True, slots=True)
class StatsRecord(_Record):
    title: str
    columns: tuple[tuple[str, str], ...]
    rows: tuple[dict[str, str], ...]
    footer: str = ""
    type: RecordType = RecordType.STATS


@dataclass(frozen=True, slots=True)
class ProbeRecord(_Record):
    at: Offset
    mean_hz: float
    samples_hz: tuple[float, ...]
    type: RecordType = RecordType.PROBE


@dataclass(frozen=True, slots=True)
class SeriesRecord(_Record):
    pass_num: int
    label: str
    cloud: XYCloud
    mode: str = "markers"
    line_dash: str = "solid"
    showlegend: bool = True
    type: RecordType = RecordType.SERIES


@dataclass(frozen=True, slots=True)
class HeatmapRecord(_Record):
    move: PassMove
    bounds: Bounds
    z: tuple[tuple[float | None, ...], ...]
    x_centers: tuple[float, ...]
    y_centers: tuple[float, ...]
    samples: XYCloud
    type: RecordType = RecordType.HEATMAP


@dataclass(frozen=True, slots=True)
class PlotArtifactRecord(_Record):
    strategy: str
    passes: int
    path: str
    type: RecordType = RecordType.PLOT


@dataclass(frozen=True, slots=True)
class SweepTraceRecord(_Record):
    pass_num: int
    phase: str
    span: AxisSpan
    cross_offsets: tuple[float, ...]
    cross_center: float
    profile: tuple[tuple[float, float], ...]
    type: RecordType = RecordType.SWEEP


@dataclass(frozen=True, slots=True)
class SweepGridTraceRecord(_Record):
    center: Offset
    bounds: Bounds
    step_size: float
    rows: int
    legs: int
    sample_count: int
    type: RecordType = RecordType.SWEEP_GRID


@dataclass(frozen=True, slots=True)
class PassTraceRecord(_Record):
    """Shared trace payload for single-pass strategies."""

    pass_num: int
    phase: str
    move: PassMove
    sample_count: int
    type: RecordType = RecordType.SWEEP_CENTROID


@dataclass(frozen=True, slots=True)
class TernaryStepRecord(_Record):
    pass_num: int
    step: TernaryStep
    type: RecordType = RecordType.TERNARY_STEP


@dataclass(frozen=True, slots=True)
class ScanTraceRecord(_Record):
    move: PassMove
    sample_count: int
    type: RecordType = RecordType.DEBUG_SCAN


@dataclass(frozen=True, slots=True)
class CircleBootstrapRecord(_Record):
    pass_num: int
    move: PassMove
    samples: XYCloud
    bounds: Bounds
    skipped: Offset | None = None
    type: RecordType = RecordType.CIRCLE_BOOTSTRAP_PASS

    def to_trace_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "type": self.type.value,
            "pass_num": self.pass_num,
            "result": _json_value(asdict(self.move.result)),
        }
        if self.skipped is not None:
            out["skipped"] = _json_value(asdict(self.skipped))
        return out


@dataclass(frozen=True, slots=True)
class CircleHarmonicPassRecord(_Record):
    pass_num: int
    trace_center: Offset
    radius: float
    move: PassMove
    samples: XYCloud
    binned: BinnedProfile
    fit: HarmonicFit | None
    rejected: bool
    reject_reasons: str = ""
    type: RecordType = RecordType.CIRCLE_PASS

    def to_trace_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "type": self.type.value,
            "pass_num": self.pass_num,
            "radius": self.radius,
            "result": _json_value(asdict(self.move.result)),
            "rejected": self.rejected,
        }
        if self.reject_reasons:
            out["reject_reasons"] = self.reject_reasons
        if self.fit is not None:
            out["harmonic"] = {
                "a": self.fit.a,
                "b": self.fit.b,
                "amp": self.fit.amplitude,
            }
        return out


@dataclass(frozen=True, slots=True)
class AccuracyRepeatRecord(_Record):
    repeat_num: int
    offset: Offset
    session_plot_path: str | None = None
    type: RecordType = RecordType.ACCURACY_REPEAT


SessionRecord: TypeAlias = (
    ScatterRecord
    | MarkerRecord
    | BoxRecord
    | StatsRecord
    | ProbeRecord
    | SeriesRecord
    | HeatmapRecord
    | PlotArtifactRecord
    | SweepTraceRecord
    | SweepGridTraceRecord
    | PassTraceRecord
    | TernaryStepRecord
    | ScanTraceRecord
    | CircleBootstrapRecord
    | CircleHarmonicPassRecord
    | AccuracyRepeatRecord
)

_PLOT_ONLY_RECORDS = (
    ScatterRecord,
    MarkerRecord,
    BoxRecord,
    StatsRecord,
    SeriesRecord,
    HeatmapRecord,
    TernaryStepRecord,
    AccuracyRepeatRecord,
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


def _record_to_dict(record: _Record) -> dict[str, Any]:
    out = _json_value(asdict(record))
    if isinstance(record, ScatterRecord) and record.cloud.freqs is None:
        out.get("cloud", {}).pop("freqs", None)
    return out


def _test_primitives() -> None:
    import json

    scatter = ScatterRecord(
        1,
        "pts",
        XYCloud((1.0, 2.0), (3.0, 4.0), (100.0, 101.0)),
    )
    scatter_no_freqs = ScatterRecord(1, "pts", XYCloud((1.0,), (2.0,)))
    marker = MarkerRecord(1, "best", Offset(1.0, 2.0), "star")
    box = BoxRecord(1, Bounds.from_box((0.0, 1.0, 0.0, 1.0)))
    stats = StatsRecord("title", (("k", "K"),), ({"k": "v"},))
    probe = ProbeRecord(Offset(1.0, 2.0), 100.0, (99.0, 101.0))

    assert scatter.to_dict()["type"] == "scatter"
    assert scatter.to_dict()["cloud"]["freqs"] == [100.0, 101.0]
    assert "freqs" not in scatter_no_freqs.to_dict()["cloud"]
    assert marker.to_dict()["at"] == {"x": 1.0, "y": 2.0}
    assert box.to_dict()["bounds"]["lo"] == {"x": 0.0, "y": 0.0}
    assert probe.to_dict()["mean_hz"] == 100.0

    for record in (scatter, scatter_no_freqs, marker, box, stats, probe):
        json.dumps(record.to_dict())

    assert pass_color(1) == PASS_COLORS[0]
    assert PassMove.compute(Offset.zero(), Offset(1.0, 0.0)).moved.x == 1.0

    circle = CircleHarmonicPassRecord(
        2,
        Offset(0.1, 0.2),
        1.0,
        PassMove.compute(Offset(0.1, 0.2), Offset(0.2, 0.3)),
        XYCloud((0.0,), (0.0,), (100.0,)),
        BinnedProfile((0.0,), (100.0,)),
        HarmonicFit(100.0, 1.0, 0.0, 1.0, 0.1, 1),
        rejected=False,
    )
    trace = circle.to_trace_dict()
    assert trace["type"] == "circle_pass"
    assert trace["harmonic"]["amp"] == 1.0
    assert "samples" not in trace


if __name__ == "__main__":
    _test_primitives()
