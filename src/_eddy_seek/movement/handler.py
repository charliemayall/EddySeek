"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Session-relative motion: discrete dwell probes and continuous sweep capture.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..common import Axis, Offset, Position

if TYPE_CHECKING:
    from klippy.klippy import Printer
    from klippy.toolhead import ToolHead

    from ..config import SeekConfig
    from ..session import SeekHost


logger = logging.getLogger(__name__)

_LDC1612_BULK_HZ = 400.0  # batch bulk client nominal rate


def move_to_xy(
    toolhead: ToolHead,
    position: Position,
    feedrate: float,
    *,
    wait: bool = False,
) -> None:
    """Queue a move to absolute machine XY (mm); feedrate in mm/min."""
    logger.debug(
        f"eddy_seek: move_to ({position.x:.4f}, {position.y:.4f}) feedrate={feedrate:.1f}"
    )
    toolhead.manual_move([position.x, position.y], feedrate / 60.0)
    if wait:
        toolhead.wait_moves()


def get_clamped_speed_for_min_samples_over_span(
    *,
    requested_mm_min: float,
    span_mm: float,
    min_samples: int,
) -> float:
    """Cap feedrate so an in-range traverse can yield ``min_samples`` at ``bulk_rate_hz``."""
    if span_mm <= 0.0 or min_samples <= 0:
        return requested_mm_min
    cap = span_mm * _LDC1612_BULK_HZ * 60.0 / min_samples
    result_speed_mm_min = min(requested_mm_min, cap)
    if result_speed_mm_min != requested_mm_min:
        logger.debug(
            f"eddy_seek: speed clamped {requested_mm_min:.1f} -> {result_speed_mm_min:.1f} mm/min "
            f"(span={span_mm:.3f} mm, min_samples={min_samples}, bulk_rate_hz={_LDC1612_BULK_HZ:.0f} Hz)"
        )
    return result_speed_mm_min


@dataclass(frozen=True, slots=True)
class MotionSample:
    """One sensor reading correlated to session-relative XY."""

    offset: Offset
    freq: float
    print_time: float


class _SessionMotionBase:
    """Track session origin and session-relative nozzle offset."""

    def __init__(self, printer: Printer, origin: Position, jog_speed: float) -> None:
        self._printer = printer
        self._origin = origin
        self._offset = Offset.zero()
        self._position = Offset.zero()
        self._jog_speed = jog_speed

    @property
    def origin(self) -> Position:
        return self._origin

    @property
    def position(self) -> Offset:
        return self._position

    def sync_offset(self, offset: Offset) -> None:
        self._offset = offset
        self._position = offset

    def jog_to(self, offset: Offset) -> None:
        """Queue a jog to session-relative offset."""
        delta = offset - self._offset
        if delta.x == 0.0 and delta.y == 0.0:
            return

        logger.debug(
            f"eddy_seek: jog delta=({delta.x:.4f}, {delta.y:.4f}) "
            f"-> offset=({offset.x:.4f}, {offset.y:.4f})"
        )
        toolhead = self._printer.lookup_object("toolhead")
        pos = toolhead.get_position()
        toolhead.manual_move(
            [pos[0] + delta.x, pos[1] + delta.y],
            self._jog_speed / 60.0,
        )
        self._offset = offset
        self._position = offset


def lookup_toolhead_position(toolhead: ToolHead, print_time: float) -> Position:
    kin = toolhead.get_kinematics()
    kin_spos = {
        s.get_name(): s.mcu_to_commanded_position(s.get_past_mcu_position(print_time))
        for s in kin.get_steppers()  # type: ignore[union-attr]
    }
    pos = kin.calc_position(kin_spos)  # type: ignore[union-attr]
    return Position.from_pair(pos)


def align_measurements(
    toolhead: ToolHead,
    origin: Position,
    measures: list[tuple[float, float]],
) -> list[MotionSample]:
    """Map ``(print_time, freq)`` pairs to session-relative ``MotionSample`` rows."""
    return [
        MotionSample(
            offset=lookup_toolhead_position(toolhead, time) - origin,
            freq=freq,
            print_time=time,
        )
        for time, freq in measures
    ]


