"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""

import math

from pytest import raises

from _eddy_seek.common import Position
from _eddy_seek.config import SeekConfig
from _eddy_seek.session import SeekSession, _sample_stdev
from _eddy_seek.strategy import TernaryStrategy, strategy_for
from _eddy_seek.strategy.centroid import (
    axis_weighted_centroid,
    frequency_weight,
    weighted_centroid,
)


class CommandError(Exception):
    """Matches ``klippy.gcode.CommandError`` for test doubles."""


class _FakeGcmd:
    error = CommandError

    def __init__(self, params: dict[str, str] | None = None) -> None:
        self._params = {k.upper(): v for k, v in (params or {}).items()}

    def get_command_parameters(self) -> dict[str, str]:
        return self._params

    def respond_info(self, msg: str) -> None:
        pass


def _test_cfg(**overrides) -> SeekConfig:
    return SeekConfig(**overrides)


class _FakeReporter:
    def info(self, msg: str) -> None:
        pass


class _RecordingSearchSession:
    def __init__(self) -> None:
        self.config = _test_cfg(max_iter=1, max_passes=1)
        self.positions: list[Position] = []

    def measure_at(self, offset: Position) -> float:
        self.positions.append(offset)
        return -((offset.x - 1.0) ** 2 + (offset.y + 1.0) ** 2)


def test_strategy_search_uses_positions():
    session = _RecordingSearchSession()
    best, passes_run = TernaryStrategy().search(session, _FakeReporter())  # type: ignore[arg-type]

    assert isinstance(best, Position)
    assert passes_run == 1
    assert session.positions
    assert all(isinstance(position, Position) for position in session.positions)


def test_strategy_weights_and_runtime_set():
    cfg = _test_cfg()
    session = SeekSession.__new__(SeekSession)
    session.config = cfg

    assert frequency_weight(100.0, 50.0, 100.0, "max") == 50.0
    assert frequency_weight(50.0, 50.0, 100.0, "max") == 0.0
    assert frequency_weight(50.0, 50.0, 100.0, "min") == 50.0

    ternary = TernaryStrategy()
    assert ternary._is_better(session, 90.0, 80.0) is True
    assert ternary._is_better(session, 70.0, 80.0) is False
    assert _sample_stdev([1.0, 3.0], 2.0) == math.sqrt(2.0)

    cfg = _test_cfg()
    changed = cfg.apply_runtime_set(_FakeGcmd({"STRATEGY": "centroid"}))
    assert changed == ["strategy=centroid"]
    assert cfg.strategy == "centroid"
    with raises(CommandError):
        cfg.apply_runtime_set(_FakeGcmd({"STRATEGY": "bogus"}))
    with raises(CommandError, match="unknown parameter 'GRD_STEP_X'"):
        cfg.apply_runtime_set(_FakeGcmd({"GRD_STEP_X": "2.5"}))

    assert strategy_for("ternary").name == "ternary"
    assert strategy_for("centroid").name == "centroid"
    assert strategy_for("sweep_centroid").name == "sweep_centroid"
    with raises(ValueError):
        strategy_for("bogus")


def test_weighted_centroid_finds_peak():
    probes = [
        (Position(-1.0, 0.0), 100.0),
        (Position(0.0, 0.0), 200.0),
        (Position(1.0, 0.0), 100.0),
    ]
    result = weighted_centroid(probes, "max")
    assert result is not None
    assert abs(result.x) < 0.01
    assert abs(result.y) < 0.01


def test_merged_centroid_couples_axes():
    """Y sweeps at a wrong X slice pull a merged 2-D centroid off the true peak."""
    probes = [
        (Position(-0.5, 0.0), 100.0),
        (Position(0.0, 0.0), 200.0),
        (Position(0.5, 0.0), 100.0),
        (Position(0.06, -0.5), 180.0),
        (Position(0.06, 0.0), 190.0),
        (Position(0.06, 0.5), 180.0),
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
