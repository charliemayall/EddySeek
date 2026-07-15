"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fakes import FakeGcmd, FakePrinter, RecordingToolhead, ok_seek_result

from eddy_seek.accuracy import run_accuracy_test
from eddy_seek.accuracy.stats import compute_accuracy_stats
from eddy_seek.common import Offset
from eddy_seek.config import SeekConfig
from eddy_seek.plotting.accuracy import (
    AccuracyRun,
    write_accuracy_comparison_plot,
    write_accuracy_plot,
)
from eddy_seek.plotting.accuracy_io import (
    load_accuracy_run,
    parse_accuracy_html,
    parse_accuracy_json,
    parse_offset_cell,
)
from eddy_seek.plotting.artifacts import write_figure
from eddy_seek.plotting.primitives import AccuracyRepeatRecord


def test_mock_does_not_inflate_reference_relative_spread():
    """Session-relative corrections vary with mock; found center from test start does not."""
    saved = Offset.zero()
    true_center = Offset(0.05, -0.03)
    mocks = [Offset(0.2, 0.1), Offset(-0.15, 0.25), Offset(0.1, -0.2)]

    session_relative = [(true_center - saved) - mock for mock in mocks]
    session_stats = compute_accuracy_stats(session_relative)
    assert session_stats.max_pair > 0.3

    reference_relative = [
        mock + offset for mock, offset in zip(mocks, session_relative)
    ]
    ref_stats = compute_accuracy_stats(reference_relative)
    assert ref_stats.max_pair == pytest.approx(0.0)
    assert ref_stats.mean.x == pytest.approx(true_center.x)
    assert ref_stats.mean.y == pytest.approx(true_center.y)


def test_accuracy_plot_writes_html(requires_plotly, plot_tmp):
    tmp_path, _, write_at = plot_tmp
    records = (
        AccuracyRepeatRecord(
            1, Offset(0.01, 0.02), session_plot_path="/tmp/repeat1.html"
        ),
        AccuracyRepeatRecord(2, Offset(-0.02, 0.01)),
        AccuracyRepeatRecord(3, Offset(0.0, -0.01)),
    )
    path = write_figure(
        tmp_path,
        write_accuracy_plot(repeats=list(records)),
        write_at=write_at,
        suffix="accuracy",
        run_label="accuracy",
    )
    assert path is not None
    assert Path(path).is_file()
    assert path.endswith("2026-07-02_14-30-00_accuracy/accuracy.html")


def test_accuracy_plot_needs_two_repeats(requires_plotly, tmp_path):
    fig = write_accuracy_plot(
        repeats=[AccuracyRepeatRecord(1, Offset(0.0, 0.0))],
    )
    assert fig is None


def test_compute_accuracy_stats():
    stats = compute_accuracy_stats(
        [
            Offset(0.0, 0.0),
            Offset(0.1, 0.0),
            Offset(0.0, 0.1),
        ]
    )
    assert stats.mean.x == pytest.approx(1 / 30)
    assert stats.mean.y == pytest.approx(1 / 30)
    assert stats.max_pair == pytest.approx((0.1**2 + 0.1**2) ** 0.5)
    assert len(stats.radial) == 3


def test_accuracy_plot_draws_spread_box(requires_plotly):
    fig = write_accuracy_plot(
        repeats=[
            AccuracyRepeatRecord(1, Offset(0.0, 0.0)),
            AccuracyRepeatRecord(2, Offset(0.1, 0.05)),
            AccuracyRepeatRecord(3, Offset(-0.02, -0.03)),
        ]
    )
    assert fig is not None
    assert len(fig.layout.shapes) == 1
    shape = fig.layout.shapes[0]
    assert shape.x0 == pytest.approx(-0.02)
    assert shape.x1 == pytest.approx(0.1)
    assert shape.y0 == pytest.approx(-0.03)
    assert shape.y1 == pytest.approx(0.05)

    texts = [a.text for a in fig.layout.annotations]
    assert "ΔX = 0.1200 mm" in texts
    assert "ΔY = 0.0800 mm" in texts


