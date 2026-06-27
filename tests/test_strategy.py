"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""

import math

from pytest import raises

from _eddy_seek.config import SeekConfig
from _eddy_seek.session import SeekSession, _sample_stdev
from _eddy_seek.strategy import CentroidStrategy, TernaryStrategy, strategy_for


class _FakeGcmd:
    def __init__(self, params: dict[str, str] | None = None) -> None:
        self._params = {k.upper(): v for k, v in (params or {}).items()}

    def get_command_parameters(self) -> dict[str, str]:
        return self._params

    def error(self, msg: str) -> None:
        raise ValueError(msg)

    def respond_info(self, msg: str) -> None:
        pass


def _test_cfg(**overrides) -> SeekConfig:
    defaults = dict(
        window_size=20,
        max_jog_x=5.0,
        max_jog_y=5.0,
        tolerance=0.1,
        dwell_time=0.5,
        jog_speed=600.0,
        search_for="max",
        strategy="ternary",
        grid_step_x=2.5,
        grid_step_y=2.5,
        max_iter=10,
        max_passes=6,
    )
    defaults.update(overrides)
    return SeekConfig(**defaults)


def test_strategy_weights_and_runtime_set():
    cfg = _test_cfg()
    session = SeekSession.__new__(SeekSession)
    session._config = cfg

    centroid = CentroidStrategy()
    assert centroid._frequency_weight(session, 100.0, 50.0, 100.0) == 50.0
    assert centroid._frequency_weight(session, 50.0, 50.0, 100.0) == 0.0
    session._config = _test_cfg(search_for="min")
    assert centroid._frequency_weight(session, 50.0, 50.0, 100.0) == 50.0

    ternary = TernaryStrategy()
    assert ternary._is_better(session, 90.0, 80.0) is False
    assert ternary._is_better(session, 70.0, 80.0) is True
    assert _sample_stdev([1.0, 3.0], 2.0) == math.sqrt(2.0)

    cfg = _test_cfg()
    changed = cfg.apply_runtime_set(_FakeGcmd({"STRATEGY": "centroid"}))
    assert changed == ["strategy=centroid"]
    assert cfg.strategy == "centroid"
    with raises(ValueError):
        cfg.apply_runtime_set(_FakeGcmd({"STRATEGY": "bogus"}))

    assert strategy_for("ternary").name == "ternary"
    assert strategy_for("centroid").name == "centroid"
    with raises(ValueError):
        strategy_for("bogus")
