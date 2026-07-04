"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from fakes import FakeKlipperConfig
from pytest import raises

from _eddy_seek.config import SeekConfig, load_seek_config


def test_validate_var():
    cfg = SeekConfig()

    assert cfg._var_ok("max_jog_x", 5.0) is True
    assert cfg._var_ok("max_jog_y", 5.0) is True
    assert cfg._var_ok("tolerance", 0.1) is True
    assert cfg._var_ok("dwell_time", 0.5) is True
    assert cfg._var_ok("jog_speed", 10.0) is True
    assert cfg._var_ok("search_for", "max") is True
    assert cfg._var_ok("strategy", "ternary") is True
    assert cfg._var_ok("grid_step_x", 2.5) is True
    assert cfg._var_ok("grid_step_y", 2.5) is True
    assert cfg._var_ok("max_iter", 10) is True
    assert cfg._var_ok("max_passes", 6) is True
    assert cfg._var_ok("save_session_trace", True) is True
    assert cfg._var_ok("save_plots", True) is True
    assert cfg._var_ok("save_session_trace", "false") is True
    assert cfg._var_ok("search_for", "bogus") is False
    assert cfg._var_ok("strategy", "bogus") is False
    assert cfg._var_ok("grid_step_x", -1.0) is False
    assert cfg._var_ok("grid_step_y", -1.0) is False
    assert cfg._var_ok("max_iter", -1) is False
    assert cfg._var_ok("max_passes", -1) is False


def test_load_seek_config_speeds_mm_s_to_mm_min():
    cfg = load_seek_config(
        FakeKlipperConfig(jog_speed="10", sweep_coarse_speed="20", sweep_fine_speed="5")
    )
    assert cfg.jog_speed == 600.0
    assert cfg.sweep_coarse_speed == 1200.0
    assert cfg.sweep_fine_speed == 300.0


def test_load_seek_config_rejects_invalid_strategy():
    with raises(ValueError, match="strategy"):
        load_seek_config(FakeKlipperConfig(strategy="bogus"))


def test_load_seek_config_rejects_invalid_search_for():
    with raises(ValueError, match="search_for"):
        load_seek_config(FakeKlipperConfig(search_for="bogus"))


def test_load_seek_config_save_sweep_plots_legacy_alias():
    cfg = load_seek_config(FakeKlipperConfig(save_sweep_plots="true"))
    assert cfg.save_plots is True


def test_load_seek_config_save_plots_wins_over_legacy():
    cfg = load_seek_config(
        FakeKlipperConfig(save_plots="false", save_sweep_plots="true")
    )
    assert cfg.save_plots is False


def test_load_seek_config_circle_harmonic_params():
    cfg = load_seek_config(
        FakeKlipperConfig(
            strategy="circle_harmonic",
            circle_radius_start="1.2",
            circle_radius_min="0.5",
            circle_speed="10",
            harmonic_step_gain="0.2",
        )
    )
    assert cfg.strategy == "circle_harmonic"
    assert cfg.circle_radius_start == 1.2
    assert cfg.circle_radius_min == 0.5
    assert cfg.circle_speed == 600.0
    assert cfg.harmonic_step_gain == 0.2


def test_load_seek_config_circle_refresh_sweeps():
    assert load_seek_config(FakeKlipperConfig()).circle_refresh_sweeps is False
    assert (
        load_seek_config(
            FakeKlipperConfig(circle_refresh_sweeps="true")
        ).circle_refresh_sweeps
        is True
    )


def test_load_seek_config_rejects_circle_radius_min_above_start():
    with raises(ValueError, match="circle_radius_min"):
        SeekConfig(circle_radius_start=0.5, circle_radius_min=1.0)


def test_load_seek_config_debug():
    assert load_seek_config(FakeKlipperConfig()).debug is False
    assert load_seek_config(FakeKlipperConfig(debug="true")).debug is True
