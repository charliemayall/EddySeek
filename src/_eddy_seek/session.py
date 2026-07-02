"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

Per-tool seek session: sensor sampling, jogging, and convergence.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from klippy.gcode import GCodeCommand
    from klippy.klippy import Printer
    from .strategy.base import SeekStrategy

import json
import logging
import math
import time
import uuid
from datetime import datetime
from pathlib import Path

from .common import Position, session_artifact_filename
from .config import SeekConfig
from .motion_guard import MotionGuard

logger = logging.getLogger(__name__)


class SeekReporter(Protocol):
    def info(self, msg: str) -> None: ...


@dataclass(frozen=True, slots=True)
class GcodeReporter:
    """Wrap Klipper ``GCodeCommand.respond_info`` for strategy use."""

    _gcmd: Any

    def info(self, msg: str) -> None:
        self._gcmd.respond_info(msg)


class SeekContext(Protocol):
    config: SeekConfig

    @property
    def session_id(self) -> str: ...

    @property
    def session_start(self) -> Position: ...

    def measure_at(self, offset: Position) -> float: ...

    def append_trace(self, probe: dict[str, Any]) -> None: ...

    def append_plot_trace(self, entry: dict[str, Any]) -> None: ...

    def sync_offset(self, offset: Position) -> None:
        """Update tracked session-relative nozzle position after continuous motion."""
        ...


class SweepContext(SeekContext, Protocol):
    host: SeekHost


