"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

Align LDC1612 (print_time, frequency) samples to session-relative XY during continuous motion.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .common import Axis, Position

if TYPE_CHECKING:
    from klippy.klippy import Printer
    from klippy.toolhead import ToolHead


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MotionSample:
    """One sensor reading correlated to session-relative XY."""

    offset: Position
    freq: float
    print_time: float


SweepSample = MotionSample


@runtime_checkable
class ContinuousMotion(Protocol):
    """Record sensor batches during continuous moves and align time to XY."""

    @property
    def origin(self) -> Position: ...

    @property
    def position(self) -> Position:
        """Session-relative nozzle position after the last capture leg."""
        ...

    def begin(self, origin: Position) -> None:
        """Reset buffers and set machine XY at seek session start."""
        ...

    def close(self) -> None:
        """Stop sensor capture for the seek session."""
        ...

    def capture_leg(
        self, line_start: Position, line_end: Position, speed: float
    ) -> None:
        """Reposition to ``line_start`` (not sampled), then sample ``line_start``→``line_end``."""
        ...

    def run_capture_legs(
        self,
        legs: Sequence[tuple[Position, Position]],
        speed: float,
    ) -> None:
        """Queue many capture legs, then wait for the toolhead to finish."""
        ...

    def collect_samples(self) -> list[MotionSample]:
        """Wait for pending move windows, then return aligned samples."""
        ...


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


class ContinuousMotionHandler:
    """Record LDC1612 batches and correlate each sample to XY via stepper history."""

    def __init__(self, printer: Printer, sensor_add_client: Callable[..., Any]) -> None:
        self._printer = printer
        self._sensor_add_client = sensor_add_client
        self._sensor_messages: list[dict] = []
        self._capture_windows: list[tuple[float, float]] = []
        self._results: list[list[MotionSample]] = []
        self._origin = Position.zero()
        self._position = Position.zero()
        self._active = False
        self._need_stop = False
        self._client_registered = False
        self._th: ToolHead | None = None
        self._last_move_end: Position | None = None

    @property
    def origin(self) -> Position:
        return self._origin

    @property
    def position(self) -> Position:
        return self._position

    @property
    def th(self) -> ToolHead:
        if self._th is None:
            return self._printer.lookup_object("toolhead")
        return self._th

    def begin(self, origin: Position) -> None:
        self._origin = origin
        self._sensor_messages = []
        self._capture_windows = []
        self._results = []
        self._need_stop = False
        self._active = True
        self._th = self._printer.lookup_object("toolhead")
        if not self._client_registered:
            self._sensor_add_client(self._on_sensor_message)
            self._client_registered = True

    def close(self) -> None:
        self._need_stop = True
        self._active = False
        self._th = None

    def capture_leg(
        self, line_start: Position, line_end: Position, speed: float
    ) -> None:
        if not self._active:
            raise RuntimeError("eddy_seek: continuous motion not active")
        if self._last_move_end is None or line_start != self._last_move_end:
            self._manual_move(line_start, speed)

        capture_start = self.th.get_last_move_time()

        self._manual_move(line_end, speed)
        self._position = line_end
        self._register_capture_window(capture_start)
        self._last_move_end = line_end

    def run_capture_legs(
        self,
        legs: Sequence[tuple[Position, Position]],
        speed: float,
    ) -> None:
        for line_start, line_end in legs:
            self.capture_leg(line_start, line_end, speed)
        self.th.wait_moves()
        self.th.get_last_move_time()

    def _manual_move(self, offset: Position, speed: float) -> None:

        machine = self._origin + offset
        self.th.limit_next_junction_speed(
            speed
        )  # jerky cornering leeds to dwell like behaviour
        self.th.manual_move([machine.x, machine.y], speed)

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


def _assert_handler_implements_protocol() -> None:
    assert isinstance(
        ContinuousMotionHandler(object(), lambda _cb: None),  # type: ignore[arg-type]
        ContinuousMotion,
    )


_assert_handler_implements_protocol()
