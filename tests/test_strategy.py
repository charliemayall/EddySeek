"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

import math

from pytest import raises

from _eddy_seek.common import Offset
from _eddy_seek.config import SeekConfig
from _eddy_seek.optimizer import (
    axis_weighted_centroid,
    frequency_is_better,
    frequency_weight,
    weighted_centroid,
)
from _eddy_seek.plotting.recorder import SessionRecorder
from _eddy_seek.session import SeekSession, _sample_stdev
from _eddy_seek.strategy import strategy_for
from _eddy_seek.strategy.base import (
    DivergenceError,
    InsufficientSamplesError,
    MaxPassesError,
    SeekExitKind,
    SeekStrategy,
    _check_pass_divergence,
)
from _eddy_seek.strategy.centroid import CentroidStrategy
from _eddy_seek.strategy.sweep_centroid import SweepCentroidStrategy


def _test_cfg(**overrides) -> SeekConfig:
    return SeekConfig(**overrides)


class _FakeReporter:
    def info(self, msg: str) -> None:
        pass


class _RecordingSearchSession:
    def __init__(self) -> None:
        self.config = _test_cfg(max_passes=6)
        self.positions: list[Offset] = []
        self.recorder = SessionRecorder(trace=False, plots=False)

    def measure_at(self, offset: Offset) -> float:
        self.positions.append(offset)
        return -((offset.x - 1.0) ** 2 + (offset.y + 1.0) ** 2)


def test_strategy_search_uses_positions():
    session = _RecordingSearchSession()
    best, passes_run = CentroidStrategy().search(session, _FakeReporter())  # type: ignore[arg-type]

    assert isinstance(best, Offset)
    assert passes_run >= 1
    assert session.positions
    assert all(isinstance(position, Offset) for position in session.positions)


def test_strategy_weights():
    cfg = _test_cfg()
    session = SeekSession.__new__(SeekSession)
    session.config = cfg

    assert frequency_weight(100.0, 50.0, 100.0, "max") == 50.0
    assert frequency_weight(50.0, 50.0, 100.0, "max") == 0.0
    assert frequency_weight(50.0, 50.0, 100.0, "min") == 50.0

    assert frequency_is_better(90.0, 80.0, session.config.search_for) is True
    assert frequency_is_better(70.0, 80.0, session.config.search_for) is False
    assert _sample_stdev([1.0, 3.0], 2.0) == math.sqrt(2.0)

    assert strategy_for("circle_harmonic").name == "circle_harmonic"
    assert strategy_for("centroid").name == "centroid"
    assert strategy_for("sweep_centroid").name == "sweep_centroid"
    assert strategy_for("debug_scan").name == "debug_scan"
    with raises(ValueError):
        strategy_for("bogus")


def test_weighted_centroid_finds_peak():
    probes = [
        (Offset(-1.0, 0.0), 100.0),
        (Offset(0.0, 0.0), 200.0),
        (Offset(1.0, 0.0), 100.0),
    ]
    result = weighted_centroid(probes, "max")
    assert result is not None
    assert abs(result.x) < 0.01
    assert abs(result.y) < 0.01


def test_merged_centroid_couples_axes():
    """Y sweeps at a wrong X slice pull a merged 2-D centroid off the true peak."""
    probes = [
        (Offset(-0.5, 0.0), 100.0),
        (Offset(0.0, 0.0), 200.0),
        (Offset(0.5, 0.0), 100.0),
        (Offset(0.06, -0.5), 180.0),
        (Offset(0.06, 0.0), 190.0),
        (Offset(0.06, 0.5), 180.0),
    ]
    result = weighted_centroid(probes, "max")
    assert result is not None
    assert result.x > 0.02


def test_axis_weighted_centroid_decouples_axes():
    x_profile = [(-0.5, 100.0), (0.0, 200.0), (0.5, 100.0)]
    y_profile = [(-0.5, 100.0), (0.0, 200.0), (0.5, 100.0)]
    result_x = axis_weighted_centroid(x_profile, "max")
    result_y = axis_weighted_centroid(y_profile, "max")
    assert result_x is not None
    assert result_y is not None
    assert abs(result_x) < 0.01
    assert abs(result_y) < 0.01


