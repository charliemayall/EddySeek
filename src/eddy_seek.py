"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

eddy_seek.py  -  Klipper extra for nozzle alignment via LDC1612.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import fields
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ._eddy_seek.accuracy_test import run_accuracy_test
from ._eddy_seek.common import Offset, Position
from ._eddy_seek.config import SeekConfig, load_seek_config
from ._eddy_seek.kconsole import KConsole, console_for_gcmd
from ._eddy_seek.movement.guard import clear_gcode_offset_xy
from ._eddy_seek.session import SeekHost, SeekSession
from ._eddy_seek.strategy import strategy_for
from ._eddy_seek.tool_align import align_all_tools, align_tool_number
from ._eddy_seek.tools import ToolAlignConfig, apply_tool_offset

if TYPE_CHECKING:
    from klippy.extras.configfile import ConfigWrapper
    from klippy.extras.ldc1612 import LDC1612
    from klippy.gcode import GCodeCommand

try:
    from .ldc1612 import LDC1612  # pyright: ignore[reportMissingImports]
except (ModuleNotFoundError, ImportError):
    # Not on host machine, use mock class
    LDC1612 = None  # pyright: ignore[assignment,misc]

logger = logging.getLogger(__name__)

_STATUS_SMOOTH_SAMPLES = 20
_QUERY_RATE_DWELL = 0.4


def _sample_rate_from_count(*, count: int, duration_s: float) -> float | None:
    if count <= 0 or duration_s <= 0.0:
        return None
    return count / duration_s


