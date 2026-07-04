"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Shared session/plot record primitives — JSON-serializable, consumed by recorder and plotters.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, TypeAlias

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
    CIRCLE_BOOTSTRAP = "circle_harmonic_bootstrap"
    CIRCLE_BOOTSTRAP_PASS = "circle_bootstrap_pass"
    CIRCLE_PASS = "circle_pass"
    CIRCLE_HARMONIC = "circle_harmonic"
    CIRCLE_HARMONIC_SLOPE = "circle_harmonic_bootstrap_slope_only"
    ACCURACY_REPEAT = "accuracy_repeat"


class ScatterMode(str, Enum):
    MARKERS = "markers"
    MARKERS_LINES = "markers+lines"


def pass_color(pass_num: int) -> str:
    return PASS_COLORS[(pass_num - 1) % len(PASS_COLORS)]


@dataclass(frozen=True, slots=True)
class ScatterRecord:
    pass_num: int
    label: str
    xs: tuple[float, ...]
    ys: tuple[float, ...]
    freqs: tuple[float, ...] | None = None
    mode: ScatterMode = ScatterMode.MARKERS
    type: RecordType = RecordType.SCATTER

    def to_dict(self) -> dict[str, Any]:
        return _record_to_dict(self)


@dataclass(frozen=True, slots=True)
class MarkerRecord:
    pass_num: int
    label: str
    x: float
    y: float
    symbol: str
    type: RecordType = RecordType.MARKER

    def to_dict(self) -> dict[str, Any]:
        return _record_to_dict(self)


@dataclass(frozen=True, slots=True)
class BoxRecord:
    pass_num: int
    x_lo: float
    x_hi: float
    y_lo: float
    y_hi: float
    type: RecordType = RecordType.BOX

    def to_dict(self) -> dict[str, Any]:
        return _record_to_dict(self)


@dataclass(frozen=True, slots=True)
class StatsRecord:
    title: str
    columns: tuple[tuple[str, str], ...]
    rows: tuple[dict[str, str], ...]
    footer: str = ""
    type: RecordType = RecordType.STATS

    def to_dict(self) -> dict[str, Any]:
        return _record_to_dict(self)


@dataclass(frozen=True, slots=True)
class ProbeRecord:
    x: float
    y: float
    mean_hz: float
    samples_hz: tuple[float, ...]
    type: RecordType = RecordType.PROBE

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "mean_hz": self.mean_hz,
            "samples_hz": list(self.samples_hz),
        }


@dataclass(frozen=True, slots=True)
class SeriesRecord:
    pass_num: int
    label: str
    xs: tuple[float, ...]
    ys: tuple[float, ...]
    mode: str = "markers"
    line_dash: str = "solid"
    showlegend: bool = True
    type: RecordType = RecordType.SERIES

    def to_dict(self) -> dict[str, Any]:
        return _record_to_dict(self)


@dataclass(frozen=True, slots=True)
class HeatmapRecord:
    center_x: float
    center_y: float
    result_x: float
    result_y: float
    x_lo: float
    x_hi: float
    y_lo: float
    y_hi: float
    z: tuple[tuple[float | None, ...], ...]
    x_centers: tuple[float, ...]
    y_centers: tuple[float, ...]
    sample_xs: tuple[float, ...]
    sample_ys: tuple[float, ...]
    sample_freqs: tuple[float, ...]
    type: RecordType = RecordType.HEATMAP

    def to_dict(self) -> dict[str, Any]:
        return _record_to_dict(self)


@dataclass(frozen=True, slots=True)
class PlotArtifactRecord:
    strategy: str
    passes: int
    path: str
    type: RecordType = RecordType.PLOT

    def to_dict(self) -> dict[str, Any]:
        out = _record_to_dict(self)
        out["type"] = "plot"
        return out


@dataclass(frozen=True, slots=True)
class SweepTraceRecord:
    pass_num: int
    phase: str
    axis: str
    cross_offsets: tuple[float, ...]
    cross_center: float
    lo: float
    hi: float
    samples: tuple[tuple[float, float], ...]
    type: RecordType = RecordType.SWEEP

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "pass": self.pass_num,
            "phase": self.phase,
            "axis": self.axis,
            "cross_offsets": list(self.cross_offsets),
            "cross_center": self.cross_center,
            "lo": self.lo,
            "hi": self.hi,
            "samples": [list(point) for point in self.samples],
        }


