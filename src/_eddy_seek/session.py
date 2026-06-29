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
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, Protocol

if TYPE_CHECKING:
    from klippy.klippy import Printer
import json
import os
import tempfile
import uuid
import time
import math
from .config import SeekConfig
from .strategy import strategy_for
import logging

logger = logging.getLogger(__name__)


class Position(NamedTuple):
    x: float
    y: float


class SeekHost(Protocol):
    printer: Printer

    def reset_capture(self) -> None: ...
    def get_capture_mean(self, min_samples: int = 5) -> float | None: ...
    def peek_capture_samples(self) -> list[float]: ...
    @property
    def capture_count(self) -> int: ...
    @property
    def save_session_trace(self) -> bool: ...
    def session_trace_config(self) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class SeekSessionResult:
    session_id: str
    start_time: float
    end_time: float
    status: Literal["ok", "failed"]
    offset: Position | None
    passes_run: int
    error_message: str | None


class SeekSession:
    _GCODE_STATE_MOVE = "eddy_seek_move"
    """Locate the eddy-sensor centre by searching for  frequency minima / maxima."""

    def __init__(self, host: SeekHost, config: SeekConfig) -> None:
        self._host = host
        self._config = config
        self._printer = host.printer
        self._gcode = self._printer.lookup_object("gcode")

        self.session_id = str(uuid.uuid4())
        self.start_time = time.time()
        self._offset = Position(0.0, 0.0)
        self._save_trace = getattr(host, "save_session_trace", False)
        self._probes: list[dict[str, Any]] = []

    @property
    def config(self) -> SeekConfig:
        return self._config

    def run(self, gcmd) -> SeekSessionResult:
        cfg = self._config
        strategy = strategy_for(cfg.strategy)
        gcmd.respond_info(
            f"EDDY_SEEK_START: strategy={cfg.strategy}  "
            f"search_for={cfg.search_for}  "
            f"max_jog=({cfg.max_jog_x},{cfg.max_jog_y}) mm  "
            f"tolerance={cfg.tolerance} mm  "
            f"dwell={cfg.dwell_time} s  "
            f"max_passes={cfg.max_passes}"
        )
        strategy.announce_start(self, gcmd)
        self._gcode.run_script_from_command(
            f"SAVE_GCODE_STATE NAME={self._GCODE_STATE_MOVE}"
        )
        best_x = 0.0
        best_y = 0.0
        passes_run = 0
        error_message = None

        try:
            best_x, best_y, passes_run = strategy.search(self, gcmd)

            self._move_to(best_x, best_y)
            gcmd.respond_info(
                f"EDDY_SEEK: done - nozzle offset from start: "
                f"X={best_x:+.4f} mm  Y={best_y:+.4f} mm  "
                f"(passes={passes_run})"
            )
            status: Literal["ok", "failed"] = "ok"
            offset = Position(best_x, best_y)

        except Exception as exc:
            error_message = str(exc)
            logger.exception("eddy_seek: search failed")
            gcmd.respond_info(f"EDDY_SEEK ERROR: {error_message}")
            status = "failed"
            offset = None
            try:
                self._move_to(0.0, 0.0)
            except Exception:
                pass
        finally:
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
        )
        if self._save_trace:
            path = _write_seek_trace(self._host, result, self._probes)
            if path is not None:
                gcmd.respond_info(f"EDDY_SEEK: session trace saved to {path}")
        return result

    def measure_at(self, x_offset: float, y_offset: float) -> float:
        toolhead = self._printer.lookup_object("toolhead")
        self._move_to(x_offset, y_offset)
        toolhead.wait_moves()

        self._host.reset_capture()
        toolhead.dwell(self._config.dwell_time)
        toolhead.wait_moves()

        mean = self._host.get_capture_mean(min_samples=3)
        if mean is None:
            raise RuntimeError(
                f"eddy_seek: no samples at offset "
                f"({x_offset:.3f}, {y_offset:.3f}) mm after "
                f"{self._config.dwell_time:.2f} s dwell. "
                "Check sensor connection, dwell_time, and i2c settings."
            )
        if self._save_trace:
            self._probes.append(
                {
                    "x": x_offset,
                    "y": y_offset,
                    "mean_hz": mean,
                    "samples_hz": self._host.peek_capture_samples(),
                }
            )
        return mean

    def _move_to(self, x_offset: float, y_offset: float) -> None:
        delta_x = x_offset - self._offset.x
        delta_y = y_offset - self._offset.y

        if abs(delta_x) < 1e-6 and abs(delta_y) < 1e-6:
            return

        toolhead = self._printer.lookup_object("toolhead")
        pos = toolhead.get_position()
        toolhead.manual_move(
            [pos[0] + delta_x, pos[1] + delta_y],
            self._config.jog_speed / 60.0,
        )
        self._offset = Position(x_offset, y_offset)


_TRACE_FILENAME = "seek_trace.json"


def _write_seek_trace(
    host: SeekHost, result: SeekSessionResult, probes: list[dict[str, Any]]
) -> str | None:
    path = os.path.join(tempfile.gettempdir(), _TRACE_FILENAME)
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
        "probes": probes,
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


def report_accuracy_stats(gcmd, offsets: list[Position]) -> None:
    n = len(offsets)
    xs = [p.x for p in offsets]
    ys = [p.y for p in offsets]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    std_x = _sample_stdev(xs, mean_x)
    std_y = _sample_stdev(ys, mean_y)

    radial = [math.hypot(x - mean_x, y - mean_y) for x, y in zip(xs, ys)]
    max_radial = max(radial)
    mean_radial = sum(radial) / n

    max_pair = 0.0
    for i in range(n):
        for j in range(i + 1, n):
            max_pair = max(max_pair, math.hypot(xs[i] - xs[j], ys[i] - ys[j]))

    gcmd.respond_info("EDDY_SEEK_ACCURACY: --- repeatability report ---")
    for i, (x, y) in enumerate(offsets, start=1):
        gcmd.respond_info(
            f"EDDY_SEEK_ACCURACY:   #{i}  X={x:+.4f} mm  Y={y:+.4f} mm  "
            f"radial={radial[i - 1]:.4f} mm"
        )
    gcmd.respond_info(
        f"EDDY_SEEK_ACCURACY: mean   X={mean_x:+.4f} mm  Y={mean_y:+.4f} mm"
    )
    gcmd.respond_info(f"EDDY_SEEK_ACCURACY: stdev  X={std_x:.4f} mm  Y={std_y:.4f} mm")
    gcmd.respond_info(
        f"EDDY_SEEK_ACCURACY: range  X=[{min(xs):+.4f}, {max(xs):+.4f}] mm  "
        f"Y=[{min(ys):+.4f}, {max(ys):+.4f}] mm"
    )
    gcmd.respond_info(
        f"EDDY_SEEK_ACCURACY: radial from mean  "
        f"max={max_radial:.4f} mm  mean={mean_radial:.4f} mm"
    )
    gcmd.respond_info(
        f"EDDY_SEEK_ACCURACY: max pairwise distance = {max_pair:.4f} mm  ({n} repeats)"
    )
