"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""

from _eddy_seek.config import SeekConfig


def test_validate_var():
    cfg = SeekConfig(
        window_size=20,
        max_jog_x=5.0,
        max_jog_y=5.0,  # type: ignore[arg-type]
        tolerance=0.1,
        dwell_time="0.5",  # type: ignore[arg-type]
        jog_speed="600.0",  # type: ignore[arg-type]
        search_for="max",
        strategy="ternary",
        grid_step_x=2.5,
        grid_step_y=2.5,
        max_iter=10,
        max_passes=6,
    )

    assert cfg._var_ok("window_size", 20) is True
    assert cfg._var_ok("max_jog_x", 5.0) is True
    assert cfg._var_ok("max_jog_y", 5.0) is True
    assert cfg._var_ok("tolerance", 0.1) is True
    assert cfg._var_ok("dwell_time", 0.5) is True
    assert cfg._var_ok("jog_speed", 600.0) is True
    assert cfg._var_ok("search_for", "max") is True
    assert cfg._var_ok("strategy", "ternary") is True
    assert cfg._var_ok("grid_step_x", 2.5) is True
    assert cfg._var_ok("grid_step_y", 2.5) is True
    assert cfg._var_ok("max_iter", 10) is True
    assert cfg._var_ok("max_passes", 6) is True
    assert cfg._var_ok("search_for", "bogus") is False
    assert cfg._var_ok("strategy", "bogus") is False
    assert cfg._var_ok("grid_step_x", -1.0) is False
    assert cfg._var_ok("grid_step_y", -1.0) is False
    assert cfg._var_ok("max_iter", -1) is False
    assert cfg._var_ok("max_passes", -1) is False