@dataclass(frozen=True, slots=True)
class SweepGridTraceRecord:
    center_x: float
    center_y: float
    x_lo: float
    x_hi: float
    y_lo: float
    y_hi: float
    step_size: float
    rows: int
    legs: int
    samples: int
    type: RecordType = RecordType.SWEEP_GRID

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "center": {"x": self.center_x, "y": self.center_y},
            "box": {
                "x_lo": self.x_lo,
                "x_hi": self.x_hi,
                "y_lo": self.y_lo,
                "y_hi": self.y_hi,
            },
            "step_size": self.step_size,
            "rows": self.rows,
            "legs": self.legs,
            "samples": self.samples,
        }


@dataclass(frozen=True, slots=True)
class SweepCentroidTraceRecord:
    pass_num: int
    phase: str
    center_x: float
    center_y: float
    result_x: float
    result_y: float
    samples: int
    type: RecordType = RecordType.SWEEP_CENTROID

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "pass": self.pass_num,
            "phase": self.phase,
            "centre": {"x": self.center_x, "y": self.center_y},
            "result": {"x": self.result_x, "y": self.result_y},
            "samples": self.samples,
        }


@dataclass(frozen=True, slots=True)
class TernaryStepRecord:
    pass_num: int
    axis: str
    iteration: int
    lo: float
    hi: float
    m1: float
    m2: float
    f1: float
    f2: float
    type: RecordType = RecordType.TERNARY_STEP

    def to_dict(self) -> dict[str, Any]:
        return _record_to_dict(self)


@dataclass(frozen=True, slots=True)
class DebugScanTraceRecord:
    center_x: float
    center_y: float
    result_x: float
    result_y: float
    samples: int
    type: RecordType = RecordType.DEBUG_SCAN

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "centre": {"x": self.center_x, "y": self.center_y},
            "result": {"x": self.result_x, "y": self.result_y},
            "samples": self.samples,
        }


@dataclass(frozen=True, slots=True)
class CircleBootstrapTraceRecord:
    pass_num: int
    result_x: float
    result_y: float
    type: RecordType = RecordType.CIRCLE_BOOTSTRAP

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "pass": self.pass_num,
            "result": {"x": self.result_x, "y": self.result_y},
        }


@dataclass(frozen=True, slots=True)
class CircleHarmonicSlopeTraceRecord:
    pass_num: int
    result_x: float
    result_y: float
    centroid_skipped_x: float | None = None
    centroid_skipped_y: float | None = None
    type: RecordType = RecordType.CIRCLE_HARMONIC_SLOPE

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "type": self.type.value,
            "pass": self.pass_num,
            "result": {"x": self.result_x, "y": self.result_y},
        }
        if self.centroid_skipped_x is not None and self.centroid_skipped_y is not None:
            out["centroid_skipped"] = {
                "x": self.centroid_skipped_x,
                "y": self.centroid_skipped_y,
            }
        return out


@dataclass(frozen=True, slots=True)
class CircleHarmonicTraceRecord:
    pass_num: int
    radius: float
    result_x: float
    result_y: float
    harmonic_a: float
    harmonic_b: float
    harmonic_amp: float
    type: RecordType = RecordType.CIRCLE_HARMONIC

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "pass": self.pass_num,
            "radius": self.radius,
            "result": {"x": self.result_x, "y": self.result_y},
            "harmonic": {
                "a": self.harmonic_a,
                "b": self.harmonic_b,
                "amp": self.harmonic_amp,
            },
        }


@dataclass(frozen=True, slots=True)
class CircleBootstrapRecord:
    pass_num: int
    center_x: float
    center_y: float
    result_x: float
    result_y: float
    moved_x: float
    moved_y: float
    sample_xs: tuple[float, ...]
    sample_ys: tuple[float, ...]
    sample_freqs: tuple[float, ...]
    x_lo: float
    x_hi: float
    y_lo: float
    y_hi: float
    type: RecordType = RecordType.CIRCLE_BOOTSTRAP_PASS

    def to_dict(self) -> dict[str, Any]:
        return _record_to_dict(self)


