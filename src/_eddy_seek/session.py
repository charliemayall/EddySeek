"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Per-tool seek session: sensor sampling, jogging, and convergence.
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from .common import Offset, Position, session_artifact_filename
from .config import SeekConfig
from .kconsole import KConsole, console_for_gcmd
from .movement.guard import KnownKinematicLimits, clear_gcode_offset_xy
from .movement.handler import MotionHandler
from .plotting.primitives import PlotArtifactRecord, ProbeRecord
from .plotting.recorder import SessionRecorder

if TYPE_CHECKING:
    from klippy.klippy import Printer

    from .strategy.base import SeekStrategy


logger = logging.getLogger(__name__)


class SeekHost(Protocol):
    printer: Printer
    seek_config: SeekConfig
    console: KConsole | None

    def reset_capture(self) -> None: ...
    def get_capture_mean(self, min_samples: int = 5) -> float | None: ...
    def peek_capture_samples(self) -> list[float]: ...
    @property
    def capture_count(self) -> int: ...
    def session_trace_config(self) -> dict[str, Any]: ...
    def add_sensor_client(self, callback: Callable[..., Any]) -> None: ...
    def acquire_sensor_stream(self) -> AbstractContextManager[None]:
        """Acquire a context manager that will release the sensor stream when exited."""
        ...


@dataclass(frozen=True, slots=True)
class SeekSessionResult:
    session_id: str
    start_time: float
    end_time: float
    status: Literal["ok", "failed"]
    offset: Offset | None
    passes_run: int
    error_message: str | None
    plot_path: str | None = None


class SeekSession:
    """Locate the eddy-sensor centre by searching for frequency minima / maxima."""

    _GCODE_STATE_MOVE = "eddy_seek_move"

    def __init__(
        self,
        host: SeekHost,
        *,
        run_id: str | None = None,
        artifact_label: str = "",
        artifact_write_at: datetime | None = None,
    ) -> None:
        self._host = host
        self.config = host.seek_config
        self._printer = host.printer
        self._gcode = self._printer.lookup_object("gcode")
        self.session_id = str(uuid.uuid4())
        self.run_id = run_id
        self.artifact_label = artifact_label
        self._artifact_write_at = artifact_write_at
        self.start_time = time.time()
        self._motion: MotionHandler | None = None
        cfg = host.seek_config
        self._save_trace = cfg.save_session_trace
        self.recorder = SessionRecorder(
            trace=cfg.save_session_trace,
            plots=cfg.save_plots,
        )
        self._session_start: Position | None = None

    @property
    def artifact_write_at(self) -> datetime:
        if self._artifact_write_at is None:
            raise RuntimeError("eddy_seek: artifact write at not set")
        return self._artifact_write_at

    def artifact_suffix(self, strategy: str) -> str:
        if self.artifact_label:
            return f"{self.artifact_label}_{strategy}"
        return strategy

    @property
    def session_start(self) -> Position:
        if self._session_start is None:
            raise RuntimeError(
                """eddy_seek: session start XY not recorded
                 when session_start was accessed"""
            )
        return self._session_start

    @property
    def motion(self) -> MotionHandler:
        if self._motion is None:
            raise RuntimeError("eddy_seek: motion handler requested before init")
        return self._motion

    def sync_offset(self, offset: Offset) -> None:
        """

        Sync the current offset to the motion handler.

        This is used to update the motion handler's position after a move.
        """
        self.motion.sync_offset(offset)

    @property
    def console(self) -> KConsole | None:
        return self._host.console

    def run(
        self,
        gcmd,
        strategy: SeekStrategy,
        *,
        boundaries: bool = True,
        announce_plot: bool | None = None,
    ) -> SeekSessionResult:
        cfg = self.config
        console = console_for_gcmd(gcmd, self._host.seek_config)
        self._host.console = console
        logger.info(
            f"eddy_seek: session {self.session_id} start "
            f"strategy={strategy.name} search_for={cfg.search_for}"
        )

        strategy.announce_start(self, console)
        self._gcode.run_script_from_command(
            f"SAVE_GCODE_STATE NAME={self._GCODE_STATE_MOVE}"
        )
        clear_gcode_offset_xy(self._printer)
        best = Offset.zero()
        passes_run = 0
        error_message = None
        session_plot_path: str | None = None
        show_plot_saved = announce_plot if announce_plot is not None else boundaries
        status: Literal["ok", "failed"] = "ok"
        try:
            with (
                KnownKinematicLimits(self._printer),
                self._host.acquire_sensor_stream(),
            ):
                self._session_start = Position.from_toolhead(self._printer)
                self._motion = MotionHandler(
                    self._printer,
                    self._host,
                    self.config,
                    self._session_start,
                    self._record_probe if self.recorder.trace else None,
                )
                best, passes_run = strategy.search(self, console)
                self._motion.jog(best)
            logger.info(
                f"eddy_seek: session {self.session_id} ok "
                f"offset=({best.x:.4f}, {best.y:.4f}) passes={passes_run}"
            )
            if boundaries:
                console.exit(
                    f"Done - offset {best.to_delta_str()} ({passes_run} passes)"
                )
            status = "ok"
            offset = best

        except Exception as exc:
            error_message = str(exc)
            logger.exception("eddy_seek: search failed")
            console.error(f"Seek failed: {error_message}")
            status = "failed"
            offset = None
            try:
                if self._motion is not None:
                    self._motion.jog(Offset.zero())
            except Exception:
                logger.warning(
                    "eddy_seek: failed to return to session start after error",
                    exc_info=True,
                )
        finally:
            session_plot_path = strategy.on_session_end(self)
            if session_plot_path is not None:
                self.recorder.record(
                    PlotArtifactRecord(
                        strategy=strategy.name,
                        passes=self.recorder.pass_count(),
                        path=session_plot_path,
                    )
                )
                if show_plot_saved:
                    console.plot_saved(session_plot_path)
            elif cfg.save_plots and status == "ok":
                console.warn(
                    "save_plots is enabled but no plot was written (is plotly installed?)"
                )
                logger.warning("eddy_seek: save_plots enabled but no plot was written")
            if self._motion is not None:
                self._motion.close()
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
                self._host,
                result,
                self.recorder.to_probe_dicts(),
                run_id=self.run_id,
                suffix=self.artifact_suffix(strategy.name),
                write_at=self.artifact_write_at,
            )
            if path is not None:
                logger.info(f"eddy_seek: session trace saved to {path}")
        logger.info(
            f"eddy_seek: session {self.session_id} finished "
            f"status={result.status} probes={len(self.recorder.to_probe_dicts())}"
        )
        return result

    def _record_probe(self, probe: dict[str, Any]) -> None:
        self.recorder.record(
            ProbeRecord(
                at=Offset(float(probe["x"]), float(probe["y"])),
                mean_hz=float(probe["mean_hz"]),
                samples_hz=tuple(probe["samples_hz"]),
            )
        )

    def measure_at(self, offset: Offset) -> float:
        return self.motion.sample(offset)


