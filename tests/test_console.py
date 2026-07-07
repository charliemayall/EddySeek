"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from pathlib import Path

from fakes import PLOT_HTML_SUFFIX, FakeGcmd

from _eddy_seek.config import SeekConfig
from _eddy_seek.kconsole import KConsole


def test_console_prefix_mapping():
    gcmd = FakeGcmd()
    console = KConsole(gcmd, SeekConfig())  # pyright: ignore[reportArgumentType]

    console.info("Pass 1: X=+0.12 Y=-0.06 mm")
    console.entry("Seeking nozzle centre (sweep_centroid)…")
    console.exit("Done - offset X=+0.12 Y=-0.06 mm (3 passes)")
    console.error("Seek failed: no samples")

    assert gcmd.raw == [
        "echo: Pass 1: X=+0.12 Y=-0.06 mm",
        "echo: ES: Seeking nozzle centre (sweep_centroid)…",
        "echo: ES: Done - offset X=+0.12 Y=-0.06 mm (3 passes)",
        "!! Seek failed: no samples",
    ]


def test_console_detail_gated_on_verbose():
    gcmd = FakeGcmd()
    quiet = KConsole(gcmd, SeekConfig(), verbose=False)  # pyright: ignore[reportArgumentType]
    quiet.detail("internal config dump")
    assert gcmd.raw == []

    gcmd.raw.clear()
    loud = KConsole(gcmd, SeekConfig(), verbose=True)  # pyright: ignore[reportArgumentType]
    loud.detail("internal config dump")
    assert gcmd.raw == ["echo: internal config dump"]


def test_console_for_verbose_gcode_param():
    gcmd = FakeGcmd(VERBOSE="1")
    console = KConsole(
        gcmd,
        SeekConfig(),  # pyright: ignore[reportArgumentType]
    )
    assert console.verbose is True


def test_console_for_verbose_from_config():
    gcmd = FakeGcmd()
    console = KConsole(
        gcmd,
        SeekConfig(debug=True),  # pyright: ignore[reportArgumentType]
    )
    assert console.verbose is True


def test_console_plot_saved():
    gcmd = FakeGcmd()
    console = KConsole(gcmd, SeekConfig())  # pyright: ignore[reportArgumentType]
    plot_path = Path("/tmp/eddy_seek_results") / PLOT_HTML_SUFFIX
    console.plot_saved(plot_path)
    assert gcmd.raw == [f"echo: 📊 Plot saved: {plot_path}"]


def test_console_warn_plot_missing():
    gcmd = FakeGcmd()
    console = KConsole(gcmd, SeekConfig())  # pyright: ignore[reportArgumentType]
    console.warn_plot_missing()
    assert len(gcmd.raw) == 1
    assert gcmd.raw[0].startswith("echo: ")
    assert "save_plots is enabled but no plot was written" in gcmd.raw[0]


def test_console_stored_on_host():
    class _Host:
        def __init__(self) -> None:
            self.seek_config = SeekConfig()
            self.console: KConsole | None = None

        def refresh_console(self, gcmd) -> KConsole:
            self.console = KConsole(gcmd, self.seek_config)  # pyright: ignore[reportArgumentType]
            return self.console

    host = _Host()
    first = host.refresh_console(FakeGcmd())
    assert host.console is first

    second = host.refresh_console(FakeGcmd())
    assert host.console is second