@dataclass(frozen=True, slots=True)
class CircleHarmonicPassRecord:
    pass_num: int
    trace_center_x: float
    trace_center_y: float
    radius: float
    result_x: float
    result_y: float
    moved_x: float
    moved_y: float
    sample_xs: tuple[float, ...]
    sample_ys: tuple[float, ...]
    sample_freqs: tuple[float, ...]
    binned_thetas: tuple[float, ...]
    binned_freqs: tuple[float, ...]
    fit_c0: float | None
    fit_a: float | None
    fit_b: float | None
    fit_amp: float | None
    fit_noise: float | None
    rejected: bool
    reject_reasons: str = ""
    type: RecordType = RecordType.CIRCLE_PASS

    def to_dict(self) -> dict[str, Any]:
        return _record_to_dict(self)


@dataclass(frozen=True, slots=True)
class AccuracyRepeatRecord:
    repeat_num: int
    offset_x: float
    offset_y: float
    session_plot_path: str | None = None
    type: RecordType = RecordType.ACCURACY_REPEAT

    def to_dict(self) -> dict[str, Any]:
        return _record_to_dict(self)


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
    | SweepCentroidTraceRecord
    | TernaryStepRecord
    | DebugScanTraceRecord
    | CircleBootstrapTraceRecord
    | CircleHarmonicSlopeTraceRecord
    | CircleHarmonicTraceRecord
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
    CircleBootstrapRecord,
    CircleHarmonicPassRecord,
    AccuracyRepeatRecord,
)


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def _record_to_dict(record: SessionRecord) -> dict[str, Any]:
    out = _json_value(asdict(record))
    if isinstance(record, ScatterRecord) and record.freqs is None:
        out.pop("freqs", None)
    return out


def _test_primitives() -> None:
    import json

    scatter = ScatterRecord(1, "pts", (1.0, 2.0), (3.0, 4.0), freqs=(100.0, 101.0))
    scatter_no_freqs = ScatterRecord(1, "pts", (1.0,), (2.0,))
    marker = MarkerRecord(1, "best", 1.0, 2.0, "star")
    box = BoxRecord(1, 0.0, 1.0, 0.0, 1.0)
    stats = StatsRecord("title", (("k", "K"),), ({"k": "v"},))
    probe = ProbeRecord(1.0, 2.0, 100.0, (99.0, 101.0))

    assert scatter.to_dict() == {
        "type": "scatter",
        "pass_num": 1,
        "label": "pts",
        "xs": [1.0, 2.0],
        "ys": [3.0, 4.0],
        "freqs": [100.0, 101.0],
        "mode": "markers",
    }
    assert "freqs" not in scatter_no_freqs.to_dict()
    assert marker.to_dict() == {
        "type": "marker",
        "pass_num": 1,
        "label": "best",
        "x": 1.0,
        "y": 2.0,
        "symbol": "star",
    }
    assert box.to_dict() == {
        "type": "box",
        "pass_num": 1,
        "x_lo": 0.0,
        "x_hi": 1.0,
        "y_lo": 0.0,
        "y_hi": 1.0,
    }
    assert stats.to_dict() == {
        "type": "stats",
        "title": "title",
        "columns": [["k", "K"]],
        "rows": [{"k": "v"}],
        "footer": "",
    }
    assert probe.to_dict() == {
        "x": 1.0,
        "y": 2.0,
        "mean_hz": 100.0,
        "samples_hz": [99.0, 101.0],
    }
    assert "type" not in probe.to_dict()

    for record in (scatter, scatter_no_freqs, marker, box, stats, probe):
        json.dumps(record.to_dict())

    assert pass_color(1) == PASS_COLORS[0]
    assert pass_color(len(PASS_COLORS) + 1) == PASS_COLORS[0]


if __name__ == "__main__":
    _test_primitives()
