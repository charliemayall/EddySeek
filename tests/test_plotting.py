"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""

import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from _eddy_seek.common import Axis, Phase, Position
from _eddy_seek.continuous_motion import MotionSample
from _eddy_seek.plotting import (
    PlotWriter,
    TernaryPassRecord,
    TernaryStep,
    plot_filename,
    write_ternary_session_plot,
)
from _eddy_seek.plotting._plotly import square_xy_plot_layout, write_html
from _eddy_seek.plotting.one_shot import OneShotRecord, write_one_shot_plot
from _eddy_seek.plotting.sweep_centroid import (
    SweepCentroidPassRecord,
    write_sweep_centroid_session_plot,
)
from _eddy_seek.config import SeekConfig
from _eddy_seek.strategy.centroid import CentroidStrategy
from _eddy_seek.strategy.one_shot import bin_frequencies


def test_plot_filename():
    when = datetime(2026, 7, 2, 14, 30)
    assert plot_filename("abcd1234-session", when) == "14_30_02_07_26_abcd1234.html"
    assert (
        plot_filename("abcd1234-session", when, suffix="accuracy")
        == "14_30_02_07_26_abcd1234_accuracy.html"
    )


def test_plot_writer_writes_accuracy_session_html():
    try:
        __import__("plotly")
    except ImportError:
        return

    with tempfile.TemporaryDirectory() as tmp:
        when = datetime(2026, 7, 2, 14, 30)
        writer = PlotWriter(Path(tmp), "abcd1234", write_at=when)
        writer.record_accuracy_repeat(
            repeat_num=1,
            offset=Position(0.01, 0.02),
            session_plot_path="/tmp/repeat1.html",
        )
        writer.record_accuracy_repeat(
            repeat_num=2,
            offset=Position(-0.02, 0.01),
        )
        writer.record_accuracy_repeat(
            repeat_num=3,
            offset=Position(0.0, -0.01),
        )
        path = writer.finalize_accuracy()
        assert path is not None
        assert os.path.isfile(path)
        assert path.endswith("14_30_02_07_26_abcd1234_accuracy.html")
        assert writer.accuracy_repeat_count == 3


def test_plot_writer_finalize_accuracy_needs_two_repeats():
    with tempfile.TemporaryDirectory() as tmp:
        writer = PlotWriter(Path(tmp), "abcd1234")
        writer.record_accuracy_repeat(repeat_num=1, offset=Position.zero())
        assert writer.finalize_accuracy() is None


def test_compute_accuracy_stats():
    from _eddy_seek.session import compute_accuracy_stats

    stats = compute_accuracy_stats(
        [
            Position(0.0, 0.0),
            Position(0.1, 0.0),
            Position(0.0, 0.1),
        ]
    )
    assert stats.mean.x == pytest.approx(1 / 30)
    assert stats.mean.y == pytest.approx(1 / 30)
    assert stats.max_pair == pytest.approx((0.1**2 + 0.1**2) ** 0.5)
    assert len(stats.radial) == 3