def test_write_figure_creates_results_dir(requires_plotly, tmp_path):
    results_dir = tmp_path / "eddy_seek_results"
    fig = write_accuracy_plot(
        repeats=[
            AccuracyRepeatRecord(1, Offset(0.0, 0.0)),
            AccuracyRepeatRecord(2, Offset(0.1, 0.0)),
        ],
    )
    assert fig is not None
    write_figure(results_dir, fig)
    assert results_dir.is_dir()
    assert any(results_dir.iterdir())


def test_run_accuracy_test_uses_repeated_seeks():
    host = MagicMock()
    host.printer = FakePrinter()
    host.seek_config = SeekConfig(save_plots=False)
    console = MagicMock()
    expected = MagicMock()

    with (
        patch(
            "eddy_seek.accuracy.test.run_repeated_seeks",
            return_value=expected,
        ) as repeated_mock,
        patch("eddy_seek.accuracy.test.finalize_repeat_seek"),
    ):
        repeated_mock.return_value = None
        run_accuracy_test(
            host,
            FakeGcmd(),
            console=console,
            repeats=2,
            mock_enabled=False,
        )
        repeated_mock.assert_called_once()

    with (
        patch(
            "eddy_seek.accuracy.test.run_repeated_seeks",
        ) as repeated_mock,
        patch("eddy_seek.accuracy.test.finalize_repeat_seek") as finalize_mock,
    ):
        from eddy_seek.common import Offset
        from eddy_seek.repeated_seek import RepeatedSeekResult

        repeated_mock.return_value = RepeatedSeekResult(
            offsets=(Offset(0.1, 0.0), Offset(0.2, 0.0)),
            durations_s=(1.0, 1.0),
            records=(),
            mean=Offset(0.15, 0.0),
        )
        run_accuracy_test(
            host,
            FakeGcmd(),
            console=console,
            repeats=2,
            mock_enabled=False,
        )
        finalize_mock.assert_called_once()


def test_run_accuracy_test_records_reference_relative_offsets_with_mock():
    true_center = Offset(0.05, -0.03)
    mocks = [Offset(0.2, 0.1), Offset(-0.15, 0.25)]
    seek_corrections = [true_center - mock for mock in mocks]

    host = MagicMock()
    host.printer = FakePrinter(toolhead=RecordingToolhead())
    host.seek_config = SeekConfig(save_plots=False)

    console = MagicMock()
    recorded: list[Offset] = []

    def capture_finalize(_host, _console, repeated, **kwargs):
        recorded.extend(repeated.offsets)

    seek_results = [ok_seek_result(offset=offset) for offset in seek_corrections]

    with (
        patch(
            "eddy_seek.accuracy.test._apply_mock_offset",
            side_effect=mocks,
        ),
        patch(
            "eddy_seek.accuracy.test.SeekSession",
            side_effect=lambda *args, **kwargs: MagicMock(
                run=MagicMock(return_value=seek_results.pop(0))
            ),
        ),
        patch(
            "eddy_seek.accuracy.test.finalize_repeat_seek",
            side_effect=capture_finalize,
        ),
    ):
        run_accuracy_test(
            host,
            FakeGcmd(),
            console=console,
            repeats=2,
            mock_enabled=True,
        )

    assert len(recorded) == 2
    assert recorded[0].x == pytest.approx(true_center.x)
    assert recorded[0].y == pytest.approx(true_center.y)
    assert recorded[1].x == pytest.approx(true_center.x)
    assert recorded[1].y == pytest.approx(true_center.y)


def test_parse_offset_cell():
    offset = parse_offset_cell("(+0.0123, -0.0045)")
    assert offset.x == pytest.approx(0.0123)
    assert offset.y == pytest.approx(-0.0045)


