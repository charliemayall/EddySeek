"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Optional HTML debug plots for alignment strategies.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from ..common import Offset, Phase, session_artifact_filename
from ..motion_handler import MotionSample
from ._plotly import plotly_available, write_html
from .accuracy import AccuracyRepeatRecord, write_accuracy_plot
from .centroid import CentroidPassRecord, write_centroid_session_plot
from .debug_scan import DebugScanRecord, write_debug_scan_plot
from .sweep_centroid import SweepCentroidPassRecord, write_sweep_centroid_session_plot
from .ternary import TernaryPassRecord, TernaryStep, write_ternary_session_plot

logger = logging.getLogger(__name__)

__all__ = [
    "AccuracyRepeatRecord",
    "PlotWriter",
    "TernaryStep",
    "plot_filename",
]


def plot_filename(
    session_id: str,
    when: datetime | None = None,
    *,
    suffix: str = "",
    run_id: str | None = None,
) -> str:
    """``{run_dir}/{label}.html`` under ``result_folder``."""
    return session_artifact_filename(
        session_id, when, suffix=suffix, run_id=run_id, ext="html"
    )


class PlotWriter:
    """Write interactive debug plots as HTML under the results folder.

    Each plot kind follows the same pattern: ``record_*`` calls during the run,
    then a single ``finalize_*`` writes one HTML file (or returns ``None`` if
    plotly is missing).
    """

    def __init__(
        self,
        results_dir: Path,
        session_id: str,
        *,
        write_at: datetime | None = None,
        suffix: str = "",
        run_id: str | None = None,
    ) -> None:
        self._results_dir = Path(results_dir)
        self._session_id = session_id
        self._write_at = write_at
        self._suffix = suffix
        self._run_id = run_id
        self._results_dir.mkdir(parents=True, exist_ok=True)
        if not plotly_available():
            logger.warning("eddy_seek: save_plots enabled but plotly is not installed")
        self._centroid_passes: list[CentroidPassRecord] = []
        self._sweep_centroid_passes: list[SweepCentroidPassRecord] = []
        self._ternary_passes: list[TernaryPassRecord] = []
        self._accuracy_repeats: list[AccuracyRepeatRecord] = []
        self._debug_scan_records: list[DebugScanRecord] = []

    @property
    def centroid_pass_count(self) -> int:
        return len(self._centroid_passes)

    @property
    def sweep_centroid_pass_count(self) -> int:
        return len(self._sweep_centroid_passes)

    @property
    def ternary_pass_count(self) -> int:
        return len(self._ternary_passes)

    @property
    def accuracy_repeat_count(self) -> int:
        return len(self._accuracy_repeats)

    @property
    def debug_scan_count(self) -> int:
        return len(self._debug_scan_records)

    def write(self, fig: Any, *, suffix: str | None = None) -> str | None:
        if not plotly_available():
            return None
        out_path = self._results_dir / plot_filename(
            self._session_id,
            self._write_at,
            suffix=suffix if suffix is not None else self._suffix,
            run_id=self._run_id,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if not write_html(str(out_path), fig):
            logger.warning(f"eddy_seek: failed to write plot to {out_path}")
            return None
        logger.info(f"eddy_seek: debug plot saved to {out_path}")
        return str(out_path)

    def record_centroid_pass(
        self,
        *,
        pass_num: int,
        center: Offset,
        result: Offset,
        moved: Offset,
        probes: list[tuple[Offset, float]],
    ) -> None:
        self._centroid_passes.append(
            CentroidPassRecord(
                pass_num=pass_num,
                center=center,
                result=result,
                moved=moved,
                probes=probes,
            )
        )

    def finalize_centroid(self, *, search_for: Literal["min", "max"]) -> str | None:
        if not self._centroid_passes:
            return None
        fig = write_centroid_session_plot(
            passes=self._centroid_passes,
            search_for=search_for,
        )
        if fig is None:
            return None
        return self.write(fig)

    def record_sweep_centroid_pass(
        self,
        *,
        pass_num: int,
        phase: Phase,
        center: Offset,
        result: Offset,
        moved: Offset,
        samples: list[MotionSample],
        box: tuple[float, float, float, float],
    ) -> None:
        self._sweep_centroid_passes.append(
            SweepCentroidPassRecord(
                pass_num=pass_num,
                phase=phase,
                center=center,
                result=result,
                moved=moved,
                samples=samples,
                box=box,
            )
        )

    def finalize_sweep_centroid(
        self, *, search_for: Literal["min", "max"]
    ) -> str | None:
        if not self._sweep_centroid_passes:
            return None
        fig = write_sweep_centroid_session_plot(
            passes=self._sweep_centroid_passes,
            search_for=search_for,
        )
        if fig is None:
            return None
        return self.write(fig)

    def record_ternary_pass(
        self,
        *,
        pass_num: int,
        result: Offset,
        moved: Offset,
        x_steps: list[TernaryStep],
        y_steps: list[TernaryStep],
        probes: list[tuple[Offset, float]],
    ) -> None:
        self._ternary_passes.append(
            TernaryPassRecord(
                pass_num=pass_num,
                result=result,
                moved=moved,
                x_steps=x_steps,
                y_steps=y_steps,
                probes=probes,
            )
        )

    def finalize_ternary(self, *, search_for: Literal["min", "max"]) -> str | None:
        if not self._ternary_passes:
            return None
        fig = write_ternary_session_plot(
            passes=self._ternary_passes,
            search_for=search_for,
        )
        if fig is None:
            return None
        return self.write(fig)

    def record_accuracy_repeat(
        self,
        *,
        repeat_num: int,
        offset: Offset,
        session_plot_path: str | None = None,
    ) -> None:
        self._accuracy_repeats.append(
            AccuracyRepeatRecord(
                repeat_num=repeat_num,
                offset=offset,
                session_plot_path=session_plot_path,
            )
        )

    def finalize_accuracy(self) -> str | None:
        if len(self._accuracy_repeats) < 2:
            return None
        fig = write_accuracy_plot(repeats=self._accuracy_repeats)
        if fig is None:
            return None
        return self.write(fig, suffix="accuracy")

    def record_debug_scan(
        self,
        *,
        center: Offset,
        result: Offset,
        samples: list[MotionSample],
        box: tuple[float, float, float, float],
        z: list[list[float | None]],
        x_centers: list[float],
        y_centers: list[float],
    ) -> None:
        self._debug_scan_records.append(
            DebugScanRecord(
                center=center,
                result=result,
                samples=samples,
                box=box,
                z=z,
                x_centers=x_centers,
                y_centers=y_centers,
            )
        )

    def finalize_debug_scan(self, *, search_for: Literal["min", "max"]) -> str | None:
        if not self._debug_scan_records:
            return None
        record = self._debug_scan_records[-1]
        fig = write_debug_scan_plot(record=record, search_for=search_for)
        if fig is None:
            return None
        return self.write(fig)