def test_accuracy_plot_draws_spread_box():
    try:
        __import__("plotly")
    except ImportError:
        return

    from _eddy_seek.plotting.accuracy import AccuracyRepeatRecord, write_accuracy_plot

    fig = write_accuracy_plot(
        repeats=[
            AccuracyRepeatRecord(1, Position(0.0, 0.0)),
            AccuracyRepeatRecord(2, Position(0.1, 0.05)),
            AccuracyRepeatRecord(3, Position(-0.02, -0.03)),
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


def test_plot_writer_creates_results_dir():
    with tempfile.TemporaryDirectory() as tmp:
        results_dir = Path(tmp) / "eddy_seek_results"
        PlotWriter(results_dir, "test-session-id")
        assert results_dir.is_dir()
        assert not any(results_dir.iterdir())


def test_plot_writer_writes_centroid_session_html():
    try:
        __import__("plotly")
    except ImportError:
        return

    probes = [
        (Position(-1.0, -1.0), 100.0),
        (Position(0.0, 0.0), 200.0),
        (Position(1.0, 1.0), 100.0),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        when = datetime(2026, 7, 2, 14, 30)
        writer = PlotWriter(Path(tmp), "abcd1234", write_at=when)
        writer.record_centroid_pass(
            pass_num=1,
            center=Position.zero(),
            result=Position(0.1, 0.0),
            moved=Position(0.1, 0.0),
            probes=probes,
        )
        writer.record_centroid_pass(
            pass_num=2,
            center=Position(0.1, 0.0),
            result=Position(0.0, 0.0),
            moved=Position(0.1, 0.0),
            probes=probes,
        )
        path = writer.finalize_centroid(search_for="max")
        assert path is not None
        assert os.path.isfile(path)
        assert path.endswith("14_30_02_07_26_abcd1234.html")
        assert writer.centroid_pass_count == 2


def test_plot_writer_writes_sweep_centroid_session_html():
    try:
        __import__("plotly")
    except ImportError:
        return

    samples = [
        MotionSample(Position(x, y), 10000.0 - 100.0 * (x * x + y * y), 0.0)
        for x in (-1.0, 0.0, 1.0)
        for y in (-1.0, 0.0, 1.0)
    ]
    with tempfile.TemporaryDirectory() as tmp:
        when = datetime(2026, 7, 2, 14, 30)
        writer = PlotWriter(Path(tmp), "abcd1234", write_at=when)
        writer.record_sweep_centroid_pass(
            pass_num=1,
            phase=Phase.COARSE,
            center=Position.zero(),
            result=Position(0.1, 0.0),
            moved=Position(0.1, 0.0),
            samples=samples,
            box=(-1.0, 1.0, -1.0, 1.0),
        )
        writer.record_sweep_centroid_pass(
            pass_num=2,
            phase=Phase.FINE,
            center=Position(0.1, 0.0),
            result=Position(0.0, 0.0),
            moved=Position(0.1, 0.0),
            samples=samples,
            box=(-0.5, 0.5, -0.5, 0.5),
        )
        path = writer.finalize_sweep_centroid(search_for="max")
        assert path is not None
        assert os.path.isfile(path)
        assert path.endswith("14_30_02_07_26_abcd1234.html")
        assert writer.sweep_centroid_pass_count == 2


def test_sweep_centroid_plot_has_square_layout():
    try:
        __import__("plotly")
    except ImportError:
        return

    samples = [
        MotionSample(Position(0.0, 0.0), 10000.0, 0.0),
    ]
    fig = write_sweep_centroid_session_plot(
        passes=[
            SweepCentroidPassRecord(
                pass_num=1,
                phase=Phase.COARSE,
                center=Position.zero(),
                result=Position(0.0, 0.0),
                moved=Position.zero(),
                samples=samples,
                box=(-1.0, 1.0, -1.0, 1.0),
            )
        ],
        search_for="max",
    )
    assert fig is not None
    layout = square_xy_plot_layout(stats_lines=3)  # strategy + 1 pass + final
    assert fig.layout.title.text in (None, "")
    assert len(fig.layout.annotations) >= 1
    assert fig.layout.autosize is False
    assert fig.layout.width == layout["width"]
    assert fig.layout.height == layout["height"]
    assert fig.layout.yaxis.scaleanchor == "x"
    assert fig.layout.yaxis.scaleratio == 1
    plot_w = fig.layout.width - layout["margin"]["l"] - layout["margin"]["r"]
    plot_h = fig.layout.height - layout["margin"]["t"] - layout["margin"]["b"]
    assert plot_w == plot_h
    assert fig.layout.legend.y < 0


def test_save_preview_plot():
    """Write a sweep-centroid HTML plot under tests/output/ for manual layout checks.

    Run: EDDY_SEEK_PREVIEW_PLOTS=1 pytest tests/test_plotting.py::test_save_preview_plot -s
    """
    if not os.environ.get("EDDY_SEEK_PREVIEW_PLOTS"):
        pytest.skip("set EDDY_SEEK_PREVIEW_PLOTS=1 to write preview HTML")
    try:
        __import__("plotly")
    except ImportError:
        pytest.skip("plotly not installed")

    samples = [
        MotionSample(
            Position(x, y),
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
            center=Position.zero(),
            result=Position(0.12, 0.04),
            moved=Position(0.12, 0.04),
            samples=samples,
            box=(-1.0, 1.0, -1.0, 1.0),
        ),
        SweepCentroidPassRecord(
            pass_num=2,
            phase=Phase.FINE,
            center=Position(0.12, 0.04),
            result=Position(0.03, -0.02),
            moved=Position(0.12, 0.04),
            samples=samples,
            box=(-0.5, 0.5, -0.5, 0.5),
        ),
        SweepCentroidPassRecord(
            pass_num=3,
            phase=Phase.FINE,
            center=Position(0.03, -0.02),
            result=Position(0.005, 0.001),
            moved=Position(0.12, 0.04),
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
    print(f"preview plot: {path}")  # noqa: T201


def test_one_shot_plot_returns_figure():
    try:
        __import__("plotly")
    except ImportError:
        return

    box = (-1.0, 1.0, -1.0, 1.0)
    samples = [
        MotionSample(
            Position(x, y),
            10000.0 - 100.0 * (x * x + y * y),
            0.0,
        )
        for x in (-0.5, 0.0, 0.5)
        for y in (-0.5, 0.0, 0.5)
    ]
    z, x_centers, y_centers = bin_frequencies(
        samples, box, tolerance=0.5, center=Position.zero(), search_for="max"
    )
    record = OneShotRecord(
        center=Position.zero(),
        result=Position(0.05, -0.02),
        samples=samples,
        box=box,
        z=z,
        x_centers=x_centers,
        y_centers=y_centers,
    )
    fig = write_one_shot_plot(record=record, search_for="max")
    assert fig is not None
    heatmaps = [trace for trace in fig.data if trace.type == "heatmap"]
    assert len(heatmaps) == 4
    base = heatmaps[0]
    assert len(base.x) == len(record.x_centers) + 1
    assert any(
        base.x[index] <= 0.0 <= base.x[index + 1] for index in range(len(base.x) - 1)
    )
    assert len(fig.layout.annotations) >= 1
    stats = fig.layout.annotations[0].text
    assert "@2×" in stats
    assert "@4×" in stats
    assert "@8×" in stats


def test_plot_writer_writes_one_shot_html():
    try:
        __import__("plotly")
    except ImportError:
        return

    box = (-1.0, 1.0, -1.0, 1.0)
    samples = [MotionSample(Position.zero(), 10000.0, 0.0)]
    z, x_centers, y_centers = bin_frequencies(
        samples, box, tolerance=0.5, center=Position.zero(), search_for="max"
    )
    with tempfile.TemporaryDirectory() as tmp:
        when = datetime(2026, 7, 2, 14, 30)
        writer = PlotWriter(Path(tmp), "abcd1234", write_at=when)
        writer.record_one_shot(
            center=Position.zero(),
            result=Position(0.01, 0.02),
            samples=samples,
            box=box,
            z=z,
            x_centers=x_centers,
            y_centers=y_centers,
        )
        path = writer.finalize_one_shot(search_for="max")
        assert path is not None
        assert os.path.isfile(path)
        assert writer.one_shot_count == 1


def test_save_preview_one_shot_plot():
    """Write a one-shot heatmap under tests/output/ for manual layout checks.

    Run: EDDY_SEEK_PREVIEW_PLOTS=1 pytest tests/test_plotting.py::test_save_preview_one_shot_plot -s
    """
    if not os.environ.get("EDDY_SEEK_PREVIEW_PLOTS"):
        pytest.skip("set EDDY_SEEK_PREVIEW_PLOTS=1 to write preview HTML")
    try:
        __import__("plotly")
    except ImportError:
        pytest.skip("plotly not installed")

    box = (-1.0, 1.0, -1.0, 1.0)
    tolerance = 0.2
    samples = [
        MotionSample(
            Position(x, y),
            10000.0 - 500.0 * (x * x + y * y),
            0.0,
        )
        for x in (-1.0, -0.5, 0.0, 0.5, 1.0)
        for y in (-1.0, -0.5, 0.0, 0.5, 1.0)
    ]
    z, x_centers, y_centers = bin_frequencies(
        samples, box, tolerance, center=Position.zero(), search_for="max"
    )
    record = OneShotRecord(
        center=Position.zero(),
        result=Position(0.0, 0.0),
        samples=samples,
        box=box,
        z=z,
        x_centers=x_centers,
        y_centers=y_centers,
    )
    fig = write_one_shot_plot(record=record, search_for="max")
    assert fig is not None

    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / "preview_one_shot.html"
    assert write_html(path, fig)
    assert path.is_file()
    print(f"preview plot: {path}")  # noqa: T201


def test_plot_writer_writes_ternary_session_html():
    try:
        __import__("plotly")
    except ImportError:
        return

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
    probes = [(Position(-0.33, 0.0), 90.0), (Position(0.33, 0.0), 80.0)]
    with tempfile.TemporaryDirectory() as tmp:
        when = datetime(2026, 7, 2, 14, 30)
        writer = PlotWriter(Path(tmp), "abcd1234", write_at=when)
        writer.record_ternary_pass(
            pass_num=1,
            result=Position(0.1, 0.0),
            moved=Position(0.1, 0.0),
            x_steps=steps,
            y_steps=[],
            probes=probes,
        )
        writer.record_ternary_pass(
            pass_num=2,
            result=Position(0.0, 0.0),
            moved=Position(0.1, 0.0),
            x_steps=steps,
            y_steps=steps,
            probes=probes,
        )
        path = writer.finalize_ternary(search_for="max")
        assert path is not None
        assert os.path.isfile(path)
        assert path.endswith("14_30_02_07_26_abcd1234.html")
        assert writer.ternary_pass_count == 2


def test_plot_writer_write_returns_none_without_plotly():
    with patch("_eddy_seek.plotting._plotly.plotly_available", return_value=False):
        with tempfile.TemporaryDirectory() as tmp:
            writer = PlotWriter(Path(tmp), "abcd1234")
            writer.record_centroid_pass(
                pass_num=1,
                center=Position.zero(),
                result=Position.zero(),
                moved=Position.zero(),
                probes=[(Position.zero(), 100.0)],
            )
            assert writer.finalize_centroid(search_for="max") is None


def test_ternary_plot_pass_bands_do_not_overlap():
    try:
        __import__("plotly")
    except ImportError:
        return

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
            result=Position(0.1, 0.0),
            moved=Position(0.1, 0.0),
            x_steps=_steps(Axis.X, 3),
            y_steps=_steps(Axis.Y, 2),
            probes=[],
        ),
        TernaryPassRecord(
            pass_num=2,
            result=Position(0.0, 0.0),
            moved=Position(0.1, 0.0),
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


def test_centroid_on_session_end_returns_plot_path():
    try:
        __import__("plotly")
    except ImportError:
        return

    with tempfile.TemporaryDirectory() as tmp:
        strategy = CentroidStrategy()
        cfg = SeekConfig(save_plots=True, result_folder=tmp)
        ctx = type(
            "Ctx",
            (),
            {"config": cfg, "session_id": "abcd1234-session"},
        )()
        strategy._plotter = PlotWriter(
            Path(tmp), ctx.session_id, write_at=datetime(2026, 7, 2, 14, 30)
        )
        strategy._plotter.record_centroid_pass(
            pass_num=1,
            center=Position.zero(),
            result=Position(0.1, 0.0),
            moved=Position(0.1, 0.0),
            probes=[(Position.zero(), 100.0)],
        )
        path = strategy.on_session_end(ctx)  # type: ignore[arg-type]
        assert path is not None
        assert path.endswith("14_30_02_07_26_abcd1234.html")
        assert os.path.isfile(path)
        assert strategy._last_plot_passes == 1
