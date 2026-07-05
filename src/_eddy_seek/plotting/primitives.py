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

from ..common import Axis, Offset
from ..harmonic import HarmonicFit
from ..movement.handler import MotionSample

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
class ScatterRecord(_Record):
    _KIND = "scatter"

    pass_num: int
    label: str
    cloud: XYCloud
    mode: ScatterMode = ScatterMode.MARKERS


@dataclass(frozen=True, slots=True)
class MarkerRecord(_Record):
    _KIND = "marker"

    pass_num: int
    label: str
    at: Offset
    symbol: str


@dataclass(frozen=True, slots=True)
class BoxRecord(_Record):
    _KIND = "box"

    pass_num: int
    bounds: Bounds


@dataclass(frozen=True, slots=True)
class StatsRecord(_Record):
    _KIND = "stats"

    title: str
    columns: tuple[tuple[str, str], ...]
    rows: tuple[dict[str, str], ...]
    footer: str = ""


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
class SweepTraceRecord(_Record):
    _KIND = "sweep"

    pass_num: int
    phase: str
    span: AxisSpan
    cross_offsets: tuple[float, ...]
    cross_center: float
    profile: tuple[tuple[float, float], ...]


@dataclass(frozen=True, slots=True)
class SweepGridTraceRecord(_Record):
    _KIND = "sweep_grid"

    center: Offset
    bounds: Bounds
    step_size: float
    rows: int
    legs: int
    sample_count: int


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
class TernaryPassRecord(_Record):
    _KIND = "ternary_pass"

    pass_num: int
    move: PassMove
    probes: XYCloud
    steps: tuple[TernaryStep, ...]

    def to_trace_dict(self) -> dict[str, Any]:
        return {
            "type": self._KIND,
            "pass_num": self.pass_num,
            "result": _json_value(asdict(self.move.result)),
            "steps": [_json_value(asdict(step)) for step in self.steps],
        }


@dataclass(frozen=True, slots=True)
class CircleBootstrapRecord(_Record):
    _KIND = "circle_bootstrap_pass"

    pass_num: int
    move: PassMove
    samples: XYCloud
    bounds: Bounds
    skipped: Offset | None = None

    def to_trace_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "type": self._KIND,
            "pass_num": self.pass_num,
            "result": _json_value(asdict(self.move.result)),
        }
        if self.skipped is not None:
            out["skipped"] = _json_value(asdict(self.skipped))
        return out


@dataclass(frozen=True, slots=True)
class CircleHarmonicPassRecord(_Record):
    _KIND = "circle_pass"

    pass_num: int
    trace_center: Offset
    radius: float
    move: PassMove
    samples: XYCloud
    binned: BinnedProfile
    fit: HarmonicFit | None
    rejected: bool
    reject_reasons: str = ""

    def to_trace_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "type": self._KIND,
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
    | TernaryPassRecord
    | CircleBootstrapRecord
    | CircleHarmonicPassRecord
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


def _record_to_dict(record: _Record) -> dict[str, Any]:
    out = _json_value(asdict(record))
    if record._KIND:
        out["type"] = record._KIND
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
    assert record_pass_num(AccuracyRepeatRecord(2, Offset.zero())) == 2

    from ..movement.handler import MotionSample

    samples = [
        MotionSample(Offset(1.0, 2.0), 100.0, 0.0),
        MotionSample(Offset(3.0, 4.0), 101.0, 0.1),
    ]
    cloud = XYCloud.from_samples(samples)
    assert cloud == XYCloud((1.0, 3.0), (2.0, 4.0), (100.0, 101.0))
    probes = [(Offset(1.0, 2.0), 100.0), (Offset(3.0, 4.0), 101.0)]
    assert XYCloud.from_probes(probes) == cloud
    assert XYCloud.from_probes(probes, freqs=False) == XYCloud((1.0, 3.0), (2.0, 4.0))

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

    sweep = SweepCentroidPassRecord(
        1,
        "coarse",
        PassMove.compute(Offset.zero(), Offset(0.1, 0.0)),
        Bounds.from_box((-1.0, 1.0, -1.0, 1.0)),
        XYCloud((0.0,), (0.0,), (100.0,)),
    )
    sweep_trace = sweep.to_trace_dict()
    assert sweep_trace["type"] == "sweep_centroid"
    assert sweep_trace["sample_count"] == 1
    assert "samples" not in sweep_trace

    centroid = CentroidPassRecord(
        1,
        PassMove.compute(Offset.zero(), Offset(0.1, 0.0)),
        XYCloud((0.0,), (0.0,), (100.0,)),
    )
    assert centroid.to_trace_dict()["sample_count"] == 1

    heatmap = HeatmapRecord(
        PassMove.compute(Offset.zero(), Offset(0.0, 0.0)),
        Bounds.from_box((-1.0, 1.0, -1.0, 1.0)),
        ((100.0,),),
        (0.0,),
        (0.0,),
        XYCloud((0.0,), (0.0,), (100.0,)),
    )
    assert heatmap.to_trace_dict()["type"] == "debug_scan"


if __name__ == "__main__":
    _test_primitives()
