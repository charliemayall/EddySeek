"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fakes import PLOT_HTML_SUFFIX, PLOT_RUN_DIR, PLOT_WRITE_AT

from _eddy_seek.common import Offset, Phase, session_artifact_filename
from _eddy_seek.config import SeekConfig
from _eddy_seek.movement.handler import MotionSample
from _eddy_seek.optimizer import bin_frequencies
from _eddy_seek.plotting import (
    render_session_plot,
    write_figure,
)
from _eddy_seek.plotting._plotly import THEME_COLORS, write_html
from _eddy_seek.plotting.artifacts import finalize_strategy_plot
from _eddy_seek.plotting.debug_scan import render_debug_scan_figure
from _eddy_seek.plotting.primitives import (
    Bounds,
    HeatmapRecord,
    PassMove,
    XYCloud,
)
from _eddy_seek.plotting.recorder import SessionRecorder
from _eddy_seek.strategy.centroid import CentroidStrategy, _record_centroid_pass
from _eddy_seek.strategy.sweep_centroid import _record_sweep_centroid_pass


def test_plot_filename():
    when = PLOT_WRITE_AT
    assert session_artifact_filename(when, ext="html") == f"{PLOT_RUN_DIR}/session.html"
    assert (
        session_artifact_filename(
            when, suffix="accuracy", run_label="accuracy", ext="html"
        )
        == "2026-07-02_14-30-00_accuracy/accuracy.html"
    )
    assert (
        session_artifact_filename(
            when, suffix="start_sweep_centroid", run_label="start", ext="html"
        )
        == "2026-07-02_14-30-00_start/start_sweep_centroid.html"
    )
    assert (
        session_artifact_filename(
            when,
            suffix="tools_t0_centroid",
            run_label="tools",
            ext="html",
        )
        == "2026-07-02_14-30-00_tools/tools_t0_centroid.html"
    )


def test_centroid_plot_writes_session_html(requires_plotly, plot_tmp):
    probes = [
        (Offset(-1.0, -1.0), 100.0),
        (Offset(0.0, 0.0), 200.0),
        (Offset(1.0, 1.0), 100.0),
    ]
    tmp_path, _, write_at = plot_tmp
    recorder = SessionRecorder(trace=False, plots=True)
    ctx = SimpleNamespace(recorder=recorder)
    _record_centroid_pass(
        ctx,  # ty: ignore[invalid-argument-type]
        1,
        Offset.zero(),
        Offset(0.1, 0.0),
        Offset(0.1, 0.0),
        probes,
    )
    _record_centroid_pass(
        ctx,  # ty: ignore[invalid-argument-type]
        2,
        Offset(0.1, 0.0),
        Offset(0.0, 0.0),
        Offset(0.1, 0.0),
        probes,
    )
    path = write_figure(
        tmp_path,
        render_session_plot("centroid", recorder.records(), search_for="max"),
        write_at=write_at,
    )
    assert path is not None
    assert Path(path).is_file()
    assert path.endswith(PLOT_HTML_SUFFIX)
    assert recorder.pass_count() == 2


