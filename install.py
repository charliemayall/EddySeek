#!/usr/bin/env python3
"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

Symlink EddySeek into Klipper's extras directory (optionally, user specified target)
"""

from __future__ import annotations

import sys
from pathlib import Path
from enum import Enum

EDDY_SEEK_DIR = Path(__file__).resolve().parent
DEFAULT_DEST = Path.home() / "klipper" / "klippy" / "extras"

_RESET = "\x1b[0m"


class COLORS(Enum):
    RED = "\x1b[31;20m"
    GREEN = "\x1b[32;20m"
    GRAY = "\x1b[90;20m"
    WHITE = "\x1b[37;20m"


def _c(text, color: COLORS):
    return color.value + text + _RESET


def cprint(text, color: COLORS):
    print(_c(text, color))


def main() -> None:
    dest = (
        Path(sys.argv[1]).expanduser().resolve()
        if len(sys.argv) > 1  # handle custom destination arg
        else DEFAULT_DEST
    )
    src_dir = EDDY_SEEK_DIR / "src"
    if not (src_dir / "eddy_seek.py").is_file():
        print(
            f"""
            Error: missing {src_dir / "eddy_seek.py"},\
            did you clone the repository?\n
            Try removing and re-cloning the repository.
            """,
            file=sys.stderr,
        )
        sys.exit(1)

    dest.mkdir(parents=True, exist_ok=True)
    for src in [src_dir / "eddy_seek.py", *(src_dir / "_eddy_seek").glob("*.py")]:
        link = dest / src.relative_to(src_dir)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.unlink(missing_ok=True)
        link.symlink_to(src.resolve())

    cprint("\u2728 EddySeek: installed \u2728".center(60), COLORS.GREEN)
    print(f"{_c('-- ', COLORS.GRAY)}{dest / 'eddy_seek.py'}")
    print(f"{_c('-- ', COLORS.GRAY)}{dest / '_eddy_seek/*.py'}")
    print(
        f"""\n{_c("Next steps:", COLORS.GREEN)}\n
    1. Add [eddy_seek] to printer.cfg
    2. Restart Klipper: FIRMWARE_RESTART
    """
    )


if __name__ == "__main__":
    main()
