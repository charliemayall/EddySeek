"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""

from _eddy_seek.tool_align import tool0_center_xy
from _eddy_seek.session import Position


def test_tool0_center_xy_offset_applies():
    center = tool0_center_xy(10.0, 20.0, Position(1.5, -0.5))
    assert center == Position(11.5, 19.5)
