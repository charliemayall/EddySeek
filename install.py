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

import argparse
import shutil
import subprocess
import sys
from enum import Enum
from pathlib import Path

EDDY_SEEK_DIR = Path(__file__).resolve().parent
DEFAULT_DEST = Path.home() / "klipper" / "klippy" / "extras"
PRINTER_CONFIG_DIR = Path.home() / "printer_data" / "config"
EDDY_SEEK_CFG = PRINTER_CONFIG_DIR / "eddy_seek.cfg"
EXAMPLE_CFG = EDDY_SEEK_DIR / "example.cfg"
EXAMPLE_INDX_CFG = EDDY_SEEK_DIR / "example_indx.cfg"
# (toolchanger_type key, menu label, example file) - keep keys in sync with tools/types.py
TOOLCHANGER_CONFIG_CHOICES: tuple[tuple[str, str, Path], ...] = (
    ("generic", "generic - Tn macros, es_Tn sections", EXAMPLE_CFG),
    ("indx", "indx - Bondtech CHANGE_TOOL", EXAMPLE_INDX_CFG),
)
KLIPPY_ENV = Path.home() / "klippy-env"
KLIPPY_PIP = KLIPPY_ENV / "bin" / "pip3"
KLIPPY_PYTHON = KLIPPY_ENV / "bin" / "python3"

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


def prompt_toolchanger_config_source() -> Path | None:
    """Prompt for a toolchanger template; default is generic."""
    print("\nSelect toolchanger template:")
    for idx, (_key, label, _path) in enumerate(TOOLCHANGER_CONFIG_CHOICES):
        prefix = "[Enter] " if idx == 0 else f"  {idx}) "
        print(f"{prefix}{label}")
    choice = input("Choice: ").strip().lower()
    if choice in ("", "0", "generic"):
        return EXAMPLE_CFG
    if choice in ("1", "indx"):
        return EXAMPLE_INDX_CFG
    print(f"{_c('-- ', COLORS.GRAY)}unknown choice {choice!r}, skipping config copy")
    return None


def offer_example_config() -> None:
    if not sys.stdin.isatty():
        return
    if not PRINTER_CONFIG_DIR.exists():
        return
    if EDDY_SEEK_CFG.exists():
        print(f"{_c('-- ', COLORS.GRAY)}config already exists: {EDDY_SEEK_CFG}")
        return
    ans = input(f"Copy starter config to {EDDY_SEEK_CFG}? (y/n): ")
    if ans.lower() != "y":
        return
    source = prompt_toolchanger_config_source()
    if source is None:
        return
    if not source.is_file():
        print(f"{_c('-- ', COLORS.GRAY)}missing {source}, skipping config copy")
        return
    PRINTER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, EDDY_SEEK_CFG)
    cprint(f"Copied {source.name} to {EDDY_SEEK_CFG}", COLORS.GREEN)
    print(
        f"""\n{_c("Config next step:", COLORS.GREEN)}
    Add this line to your printer.cfg:

        [include {EDDY_SEEK_CFG.name}]

    Then edit {EDDY_SEEK_CFG} for your machine (I2C, sensor_z, tool settings).
    """
    )


def plotly_installed_in_klippy_env() -> bool:
    if not KLIPPY_PYTHON.is_file():
        return False
    result = subprocess.run(
        [str(KLIPPY_PYTHON), "-c", "import plotly"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def offer_plotly_install() -> None:
    if not sys.stdin.isatty():
        return
    if not KLIPPY_PIP.is_file():
        print(f"{_c('-- ', COLORS.GRAY)}klippy-env not found, skipping plotly install")
        return
    if plotly_installed_in_klippy_env():
        print(f"{_c('-- ', COLORS.GRAY)}plotly already installed in klippy-env")
        return
    ans = input("Install plotly for HTML debug plots? (y/n): ")
    if ans.lower() != "y":
        return
    subprocess.run([str(KLIPPY_PIP), "install", "plotly"], check=True)
    cprint("Installed plotly in klippy-env", COLORS.GREEN)


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


def parse_args(argv: list[str] | None = None) -> tuple[bool, Path]:
    parser = argparse.ArgumentParser(
        description="Symlink EddySeek into Klipper's extras directory.",
    )
    parser.add_argument(
        "--skip",
        action="store_true",
        help="Skip interactive prompts (config copy, plotly install, Klipper restart).",
    )
    parser.add_argument(
        "dest",
        nargs="?",
        type=Path,
        default=DEFAULT_DEST,
        help=f"Klipper extras directory (default: {DEFAULT_DEST})",
    )
    args = parser.parse_args(argv)
    return args.skip, args.dest.expanduser().resolve()


def main(argv: list[str] | None = None) -> None:
    warn_for_python_version()
    skip, dest = parse_args(argv)
    src_dir = EDDY_SEEK_DIR / "src"
    pkg_src = src_dir / "eddy_seek"
    if not (pkg_src / "__init__.py").is_file():
        print(
            f"""
            Error: missing {pkg_src / "__init__.py"},\
            did you clone the repository?\n
            Try removing and re-cloning the repository.
            """,
            file=sys.stderr,
        )
        sys.exit(1)

    dest.mkdir(parents=True, exist_ok=True)

    pkg = dest / "eddy_seek"
    if pkg.is_symlink() or pkg.is_file():
        pkg.unlink()
    elif pkg.is_dir() and not pkg.is_symlink():
        shutil.rmtree(pkg)
    pkg.symlink_to(pkg_src.resolve())

    caches = purge_pycache(src_dir, dest)
    if caches:
        print(f"{_c('-- ', COLORS.GRAY)}cleaned up caches")

    cprint("\u2728 EddySeek: installed \u2728".center(60), COLORS.GREEN)
    print(f"{_c('-- ', COLORS.GRAY)}{dest / 'eddy_seek/'}")
    print(
        f"""\n{_c("Next steps:", COLORS.GREEN)}\n
    1. Configure EddySeek in printer.cfg (see example*.cfg or eddy_seek.cfg)
    2. Restart Klipper: sudo systemctl restart klipper

    After git pull, re-run ./install.sh then restart Klipper.
    """
    )
    if not skip:
        offer_example_config()
        offer_plotly_install()
        restart_klipper()


if __name__ == "__main__":
    main()