def axis_profile(
    samples: list[MotionSample],
    axis: Axis,
    lo: float | None = None,
    hi: float | None = None,
) -> list[tuple[float, float]]:
    """Project samples onto one axis, optionally clipping to ``[lo, hi]``."""
    if axis is Axis.X:
        points = [(s.offset.x, s.freq) for s in samples]
    else:
        points = [(s.offset.y, s.freq) for s in samples]
    if lo is not None and hi is not None:
        if lo > hi:
            lo, hi = hi, lo
        points = [(coord, freq) for coord, freq in points if lo <= coord <= hi]
    return points


class MotionHandler(_SessionMotionBase):
    """

    Handle motion <---> sensor capture for continuous and discrete motion.

    Discrete dwell probes and continuous LDC1612 sweep capture.

    """

    _MAX_SCV = 10.0

    def __init__(
        self,
        printer: Printer,
        host: SeekHost,
        config: SeekConfig,
        origin: Position,
        trace_cb: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(printer, origin, config.jog_speed)
        self._host = host
        self._config = config
        self._trace_cb = trace_cb
        self._sensor_messages: list[dict] = []
        self._capture_windows: list[tuple[float, float]] = []
        self._results: list[list[MotionSample]] = []
        self._active = False
        self._need_stop = False
        self._client_registered = False
        self._th: ToolHead | None = None
        self._last_move_end: Offset | None = None

    @property
    def th(self) -> ToolHead:
        if self._th is None:
            return self._printer.lookup_object("toolhead")
        return self._th

    def jog(self, offset: Offset) -> None:
        """Jog to session-relative offset and wait for the move to finish."""
        self.jog_to(offset)
        self.th.wait_moves()

    def move_to(self, position: Position) -> None:
        """Queue a move to absolute machine XY (mm)."""
        offset = position - self._origin
        if offset.x == self._offset.x and offset.y == self._offset.y:
            return

        move_to_xy(self.th, position, self._jog_speed)
        self._offset = offset
        self._position = offset

    def sample(self, offset: Offset) -> float:
        """Move to offset, dwell, and return mean LDC1612 frequency."""
        self.jog(offset)
        toolhead = self.th

        self._host.reset_capture()
        toolhead.dwell(self._config.dwell_time)
        toolhead.wait_moves()

        mean = self._host.get_capture_mean(min_samples=3)
        if mean is None:
            logger.debug(
                f"eddy_seek: measure_at ({offset.x:.4f}, {offset.y:.4f}) failed "
                f"({self._host.capture_count} samples)"
            )
            raise RuntimeError(
                f"eddy_seek: no samples at offset "
                f"({offset.x:.3f}, {offset.y:.3f}) mm after "
                f"{self._config.dwell_time:.2f} s dwell. "
                "Check sensor connection, dwell_time, and i2c settings."
            )
        logger.debug(
            f"eddy_seek: measure_at ({offset.x:.4f}, {offset.y:.4f}) -> {mean:.2f} Hz "
            f"({self._host.capture_count} samples)"
        )
        if self._trace_cb is not None:
            self._trace_cb(
                {
                    "x": offset.x,
                    "y": offset.y,
                    "mean_hz": mean,
                    "samples_hz": self._host.peek_capture_samples(),
                }
            )
        return mean

    def begin(self, origin: Position) -> None:
        self._origin = origin
        self._sensor_messages = []
        self._capture_windows = []
        self._results = []
        self._need_stop = False
        self._active = True
        self._th = self._printer.lookup_object("toolhead")
        if not self._client_registered:
            self._host.add_sensor_client(self._on_sensor_message)
            self._client_registered = True

    def close(self) -> None:
        self._need_stop = True
        self._active = False
        self._th = None

    def capture_leg(self, line_start: Offset, line_end: Offset, speed: float) -> None:
        if not self._active:
            raise RuntimeError("eddy_seek: continuous motion not active")
        if self._last_move_end is None or line_start != self._last_move_end:
            # Start so don't clamp speed
            self._manual_move(line_start, speed)
        clamped_speed = get_clamped_speed_for_min_samples_over_span(
            requested_mm_min=speed,
            span_mm=line_start.distance_to(line_end),
            min_samples=self._config.min_sweep_samples,
        )

        capture_start_t = self.th.get_last_move_time()
        self._manual_move(line_end, clamped_speed)
        self._position = line_end
        self._register_capture_window(
            capture_start_t
        )  # make cb for move end (start,end)->(...)
        self._last_move_end = line_end

    def run_capture_legs(
        self,
        legs: Sequence[tuple[Offset, Offset]],
        speed: float,
    ) -> None:
        for line_start, line_end in legs:
            self.capture_leg(line_start, line_end, speed)
        self.th.wait_moves()
        self.th.get_last_move_time()

    def _manual_move(self, offset: Offset, speed: float) -> None:
        machine = self._origin + offset
        speed_mm_s = speed / 60.0
        self.th.limit_next_junction_speed(
            min(speed_mm_s, self._MAX_SCV)
        )  # jerky cornering leeds to dwell like behaviour
        self.th.manual_move([machine.x, machine.y], speed_mm_s)

    def _register_capture_window(self, capture_start: float) -> None:
        def _end_cb(end_time: float) -> None:
            self._enqueue_capture_window(capture_start, end_time)

        self.th.register_lookahead_callback(_end_cb)

    def _on_sensor_message(self, msg: dict) -> bool:
        if self._need_stop:
            self._sensor_messages.clear()
            return False
        if self._active:
            self._sensor_messages.append(msg)
            self._process_buffered_data()
        return True

    def _extract_freq_window(
        self, start_time: float, end_time: float
    ) -> list[tuple[float, float]]:
        """Extract LDC1612 (print_time, freq) samples in ``[start_time, end_time]``."""
        measures: list[tuple[float, float]] = []
        msg_num = 0
        discard_msgs = 0
        while msg_num < len(self._sensor_messages):
            msg = self._sensor_messages[msg_num]
            msg_num += 1
            data = msg.get("data", [])
            if not data:
                continue
            if data[0][0] > end_time:
                break
            if data[-1][0] < start_time:
                discard_msgs = msg_num
                continue
            for measure in data:
                time = float(measure[0])
                if time < start_time:
                    continue
                if time > end_time:
                    break
                measures.append((time, float(measure[1])))
        if discard_msgs:
            del self._sensor_messages[:discard_msgs]
        return measures

    def _enqueue_capture_window(self, capture_start: float, capture_end: float) -> None:
        self._capture_windows.append((capture_start, capture_end))
        self._process_buffered_data()

    def _process_buffered_data(self) -> None:
        while self._sensor_messages and self._capture_windows:
            capture_start, capture_end = self._capture_windows[0]
            last_msg = self._sensor_messages[-1]
            data = last_msg.get("data", [])
            if not data or data[-1][0] < capture_end:
                break
            measures = self._extract_freq_window(capture_start, capture_end)
            samples = align_measurements(self.th, self._origin, measures)
            self._results.append(samples)
            self._capture_windows.pop(0)

    def _wait_for_pending(self) -> None:
        reactor = self._printer.get_reactor()
        while self._capture_windows:
            _capture_start, capture_end = self._capture_windows[0]
            systime = reactor.monotonic()
            if self._sensor_outage(systime, capture_end):
                raise RuntimeError(
                    "eddy_seek: LDC1612 sensor outage during sweep "
                    f"(no data by t={capture_end:.3f})"
                )
            reactor.pause(systime + 0.010)  # type: ignore[union-attr]
            self._process_buffered_data()

    def _sensor_outage(self, systime: float, end_time: float) -> bool:
        try:
            mcu = self._printer.lookup_object("mcu")
            est = mcu.estimated_print_time(systime)  # type: ignore[union-attr]
            return est > end_time + 1.0
        except Exception:
            return False

    def collect_samples(self) -> list[MotionSample]:
        self._wait_for_pending()
        flat: list[MotionSample] = []
        for batch in self._results:
            flat.extend(batch)
        self._results.clear()
        return flat


def _assert_speed_clamp_for_min_samples() -> None:
    cap = get_clamped_speed_for_min_samples_over_span(
        requested_mm_min=3000.0,
        span_mm=2.0,
        min_samples=20,
    )
    assert cap == 2400.0
    assert (
        get_clamped_speed_for_min_samples_over_span(
            requested_mm_min=1200.0,
            span_mm=2.0,
            min_samples=20,
        )
        == 1200.0
    )


_assert_speed_clamp_for_min_samples()