def test_check_pass_divergence_too_few_positions():
    _check_pass_divergence(
        "test",
        [Offset.zero(), Offset(1.0, 0.0)],
        tolerance=0.1,
        pass_num=1,
    )


def test_check_pass_divergence_shrinking_corrections_ok():
    positions = [Offset.zero(), Offset(2.0, 0.0), Offset(3.0, 0.0)]
    _check_pass_divergence("test", positions, tolerance=0.1, pass_num=2)


def test_check_pass_divergence_raises_when_corrections_grow():
    positions = [Offset.zero(), Offset(1.0, 0.0), Offset(2.3, 0.0)]
    with raises(DivergenceError, match="pass corrections diverging") as exc_info:
        _check_pass_divergence("test", positions, tolerance=0.1, pass_num=2)
    err = exc_info.value
    assert err.strategy == "test"
    assert err.pass_num == 2
    assert err.previous == Offset(1.0, 0.0)
    assert err.exit_kind is SeekExitKind.DIVERGENCE


def test_check_pass_divergence_skips_when_prior_correction_below_tolerance():
    positions = [Offset.zero(), Offset.zero(), Offset(1.0, 0.0)]
    _check_pass_divergence("test", positions, tolerance=0.1, pass_num=2)


def test_check_pass_divergence_raises_at_tolerance_boundary():
    positions = [Offset.zero(), Offset(0.1, 0.0), Offset(0.226, 0.0)]
    with raises(DivergenceError, match="pass corrections diverging"):
        _check_pass_divergence("test", positions, tolerance=0.1, pass_num=2)


class _ScriptedStrategy(SeekStrategy):
    def __init__(self, steps: list[Offset]) -> None:
        self._steps = steps

    @property
    def name(self) -> str:
        return "scripted"

    def announce_start(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, ctx: SeekSession, console: _FakeReporter
    ) -> None:
        return None

    def _step(self, ctx: SeekSession, pass_num: int, best: Offset) -> Offset:
        return self._steps[pass_num - 1]

    def _pass_message(
        self,
        pass_num: int,
        new: Offset,
        moved: Offset,
        ctx: SeekSession,
    ) -> str:
        return f"pass {pass_num}"


def test_sweep_centroid_skips_divergence_on_coarse_passes():
    session = SeekSession.__new__(SeekSession)
    session.config = _test_cfg()
    strategy = SweepCentroidStrategy()
    coarse_phases = session.config.coarse_phases
    for pass_num in range(1, coarse_phases + 1):
        assert not strategy.should_check_divergence(session, pass_num)
    assert strategy.should_check_divergence(session, coarse_phases + 1)


def test_search_aborts_on_pass_divergence():
    session = SeekSession.__new__(SeekSession)
    session.config = _test_cfg(max_passes=6, tolerance=0.1)
    strategy = _ScriptedStrategy(
        [
            Offset(1.0, 0.0),
            Offset(2.3, 0.0),
        ]
    )
    with raises(DivergenceError, match="pass corrections diverging at pass 2"):
        strategy.search(session, _FakeReporter())  # type: ignore[arg-type]


def test_search_raises_when_max_passes_exhausted_without_convergence():
    session = SeekSession.__new__(SeekSession)
    session.config = _test_cfg(max_passes=2, tolerance=0.01)
    strategy = _ScriptedStrategy(
        [
            Offset(1.0, 0.0),
            Offset(0.5, 0.0),
        ]
    )
    with raises(MaxPassesError) as exc_info:
        strategy.search(session, _FakeReporter())  # type: ignore[arg-type]
    err = exc_info.value
    assert err.strategy == "scripted"
    assert err.max_passes == 2
    assert err.tolerance == 0.01
    assert err.exit_kind is SeekExitKind.MAX_PASSES


def test_insufficient_samples_error_exit_kind():
    err = InsufficientSamplesError("debug_scan", count=3, min_samples=20)
    assert err.exit_kind is SeekExitKind.INSUFFICIENT_SAMPLES
    assert err.count == 3
    assert err.min_samples == 20
    assert "debug_scan collected 3 in-range samples" in str(err)
