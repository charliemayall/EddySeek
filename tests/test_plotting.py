"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fakes import PLOT_HTML_SUFFIX, PLOT_SESSION_ID, PLOT_WRITE_AT

from _eddy_seek.common import Axis, Offset, Phase
from _eddy_seek.config import SeekConfig
from _eddy_seek.motion_handler import MotionSample
from _eddy_seek.optimizer import bin_frequencies
from _eddy_seek.plotting import (
    PlotWriter,
    TernaryPassRecord,
    TernaryStep,
    plot_filename,
    write_ternary_session_plot,
)
from _eddy_seek.plotting._plotly import THEME_COLORS, write_html
from _eddy_seek.plotting.debug_scan import DebugScanRecord, write_debug_scan_plot
from _eddy_seek.plotting.sweep_centroid import (
    SweepCentroidPassRecord,
    write_sweep_centroid_session_plot,
)
from _eddy_seek.strategy.centroid import CentroidStrategy


def test_plot_filename():
    when = PLOT_WRITE_AT
    assert (
        plot_filename("abcd1234-session", when)
        == "14_30_02_07_26_abcd1234/session.html"
    )
    assert (
        plot_filename("abcd1234-session", when, suffix="accuracy")
        == "14_30_02_07_26_abcd1234/accuracy.html"
    )
    assert (
        plot_filename("abcd1234-session", when, suffix="start_ternary")
        == "14_30_02_07_26_abcd1234/start_ternary.html"
    )
    assert (
        plot_filename(
            "full-session-id",
            when,
            suffix="tools_t0_ternary",
            run_id="batch999",
        )
        == "14_30_02_07_26_batch999/tools_t0_ternary.html"
    )


def test_plot_writer_writes_accuracy_session_html(requires_plotly, plot_tmp):
    writer, _tmp_path = plot_tmp
    writer.record_accuracy_repeat(
        repeat_num=1,
        offset=Offset(0.01, 0.02),
        session_plot_path="/tmp/repeat1.html",
    )
    writer.record_accuracy_repeat(
        repeat_num=2,
        offset=Offset(-0.02, 0.01),
    )
    writer.record_accuracy_repeat(
        repeat_num=3,
        offset=Offset(0.0, -0.01),
    )
    path = writer.finalize_accuracy()
    assert path is not None
    assert os.path.isfile(path)
    assert path.endswith("14_30_02_07_26_abcd1234/accuracy.html")
    assert writer.accuracy_repeat_count == 3


def test_plot_writer_finalize_accuracy_needs_two_repeats(tmp_path):
    writer = PlotWriter(tmp_path, PLOT_SESSION_ID)
    writer.record_accuracy_repeat(repeat_num=1, offset=Offset.zero())
    assert writer.finalize_accuracy() is None


def test_compute_accuracy_stats():
    from _eddy_seek.session import compute_accuracy_stats

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
    from _eddy_seek.plotting.accuracy import AccuracyRepeatRecord, write_accuracy_plot

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


def test_plot_writer_creates_results_dir(tmp_path):
    results_dir = tmp_path / "eddy_seek_results"
    PlotWriter(results_dir, "test-session-id")
    assert results_dir.is_dir()
    assert not any(results_dir.iterdir())


def test_plot_writer_writes_centroid_session_html(requires_plotly, plot_tmp):
    probes = [
        (Offset(-1.0, -1.0), 100.0),
        (Offset(0.0, 0.0), 200.0),
        (Offset(1.0, 1.0), 100.0),
    ]
    writer, _tmp_path = plot_tmp
    writer.record_centroid_pass(
        pass_num=1,
        center=Offset.zero(),
        result=Offset(0.1, 0.0),
        moved=Offset(0.1, 0.0),
        probes=probes,
    )
    writer.record_centroid_pass(
        pass_num=2,
        center=Offset(0.1, 0.0),
        result=Offset(0.0, 0.0),
        moved=Offset(0.1, 0.0),
        probes=probes,
    )
    path = writer.finalize_centroid(search_for="max")
    assert path is not None
    assert os.path.isfile(path)
    assert path.endswith(PLOT_HTML_SUFFIX)
    assert writer.centroid_pass_count == 2


