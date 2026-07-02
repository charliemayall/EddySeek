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

from _eddy_seek.common import Axis, Phase, Position
from _eddy_seek.continuous_motion import MotionSample
from _eddy_seek.plotting import PlotWriter, TernaryStep, plot_filename
from _eddy_seek.plotting.ternary import TernaryPassRecord, write_ternary_session_plot
from _eddy_seek.config import SeekConfig
from _eddy_seek.strategy.centroid import CentroidStrategy


def test_plot_filename():
    when = datetime(2026, 7, 2, 14, 30)
    assert plot_filename("abcd1234-session", when) == "14_30_02_07_26_abcd1234.html"


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
