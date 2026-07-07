"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_install():
    spec = importlib.util.spec_from_file_location("install", ROOT / "install.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("install.py not found")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


install = _load_install()


class _FakeVersionInfo:
    def __init__(self, major: int, minor: int) -> None:
        self.major = major
        self.minor = minor

    def __lt__(self, other: tuple[int, ...]) -> bool:
        return (self.major, self.minor) < other


def _set_python_version(
    monkeypatch: pytest.MonkeyPatch, major: int, minor: int
) -> None:
    monkeypatch.setattr(sys, "version_info", _FakeVersionInfo(major, minor))


def test_warn_for_python_version_silent_on_supported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_python_version(monkeypatch, 3, 10)
    install.warn_for_python_version()
    assert capsys.readouterr().out == ""


def test_warn_for_python_version_warns_on_old_python(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_python_version(monkeypatch, 3, 9)
    install.warn_for_python_version()
    out = capsys.readouterr().out
    assert "Error: Python version is not supported" in out
    assert "Minimum version --> 3.10" in out
    assert "Your version --> 3.9" in out
    assert "EddySeek may not work as expected" in out
