"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

LDC1612_STUB = (
    "class LDC1612:\n"
    "    def __init__(self, config): pass\n"
    "    def add_client(self, cb): pass\n"
)


def _purge_eddy_seek_modules() -> None:
    for name in list(sys.modules):
        if name == "extras.eddy_seek" or name.startswith("extras._eddy_seek"):
            del sys.modules[name]


@pytest.fixture
def klippy_extras(tmp_path):
    extras = tmp_path / "klippy" / "extras"
    extras.mkdir(parents=True)

    (extras / "ldc1612.py").write_text(LDC1612_STUB)
    (extras / "eddy_seek.py").symlink_to(ROOT / "src" / "eddy_seek.py")
    (extras / "_eddy_seek").symlink_to(ROOT / "src" / "_eddy_seek")

    sys.path.insert(0, str(tmp_path / "klippy"))
    yield extras
    sys.path.pop(0)
    _purge_eddy_seek_modules()


def test_install_script(tmp_path):
    install_dir = tmp_path / "klippy" / "extras"
    cache = ROOT / "src" / "_eddy_seek" / "__pycache__"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "motion_guard.cpython-311.pyc").write_bytes(b"stale")

    result = subprocess.run(
        ["./install.sh", str(install_dir)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "EddySeek: installed" in result.stdout
    assert "cleared" in result.stdout
    assert not cache.exists()

    assert (install_dir / "eddy_seek.py").resolve() == (
        ROOT / "src" / "eddy_seek.py"
    ).resolve()
    assert (install_dir / "_eddy_seek").resolve() == (
        ROOT / "src" / "_eddy_seek"
    ).resolve()
    assert (install_dir / "_eddy_seek" / "config.py").resolve() == (
        ROOT / "src" / "_eddy_seek" / "config.py"
    ).resolve()

    (install_dir / "ldc1612.py").write_text(LDC1612_STUB)
    sys.path.insert(0, str(tmp_path / "klippy"))
    try:
        mod = importlib.import_module("extras.eddy_seek")
        assert hasattr(mod, "load_config")
        assert hasattr(mod, "EddySeek")
    finally:
        sys.path.pop(0)
        _purge_eddy_seek_modules()


def test_eddy_seek_relative_imports_after_install(klippy_extras):
    mod = importlib.import_module("extras.eddy_seek")
    assert mod.__name__ == "extras.eddy_seek"
    assert hasattr(mod, "load_config")
    assert hasattr(mod, "EddySeek")

    # Relative imports from eddy_seek.py resolved the _eddy_seek package members.
    assert importlib.import_module("extras._eddy_seek.config") is not None
    assert importlib.import_module("extras._eddy_seek.tool_align") is not None
    assert importlib.import_module("extras._eddy_seek.strategy") is not None
    assert importlib.import_module("extras._eddy_seek.strategy.sweep") is not None
