"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

eddy_seek.py  -  Klipper extra for nozzle alignment via LDC1612.

Uses ``extras.ldc1612.LDC1612`` for the sensor data stream.
Uses ``_eddy_seek`` for XY search and multi-tool alignment.

printer.cfg example
-------------------
    [eddy_seek]
    sensor_type: ldc1612
    i2c_address: 42
    i2c_mcu: mcu
    i2c_bus: i2c1
    sensor_x: 150.0
    sensor_y: 150.0
    tool_count: 4
    tool_prefix: T
    load_tool_macro_prefix: T
    window_size: 20
    max_jog_x: 5.0
    max_jog_y: 5.0
    tolerance: 0.1
    dwell_time: 0.5
    jog_speed: 600
    search_for: max
    strategy: sweep_centroid
    grid_step_x: 2.5
    grid_step_y: 2.5
    max_iter: 10
    max_passes: 6
    save_session_trace: False
    sweep_coarse_speed: 20
    sweep_fine_speed: 10

G-code: EDDY_SEEK_QUERY, EDDY_SEEK_RESET, EDDY_SEEK_SET, EDDY_SEEK_START,
EDDY_SEEK_ACCURACY, EDDY_SEEK_TOOL, EDDY_SEEK_TOOLS, EDDY_SEEK_APPLY_OFFSET
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from klippy.extras.configfile import ConfigWrapper
    from klippy.extras.ldc1612 import LDC1612

try:
    from .ldc1612 import LDC1612
    from ._eddy_seek.common import Position
    from ._eddy_seek.config import load_seek_config
    from ._eddy_seek.tools import ToolAlignConfig, apply_tool_offset
    from ._eddy_seek.strategy import strategy_for
    from ._eddy_seek.plotting import PlotWriter
    from ._eddy_seek.session import (
        SeekHost,
        SeekSession,
        report_accuracy_stats,
    )
    from ._eddy_seek.tool_align import align_all_tools, align_tool_number
except ImportError:
    from _eddy_seek.common import Position  # type: ignore[no-redef]
    from _eddy_seek.config import load_seek_config  # type: ignore[no-redef]
    from _eddy_seek.tools import ToolAlignConfig, apply_tool_offset  # type: ignore[no-redef]
    from _eddy_seek.strategy import strategy_for  # type: ignore[no-redef]
    from _eddy_seek.plotting import PlotWriter  # type: ignore[no-redef]
    from _eddy_seek.session import (  # type: ignore[no-redef]
        SeekHost,
        SeekSession,
        report_accuracy_stats,
    )
    from _eddy_seek.tool_align import (  # type: ignore[no-redef]
        align_all_tools,
        align_tool_number,
    )

logger = logging.getLogger(__name__)


