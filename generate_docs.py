#!/usr/bin/env python3
"""Regenerate USER_GUIDE reference sections from code metadata."""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from eddy_seek.docs_ref import (
    gcode_commands_table,
    generated_marker,
    seek_config_main_table,
    seek_config_sweep_table,
)

SCROLL_OF_TRUTH = REPO_ROOT / "docs" / "USER_GUIDE.md"

SECTIONS: dict[str, str] = {
    "seek-config-main": seek_config_main_table,
    "seek-config-sweep": seek_config_sweep_table,
    "gcode-commands": gcode_commands_table,
}


def _replace_section(text: str, name: str, body: str) -> str:
    begin = generated_marker(name, begin=True)
    end = generated_marker(name, begin=False)
    pattern = re.compile(
        rf"{re.escape(begin)}\n.*?\n{re.escape(end)}",
        re.DOTALL,
    )
    replacement = f"{begin}\n{body}\n{end}"
    if not pattern.search(text):
        raise SystemExit(f"missing markers for section {name!r} in {SCROLL_OF_TRUTH}")
    return pattern.sub(replacement, text, count=1)


def main() -> int:
    text = SCROLL_OF_TRUTH.read_text(encoding="utf-8")
    for name, builder in SECTIONS.items():
        text = _replace_section(text, name, builder())
    SCROLL_OF_TRUTH.write_text(text, encoding="utf-8")
    print(f"updated {SCROLL_OF_TRUTH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