def test_plot_writer_writes_sweep_centroid_session_html(requires_plotly, plot_tmp):
    samples = [
        MotionSample(Offset(x, y), 10000.0 - 100.0 * (x * x + y * y), 0.0)
        for x in (-1.0, 0.0, 1.0)
        for y in (-1.0, 0.0, 1.0)
    ]
    writer, _tmp_path = plot_tmp
    writer.record_sweep_centroid_pass(
        pass_num=1,
        phase=Phase.COARSE,
        center=Offset.zero(),
        result=Offset(0.1, 0.0),
        moved=Offset(0.1, 0.0),
        samples=samples,
        box=(-1.0, 1.0, -1.0, 1.0),
    )
    writer.record_sweep_centroid_pass(
        pass_num=2,
        phase=Phase.FINE,
        center=Offset(0.1, 0.0),
        result=Offset(0.0, 0.0),
        moved=Offset(0.1, 0.0),
        samples=samples,
        box=(-0.5, 0.5, -0.5, 0.5),
    )
    path = writer.finalize_sweep_centroid(search_for="max")
    assert path is not None
    assert os.path.isfile(path)
    assert path.endswith(PLOT_HTML_SUFFIX)
    assert writer.sweep_centroid_pass_count == 2


def test_sweep_centroid_plot_has_square_layout(requires_plotly):
    samples = [
        MotionSample(Offset(0.0, 0.0), 10000.0, 0.0),
    ]
    fig = write_sweep_centroid_session_plot(
        passes=[
            SweepCentroidPassRecord(
                pass_num=1,
                phase=Phase.COARSE,
                center=Offset.zero(),
                result=Offset(0.0, 0.0),
                moved=Offset.zero(),
                samples=samples,
                box=(-1.0, 1.0, -1.0, 1.0),
            )
        ],
        search_for="max",
    )
    assert fig is not None
    assert fig.layout.title.text in (None, "")
    assert fig.layout.autosize is True
    assert fig.layout.yaxis.scaleanchor == "x"
    assert fig.layout.yaxis.scaleratio == 1
    assert fig.layout.legend.y < 0
    assert fig.layout.paper_bgcolor == THEME_COLORS.background
    header = fig.layout.meta["eddy_header"]
    assert "Sweep centroid" in header["title"]
    assert header["tables"][0]["rows"]
    assert header["tables"][0]["rows"][0]["pass"] == "1"
    assert "Final:" in header["final"]
    assert fig.layout.meta["eddy_chart"] == "square"


def test_write_html_includes_flex_shell(requires_plotly, tmp_path):
    samples = [MotionSample(Offset(0.0, 0.0), 10000.0, 0.0)]
    fig = write_sweep_centroid_session_plot(
        passes=[
            SweepCentroidPassRecord(
                pass_num=1,
                phase=Phase.COARSE,
                center=Offset.zero(),
                result=Offset(0.0, 0.0),
                moved=Offset.zero(),
                samples=samples,
                box=(-1.0, 1.0, -1.0, 1.0),
            )
        ],
        search_for="max",
    )
    assert fig is not None
    path = tmp_path / "shell.html"
    assert write_html(path, fig)
    html = path.read_text(encoding="utf-8")
    assert 'class="page"' in html
    assert "#1e293b" in html
    assert "chart-inner--square" in html
    assert "Sweep centroid" in html
    assert "stats-table" in html
    assert "responsive" in html


def test_save_preview_plot(requires_plotly):
    """Write a sweep-centroid HTML plot under tests/output/ for manual layout checks.

    Run: EDDY_SEEK_PREVIEW_PLOTS=1 pytest tests/test_plotting.py::test_save_preview_plot -s
    """
    if not os.environ.get("EDDY_SEEK_PREVIEW_PLOTS"):
        pytest.skip("set EDDY_SEEK_PREVIEW_PLOTS=1 to write preview HTML")

    samples = [
        MotionSample(
            Offset(x, y),
            10000.0 - 100.0 * (x * x + y * y),
            0.0,
        )
        for x in (-1.0, -0.5, 0.0, 0.5, 1.0)
        for y in (-1.0, -0.5, 0.0, 0.5, 1.0)
    ]
    passes = [
        SweepCentroidPassRecord(
            pass_num=1,
            phase=Phase.COARSE,
            center=Offset.zero(),
            result=Offset(0.12, 0.04),
            moved=Offset(0.12, 0.04),
            samples=samples,
            box=(-1.0, 1.0, -1.0, 1.0),
        ),
        SweepCentroidPassRecord(
            pass_num=2,
            phase=Phase.FINE,
            center=Offset(0.12, 0.04),
            result=Offset(0.03, -0.02),
            moved=Offset(0.12, 0.04),
            samples=samples,
            box=(-0.5, 0.5, -0.5, 0.5),
        ),
        SweepCentroidPassRecord(
            pass_num=3,
            phase=Phase.FINE,
            center=Offset(0.03, -0.02),
            result=Offset(0.005, 0.001),
            moved=Offset(0.12, 0.04),
            samples=samples,
            box=(-0.2, 0.2, -0.2, 0.2),
        ),
    ]
    fig = write_sweep_centroid_session_plot(passes=passes, search_for="max")
    assert fig is not None

    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / "preview_sweep_centroid.html"
    assert write_html(path, fig)
    assert path.is_file()
    print(f"preview plot: {path}")


