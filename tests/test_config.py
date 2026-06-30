"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""

from pytest import raises

from _eddy_seek.config import SeekConfig, load_seek_config


def test_validate_var():
    cfg = SeekConfig()

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
    assert cfg._var_ok("save_session_trace", True) is True
    assert cfg._var_ok("save_session_trace", "false") is True
    assert cfg._var_ok("search_for", "bogus") is False
    assert cfg._var_ok("strategy", "bogus") is False
    assert cfg._var_ok("grid_step_x", -1.0) is False
    assert cfg._var_ok("grid_step_y", -1.0) is False
    assert cfg._var_ok("max_iter", -1) is False
    assert cfg._var_ok("max_passes", -1) is False


class _FakeConfig:
    def __init__(self, **options: str) -> None:
        self._options = options

    def get(self, key: str, default: str = "") -> str:
        return self._options.get(key, default)

    def getint(self, key: str, default: int, **kwargs) -> int:
        return int(self._options.get(key, default))

    def getfloat(self, key: str, default: float, **kwargs) -> float:
        return float(self._options.get(key, default))

    def getboolean(self, key: str, default: bool = False, **kwargs) -> bool:
        if key not in self._options:
            return default
        return self._options[key].lower() in ("true", "1", "yes", "on")

    def error(self, msg: str) -> ValueError:
        return ValueError(msg)


def test_load_seek_config_rejects_invalid_strategy():
    with raises(ValueError, match="strategy"):
        load_seek_config(_FakeConfig(strategy="bogus"))


def test_load_seek_config_rejects_invalid_search_for():
    with raises(ValueError, match="search_for"):
        load_seek_config(_FakeConfig(search_for="bogus"))
