"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

import math
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fakes import PLOT_HTML_SUFFIX, PLOT_SESSION_ID, PLOT_WRITE_AT

from _eddy_seek.common import Axis, Offset, Phase, Position
from _eddy_seek.config import SeekConfig
from _eddy_seek.movement.handler import MotionSample
from _eddy_seek.optimizer import bin_frequencies
from _eddy_seek.plotting import accuracy as _accuracy_plot  # noqa: F401
from _eddy_seek.plotting import plot_filename, render_session_plot, write_figure
from _eddy_seek.plotting._plotly import THEME_COLORS, write_html
from _eddy_seek.plotting.debug_scan import DebugScanRecord, write_debug_scan_plot
from _eddy_seek.plotting.primitives import (
    AccuracyRepeatRecord,
    CircleBootstrapRecord,
    CircleHarmonicPassRecord,
    HeatmapRecord,
)
from _eddy_seek.plotting.recorder import SessionRecorder
from _eddy_seek.strategy import (  # noqa: F401
    centroid,
    circle_harmonic,
    debug_scan,
    sweep_centroid,
    ternary,
)
from _eddy_seek.strategy.centroid import CentroidStrategy, _record_centroid_pass
from _eddy_seek.strategy.sweep_centroid import _record_sweep_centroid_pass
from _eddy_seek.strategy.ternary import TernaryStep


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


def _write_strategy_plot(tmp_path, strategy_name: str, records, *, search_for="max"):
    fig = render_session_plot(strategy_name, records, search_for=search_for)
    assert fig is not None
    return write_figure(tmp_path, PLOT_SESSION_ID, fig, write_at=PLOT_WRITE_AT)


def test_accuracy_plot_writes_html(requires_plotly, plot_tmp):
    tmp_path, session_id, write_at = plot_tmp
    records = (
        AccuracyRepeatRecord(1, 0.01, 0.02, session_plot_path="/tmp/repeat1.html"),
        AccuracyRepeatRecord(2, -0.02, 0.01),
        AccuracyRepeatRecord(3, 0.0, -0.01),
    )
    path = write_figure(
        tmp_path,
        session_id,
        render_session_plot("accuracy", records, search_for="max"),
        write_at=write_at,
        suffix="accuracy",
    )
    assert path is not None
    assert os.path.isfile(path)
    assert path.endswith("14_30_02_07_26_abcd1234/accuracy.html")