def test_debug_scan_plot_returns_figure(requires_plotly):
    box = (-1.0, 1.0, -1.0, 1.0)
    samples = [
        MotionSample(
            Offset(x, y),
            10000.0 - 100.0 * (x * x + y * y),
            0.0,
        )
        for x in (-0.5, 0.0, 0.5)
        for y in (-0.5, 0.0, 0.5)
    ]
    z, x_centers, y_centers = bin_frequencies(
        samples, box, tolerance=0.5, center=Offset.zero(), search_for="max"
    )
    record = DebugScanRecord(
        center=Offset.zero(),
        result=Offset(0.05, -0.02),
        samples=samples,
        box=box,
        z=z,
        x_centers=x_centers,
        y_centers=y_centers,
    )
    fig = write_debug_scan_plot(record=record, search_for="max")
    assert fig is not None
    heatmaps = [trace for trace in fig.data if trace.type == "heatmap"]
    assert len(heatmaps) == 5
    base = heatmaps[0]
    assert len(base.x) == len(record.x_centers) + 1
    assert any(
        base.x[index] <= 0.0 <= base.x[index + 1] for index in range(len(base.x) - 1)
    )
    header = fig.layout.meta["eddy_header"]
    scale_table = header["tables"][0]
    assert scale_table["rows"][1]["scale"] == "@2×"
    assert scale_table["rows"][2]["scale"] == "@4×"
    assert scale_table["rows"][3]["scale"] == "@8×"
    summary = {row["metric"]: row["value"] for row in header["tables"][1]["rows"]}
    assert summary["centroid"] != "n/a"
    assert "prominence" in summary
    assert "FWHM X" in summary
    assert "FWHM Y" in summary
    assert fig.layout.meta["eddy_chart"] == "wide"
    marginals = [
        trace
        for trace in fig.data
        if trace.type == "scatter" and trace.mode == "lines+markers"
    ]
    assert len(marginals) == 2


def test_plot_writer_writes_debug_scan_html(requires_plotly, plot_tmp):
    box = (-1.0, 1.0, -1.0, 1.0)
    samples = [MotionSample(Offset.zero(), 10000.0, 0.0)]
    z, x_centers, y_centers = bin_frequencies(
        samples, box, tolerance=0.5, center=Offset.zero(), search_for="max"
    )
    writer, _tmp_path = plot_tmp
    writer.record_debug_scan(
        center=Offset.zero(),
        result=Offset(0.01, 0.02),
        samples=samples,
        box=box,
        z=z,
        x_centers=x_centers,
        y_centers=y_centers,
    )
    path = writer.finalize_debug_scan(search_for="max")
    assert path is not None
    assert os.path.isfile(path)
    assert writer.debug_scan_count == 1


def test_save_preview_debug_scan_plot(requires_plotly):
    """Write a debug-scan heatmap under tests/output/ for manual layout checks.

    Run: EDDY_SEEK_PREVIEW_PLOTS=1 pytest tests/test_plotting.py::test_save_preview_debug_scan_plot -s
    """
    if not os.environ.get("EDDY_SEEK_PREVIEW_PLOTS"):
        pytest.skip("set EDDY_SEEK_PREVIEW_PLOTS=1 to write preview HTML")

    box = (-1.0, 1.0, -1.0, 1.0)
    tolerance = 0.2
    samples = [
        MotionSample(
            Offset(x, y),
            10000.0 - 500.0 * (x * x + y * y),
            0.0,
        )
        for x in (-1.0, -0.5, 0.0, 0.5, 1.0)
        for y in (-1.0, -0.5, 0.0, 0.5, 1.0)
    ]
    z, x_centers, y_centers = bin_frequencies(
        samples, box, tolerance, center=Offset.zero(), search_for="max"
    )
    record = DebugScanRecord(
        center=Offset.zero(),
        result=Offset(0.0, 0.0),
        samples=samples,
        box=box,
        z=z,
        x_centers=x_centers,
        y_centers=y_centers,
    )
    fig = write_debug_scan_plot(record=record, search_for="max")
    assert fig is not None

    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / "preview_debug_scan.html"
    assert write_html(path, fig)
    assert path.is_file()
    print(f"preview plot: {path}")


