"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Shared pytest fixtures and helpers.
"""

from __future__ import annotations

import pytest
from fakes import PLOT_SESSION_ID, PLOT_WRITE_AT

from _eddy_seek.plotting import PlotWriter


@pytest.fixture
def requires_plotly():
    pytest.importorskip("plotly")


@pytest.fixture
def plot_tmp(tmp_path):
    writer = PlotWriter(tmp_path, PLOT_SESSION_ID, write_at=PLOT_WRITE_AT)
    return writer, tmp_path
