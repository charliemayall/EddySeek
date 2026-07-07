"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Compare EDDY_SEEK_ACCURACY runs offline (HTML or JSON inputs).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .plotting._plotly import plotly_available, write_html
from .plotting.accuracy import (
    AccuracyRun,
    write_accuracy_comparison_plot,
)
from .plotting.accuracy_io import load_accuracy_run


def _default_label(path: Path) -> str:
    stem = path.stem
    for prefix in ("accuracy", "session"):
        if stem.startswith(prefix):
            return stem
    return stem


def _write_png(fig: object, output: Path) -> None:
    try:
        import kaleido  # noqa: F401  # pyright: ignore[reportMissingImports]
    except ImportError as exc:
        msg = "PNG export requires kaleido: pip install kaleido"
        raise SystemExit(msg) from exc
    fig.write_image(str(output))  # type: ignore[attr-defined]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare EDDY_SEEK_ACCURACY HTML or JSON runs.",
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="accuracy plot HTML or JSON files (minimum 2)",
    )
    parser.add_argument(
        "--labels",
        nargs="+",
        help="strategy labels (defaults to input filenames)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="output HTML or PNG path",
    )
    parser.add_argument(
        "--png",
        action="store_true",
        help="write PNG instead of HTML (requires kaleido)",
    )
    args = parser.parse_args(argv)

    if len(args.inputs) < 2:
        parser.error("need at least 2 accuracy inputs")

    labels = args.labels or [_default_label(path) for path in args.inputs]
    if len(labels) != len(args.inputs):
        parser.error("--labels count must match input count")

    if not plotly_available():
        print("plotly is required: uv sync --extra debug", file=sys.stderr)
        return 1

    runs: list[AccuracyRun] = []
    for path, label in zip(args.inputs, labels):
        if not path.is_file():
            print(f"input not found: {path}", file=sys.stderr)
            return 1
        try:
            strategy, records, durations_s = load_accuracy_run(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            return 1
        runs.append((strategy or label, records, durations_s))

    fig = write_accuracy_comparison_plot(runs=runs)
    if fig is None:
        print("failed to build comparison plot", file=sys.stderr)
        return 1

    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.png or output.suffix.lower() == ".png":
        _write_png(fig, output)
    elif not write_html(output, fig):
        print(f"failed to write {output}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