def _write_seek_trace(
    host: SeekHost,
    result: SeekSessionResult,
    probes: list[dict[str, Any]],
    *,
    run_id: str | None = None,
    suffix: str = "",
    write_at: datetime | None = None,
) -> str | None:
    results_dir = Path(host.seek_config.result_folder)
    results_dir.mkdir(parents=True, exist_ok=True)
    out = results_dir / session_artifact_filename(
        result.session_id,
        write_at or datetime.now(),
        suffix=suffix,
        run_id=run_id,
        ext="json",
    )
    path = str(out)
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
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as trace_file:
            json.dump(payload, trace_file, indent=2)
            trace_file.write("\n")
        logger.info(f"eddy_seek: session trace saved to {path}")
        return path
    except OSError as exc:
        logger.warning(f"eddy_seek: failed to write session trace to {path}: {exc}")
        return None


def _sample_stdev(values: list[float], mean: float) -> float:
    if len(values) < 2:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


@dataclass(frozen=True, slots=True)
class AccuracyStats:
    mean: Offset
    std_x: float
    std_y: float
    radial: tuple[float, ...]
    max_radial: float
    mean_radial: float
    max_pair: float
    xs_range: tuple[float, float]
    ys_range: tuple[float, float]


def compute_accuracy_stats(offsets: list[Offset]) -> AccuracyStats:
    n = len(offsets)
    xs = [p.x for p in offsets]
    ys = [p.y for p in offsets]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    std_x = _sample_stdev(xs, mean_x)
    std_y = _sample_stdev(ys, mean_y)

    mean = Offset(mean_x, mean_y)
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


def report_accuracy_stats(
    console: KConsole,
    offsets: list[Offset],
    *,
    durations_s: list[float] | None = None,
) -> None:
    n = len(offsets)
    stats = compute_accuracy_stats(offsets)
    output = []
    for i, offset in enumerate(offsets, start=1):
        line = (
            f"#{i}  X={offset.x:+.4f} mm  Y={offset.y:+.4f} mm  "
            f"radial={stats.radial[i - 1]:.4f} mm"
        )
        if durations_s is not None and i <= len(durations_s):
            line += f"  t={durations_s[i - 1]:.1f}s"
        console.detail(line)
    output.extend(
        [
            f"Repeatability ({n} runs):",
            console.BR,
            f"mean X={stats.mean.x:+.4f} Y={stats.mean.y:+.4f} mm",
            console.BR,
            f"σ X={stats.std_x:.3f} Y={stats.std_y:.3f} mm",
            console.BR,
            console.BR,
            f"Max scatter: {stats.max_radial:.3f} mm",
            console.BR,
            f"Max pairwise {stats.max_pair:.3f} mm",
            console.BR,
        ]
    )

    if durations_s:
        mean_t = sum(durations_s) / len(durations_s)
        output.extend(
            [
                console.BR,
                f"Seek time ({len(durations_s)} runs): ",
                console.BR,
                f"mean {mean_t:.1f}s ",
                console.BR,
                f"(min {min(durations_s):.1f}s, max {max(durations_s):.1f}s)",
            ]
        )
    console.info("".join(output))
    logger.info(
        f"eddy_seek: accuracy report n={n} mean=({stats.mean.x:.4f}, {stats.mean.y:.4f}) "
        f"stdev=({stats.std_x:.4f}, {stats.std_y:.4f}) "
        f"max_radial={stats.max_radial:.4f} max_pair={stats.max_pair:.4f}"
        + (
            f" seek_time_mean={sum(durations_s) / len(durations_s):.2f}s"
            if durations_s
            else ""
        )
    )
