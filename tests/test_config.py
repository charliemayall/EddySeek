"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from datetime import datetime

from fakes import CommandError, FakeGcmd, FakeKlipperConfig, as_config
from pytest import raises

from eddy_seek.common import session_artifact_run_dir
from eddy_seek.config import (
    SeekConfig,
    _field_name_for_key,
    _parse_runtime_value,
    load_seek_config,
)


def _runtime_value_ok(key: str, value) -> bool:
    try:
        _parse_runtime_value(_field_name_for_key(key), key, value)
        return True
    except ValueError:
        return False


def test_validate_var():
    assert _runtime_value_ok("max_jog_x", 5.0) is True
    assert _runtime_value_ok("max_jog_y", 5.0) is True
    assert _runtime_value_ok("tolerance", 0.1) is True
    assert _runtime_value_ok("dwell_time", 0.5) is True
    assert _runtime_value_ok("jog_speed", 10.0) is True
    assert _runtime_value_ok("search_for", "max") is True
    assert _runtime_value_ok("strategy", "debug_scan") is True
    assert _runtime_value_ok("max_passes", 6) is True
    assert _runtime_value_ok("save_session_trace", True) is True
    assert _runtime_value_ok("save_plots", True) is True
    assert _runtime_value_ok("save_session_trace", "false") is True
    assert _runtime_value_ok("search_for", "bogus") is False
    assert _runtime_value_ok("strategy", "bogus") is False
    assert _runtime_value_ok("max_passes", -1) is False
    assert _runtime_value_ok("tolerance", 0) is False
    assert _runtime_value_ok("jog_speed", 0) is False
    assert _runtime_value_ok("max_jog_x", -0.1) is False


def test_strategy_from_gcmd():
    cfg = SeekConfig()
    assert cfg.strategy_from_gcmd(FakeGcmd()) == "sweep_centroid"
    assert cfg.strategy_from_gcmd(FakeGcmd({"STRATEGY": "centroid"})) == "centroid"

    with raises(CommandError, match="invalid STRATEGY='bogus'"):
        cfg.strategy_from_gcmd(FakeGcmd({"STRATEGY": "bogus"}))


def test_apply_runtime_set():
    cfg = SeekConfig()
    changed = cfg.apply_runtime_set(FakeGcmd({"STRATEGY": "centroid"}))
    assert changed == ["strategy --> centroid"]
    assert cfg.strategy == "centroid"

    with raises(CommandError):
        cfg.apply_runtime_set(FakeGcmd({"STRATEGY": "bogus"}))

    with raises(CommandError, match=r"Unknown parameter 'GRD_STEP_X'"):
        cfg.apply_runtime_set(FakeGcmd({"GRD_STEP_X": "2.5"}))

    with raises(CommandError, match="Can only be set via your config file"):
        cfg.apply_runtime_set(FakeGcmd({"DEBUG": "1"}))

    changed = cfg.apply_runtime_set(FakeGcmd({"STRATEGY": "debug_scan"}))
    assert changed == ["strategy --> debug_scan"]
    assert cfg.strategy == "debug_scan"


def test_grid_step_derived_from_max_jog():
    cfg = SeekConfig(max_jog_x=3.0, max_jog_y=4.0)
    assert cfg.grid_step_x == 1.5
    assert cfg.grid_step_y == 2.0


def test_session_artifact_run_dir_sortable():
    when = datetime(2026, 7, 2, 14, 30, 45)
    assert (
        session_artifact_run_dir(when, run_label="tools") == "2026-07-02_14-30-45_tools"
    )
    assert (
        session_artifact_run_dir(when, run_label="start") == "2026-07-02_14-30-45_start"
    )


def test_load_seek_config_speeds_mm_s_to_mm_min():
    cfg = load_seek_config(
        as_config(
            FakeKlipperConfig(
                jog_speed="10", sweep_coarse_speed="20", sweep_fine_speed="5"
            )
        )
    )
    assert cfg.jog_speed == 600.0
    assert cfg.sweep_coarse_speed == 1200.0
    assert cfg.sweep_fine_speed == 300.0


def test_load_seek_config_rejects_invalid_strategy():
    with raises(ValueError, match="strategy"):
        load_seek_config(as_config(FakeKlipperConfig(strategy="bogus")))


def test_load_seek_config_rejects_invalid_search_for():
    with raises(ValueError, match="search_for"):
        load_seek_config(as_config(FakeKlipperConfig(search_for="bogus")))


def test_load_seek_config_sweep_arc_resolution():
    cfg = load_seek_config(as_config(FakeKlipperConfig(sweep_arc_resolution="0.2")))
    assert cfg.sweep_arc_resolution == 0.2


def test_load_seek_config_debug():
    assert load_seek_config(as_config(FakeKlipperConfig())).debug is False
    assert load_seek_config(as_config(FakeKlipperConfig(debug="true"))).debug is True


def test_load_seek_config_sweep_coarse_defaults():
    cfg = load_seek_config(as_config(FakeKlipperConfig()))
    assert cfg.coarse_phases == 2
    assert cfg.coarse_cross_passes == 3


def test_apply_runtime_set_coarse_sweep_params():
    cfg = SeekConfig()
    changed = cfg.apply_runtime_set(
        FakeGcmd({"COARSE_PHASES": "3", "COARSE_CROSS_PASSES": "2"})
    )
    assert changed == ["coarse_phases --> 3", "coarse_cross_passes --> 2"]
    assert cfg.coarse_phases == 3
    assert cfg.coarse_cross_passes == 2