def _sweep_centroid_records(
    samples, *, pass_num=1, phase=Phase.COARSE, box=(-1.0, 1.0, -1.0, 1.0)
):
    recorder = SessionRecorder(trace=False, plots=True)
    ctx = SimpleNamespace(recorder=recorder)
    _record_sweep_centroid_pass(
        ctx,  # ty: ignore[invalid-argument-type]
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
    tmp_path, _, write_at = plot_tmp
    recorder = SessionRecorder(trace=False, plots=True)
    ctx = SimpleNamespace(recorder=recorder)
    _record_sweep_centroid_pass(
        ctx,  # ty: ignore[invalid-argument-type]
        1,
        Phase.COARSE,
        Offset.zero(),
        Offset(0.1, 0.0),
        Offset(0.1, 0.0),
        samples,
        (-1.0, 1.0, -1.0, 1.0),
    )
    _record_sweep_centroid_pass(
        ctx,  # ty: ignore[invalid-argument-type]
        2,
        Phase.FINE,
        Offset(0.1, 0.0),
        Offset(0.0, 0.0),
        Offset(0.1, 0.0),
        samples,
        (-0.5, 0.5, -0.5, 0.5),
    )
    path = write_figure(
        tmp_path,
        render_session_plot("sweep_centroid", recorder.records(), search_for="max"),
        write_at=write_at,
    )
    assert path is not None
    assert Path(path).is_file()
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
            ctx,  # ty: ignore[invalid-argument-type]
            pass_num,
            phase,
            center,
            result,
            Offset(0.12, 0.04),
            samples,
            box,
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
    record = HeatmapRecord(
        move=PassMove.compute(Offset.zero(), Offset(0.05, -0.02)),
        bounds=Bounds.from_box(box),
        z=tuple(tuple(row) for row in z),
        x_centers=tuple(x_centers),
        y_centers=tuple(y_centers),
        samples=XYCloud(
            tuple(sample.offset.x for sample in samples),
            tuple(sample.offset.y for sample in samples),
            tuple(sample.freq for sample in samples),
        ),
    )
    fig = render_debug_scan_figure(record, search_for="max")
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
    assert scale_table["rows"][1]["scale"] == "@2x"
    assert scale_table["rows"][2]["scale"] == "@4x"
    assert scale_table["rows"][3]["scale"] == "@8x"
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
    tmp_path, _, write_at = plot_tmp
    records = (
        HeatmapRecord(
            move=PassMove.compute(Offset.zero(), Offset(0.01, 0.02)),
            bounds=Bounds.from_box(box),
            z=tuple(tuple(row) for row in z),
            x_centers=tuple(x_centers),
            y_centers=tuple(y_centers),
            samples=XYCloud((0.0,), (0.0,), (10000.0,)),
        ),
    )
    path = write_figure(
        tmp_path,
        render_session_plot("debug_scan", records, search_for="max"),
        write_at=write_at,
    )
    assert path is not None
    assert Path(path).is_file()


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
    record = HeatmapRecord(
        move=PassMove.compute(Offset.zero(), Offset(0.0, 0.0)),
        bounds=Bounds.from_box(box),
        z=tuple(tuple(row) for row in z),
        x_centers=tuple(x_centers),
        y_centers=tuple(y_centers),
        samples=XYCloud(
            tuple(sample.offset.x for sample in samples),
            tuple(sample.offset.y for sample in samples),
            tuple(sample.freq for sample in samples),
        ),
    )
    fig = render_debug_scan_figure(record, search_for="max")
    assert fig is not None

    out_dir = Path(__file__).resolve().parent / "output"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / "preview_debug_scan.html"
    assert write_html(path, fig)
    assert path.is_file()
    print(f"preview plot: {path}")


def test_render_returns_none_without_plotly(tmp_path):
    with patch("_eddy_seek.plotting.centroid.plotly_available", return_value=False):
        recorder = SessionRecorder(trace=False, plots=True)
        ctx = SimpleNamespace(recorder=recorder)
        _record_centroid_pass(
            ctx,  # ty: ignore[invalid-argument-type]
            1,
            Offset.zero(),
            Offset.zero(),
            Offset.zero(),
            [(Offset.zero(), 100.0)],
        )
        assert (
            render_session_plot("centroid", recorder.records(), search_for="max")
            is None
        )


def test_centroid_finalize_strategy_plot_returns_plot_path(requires_plotly, tmp_path):
    strategy = CentroidStrategy()
    cfg = SeekConfig(save_plots=True, result_folder=str(tmp_path))
    recorder = SessionRecorder(trace=False, plots=True)
    printer = MagicMock()
    ctx = type(
        "Ctx",
        (),
        {
            "config": cfg,
            "session_id": "abcd1234-session",
            "run_label": "tools",
            "artifact_label": "tools_t0",
            "artifact_write_at": PLOT_WRITE_AT,
            "artifact_suffix": lambda _self, name: f"tools_t0_{name}",
            "recorder": recorder,
            "_printer": printer,
        },
    )()
    _record_centroid_pass(
        ctx,  # ty: ignore[invalid-argument-type]
        1,
        Offset.zero(),
        Offset(0.1, 0.0),
        Offset(0.1, 0.0),
        [(Offset.zero(), 100.0)],
    )
    path = finalize_strategy_plot(ctx, strategy.name)  # ty: ignore[invalid-argument-type]
    assert path is not None
    assert path.endswith("2026-07-02_14-30-00_tools/tools_t0_centroid.html")
    assert Path(path).is_file()
    assert recorder.pass_count() == 1