def test_plot_writer_writes_ternary_session_html(requires_plotly, plot_tmp):
    steps = [
        TernaryStep(
            axis=Axis.X,
            iteration=0,
            lo=-1.0,
            hi=1.0,
            m1=-0.33,
            m2=0.33,
            f1=90.0,
            f2=80.0,
        )
    ]
    probes = [(Offset(-0.33, 0.0), 90.0), (Offset(0.33, 0.0), 80.0)]
    writer, _tmp_path = plot_tmp
    writer.record_ternary_pass(
        pass_num=1,
        result=Offset(0.1, 0.0),
        moved=Offset(0.1, 0.0),
        x_steps=steps,
        y_steps=[],
        probes=probes,
    )
    writer.record_ternary_pass(
        pass_num=2,
        result=Offset(0.0, 0.0),
        moved=Offset(0.1, 0.0),
        x_steps=steps,
        y_steps=steps,
        probes=probes,
    )
    path = writer.finalize_ternary(search_for="max")
    assert path is not None
    assert os.path.isfile(path)
    assert path.endswith(PLOT_HTML_SUFFIX)
    assert writer.ternary_pass_count == 2


def test_plot_writer_write_returns_none_without_plotly(tmp_path):
    with patch("_eddy_seek.plotting._plotly.plotly_available", return_value=False):
        writer = PlotWriter(tmp_path, PLOT_SESSION_ID)
        writer.record_centroid_pass(
            pass_num=1,
            center=Offset.zero(),
            result=Offset.zero(),
            moved=Offset.zero(),
            probes=[(Offset.zero(), 100.0)],
        )
        assert writer.finalize_centroid(search_for="max") is None


def test_ternary_plot_pass_bands_do_not_overlap(requires_plotly):
    def _steps(axis: Axis, count: int) -> list[TernaryStep]:
        return [
            TernaryStep(
                axis=axis,
                iteration=i,
                lo=-1.0,
                hi=1.0,
                m1=-0.33,
                m2=0.33,
                f1=90.0,
                f2=80.0,
            )
            for i in range(count)
        ]

    passes = [
        TernaryPassRecord(
            pass_num=1,
            result=Offset(0.1, 0.0),
            moved=Offset(0.1, 0.0),
            x_steps=_steps(Axis.X, 3),
            y_steps=_steps(Axis.Y, 2),
            probes=[],
        ),
        TernaryPassRecord(
            pass_num=2,
            result=Offset(0.0, 0.0),
            moved=Offset(0.1, 0.0),
            x_steps=_steps(Axis.X, 2),
            y_steps=_steps(Axis.Y, 3),
            probes=[],
        ),
    ]
    fig = write_ternary_session_plot(passes=passes, search_for="max")
    assert fig is not None

    x_bracket_ranges: list[tuple[float, float]] = []
    for trace in fig.data:
        ys = trace.y
        if ys is None or len(ys) != 5:
            continue
        if getattr(trace, "yaxis", "y") != "y2":
            continue
        x_bracket_ranges.append((min(ys), max(ys)))

    assert len(x_bracket_ranges) == 5  # 3 pass-1 + 2 pass-2 x brackets
    pass1_max = max(end for _, end in x_bracket_ranges[:3])
    pass2_min = min(start for start, _ in x_bracket_ranges[3:])
    assert pass2_min > pass1_max


def test_centroid_on_session_end_returns_plot_path(requires_plotly, tmp_path):
    strategy = CentroidStrategy()
    cfg = SeekConfig(save_plots=True, result_folder=str(tmp_path))
    ctx = type(
        "Ctx",
        (),
        {
            "config": cfg,
            "session_id": "abcd1234-session",
            "run_id": "batch123",
            "artifact_label": "tools_t0",
            "artifact_write_at": PLOT_WRITE_AT,
            "artifact_suffix": lambda _self, name: f"tools_t0_{name}",
        },
    )()
    strategy._plotter = PlotWriter(
        Path(tmp_path),
        ctx.session_id,
        write_at=ctx.artifact_write_at,
        suffix=ctx.artifact_suffix(strategy.name),
        run_id=ctx.run_id,
    )
    strategy._plotter.record_centroid_pass(
        pass_num=1,
        center=Offset.zero(),
        result=Offset(0.1, 0.0),
        moved=Offset(0.1, 0.0),
        probes=[(Offset.zero(), 100.0)],
    )
    path = strategy.on_session_end(ctx)  # type: ignore[arg-type]
    assert path is not None
    assert path.endswith("14_30_02_07_26_batch123/tools_t0_centroid.html")
    assert os.path.isfile(path)
    assert strategy._last_plot_passes == 1