class SeekHost(Protocol):
    printer: Printer
    seek_config: SeekConfig

    def reset_capture(self) -> None: ...
    def get_capture_mean(self, min_samples: int = 5) -> float | None: ...
    def peek_capture_samples(self) -> list[float]: ...
    @property
    def capture_count(self) -> int: ...
    def session_trace_config(self) -> dict[str, Any]: ...
    def add_sensor_client(self, callback: Callable[..., Any]) -> None: ...
    def acquire_sensor_stream(self) -> None: ...
    def release_sensor_stream(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SeekSessionResult:
    session_id: str
    start_time: float
    end_time: float
    status: Literal["ok", "failed"]
    offset: Position | None
    passes_run: int
    error_message: str | None
    plot_path: str | None = None


class SeekSession(SeekContext):
    """Locate the eddy-sensor centre by searching for frequency minima / maxima."""

    _GCODE_STATE_MOVE = "eddy_seek_move"

    def __init__(self, host: SeekHost) -> None:
        self._host = host
        self.config = host.seek_config
        self._printer = host.printer
        self._gcode = self._printer.lookup_object("gcode")
        self._session_id = str(uuid.uuid4())
        self.start_time = time.time()
        self._offset = Position.zero()
        self._save_trace = host.seek_config.save_session_trace
        self._probes: list[dict[str, Any]] = []
        self._plot_traces: list[dict[str, Any]] = []
        self._session_start: Position | None = None

    @property
    def host(self) -> SeekHost:
        return self._host

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def session_start(self) -> Position:
        if self._session_start is None:
            raise RuntimeError("eddy_seek: session start XY not recorded")
        return self._session_start

    def append_trace(self, probe: dict[str, Any]) -> None:
        if self._save_trace:
            self._probes.append(probe)

    def append_plot_trace(self, entry: dict[str, Any]) -> None:
        self._plot_traces.append(entry)

    def sync_offset(self, offset: Position) -> None:
        self._offset = offset

    def run(self, gcmd, strategy: SeekStrategy) -> SeekSessionResult:
        cfg = self.config
        reporter = GcodeReporter(gcmd)
        logger.debug(
            "eddy_seek: session %s start strategy=%s search_for=%s",
            self.session_id,
            strategy.name,
            cfg.search_for,
        )
        gcmd.respond_info(
            f"EDDY_SEEK_START: strategy={strategy.name}  "
            f"search_for={cfg.search_for}  "
            f"max_jog=({cfg.max_jog_x},{cfg.max_jog_y}) mm  "
            f"tolerance={cfg.tolerance} mm  "
            f"dwell={cfg.dwell_time} s  "
            f"max_passes={cfg.max_passes}"
        )
        strategy.announce_start(self, reporter)
        self._gcode.run_script_from_command(
            f"SAVE_GCODE_STATE NAME={self._GCODE_STATE_MOVE}"
        )
        best = Position.zero()
        passes_run = 0
        error_message = None

        try:
            self._host.acquire_sensor_stream()
            with MotionGuard(self._printer, gcmd):
                self._session_start = Position.from_toolhead(self._printer)
                best, passes_run = strategy.search(self, reporter)
                self._move_to(best)
            logger.debug(
                "eddy_seek: session %s ok offset=(%.4f, %.4f) passes=%d",
                self.session_id,
                best.x,
                best.y,
                passes_run,
            )
            gcmd.respond_info(
                f"EDDY_SEEK: done - nozzle offset from start: "
                f"X={best.x:+.4f} mm  Y={best.y:+.4f} mm  "
                f"(passes={passes_run})"
            )
            status: Literal["ok", "failed"] = "ok"
            offset = best

        except Exception as exc:
            error_message = str(exc)
            logger.exception("eddy_seek: search failed")
            gcmd.respond_info(f"EDDY_SEEK ERROR: {error_message}")
            status = "failed"
            offset = None
            try:
                self._move_to(Position.zero())
            except Exception:
                logger.warning(
                    "eddy_seek: failed to return to session start after error",
                    exc_info=True,
                )
        finally:
            session_plot_path = strategy.on_session_end(self)
            if session_plot_path is not None:
                self.append_plot_trace(
                    {
                        "type": "plot",
                        "strategy": strategy.name,
                        "passes": getattr(strategy, "_last_plot_passes", 0),
                        "path": session_plot_path,
                    }
                )
                gcmd.respond_info(f"EDDY_SEEK: debug plot saved to {session_plot_path}")
            self._host.release_sensor_stream()
            self._gcode.run_script_from_command(
                f"RESTORE_GCODE_STATE NAME={self._GCODE_STATE_MOVE}"
            )
        result = SeekSessionResult(
            session_id=self.session_id,
            start_time=self.start_time,
            end_time=time.time(),
            status=status,
            offset=offset,
            passes_run=passes_run,
            error_message=error_message,
            plot_path=session_plot_path,
        )
        if self._save_trace:
            path = _write_seek_trace(
                self._host, result, self._probes, self._plot_traces
            )
            if path is not None:
                gcmd.respond_info(f"EDDY_SEEK: session trace saved to {path}")
        logger.debug(
            "eddy_seek: session %s finished status=%s probes=%d",
            self.session_id,
            result.status,
            len(self._probes),
        )
        return result

    def measure_at(self, offset: Position) -> float:
        toolhead = self._printer.lookup_object("toolhead")
        self._move_to(offset)
        toolhead.wait_moves()

        self._host.reset_capture()
        toolhead.dwell(self.config.dwell_time)
        toolhead.wait_moves()

        mean = self._host.get_capture_mean(min_samples=3)
        if mean is None:
            logger.debug(
                "eddy_seek: measure_at (%.4f, %.4f) failed (%d samples)",
                offset.x,
                offset.y,
                self._host.capture_count,
            )
            raise RuntimeError(
                f"eddy_seek: no samples at offset "
                f"({offset.x:.3f}, {offset.y:.3f}) mm after "
                f"{self.config.dwell_time:.2f} s dwell. "
                "Check sensor connection, dwell_time, and i2c settings."
            )
        logger.debug(
            "eddy_seek: measure_at (%.4f, %.4f) -> %.2f Hz (%d samples)",
            offset.x,
            offset.y,
            mean,
            self._host.capture_count,
        )
        if self._save_trace:
            self.append_trace(
                {
                    "x": offset.x,
                    "y": offset.y,
                    "mean_hz": mean,
                    "samples_hz": self._host.peek_capture_samples(),
                }
            )
        return mean

    def _move_to(self, offset: Position) -> None:
        delta = offset - self._offset
        if delta.x == 0.0 and delta.y == 0.0:
            return

        logger.debug(
            "eddy_seek: jog delta=(%.4f, %.4f) -> offset=(%.4f, %.4f)",
            delta.x,
            delta.y,
            offset.x,
            offset.y,
        )
        toolhead = self._printer.lookup_object("toolhead")
        pos = toolhead.get_position()
        toolhead.manual_move(
            [pos[0] + delta.x, pos[1] + delta.y],
            self.config.jog_speed / 60.0,
        )
        self._offset = offset


def _write_seek_trace(
    host: SeekHost,
    result: SeekSessionResult,
    probes: list[dict[str, Any]],
    plot_traces: list[dict[str, Any]],
) -> str | None:
    results_dir = Path(host.seek_config.result_folder)
    results_dir.mkdir(parents=True, exist_ok=True)
    path = str(
        results_dir
        / session_artifact_filename(
            result.session_id,
            datetime.fromtimestamp(result.start_time),
            ext="json",
        )
    )
    payload = {
        "metadata": {
            "session_id": result.session_id,
            "start_time": result.start_time,
            "end_time": result.end_time,
            "status": result.status,
            "offset": (
                {"x": result.offset.x, "y": result.offset.y}
                if result.offset is not None
                else None
            ),
            "passes_run": result.passes_run,
            "error_message": result.error_message,
            "config": host.session_trace_config(),
        },
        "probes": probes + plot_traces,
    }
    try:
        with open(path, "w", encoding="utf-8") as trace_file:
            json.dump(payload, trace_file, indent=2)
            trace_file.write("\n")
        logger.info("eddy_seek: session trace saved to %s", path)
        return path
    except OSError as exc:
        logger.warning("eddy_seek: failed to write session trace to %s: %s", path, exc)
        return None


def _sample_stdev(values: list[float], mean: float) -> float:
    if len(values) < 2:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


@dataclass(frozen=True, slots=True)
class AccuracyStats:
    mean: Position
    std_x: float
    std_y: float
    radial: tuple[float, ...]
    max_radial: float
    mean_radial: float
    max_pair: float
    xs_range: tuple[float, float]
    ys_range: tuple[float, float]


def compute_accuracy_stats(offsets: list[Position]) -> AccuracyStats:
    n = len(offsets)
    xs = [p.x for p in offsets]
    ys = [p.y for p in offsets]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    std_x = _sample_stdev(xs, mean_x)
    std_y = _sample_stdev(ys, mean_y)

    mean = Position(mean_x, mean_y)
    radial = tuple(offset.distance_to(mean) for offset in offsets)
    max_radial = max(radial)
    mean_radial = sum(radial) / n

    max_pair = 0.0
    for i in range(n):
        for j in range(i + 1, n):
            max_pair = max(max_pair, offsets[i].distance_to(offsets[j]))

    return AccuracyStats(
        mean=mean,
        std_x=std_x,
        std_y=std_y,
        radial=radial,
        max_radial=max_radial,
        mean_radial=mean_radial,
        max_pair=max_pair,
        xs_range=(min(xs), max(xs)),
        ys_range=(min(ys), max(ys)),
    )


def report_accuracy_stats(gcmd: GCodeCommand, offsets: list[Position]) -> None:
    n = len(offsets)
    stats = compute_accuracy_stats(offsets)

    gcmd.respond_info("EDDY_SEEK_ACCURACY: --- repeatability report ---")
    for i, offset in enumerate(offsets, start=1):
        gcmd.respond_info(
            f"EDDY_SEEK_ACCURACY:   #{i}  X={offset.x:+.4f} mm  "
            f"Y={offset.y:+.4f} mm  "
            f"radial={stats.radial[i - 1]:.4f} mm"
        )
    gcmd.respond_info(
        f"EDDY_SEEK_ACCURACY: mean   X={stats.mean.x:+.4f} mm  Y={stats.mean.y:+.4f} mm"
    )
    gcmd.respond_info(
        f"EDDY_SEEK_ACCURACY: stdev  X={stats.std_x:.4f} mm  Y={stats.std_y:.4f} mm"
    )
    gcmd.respond_info(
        f"EDDY_SEEK_ACCURACY: range  X=[{stats.xs_range[0]:+.4f}, "
        f"{stats.xs_range[1]:+.4f}] mm  "
        f"Y=[{stats.ys_range[0]:+.4f}, {stats.ys_range[1]:+.4f}] mm"
    )
    gcmd.respond_info(
        f"EDDY_SEEK_ACCURACY: radial from mean  "
        f"max={stats.max_radial:.4f} mm  mean={stats.mean_radial:.4f} mm"
    )
    gcmd.respond_info(
        f"EDDY_SEEK_ACCURACY: max pairwise distance = {stats.max_pair:.4f} mm  "
        f"({n} repeats)"
    )
    logger.debug(
        "eddy_seek: accuracy report n=%d mean=(%.4f, %.4f) stdev=(%.4f, %.4f) "
        "max_radial=%.4f max_pair=%.4f",
        n,
        stats.mean.x,
        stats.mean.y,
        stats.std_x,
        stats.std_y,
        stats.max_radial,
        stats.max_pair,
    )