def test_parse_accuracy_json(tmp_path):
    payload = {
        "strategy": "sweep_centroid",
        "offsets": [[0.01, 0.02], [-0.02, 0.01], [0.0, -0.01]],
        "durations_s": [4.2, 3.8, 4.0],
    }
    path = tmp_path / "run.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    strategy, records, durations = parse_accuracy_json(path)
    assert strategy == "sweep_centroid"
    assert len(records) == 3
    assert records[0].offset == Offset(0.01, 0.02)
    assert durations == pytest.approx([4.2, 3.8, 4.0])


def test_parse_accuracy_html_round_trip(requires_plotly, plot_tmp):
    tmp_path, _, write_at = plot_tmp
    records = (
        AccuracyRepeatRecord(1, Offset(0.01, 0.02)),
        AccuracyRepeatRecord(2, Offset(-0.02, 0.01)),
        AccuracyRepeatRecord(3, Offset(0.0, -0.01)),
    )
    path = write_figure(
        tmp_path,
        write_accuracy_plot(repeats=list(records)),
        write_at=write_at,
        suffix="accuracy",
        run_label="accuracy",
    )
    assert path is not None
    parsed = parse_accuracy_html(path)
    assert len(parsed) == 3
    assert parsed[0].offset.x == pytest.approx(0.01)
    assert parsed[2].offset.y == pytest.approx(-0.01)


def test_load_accuracy_run_json_file(tmp_path):
    path = tmp_path / "run.json"
    path.write_text(
        json.dumps({"strategy": "centroid", "offsets": [[0.0, 0.0], [0.1, 0.0]]}),
        encoding="utf-8",
    )
    strategy, records, durations = load_accuracy_run(path)
    assert strategy == "centroid"
    assert len(records) == 2
    assert durations is None


def test_write_accuracy_comparison_plot(requires_plotly):
    runs: list[AccuracyRun] = [
        (
            "sweep_centroid",
            [
                AccuracyRepeatRecord(1, Offset(0.0, 0.0)),
                AccuracyRepeatRecord(2, Offset(0.05, 0.02)),
                AccuracyRepeatRecord(3, Offset(-0.01, -0.03)),
            ],
            [4.0, 4.2, 3.9],
        ),
        (
            "centroid",
            [
                AccuracyRepeatRecord(1, Offset(0.01, -0.01)),
                AccuracyRepeatRecord(2, Offset(0.02, 0.0)),
                AccuracyRepeatRecord(3, Offset(0.0, 0.02)),
            ],
            None,
        ),
    ]
    fig = write_accuracy_comparison_plot(runs=runs)
    assert fig is not None
    assert fig.layout.xaxis.range == fig.layout.xaxis2.range
    assert fig.layout.yaxis.range == fig.layout.yaxis2.range
    header = fig.layout.meta["eddy_header"]
    assert header["title"].startswith("EDDY_SEEK_ACCURACY comparison")
    assert len(header["tables"][0]["rows"]) == 2


def test_accuracy_compare_cli(requires_plotly, plot_tmp, tmp_path):
    from eddy_seek.accuracy.compare import main

    results_dir, _, write_at = plot_tmp
    records_a = (
        AccuracyRepeatRecord(1, Offset(0.0, 0.0)),
        AccuracyRepeatRecord(2, Offset(0.05, 0.02)),
    )
    records_b = (
        AccuracyRepeatRecord(1, Offset(0.01, -0.01)),
        AccuracyRepeatRecord(2, Offset(0.02, 0.0)),
    )
    path_a = write_figure(
        results_dir,
        write_accuracy_plot(repeats=list(records_a)),
        write_at=write_at,
        suffix="accuracy_sweep",
        run_label="accuracy",
    )
    path_b = write_figure(
        results_dir,
        write_accuracy_plot(repeats=list(records_b)),
        write_at=write_at,
        suffix="accuracy_centroid",
        run_label="accuracy",
    )
    assert path_a is not None and path_b is not None
    out = tmp_path / "compare.html"
    assert (
        main(
            [
                path_a,
                path_b,
                "--labels",
                "sweep_centroid",
                "centroid",
                "-o",
                str(out),
            ]
        )
        == 0
    )
    assert out.is_file()
    assert "EDDY_SEEK_ACCURACY comparison" in out.read_text(encoding="utf-8")