class EddySeek(SeekHost):
    def __init__(self, config: ConfigWrapper) -> None:
        self.printer = config.get_printer()
        self.seek_config = load_seek_config(config)
        self._tools = ToolAlignConfig(config)
        self._status_samples: list[float] = []
        self._capture_buf: list[float] = []
        self._capture_count: int = 0
        self._capturing: bool = False
        self._total_samples: int = 0
        self._last_freq: float = 0.0
        self._tool0_center: Position | None = None
        self._sensor = self._load_ldc1612(config)
        self._stream_refs = 0
        self._batch_client_added = False
        self._sample_rate_hz: float | None = None
        self.console: KConsole | None = None
        gcode = self.printer.lookup_object("gcode")
        gcode.register_command(
            "EDDY_SEEK_QUERY",
            self.cmd_EDDY_SEEK_QUERY,
            desc="Print current LDC1612 frequency to console",
        )
        gcode.register_command(
            "EDDY_SEEK_RESET",
            self.cmd_EDDY_SEEK_RESET,
            desc="Clear capture buffer before a new alignment measurement",
        )
        gcode.register_command(
            "EDDY_SEEK_SET",
            self.cmd_EDDY_SEEK_SET,
            desc="Temporarily override seek settings until Klipper restart",
        )
        gcode.register_command(
            "EDDY_SEEK_START",
            self.cmd_EDDY_SEEK_START,
            desc="Run XY seek search to find the eddy sensor centre",
        )
        gcode.register_command(
            "EDDY_SEEK_ACCURACY",
            self.cmd_EDDY_SEEK_ACCURACY,
            desc="Run seek REPEATS times and report repeatability statistics",
        )
        gcode.register_command(
            "EDDY_SEEK_TOOL",
            self.cmd_EDDY_SEEK_TOOL,
            desc="Align a single tool on the eddy sensor",
        )
        gcode.register_command(
            "EDDY_SEEK_TOOLS",
            self.cmd_EDDY_SEEK_TOOLS,
            desc="Align all tools against tool 0 on the eddy sensor",
        )
        gcode.register_command(
            "EDDY_SEEK_APPLY_OFFSET",
            self.cmd_EDDY_SEEK_APPLY_OFFSET,
            desc="Apply saved XY offset for a tool via SET_GCODE_OFFSET",
        )
        if self.seek_config.debug:
            gcode.register_command(
                "ES_DEBUG_CONSOLE",
                self.cmd_DEBUG_CONSOLE,
                desc="test console_for_gcmd",
            )

        self.printer.add_object("eddy_seek", self)
        self.printer.register_event_handler("klippy:disconnect", self._on_disconnect)
        logger.info(
            f"eddy_seek: initialised (sensor={self._sensor.name!r}) {self.seek_config.to_dict()}"
        )

    def add_sensor_client(self, callback) -> None:
        self._sensor.add_client(callback)

    def refresh_console(self, gcmd: GCodeCommand) -> KConsole:
        self.console = console_for_gcmd(gcmd, self.seek_config)
        return self.console

    def _stop_sensor_stream(self) -> None:
        """Drop stream interest; batch client unregisters on the next bulk tick."""
        if self._stream_refs == 0 and not self._batch_client_added:
            return
        self._stream_refs = 0
        if self._batch_client_added:
            self._handle_batch({"data": []})
        logger.info(f"eddy_seek: stopped sensor stream on {self._sensor.name!r}")

    def _on_disconnect(self) -> None:
        """Release LDC1612 sampling before Klipper restart or shutdown."""
        self._capturing = False
        self._stop_sensor_stream()
        del self._sensor
        logger.info("eddy_seek: klippy disconnect")

    @contextmanager
    def acquire_sensor_stream(self) -> Iterator[None]:
        """Start LDC1612 bulk sampling while a seek session or query needs data."""
        self._stream_refs += 1
        if self._stream_refs == 1 and not self._batch_client_added:
            self._sensor.add_client(self._handle_batch)
            self._batch_client_added = True
            logger.info(f"eddy_seek: started sensor stream on {self._sensor.name!r}")
        try:
            yield
        finally:
            self._stream_refs -= 1
            if self._stream_refs == 0:
                self._stop_sensor_stream()

    @property
    def capture_count(self) -> int:
        return self._capture_count

    def peek_capture_samples(self) -> list[float]:
        return list(self._capture_buf)

    def session_trace_config(self) -> dict[str, Any]:
        return {
            "seek": self.seek_config.to_dict(),
            "tools": {
                "tool_count": self._tools.tool_count,
                "tool_prefix": self._tools.tool_prefix,
                "load_tool_macro_prefix": self._tools.load_tool_macro,
                "tools": [tool.to_dict() for tool in self._tools.tools],
            },
            "sensor_name": self._sensor.name,
        }

    @staticmethod
    def _load_ldc1612(config: ConfigWrapper) -> LDC1612:
        sensor_type = config.get("sensor_type", "").strip().lower()
        if sensor_type != "ldc1612":
            raise config.error(
                f"eddy_seek: sensor_type must be 'ldc1612' (got {sensor_type!r})"
            )
        return LDC1612(config)  # pyright: ignore[reportUnknownReturnType]

    def _handle_batch(self, msg: dict) -> bool:
        if self._stream_refs <= 0:  # No one is listening, don't process msg
            self._batch_client_added = False
            return False
        data = msg.get("data", [])
        if not data:
            return True
        for pt in data:
            f = float(pt[1])
            self._last_freq = f
            self._total_samples += 1

            self._status_samples.append(f)
            if len(self._status_samples) > _STATUS_SMOOTH_SAMPLES:
                self._status_samples.pop(0)

            if self._capturing:
                self._capture_buf.append(f)
                self._capture_count += 1
        return True

    def reset_capture(self) -> None:
        logger.info(
            f"eddy_seek: capture started (discarded {self._capture_count} samples)"
        )
        self._capture_buf = []
        self._capture_count = 0
        self._capturing = True

    def get_capture_mean(self, min_samples: int = 5) -> float | None:
        buf = list(self._capture_buf)
        self._capturing = False
        if len(buf) < min_samples:
            logger.info(
                f"eddy_seek: capture too few samples ({len(buf)} < {min_samples})"
            )
            return None
        mean = sum(buf) / len(buf)
        logger.info(f"eddy_seek: capture mean={mean:.2f} Hz from {len(buf)} samples")
        return mean

    def get_status(self, eventtime: float | None = None) -> dict:
        """called by klipper, do not remove parameters"""
        smooth_mean = (
            sum(self._status_samples) / len(self._status_samples)
            if self._status_samples
            else 0.0
        )
        capture_mean = (
            sum(self._capture_buf) / len(self._capture_buf)
            if self._capture_buf
            else 0.0
        )
        tools = {
            self._tools.section_name(tool.tool_number): tool.to_dict()
            for tool in self._tools.tools
        }
        return {
            "last_freq": round(self._last_freq, 2),
            "smooth_mean": round(smooth_mean, 2),
            "capture_mean": round(capture_mean, 2),
            "capture_count": self._capture_count,
            "total_samples": self._total_samples,
            "sample_rate_hz": (
                round(self._sample_rate_hz, 1)
                if self._sample_rate_hz is not None
                else None
            ),
            "tools": tools,
        }

    def cmd_EDDY_SEEK_QUERY(self, gcmd: GCodeCommand) -> None:
        console = self.refresh_console(gcmd)
        with self.acquire_sensor_stream():
            toolhead = self.printer.lookup_object("toolhead")
            before = self._total_samples
            self.reset_capture()
            toolhead.dwell(_QUERY_RATE_DWELL)
            toolhead.wait_moves()
            gained = self._total_samples - before
            measured = _sample_rate_from_count(
                count=gained, duration_s=_QUERY_RATE_DWELL
            )
            self._sample_rate_hz = round(measured, 1) if measured is not None else None
            self._capturing = False
            status = self.get_status(0)
            rate = status["sample_rate_hz"]
            rate_text = f"{rate:.0f} Hz" if rate is not None else "n/a"
            if gained:
                console.info(
                    f"Sensor {status['smooth_mean']:.1f} Hz "
                    f"(capture: {status['capture_mean']:.1f} Hz, "
                    f"{status['capture_count']} samples, sample_rate: {rate_text})"
                )
            else:
                raise gcmd.error(
                    "No samples gained during query, check sensor connection"
                )

    def cmd_EDDY_SEEK_RESET(self, gcmd: GCodeCommand) -> None:
        console = self.refresh_console(gcmd)
        prev_count = self._capture_count
        self.reset_capture()
        console.info(f"Capture buffer reset (discarded {prev_count} samples)")

    def cmd_EDDY_SEEK_SET(self, gcmd: GCodeCommand) -> None:
        console = self.refresh_console(gcmd)
        changes = self.seek_config.apply_runtime_set(gcmd)
        if not changes:
            logger.info("eddy_seek: EDDY_SEEK_SET query (no changes)")
            console.info(self.seek_config.format_seek_config())
            strategies = "|".join(
                sorted(
                    next(
                        f for f in fields(SeekConfig) if f.name == "strategy"
                    ).metadata["enum"]
                )
            )
            console.info(
                f"Pass STRATEGY={strategies}, TOLERANCE=…, etc. "
                "to override config values (overrides values until restart)"
            )
            return

        logger.info(f"eddy_seek: EDDY_SEEK_SET applied: {', '.join(changes)}")
        console.info(f"Updated {', '.join(changes)}")

    def cmd_EDDY_SEEK_START(self, gcmd: GCodeCommand) -> None:
        logger.info("eddy_seek: EDDY_SEEK_START")
        console = self.refresh_console(gcmd)
        console.entry("Seeking nozzle centre…")
        write_at = datetime.now()
        run_id = uuid.uuid4().hex[:8]
        SeekSession(
            self,
            run_id=run_id,
            run_label="start",
            artifact_label="start",
            artifact_write_at=write_at,
        ).run(gcmd, strategy_for(self.seek_config.strategy))

    def cmd_EDDY_SEEK_TOOL(self, gcmd: GCodeCommand) -> None:
        tool_number = gcmd.get_int("TOOL", -1, minval=0)
        if tool_number == -1:
            raise gcmd.error("TOOL=<number> is required for EDDY_SEEK_TOOL")
        repeats = gcmd.get_int("REPEATS", 1, minval=1, maxval=50)
        logger.info(f"eddy_seek: EDDY_SEEK_TOOL tool={tool_number} repeats={repeats}")
        console = self.refresh_console(gcmd)
        console.entry(f"Aligning tool {tool_number}…")
        write_at = datetime.now()
        run_id = uuid.uuid4().hex[:8]
        try:
            tool, tool0_center, error = align_tool_number(
                self,
                self._tools,
                gcmd,
                tool_number,
                self._tool0_center,
                console=console,
                run_id=run_id,
                run_label="tool",
                artifact_write_at=write_at,
                repeats=repeats,
            )
            if error is not None:
                console.error(f"Tool {tool_number} alignment failed: {error}")
                return
            if tool0_center is not None:
                self._tool0_center = tool0_center
            if tool is not None:
                self._tools.update_tool(tool)
                if tool_number != 0:
                    self._tools.save_tool(tool)
        finally:
            clear_gcode_offset_xy(self.printer)

    def cmd_EDDY_SEEK_TOOLS(self, gcmd: GCodeCommand) -> None:
        self.refresh_console(gcmd)
        tool_count = gcmd.get_int("TOOLS", self._tools.tool_count, minval=1)
        repeats = gcmd.get_int("REPEATS", 1, minval=1, maxval=50)
        logger.info(f"eddy_seek: EDDY_SEEK_TOOLS tools={tool_count} repeats={repeats}")
        result = align_all_tools(self, self._tools, gcmd, tool_count, repeats=repeats)
        if result.tool0_center is not None:
            self._tool0_center = result.tool0_center
        if result.status == "ok":
            self._tools.save_tools()

    def cmd_EDDY_SEEK_APPLY_OFFSET(self, gcmd: GCodeCommand) -> None:
        tool_number = gcmd.get_int("TOOL", 0, minval=0)
        logger.info(f"eddy_seek: EDDY_SEEK_APPLY_OFFSET tool={tool_number}")
        console = self.refresh_console(gcmd)
        try:
            tool = apply_tool_offset(self._tools, self.printer, tool_number)
        except ValueError as exc:
            console.warn(str(exc))
            return
        eff = tool.effective_offset
        if tool.manual_offset != Offset.zero():
            console.info(
                f"Tool {tool_number} offset applied (manual_adjust + calibrated) - X={eff.x:.4f} Y={eff.y:.4f} mm"
            )
        else:
            console.info(
                f"Tool {tool_number} offset applied (calibrated) - X={eff.x:.4f} Y={eff.y:.4f} mm"
            )

    def cmd_EDDY_SEEK_ACCURACY(self, gcmd: GCodeCommand) -> None:
        repeats = gcmd.get_int("REPEATS", 3, minval=2, maxval=50)
        mock_enabled = bool(gcmd.get_int("MOCK", 0, minval=0, maxval=1))
        logger.info(
            f"eddy_seek: EDDY_SEEK_ACCURACY repeats={repeats} mock={mock_enabled}"
        )
        console = self.refresh_console(gcmd)
        console.entry(f"Running {repeats} seek repeat(s) from current position")
        run_accuracy_test(
            self,
            gcmd,
            console=console,
            repeats=repeats,
            mock_enabled=mock_enabled,
        )

    def cmd_DEBUG_CONSOLE(self, gcmd: GCodeCommand) -> None:
        console = self.refresh_console(gcmd)
        console.entry("Debug console")
        console.info("This is a test")
        console.warn("This is a warning")
        console.error("This is an error")
        console.detail("This is a detail")
        console.exit("Debug console complete")


def load_config(config: ConfigWrapper):
    return EddySeek(config)
