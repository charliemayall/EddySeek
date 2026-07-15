"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

from eddy_seek.config import SeekConfig
from eddy_seek.docs_ref import (
    gcode_commands_table,
    generated_marker,
    seek_config_main_table,
    seek_config_sweep_table,
)
from eddy_seek.gcode_commands import GCODE_COMMAND_NAMES

REPO_ROOT = Path(__file__).resolve().parents[1]
USER_GUIDE = REPO_ROOT / "docs" / "USER_GUIDE.md"


def _section_body(name: str) -> str:
    text = USER_GUIDE.read_text(encoding="utf-8")
    begin = generated_marker(name, begin=True)
    end = generated_marker(name, begin=False)
    pattern = re.compile(
        rf"{re.escape(begin)}\n(.*?)\n{re.escape(end)}",
        re.DOTALL,
    )
    match = pattern.search(text)
    assert match is not None, f"missing section {name!r}"
    return match.group(1).strip()


def test_seek_config_fields_have_doc_metadata():
    missing = [spec.name for spec in fields(SeekConfig) if "doc" not in spec.metadata]
    assert not missing, f"SeekConfig fields missing doc metadata: {missing}"


def test_generated_seek_config_main_matches_user_guide():
    assert _section_body("seek-config-main") == seek_config_main_table()


def test_generated_seek_config_sweep_matches_user_guide():
    assert _section_body("seek-config-sweep") == seek_config_sweep_table()


def test_generated_gcode_commands_matches_user_guide():
    assert _section_body("gcode-commands") == gcode_commands_table()


def test_user_guide_lists_all_gcode_commands():
    guide = USER_GUIDE.read_text(encoding="utf-8")
    missing = sorted(name for name in GCODE_COMMAND_NAMES if name not in guide)
    assert not missing, f"USER_GUIDE missing G-code commands: {missing}"