class EddySeek(SeekHost):
    def __init__(self, config: ConfigWrapper) -> None:
        self.printer = config.get_printer()
        self.seek_config = load_seek_config(config)
        self._tools = ToolAlignConfig(config)
        self._window: list[float] = []
        self._capture_buf: list[float] = []
        self._capture_count: int = 0
        self._capturing: bool = False
        self._total_samples: int = 0
        self._last_freq: float = 0.0
        self._tool0_center: Position | None = None
        self._sensor = self._load_ldc1612(config)
        self._stream_refs = 0
        self._stream_active = False
        self._batch_client_added = False
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

        self.printer.add_object("eddy_seek", self)
        logger.info(
            "eddy_seek: initialised (sensor=%r window_size=%d)",
            self._sensor.name,
            self.seek_config.window_size,
        )

    def add_sensor_client(self, callback) -> None:
        self._sensor.add_client(callback)  # type: ignore[arg-type]

    def acquire_sensor_stream(self) -> None:
        """Start LDC1612 bulk sampling while a seek session or query needs data."""
        self._stream_refs += 1
        if self._stream_refs == 1:
            self._stream_active = True
            if not self._batch_client_added:
                self._sensor.add_client(self._handle_batch)  # type: ignore[arg-type]
                self._batch_client_added = True
                logger.debug(
                    "eddy_seek: started sensor stream on %r", self._sensor.name
                )

    def release_sensor_stream(self) -> None:
        """Stop bulk sampling once no seek session or query holds a stream ref."""
        if self._stream_refs <= 0:
            return
        self._stream_refs -= 1
        if self._stream_refs == 0:
            self._stream_active = False
            logger.debug("eddy_seek: stopping sensor stream on %r", self._sensor.name)

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
                "eddy_seek: sensor_type must be 'ldc1612' (got %r)" % (sensor_type,)
            )
        return LDC1612(config)  # type: ignore[reportUnknownReturnType]

    def _handle_batch(self, msg: dict) -> bool:
        if not self._stream_active:
            self._batch_client_added = False
            return False
        data = msg.get("data", [])
        if not data:
            return True
        for pt in data:
            f = float(pt[1])
            self._last_freq = f
            self._total_samples += 1

            self._window.append(f)
            if len(self._window) > self.seek_config.window_size:
                self._window.pop(0)

            if self._capturing:
                self._capture_buf.append(f)
                self._capture_count += 1
        return True

    def reset_capture(self) -> None:
        logger.debug(
            "eddy_seek: capture started (discarded %d samples)", self._capture_count
        )
        self._capture_buf = []
        self._capture_count = 0
        self._capturing = True

    def get_capture_mean(self, min_samples: int = 5) -> float | None:
        buf = list(self._capture_buf)
        self._capturing = False
        if len(buf) < min_samples:
            logger.debug(
                "eddy_seek: capture too few samples (%d < %d)",
                len(buf),
                min_samples,
            )
            return None
        mean = sum(buf) / len(buf)
        logger.debug(
            "eddy_seek: capture mean=%.2f Hz from %d samples",
            mean,
            len(buf),
        )
        return mean

    def get_status(self, eventtime: float) -> dict:
        # Used by klipper, do not remove parameter eventtime
        window_mean = sum(self._window) / len(self._window) if self._window else 0.0
        capture_mean = (
            sum(self._capture_buf) / len(self._capture_buf)
            if self._capture_buf
            else 0.0
        )
        tools = {
            self._tools.section_name(tool.tool_number): {
                "offset_x": round(tool.offset.x, 4),
                "offset_y": round(tool.offset.y, 4),
                "manual_adjust_x": round(tool.manual_offset.x, 4),
                "manual_adjust_y": round(tool.manual_offset.y, 4),
                "effective_offset_x": round(tool.effective_offset.x, 4),
                "effective_offset_y": round(tool.effective_offset.y, 4),
                "is_calibrated": tool.is_calibrated,
            }
            for tool in self._tools.tools
        }
        return {
            "last_freq": round(self._last_freq, 2),
            "window_mean": round(window_mean, 2),
            "capture_mean": round(capture_mean, 2),
            "capture_count": self._capture_count,
            "total_samples": self._total_samples,
            "tools": tools,
        }

    def cmd_EDDY_SEEK_QUERY(self, gcmd) -> None:
        self.acquire_sensor_stream()
        try:
            toolhead = self.printer.lookup_object("toolhead")
            toolhead.dwell(0.2)
            toolhead.wait_moves()
            status = self.get_status(0)
            gcmd.respond_info(
                "EDDY_SEEK: last={last_freq:.1f} Hz  "
                "window_mean={window_mean:.1f} Hz  "
                "capture_mean={capture_mean:.1f} Hz  "
                "capture_count={capture_count}  "
                "total={total_samples}".format(**status)
            )
        finally:
            self.release_sensor_stream()

    def cmd_EDDY_SEEK_RESET(self, gcmd) -> None:
        prev_count = self._capture_count
        self.reset_capture()
        logger.info(
            "eddy_seek: capture buffer reset (discarded %d samples)",
            prev_count,
        )
        gcmd.respond_info("EDDY_SEEK: capture buffer reset")

    def cmd_EDDY_SEEK_SET(self, gcmd) -> None:
        changes = self.seek_config.apply_runtime_set(gcmd)
        if not changes:
            logger.debug("eddy_seek: EDDY_SEEK_SET query (no changes)")
            gcmd.respond_info("EDDY_SEEK_SET: " + self.seek_config.format_seek_config())
            gcmd.respond_info(
                "EDDY_SEEK_SET: pass STRATEGY=ternary|centroid|sweep_centroid, TOLERANCE=…, etc. to override config values "
                "(overrides values until restart)"
            )
            return

        logger.debug("eddy_seek: EDDY_SEEK_SET applied: %s", ", ".join(changes))
        gcmd.respond_info("EDDY_SEEK_SET: " + ", ".join(changes))

    def cmd_EDDY_SEEK_START(self, gcmd) -> None:
        logger.debug("eddy_seek: EDDY_SEEK_START")
        SeekSession(self).run(gcmd, strategy_for(self.seek_config.strategy))

    def cmd_EDDY_SEEK_TOOL(self, gcmd) -> None:
        tool_number = gcmd.get_int("TOOL", 0, minval=0)
        logger.debug("eddy_seek: EDDY_SEEK_TOOL tool=%d", tool_number)
        gcode = self.printer.lookup_object("gcode")
        gcode.run_script_from_command("SAVE_GCODE_STATE NAME=EDDY_SEEK_TOOL")
        try:
            tool, tool0_center, error = align_tool_number(
                self,
                self._tools,
                gcmd,
                tool_number,
                self._tool0_center,
                label="EDDY_SEEK_TOOL",
            )
            if error is not None:
                gcmd.respond_info(f"EDDY_SEEK_TOOL ERROR: {error}")
                return
            if tool0_center is not None:
                self._tool0_center = tool0_center
            if tool is not None:
                self._tools.update_tool(tool)
                if tool_number == 0:
                    gcmd.respond_info(
                        "Tool 0 center found, you can now align other tools"
                    )
                else:
                    self._tools.save_tool(tool)
                    gcmd.respond_info(
                        "EDDY_SEEK_TOOL: offset staged - run SAVE_CONFIG to persist"
                    )
        finally:
            gcode.run_script_from_command(
                "RESTORE_GCODE_STATE NAME=EDDY_SEEK_TOOL MOVE=1"
            )

    def cmd_EDDY_SEEK_TOOLS(self, gcmd) -> None:
        tool_count = gcmd.get_int("TOOLS", self._tools.tool_count, minval=1)
        logger.debug("eddy_seek: EDDY_SEEK_TOOLS tools=%d", tool_count)
        result = align_all_tools(self, self._tools, gcmd, tool_count)
        if result.tool0_center is not None:
            self._tool0_center = result.tool0_center
        if result.status == "ok":
            self._tools.save_tools()
            gcmd.respond_info(
                "EDDY_SEEK_TOOLS: offsets staged - run SAVE_CONFIG to persist"
            )

    def cmd_EDDY_SEEK_APPLY_OFFSET(self, gcmd) -> None:
        tool_number = gcmd.get_int("TOOL", 0, minval=0)
        logger.debug("eddy_seek: EDDY_SEEK_APPLY_OFFSET tool=%d", tool_number)
        try:
            tool = apply_tool_offset(self._tools, self.printer, tool_number)
        except ValueError as exc:
            raise gcmd.error(f"EDDY_SEEK_APPLY_OFFSET: {exc}") from exc
        eff = tool.effective_offset
        gcmd.respond_info(
            f"EDDY_SEEK_APPLY_OFFSET: tool {tool_number} "
            f"X={eff.x:+.4f} mm  Y={eff.y:+.4f} mm"
        )

    def cmd_EDDY_SEEK_ACCURACY(self, gcmd) -> None:
        repeats = gcmd.get_int("REPEATS", 3, minval=2, maxval=50)
        logger.debug("eddy_seek: EDDY_SEEK_ACCURACY repeats=%d", repeats)
        gcmd.respond_info(
            f"EDDY_SEEK_ACCURACY: running {repeats} seek repeat(s) "
            f"from current position"
        )

        gcode = self.printer.lookup_object("gcode")
        gcode.run_script_from_command("SAVE_GCODE_STATE NAME=EDDY_SEEK_ACCURACY")

        cfg = self.seek_config
        accuracy_id = str(uuid.uuid4())
        plotter: PlotWriter | None = None
        if cfg.save_plots:
            plotter = PlotWriter(Path(cfg.result_folder), accuracy_id)

        offsets: list[Position] = []
        try:
            for repeat in range(1, repeats + 1):
                if repeat > 1:
                    gcode.run_script_from_command(
                        "RESTORE_GCODE_STATE NAME=EDDY_SEEK_ACCURACY MOVE=1"
                    )

                gcmd.respond_info(f"EDDY_SEEK_ACCURACY: repeat {repeat}/{repeats}")
                session = SeekSession(self)
                result = session.run(gcmd, strategy_for(self.seek_config.strategy))

                if result.status != "ok" or result.offset is None:
                    gcmd.respond_info(
                        f"EDDY_SEEK_ACCURACY: repeat {repeat} failed"
                        + (f" - {result.error_message}" if result.error_message else "")
                    )
                    break

                offsets.append(result.offset)
                if plotter is not None:
                    plotter.record_accuracy_repeat(
                        repeat_num=repeat,
                        offset=result.offset,
                        session_plot_path=result.plot_path,
                    )
                gcmd.respond_info(
                    f"EDDY_SEEK_ACCURACY: repeat {repeat} result "
                    f"X={result.offset.x:+.4f} mm  Y={result.offset.y:+.4f} mm"
                )

            if len(offsets) < 2:
                gcmd.respond_info(
                    "EDDY_SEEK_ACCURACY: need at least 2 successful repeats "
                    "for deviation report"
                )
                return

            report_accuracy_stats(gcmd, offsets)
            if plotter is not None:
                accuracy_plot_path = plotter.finalize_accuracy()
                if accuracy_plot_path is not None:
                    gcmd.respond_info(
                        f"EDDY_SEEK_ACCURACY: debug plot saved to {accuracy_plot_path}"
                    )
        finally:
            gcode.run_script_from_command(
                "RESTORE_GCODE_STATE NAME=EDDY_SEEK_ACCURACY MOVE=1"
            )


def load_config(config: ConfigWrapper):
    return EddySeek(config)
