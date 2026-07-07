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

import shutil
import subprocess
import sys
from enum import Enum
from pathlib import Path

EDDY_SEEK_DIR = Path(__file__).resolve().parent
DEFAULT_DEST = Path.home() / "klipper" / "klippy" / "extras"

_RESET = "\x1b[0m"


class COLORS(str, Enum):
    RED = "\x1b[31;20m"
    GREEN = "\x1b[32;20m"
    GRAY = "\x1b[90;20m"
    WHITE = "\x1b[37;20m"


def _c(text, color: COLORS):
    return color.value + text + _RESET


def cprint(text, color: COLORS):
    print(_c(text, color))


def restart_klipper() -> None:
    if not sys.stdin.isatty():
        return
    ans = input("Restart Klipper? (y/n): ")
    if ans.lower() == "y":
        subprocess.run(["sudo", "systemctl", "restart", "klipper"], check=True)
        print("Klipper restarted")


def purge_pycache(*roots: Path) -> int:
    """
    Remove stale bytecode caches under ``roots`` (returns dirs removed).

    Likely not necessary using git install route, but __pycache__ dirs
    caused issues when direct deploying via `make deploy` route.
    """
    removed = 0
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for cache in root.resolve().rglob("__pycache__"):
            if not cache.is_dir():
                continue
            key = cache.resolve()
            if key in seen:
                continue
            shutil.rmtree(cache)
            seen.add(key)
            removed += 1
    return removed


def warn_for_python_version():
    MIN = (3, 10)
    if sys.version_info < MIN:
        print(
            _c(
                f"Error: Python version is not supported\n"
                f"Minimum version --> {MIN[0]}.{MIN[1]}\n"
                f"Your version --> {sys.version_info.major}.{sys.version_info.minor}\n"
                f"EddySeek may not work as expected",
                COLORS.RED,
            )
        )


def main() -> None:
    warn_for_python_version()
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

    entry = dest / "eddy_seek.py"
    entry.unlink(missing_ok=True)
    entry.symlink_to((src_dir / "eddy_seek.py").resolve())

    pkg = dest / "_eddy_seek"
    if pkg.is_symlink() or pkg.is_file():
        pkg.unlink()
    elif pkg.is_dir():
        shutil.rmtree(pkg)
    pkg.symlink_to((src_dir / "_eddy_seek").resolve())

    caches = purge_pycache(src_dir, dest)
    if caches:
        print(f"{_c('-- ', COLORS.GRAY)}cleaned up caches")

    cprint("\u2728 EddySeek: installed \u2728".center(60), COLORS.GREEN)
    print(f"{_c('-- ', COLORS.GRAY)}{dest / 'eddy_seek.py'}")
    print(f"{_c('-- ', COLORS.GRAY)}{dest / '_eddy_seek/'}")
    print(
        f"""\n{_c("Next steps:", COLORS.GREEN)}\n
    1. Add [eddy_seek] to printer.cfg (set sensor_x/sensor_y for your coil)
    2. Restart Klipper: sudo systemctl restart klipper

    After git pull, re-run ./install.sh then restart Klipper.
    """
    )
    restart_klipper()


if __name__ == "__main__":
    main()
