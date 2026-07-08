"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Per-tool seek session: sensor sampling, jogging, and convergence.
"""

from __future__ import annotations

import json
import logging
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
from .kconsole import KConsole
from .movement.gcode_state import GCodeState
from .movement.guard import KnownKinematicLimits, clear_gcode_offset_xy
from .movement.handler import MIN_CAPTURE_SAMPLES, MotionHandler
from .plot_announce import announce_seek_plot
from .plotting.artifacts import finalize_strategy_plot
from .plotting.primitives import PlotArtifactRecord, ProbeRecord
from .plotting.recorder import SessionRecorder

if TYPE_CHECKING:
    from klippy.klippy import Printer

    from .strategy.base import SeekExitKind, SeekStrategy


logger = logging.getLogger(__name__)


class SeekHost(Protocol):
    printer: Printer
    seek_config: SeekConfig
    console: KConsole | None

    def reset_capture(self) -> None: ...
    def get_capture_mean(
        self, min_samples: int = MIN_CAPTURE_SAMPLES
    ) -> float | None: ...
    def peek_capture_samples(self) -> list[float]: ...
    @property
    def capture_count(self) -> int: ...
    def session_trace_config(self) -> dict[str, Any]: ...
    def add_sensor_client(self, callback: Callable[..., Any]) -> None: ...
    def acquire_sensor_stream(self) -> AbstractContextManager[None]:
        """Acquire a context manager that will release the sensor stream when exited."""
        ...


@dataclass(frozen=True, slots=True)
class _SearchRun:
    status: Literal["ok", "failed"]
    offset: Offset | None
    passes_run: int
    error_message: str | None
    session_plot_path: str | None
    exit_kind: SeekExitKind | None = None


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
    exit_kind: SeekExitKind | None = None


class SeekSession:
    """Locate the eddy-sensor centre by searching for frequency minima / maxima."""

    _GCODE_STATE_MOVE = "eddy_seek_move"

    def __init__(
        self,
        host: SeekHost,
        *,
        run_id: str | None = None,
        run_label: str = "run",
        artifact_label: str = "",
        artifact_write_at: datetime | None = None,
    ) -> None:
        self._host = host
        self.config = host.seek_config
        self._printer = host.printer
        self._gcode = self._printer.lookup_object("gcode")
        self.session_id = str(uuid.uuid4())
        self.run_id = run_id
        self.run_label = run_label
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
        console = KConsole(gcmd, self._host.seek_config)
        self._host.console = console
        logger.info(
            f"eddy_seek: session {self.session_id} start "
            f"strategy={strategy.name} search_for={cfg.search_for}"
        )

        strategy.announce_start(self, console)
        clear_gcode_offset_xy(self._printer)
        show_plot_saved = announce_plot if announce_plot is not None else boundaries
        with GCodeState(self._gcode, self._GCODE_STATE_MOVE):
            search = self._execute_search(
                strategy,
                console,
                boundaries=boundaries,
                show_plot_saved=show_plot_saved,
            )
        result = SeekSessionResult(
            session_id=self.session_id,
            start_time=self.start_time,
            end_time=time.time(),
            status=search.status,
            offset=search.offset,
            passes_run=search.passes_run,
            error_message=search.error_message,
            plot_path=search.session_plot_path,
            exit_kind=search.exit_kind,
        )
        if self._save_trace:
            _write_seek_trace(
                self._host,
                result,
                self.recorder.to_probe_dicts(),
                run_label=self.run_label,
                suffix=self.artifact_suffix(strategy.name),
                write_at=self.artifact_write_at,
            )

        return result

    def _execute_search(
        self,
        strategy: SeekStrategy,
        console: KConsole,
        *,
        boundaries: bool,
        show_plot_saved: bool,
    ) -> _SearchRun:
        from .strategy.base import DivergenceError, SeekExitError, SeekExitKind

        best = Offset.zero()
        passes_run = 0
        error_message: str | None = None
        session_plot_path: str | None = None
        status: Literal["ok", "failed"] = "ok"
        offset: Offset | None = None
        exit_kind: SeekExitKind | None = None
        try:
            with (
                KnownKinematicLimits(self._printer),
                self._host.acquire_sensor_stream(),
            ):
                self._session_start = Position.from_toolhead(
                    self._printer.lookup_object("toolhead")
                )
                self._motion = MotionHandler(
                    self._printer,
                    self._host,
                    self.config,
                    self._session_start,
                    self._get_single_sample if self.recorder.trace else None,
                )
                best, passes_run = strategy.search(self, console)
                self._motion.jog(best)
            logger.info(
                f"eddy_seek: session {self.session_id} ok "
                f"offset=({best.x:.4f}, {best.y:.4f}) passes={passes_run}"
            )
            if boundaries:
                console.exit(
                    f"Done - offset {best.to_console_str()} ({passes_run} passes)"
                )
            status = "ok"
            offset = best
            exit_kind = SeekExitKind.CONVERGED
        except SeekExitError as err:
            error_message = self._report_search_failure(console, err)
            status = "failed"
            offset = None
            exit_kind = err.exit_kind
            if isinstance(err, DivergenceError):
                self._recover_motion_jog(
                    err.previous,
                    warning="eddy_seek: failed to return to previous offset after divergence",
                )
        except Exception as exc:
            error_message = self._report_search_failure(console, exc)
            status = "failed"
            offset = None
            self._recover_motion_jog(
                Offset.zero(),
                warning="eddy_seek: failed to return to session start after error",
            )
        finally:
            # close motion before plot I/O so reactor isn't blocked with client active
            if self._motion is not None:
                self._motion.close()
            session_plot_path = self._finalize_session_plot(
                strategy,
                console,
                status=status,
                show_plot_saved=show_plot_saved,
            )
        return _SearchRun(
            status=status,
            offset=offset,
            passes_run=passes_run,
            error_message=error_message,
            session_plot_path=session_plot_path,
            exit_kind=exit_kind,
        )

    def _report_search_failure(self, console: KConsole, err: BaseException) -> str:
        error_message = str(err)
        logger.exception("eddy_seek: search failed")
        console.error(f"Seek failed: {error_message}")
        return error_message

    def _recover_motion_jog(self, offset: Offset, *, warning: str) -> None:
        try:
            if self._motion is not None:
                self._motion.jog(offset)
        except Exception:
            logger.warning(warning, exc_info=True)

    def _finalize_session_plot(
        self,
        strategy: SeekStrategy,
        console: KConsole,
        *,
        status: Literal["ok", "failed"],
        show_plot_saved: bool,
    ) -> str | None:
        cfg = self.config
        session_plot_path = finalize_strategy_plot(self, strategy.name)
        if session_plot_path is not None:
            self.recorder.record(
                PlotArtifactRecord(
                    strategy=strategy.name,
                    passes=self.recorder.pass_count(),
                    path=session_plot_path,
                )
            )
            announce_seek_plot(
                console,
                plot_path=session_plot_path,
                status=status,
                save_plots=cfg.save_plots,
                enabled=show_plot_saved,
            )
        return session_plot_path

    def _get_single_sample(self, probe: ProbeRecord) -> None:
        self.recorder.record(probe)

    def measure_at(self, offset: Offset) -> float:
        return self.motion.sample(offset)


def _write_seek_trace(
    host: SeekHost,
    result: SeekSessionResult,
    probes: list[dict[str, Any]],
    *,
    run_label: str = "run",
    suffix: str = "",
    write_at: datetime | None = None,
) -> str | None:
    results_dir = Path(host.seek_config.result_folder)
    results_dir.mkdir(parents=True, exist_ok=True)
    out = results_dir / session_artifact_filename(
        write_at or datetime.now(),
        suffix=suffix,
        run_label=run_label,
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
            "exit_kind": (
                str(result.exit_kind) if result.exit_kind is not None else None
            ),
            "config": host.session_trace_config(),
        },
        "probes": probes,
    }
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2))
        logger.info(f"eddy_seek: session trace saved to {path}")
        return path
    except OSError as exc:
        logger.warning(f"eddy_seek: failed to write session trace to {path}: {exc}")
        return None