def test_accuracy_plot_needs_two_repeats(requires_plotly, tmp_path):
    fig = render_session_plot(
        "accuracy",
        [AccuracyRepeatRecord(1, 0.0, 0.0)],
        search_for="max",
    )
    assert fig is None


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
    from _eddy_seek.plotting.accuracy import write_accuracy_plot

    fig = write_accuracy_plot(
        repeats=[
            AccuracyRepeatRecord(1, 0.0, 0.0),
            AccuracyRepeatRecord(2, 0.1, 0.05),
            AccuracyRepeatRecord(3, -0.02, -0.03),
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
    fig = render_session_plot(
        "accuracy",
        [
            AccuracyRepeatRecord(1, 0.0, 0.0),
            AccuracyRepeatRecord(2, 0.1, 0.0),
        ],
        search_for="max",
    )
    assert fig is not None
    write_figure(results_dir, "test-session-id", fig)
    assert results_dir.is_dir()
    assert any(results_dir.iterdir())


def test_centroid_plot_writes_session_html(requires_plotly, plot_tmp):
    probes = [
        (Offset(-1.0, -1.0), 100.0),
        (Offset(0.0, 0.0), 200.0),
        (Offset(1.0, 1.0), 100.0),
    ]
    tmp_path, session_id, write_at = plot_tmp
    recorder = SessionRecorder(trace=False, plots=True)
    ctx = SimpleNamespace(recorder=recorder)
    _record_centroid_pass(
        ctx, 1, Offset.zero(), Offset(0.1, 0.0), Offset(0.1, 0.0), probes
    )  # type: ignore[arg-type]
    _record_centroid_pass(
        ctx, 2, Offset(0.1, 0.0), Offset(0.0, 0.0), Offset(0.1, 0.0), probes
    )  # type: ignore[arg-type]
    path = write_figure(
        tmp_path,
        session_id,
        render_session_plot("centroid", recorder.records(), search_for="max"),
        write_at=write_at,
    )
    assert path is not None
    assert os.path.isfile(path)
    assert path.endswith(PLOT_HTML_SUFFIX)
    assert recorder.pass_count() == 2


def _sweep_centroid_records(
    samples, *, pass_num=1, phase=Phase.COARSE, box=(-1.0, 1.0, -1.0, 1.0)
):
    recorder = SessionRecorder(trace=False, plots=True)
    ctx = SimpleNamespace(recorder=recorder)
    _record_sweep_centroid_pass(
        ctx,  # type: ignore[arg-type]
        pass_num,
        phase,
        Offset.zero(),
        Offset(0.0, 0.0),
        Offset.zero(),
        samples,
        box,
    )
    return recorder.records()


def test_sweep_centroid_plot_writes_session_html(requires_plotly, plot_tmp):
    samples = [
        MotionSample(Offset(x, y), 10000.0 - 100.0 * (x * x + y * y), 0.0)
        for x in (-1.0, 0.0, 1.0)
        for y in (-1.0, 0.0, 1.0)
    ]
    tmp_path, session_id, write_at = plot_tmp
    recorder = SessionRecorder(trace=False, plots=True)
    ctx = SimpleNamespace(recorder=recorder)
    _record_sweep_centroid_pass(
        ctx,
        1,
        Phase.COARSE,
        Offset.zero(),
        Offset(0.1, 0.0),
        Offset(0.1, 0.0),
        samples,
        (-1.0, 1.0, -1.0, 1.0),  # type: ignore[arg-type]
    )
    _record_sweep_centroid_pass(
        ctx,
        2,
        Phase.FINE,
        Offset(0.1, 0.0),
        Offset(0.0, 0.0),
        Offset(0.1, 0.0),
        samples,
        (-0.5, 0.5, -0.5, 0.5),  # type: ignore[arg-type]
    )
    path = write_figure(
        tmp_path,
        session_id,
        render_session_plot("sweep_centroid", recorder.records(), search_for="max"),
        write_at=write_at,
    )
    assert path is not None
    assert os.path.isfile(path)
    assert path.endswith(PLOT_HTML_SUFFIX)
    assert recorder.pass_count() == 2


def test_sweep_centroid_plot_has_square_layout(requires_plotly):
    samples = [MotionSample(Offset(0.0, 0.0), 10000.0, 0.0)]
    fig = render_session_plot(
        "sweep_centroid",
        _sweep_centroid_records(samples),
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
    fig = render_session_plot(
        "sweep_centroid",
        _sweep_centroid_records(samples),
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
    passes_records: list = []
    for pass_num, phase, center, result, box in (
        (1, Phase.COARSE, Offset.zero(), Offset(0.12, 0.04), (-1.0, 1.0, -1.0, 1.0)),
        (
            2,
            Phase.FINE,
            Offset(0.12, 0.04),
            Offset(0.03, -0.02),
            (-0.5, 0.5, -0.5, 0.5),
        ),
        (
            3,
            Phase.FINE,
            Offset(0.03, -0.02),
            Offset(0.005, 0.001),
            (-0.2, 0.2, -0.2, 0.2),
        ),
    ):
        recorder = SessionRecorder(trace=False, plots=True)
        ctx = SimpleNamespace(recorder=recorder)
        _record_sweep_centroid_pass(
            ctx,
            pass_num,
            phase,
            center,
            result,
            Offset(0.12, 0.04),
            samples,
            box,  # type: ignore[arg-type]
        )
        passes_records.extend(recorder.records())
    fig = render_session_plot("sweep_centroid", passes_records, search_for="max")
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


def test_debug_scan_plot_writes_html(requires_plotly, plot_tmp):
    box = (-1.0, 1.0, -1.0, 1.0)
    samples = [MotionSample(Offset.zero(), 10000.0, 0.0)]
    z, x_centers, y_centers = bin_frequencies(
        samples, box, tolerance=0.5, center=Offset.zero(), search_for="max"
    )
    tmp_path, session_id, write_at = plot_tmp
    records = (
        HeatmapRecord(
            center=Offset.zero(),
            result=Offset(0.01, 0.02),
            lo=Position(box[0], box[2]),
            hi=Position(box[1], box[3]),
            z=tuple(tuple(row) for row in z),
            x_centers=tuple(x_centers),
            y_centers=tuple(y_centers),
            sample_xs=(0.0,),
            sample_ys=(0.0,),
            sample_freqs=(10000.0,),
        ),
    )
    path = write_figure(
        tmp_path,
        session_id,
        render_session_plot("debug_scan", records, search_for="max"),
        write_at=write_at,
    )
    assert path is not None
    assert os.path.isfile(path)


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


def test_ternary_plot_writes_session_html(requires_plotly, plot_tmp):
    from _eddy_seek.strategy.ternary import _record_ternary_pass

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
    tmp_path, session_id, write_at = plot_tmp
    recorder = SessionRecorder(trace=False, plots=True)
    ctx = SimpleNamespace(recorder=recorder)
    _record_ternary_pass(ctx, 1, Offset(0.1, 0.0), Offset(0.1, 0.0), steps, [], probes)  # type: ignore[arg-type]
    _record_ternary_pass(
        ctx, 2, Offset(0.0, 0.0), Offset(0.1, 0.0), steps, steps, probes
    )  # type: ignore[arg-type]
    path = write_figure(
        tmp_path,
        session_id,
        render_session_plot("ternary", recorder.records(), search_for="max"),
        write_at=write_at,
    )
    assert path is not None
    assert os.path.isfile(path)
    assert path.endswith(PLOT_HTML_SUFFIX)
    assert recorder.pass_count() == 2


def test_render_returns_none_without_plotly(tmp_path):
    with patch("_eddy_seek.strategy.centroid.plotly_available", return_value=False):
        recorder = SessionRecorder(trace=False, plots=True)
        ctx = SimpleNamespace(recorder=recorder)
        _record_centroid_pass(
            ctx,
            1,
            Offset.zero(),
            Offset.zero(),
            Offset.zero(),
            [(Offset.zero(), 100.0)],  # type: ignore[arg-type]
        )
        assert (
            render_session_plot("centroid", recorder.records(), search_for="max")
            is None
        )


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

    from _eddy_seek.strategy.ternary import _record_ternary_pass

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

    recorder = SessionRecorder(trace=False, plots=True)
    ctx = SimpleNamespace(recorder=recorder)
    _record_ternary_pass(
        ctx,
        1,
        Offset(0.1, 0.0),
        Offset(0.1, 0.0),
        _steps(Axis.X, 3),
        _steps(Axis.Y, 2),
        [],  # type: ignore[arg-type]
    )
    _record_ternary_pass(
        ctx,
        2,
        Offset(0.0, 0.0),
        Offset(0.1, 0.0),
        _steps(Axis.X, 2),
        _steps(Axis.Y, 3),
        [],  # type: ignore[arg-type]
    )
    fig = render_session_plot("ternary", recorder.records(), search_for="max")
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


def test_circle_harmonic_plot_writes_session_html(requires_plotly, plot_tmp):
    import math

    samples = [
        MotionSample(Offset(x, y), 10000.0 - 100.0 * (x * x + y * y), 0.0)
        for x in (-1.0, 0.0, 1.0)
        for y in (-1.0, 0.0, 1.0)
    ]
    circle_samples = [
        MotionSample(
            Offset(0.3 + math.cos(theta), 0.1 + math.sin(theta)),
            10000.0 + 50.0 * math.cos(theta),
            0.0,
        )
        for theta in [2.0 * math.pi * i / 36.0 for i in range(36)]
    ]
    binned = [
        (2.0 * math.pi * i / 36.0, 10000.0 + 50.0 * math.cos(2.0 * math.pi * i / 36.0))
        for i in range(36)
    ]
    records = (
        CircleBootstrapRecord(
            pass_num=1,
            center_x=0.0,
            center_y=0.0,
            result_x=0.3,
            result_y=0.1,
            moved_x=0.3,
            moved_y=0.1,
            sample_xs=tuple(s.offset.x for s in samples),
            sample_ys=tuple(s.offset.y for s in samples),
            sample_freqs=tuple(s.freq for s in samples),
            x_lo=-1.0,
            x_hi=1.0,
            y_lo=-1.0,
            y_hi=1.0,
        ),
        CircleHarmonicPassRecord(
            pass_num=2,
            trace_center_x=0.3,
            trace_center_y=0.1,
            radius=1.0,
            result_x=0.3,
            result_y=0.1,
            moved_x=0.0,
            moved_y=0.0,
            sample_xs=tuple(s.offset.x for s in circle_samples),
            sample_ys=tuple(s.offset.y for s in circle_samples),
            sample_freqs=tuple(s.freq for s in circle_samples),
            binned_thetas=tuple(theta for theta, _ in binned),
            binned_freqs=tuple(freq for _, freq in binned),
            fit_c0=10000.0,
            fit_a=50.0,
            fit_b=0.0,
            fit_amp=50.0,
            fit_noise=1.0,
            rejected=True,
            reject_reasons="snr (amp=50.00 < 2×noise=2.00)",
        ),
    )
    tmp_path, session_id, write_at = plot_tmp
    path = write_figure(
        tmp_path,
        session_id,
        render_session_plot("circle_harmonic", records, search_for="max"),
        write_at=write_at,
    )
    assert path is not None
    assert os.path.isfile(path)
    assert path.endswith(PLOT_HTML_SUFFIX)


def test_circle_harmonic_plot_has_wide_layout(requires_plotly):
    records = (
        CircleBootstrapRecord(
            pass_num=1,
            center_x=0.0,
            center_y=0.0,
            result_x=0.2,
            result_y=0.0,
            moved_x=0.2,
            moved_y=0.0,
            sample_xs=(0.0,),
            sample_ys=(0.0,),
            sample_freqs=(10000.0,),
            x_lo=-1.0,
            x_hi=1.0,
            y_lo=-1.0,
            y_hi=1.0,
        ),
        CircleHarmonicPassRecord(
            pass_num=2,
            trace_center_x=0.2,
            trace_center_y=0.0,
            radius=0.5,
            result_x=0.2,
            result_y=0.0,
            moved_x=0.0,
            moved_y=0.0,
            sample_xs=(0.2,),
            sample_ys=(0.5,),
            sample_freqs=(10050.0,),
            binned_thetas=(0.0, math.pi),
            binned_freqs=(10050.0, 9950.0),
            fit_c0=10000.0,
            fit_a=50.0,
            fit_b=0.0,
            fit_amp=50.0,
            fit_noise=1.0,
            rejected=False,
        ),
    )
    fig = render_session_plot("circle_harmonic", records, search_for="max")
    assert fig is not None
    assert fig.layout.meta["eddy_chart"] == "wide"
    header = fig.layout.meta["eddy_header"]
    assert "Circle harmonic" in header["title"]


def test_centroid_on_session_end_returns_plot_path(requires_plotly, tmp_path):
    strategy = CentroidStrategy()
    cfg = SeekConfig(save_plots=True, result_folder=str(tmp_path))
    recorder = SessionRecorder(trace=False, plots=True)
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
            "recorder": recorder,
        },
    )()
    _record_centroid_pass(
        ctx,
        1,
        Offset.zero(),
        Offset(0.1, 0.0),
        Offset(0.1, 0.0),
        [(Offset.zero(), 100.0)],  # type: ignore[arg-type]
    )
    path = strategy.on_session_end(ctx)  # type: ignore[arg-type]
    assert path is not None
    assert path.endswith("14_30_02_07_26_batch123/tools_t0_centroid.html")
    assert os.path.isfile(path)
    assert recorder.pass_count() == 1
