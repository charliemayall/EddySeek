"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Load EDDY_SEEK_ACCURACY results from saved HTML or JSON for offline comparison.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..common import Offset
from .primitives import AccuracyRepeatRecord

_OFFSET_RE = re.compile(
    r"\(\s*([+-]?(?:\d+\.?\d*|\.\d+))\s*,\s*([+-]?(?:\d+\.?\d*|\.\d+))\s*\)"
)
_TABLE_RE = re.compile(
    r'<table class="stats-table">.*?<thead>.*?</thead>\s*<tbody>(.*?)</tbody>',
    re.DOTALL,
)
_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.DOTALL)
_CELL_RE = re.compile(r"<td>(.*?)</td>", re.DOTALL)
_HEADER_RE = re.compile(r"<th>(.*?)</th>", re.DOTALL)


def parse_offset_cell(text: str) -> Offset:
    match = _OFFSET_RE.search(text.strip())
    if match is None:
        msg = f"expected offset like (+0.0123, -0.0045), got {text!r}"
        raise ValueError(msg)
    return Offset(float(match.group(1)), float(match.group(2)))


def _parse_accuracy_table_html(html: str) -> list[AccuracyRepeatRecord]:
    table_match = _TABLE_RE.search(html)
    if table_match is None:
        msg = "no accuracy stats-table found in HTML"
        raise ValueError(msg)

    header_match = re.search(
        r'<table class="stats-table">.*?<thead>.*?<tr>(.*?)</tr>',
        html,
        re.DOTALL,
    )
    if header_match is None:
        msg = "no accuracy table header found in HTML"
        raise ValueError(msg)

    headers = [
        re.sub(r"\s+", " ", cell.strip())
        for cell in _HEADER_RE.findall(header_match.group(1))
    ]
    try:
        offset_idx = headers.index("Offset (mm)")
    except ValueError as exc:
        msg = "accuracy table missing Offset (mm) column"
        raise ValueError(msg) from exc
    repeat_idx = headers.index("Repeat") if "Repeat" in headers else 0

    records: list[AccuracyRepeatRecord] = []
    for row_html in _ROW_RE.findall(table_match.group(1)):
        cells = [cell.strip() for cell in _CELL_RE.findall(row_html)]
        if len(cells) <= offset_idx:
            continue
        repeat_num = int(cells[repeat_idx])
        offset = parse_offset_cell(cells[offset_idx])
        records.append(AccuracyRepeatRecord(repeat_num, offset))
    if len(records) < 2:
        msg = f"need at least 2 repeats, found {len(records)}"
        raise ValueError(msg)
    return records


def parse_accuracy_html(path: Path | str) -> list[AccuracyRepeatRecord]:
    text = Path(path).read_text(encoding="utf-8")
    return _parse_accuracy_table_html(text)


def _records_from_offsets(
    offsets: list[list[float] | tuple[float, float]],
) -> list[AccuracyRepeatRecord]:
    if len(offsets) < 2:
        msg = f"need at least 2 offsets, found {len(offsets)}"
        raise ValueError(msg)
    return [
        AccuracyRepeatRecord(repeat_num, Offset(float(x), float(y)))
        for repeat_num, (x, y) in enumerate(offsets, start=1)
    ]


def parse_accuracy_json(
    path: Path | str,
) -> tuple[str | None, list[AccuracyRepeatRecord], list[float] | None]:
    payload: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        if len(payload) != 1:
            msg = "JSON array must contain exactly one run object"
            raise ValueError(msg)
        payload = payload[0]
    if not isinstance(payload, dict):
        msg = "accuracy JSON must be an object or single-element array"
        raise ValueError(msg)

    strategy = payload.get("strategy")
    if strategy is not None and not isinstance(strategy, str):
        msg = "strategy must be a string"
        raise ValueError(msg)

    raw_offsets = payload.get("offsets")
    if not isinstance(raw_offsets, list):
        msg = "offsets must be a list of [x, y] pairs"
        raise ValueError(msg)

    durations: list[float] | None = None
    raw_durations = payload.get("durations_s")
    if raw_durations is not None:
        if not isinstance(raw_durations, list):
            msg = "durations_s must be a list of numbers"
            raise ValueError(msg)
        durations = [float(value) for value in raw_durations]

    return strategy, _records_from_offsets(raw_offsets), durations


def load_accuracy_run(
    path: Path | str,
) -> tuple[str | None, list[AccuracyRepeatRecord], list[float] | None]:
    source = Path(path)
    if source.suffix.lower() == ".json":
        return parse_accuracy_json(source)
    return None, parse_accuracy_html(source), None
