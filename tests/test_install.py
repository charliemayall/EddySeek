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


@pytest.fixture
def config_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_dir = tmp_path / "printer_data" / "config"
    monkeypatch.setattr(install, "PRINTER_CONFIG_DIR", config_dir)
    monkeypatch.setattr(install, "EDDY_SEEK_CFG", config_dir / "eddy_seek.cfg")
    monkeypatch.setattr(install, "EXAMPLE_CFG", ROOT / "example.cfg")
    return config_dir


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


def test_offer_example_config_skips_when_not_tty(
    monkeypatch: pytest.MonkeyPatch, config_paths: Path
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    install.offer_example_config()
    assert not install.EDDY_SEEK_CFG.exists()


def test_offer_example_config_skips_existing_file(
    monkeypatch: pytest.MonkeyPatch,
    config_paths: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    config_paths.mkdir(parents=True)
    install.EDDY_SEEK_CFG.write_text("[eddy_seek]\n")
    install.offer_example_config()
    assert install.EDDY_SEEK_CFG.read_text() == "[eddy_seek]\n"
    assert "config already exists" in capsys.readouterr().out


def test_offer_example_config_copies_on_yes(
    monkeypatch: pytest.MonkeyPatch,
    config_paths: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "y")
    config_paths.mkdir(parents=True)
    install.offer_example_config()
    assert install.EDDY_SEEK_CFG.is_file()
    out = capsys.readouterr().out
    assert "Copied example config" in out
    assert "[include eddy_seek.cfg]" in out


def test_offer_example_config_skips_on_no(
    monkeypatch: pytest.MonkeyPatch, config_paths: Path
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    install.offer_example_config()
    assert not install.EDDY_SEEK_CFG.exists()


def test_offer_example_config_skips_on_directory_not_found(
    monkeypatch: pytest.MonkeyPatch, config_paths: Path
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert not config_paths.exists()
    install.offer_example_config()
    assert not config_paths.exists()
    assert not install.EDDY_SEEK_CFG.exists()


@pytest.fixture
def klippy_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env = tmp_path / "klippy-env"
    bin_dir = env / "bin"
    bin_dir.mkdir(parents=True)
    pip = bin_dir / "pip3"
    python = bin_dir / "python3"
    pip.write_text("#!/bin/sh\necho pip $@\n")
    python.write_text("#!/bin/sh\necho python $@\n")
    pip.chmod(0o755)
    python.chmod(0o755)
    monkeypatch.setattr(install, "KLIPPY_ENV", env)
    monkeypatch.setattr(install, "KLIPPY_PIP", pip)
    monkeypatch.setattr(install, "KLIPPY_PYTHON", python)
    return env, pip, python


def test_offer_plotly_install_skips_when_not_tty(
    monkeypatch: pytest.MonkeyPatch, klippy_env
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    install.offer_plotly_install()


def test_offer_plotly_install_skips_when_klippy_env_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(install, "KLIPPY_PIP", Path("/nonexistent/pip3"))
    install.offer_plotly_install()
    assert "klippy-env not found" in capsys.readouterr().out


def test_offer_plotly_install_skips_when_already_installed(
    monkeypatch: pytest.MonkeyPatch,
    klippy_env,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(install, "plotly_installed_in_klippy_env", lambda: True)
    install.offer_plotly_install()
    assert "plotly already installed" in capsys.readouterr().out


def test_offer_plotly_install_runs_pip_on_yes(
    monkeypatch: pytest.MonkeyPatch, klippy_env
) -> None:
    _env, pip, _python = klippy_env
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(install, "plotly_installed_in_klippy_env", lambda: False)
    monkeypatch.setattr("builtins.input", lambda _: "y")
    calls: list[list[str]] = []

    def fake_run(cmd, *, check):
        calls.append(cmd)
        assert check is True

    monkeypatch.setattr(install.subprocess, "run", fake_run)
    install.offer_plotly_install()
    assert calls == [[str(pip), "install", "plotly"]]


def test_offer_plotly_install_skips_on_no(
    monkeypatch: pytest.MonkeyPatch, klippy_env
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(install, "plotly_installed_in_klippy_env", lambda: False)
    monkeypatch.setattr("builtins.input", lambda _: "n")

    def fail_run(*_args, **_kwargs):
        raise AssertionError("subprocess.run should not be called")

    monkeypatch.setattr(install.subprocess, "run", fail_run)
    install.offer_plotly_install()
