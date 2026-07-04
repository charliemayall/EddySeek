#!/usr/bin/env python3
"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

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


class COLORS(Enum):
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
    if ans == "y":
        subprocess.run(["sudo", "systemctl", "restart", "klipper"], check=True)
        print("Klipper restarted")


def purge_pycache(*roots: Path) -> int:
    """Remove stale bytecode caches under ``roots`` (returns dirs removed)."""
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

    entry = dest / "eddy_seek.py"
    entry.unlink(missing_ok=True)
    entry.symlink_to((src_dir / "eddy_seek.py").resolve())

    src_pkg = src_dir / "_eddy_seek"
    if not src_pkg.is_dir() or not (src_pkg / "config.py").is_file():
        print(
            f"""
            Error: missing {src_pkg},\
            did you clone the repository?\n
            Try removing and re-cloning the repository.
            """,
            file=sys.stderr,
        )
        sys.exit(1)

    pkg = dest / "_eddy_seek"
    if pkg.is_symlink() or pkg.is_file():
        pkg.unlink()
    elif pkg.is_dir():
        shutil.rmtree(pkg)
    pkg.symlink_to((src_dir / "_eddy_seek").resolve())

    caches = purge_pycache(src_dir, dest)
    if caches:
        print(f"{_c('-- ', COLORS.GRAY)}cleared {caches} __pycache__ dir(s)")

    cprint("\u2728 EddySeek: installed \u2728".center(60), COLORS.GREEN)
    print(f"{_c('-- ', COLORS.GRAY)}{dest / 'eddy_seek.py'}")
    print(f"{_c('-- ', COLORS.GRAY)}{dest / '_eddy_seek/'}")
    print(
        f"""\n{_c("Next steps:", COLORS.GREEN)}\n
    1. Add [eddy_seek] to printer.cfg and set config options
    2. Restart Klipper: sudo systemctl restart klipper

    After git pull, re-run ./install.sh then restart Klipper.
    """
    )
    restart_klipper()


if __name__ == "__main__":
    main()
