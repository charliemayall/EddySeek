"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Klipper extra package entry (see klippy/extras/display/__init__.py).
"""

from __future__ import annotations

from . import host


def load_config(config):
    return host.load_config(config)
