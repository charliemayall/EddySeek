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
from typing import TYPE_CHECKING, Literal, overload

from ..common import Axis, Offset, Position
from .kinematic_guard import MAX_SCV

if TYPE_CHECKING:
    from klippy.klippy import Printer
    from klippy.toolhead import ToolHead

    from ..config import SeekConfig
    from ..plotting.primitives import ProbeRecord
    from ..session import SeekHost


logger = logging.getLogger(__name__)

_LDC1612_BULK_HZ = 400.0  # batch bulk client nominal rate
MIN_CAPTURE_SAMPLES = 3


def manual_move_xy(toolhead: ToolHead, position: Position, speed_mm_s: float) -> None:
    """Queue absolute machine XY; rejects session-relative ``Offset``."""
    if position.is_relative:
        raise TypeError(
            "eddy_seek: manual_move attempted to move to a relative position. Moves must use absolute positions."
            f"(got ({position.x:.4f}, {position.y:.4f}))"
        )
    toolhead.manual_move([position.x, position.y], speed_mm_s)


def move_to_xy(
    toolhead: ToolHead,
    position: Position,
    feedrate: float,
    *,
    wait: bool = False,
) -> None:
    """Queue a move to absolute machine XY (mm); feedrate in mm/min."""
    logger.info(
        f"eddy_seek: move_to ({position.x:.4f}, {position.y:.4f}) feedrate={feedrate:.1f}"
    )
    manual_move_xy(toolhead, position, feedrate / 60.0)
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
        logger.info(
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
        self._session_offset = Offset.zero()
        self._jog_speed = jog_speed

    @property
    def origin(self) -> Position:
        return self._origin

    @property
    def position(self) -> Offset:
        return self._session_offset

    def _commit(self, offset: Offset) -> None:
        self._session_offset = offset

    def sync_offset(self, offset: Offset) -> None:
        self._commit(offset)

    def jog_to(self, offset: Offset) -> None:
        """Queue a jog to session-relative offset."""
        delta = offset - self._session_offset
        if delta.x == 0.0 and delta.y == 0.0:
            return

        logger.info(
            f"eddy_seek: jog delta=({delta.x:.4f}, {delta.y:.4f}) "
            f"-> offset=({offset.x:.4f}, {offset.y:.4f})"
        )
        toolhead = self._printer.lookup_object("toolhead")
        machine = Position.from_pair(toolhead.get_position()) + delta
        manual_move_xy(toolhead, machine, self._jog_speed / 60.0)
        self._commit(offset)


def lookup_toolhead_position(toolhead: ToolHead, print_time: float) -> Position:
    kin = toolhead.get_kinematics()
    kin_spos = {
        s.get_name(): s.mcu_to_commanded_position(s.get_past_mcu_position(print_time))
        for s in kin.get_steppers()  # ty: ignore[unresolved-attribute]
    }
    pos = kin.calc_position(kin_spos)  # ty: ignore[unresolved-attribute]
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

    def __init__(
        self,
        printer: Printer,
        host: SeekHost,
        config: SeekConfig,
        origin: Position,
        trace_cb: Callable[[ProbeRecord], None] | None = None,
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

    def sample(self, offset: Offset) -> float:
        """Move to offset, dwell, and return mean LDC1612 frequency."""
        self.jog(offset)
        toolhead = self.th

        self._host.reset_capture()
        toolhead.dwell(self._config.dwell_time)
        toolhead.wait_moves()

        mean = self._host.get_capture_mean(min_samples=MIN_CAPTURE_SAMPLES)
        if mean is None:
            logger.info(
                f"eddy_seek: measure_at ({offset.x:.4f}, {offset.y:.4f}) failed "
                f"({self._host.capture_count} samples)"
            )
            raise RuntimeError(
                f"eddy_seek: no samples at offset "
                f"({offset.x:.3f}, {offset.y:.3f}) mm after "
                f"{self._config.dwell_time:.2f} s dwell. "
                "Check sensor connection, dwell_time, and i2c settings."
            )
        logger.info(
            f"eddy_seek: measure_at ({offset.x:.4f}, {offset.y:.4f}) -> {mean:.2f} Hz "
            f"({self._host.capture_count} samples)"
        )
        if self._trace_cb is not None:
            from ..plotting.primitives import ProbeRecord

            # fix for circular import
            self._trace_cb(
                ProbeRecord(
                    at=offset,
                    mean_hz=mean,
                    samples_hz=tuple(self._host.peek_capture_samples()),
                )
            )
        return mean

    def begin(self, origin: Position) -> None:
        self._origin = origin
        self._sensor_messages = []
        self._capture_windows = []
        self._results = []
        self._need_stop = False
        self._last_move_end = None
        self._active = True
        self._th = self._printer.lookup_object("toolhead")
        if not self._client_registered:
            self._host.add_sensor_client(self._on_sensor_message)
            self._client_registered = True

    def close(self) -> None:
        self._need_stop = True
        self._active = False
        self._th = None

    def move_leg(self, line_start: Offset, line_end: Offset, speed: float) -> None:
        """Traverse a leg without sensor capture (circle lead-in)."""
        if not self._active:
            raise RuntimeError("eddy_seek: continuous motion not active")
        if self._last_move_end is None or line_start != self._last_move_end:
            self._manual_move(line_start, speed)
            self._last_move_end = line_start
        self._manual_move(line_end, speed)
        self._commit(line_end)
        self._last_move_end = line_end

    def capture_leg(self, line_start: Offset, line_end: Offset, speed: float) -> None:
        if not self._active:
            raise RuntimeError("eddy_seek: continuous motion not active")
        if self._last_move_end is None or line_start != self._last_move_end:
            self._manual_move(line_start, speed)
            self._last_move_end = line_start

        capture_start_t = self.th.get_last_move_time()
        self._manual_move(line_end, speed)
        self._commit(line_end)
        self._register_capture_window(
            capture_start_t
        )  # make cb for move end (start,end)->(...)
        self._last_move_end = line_end

    def run_capture_legs(
        self,
        legs: Sequence[tuple[Offset, Offset]],
        speed: float,
        *,
        lead_in_legs: Sequence[tuple[Offset, Offset]] | None = None,
        between_leg_moves: (
            Sequence[Sequence[tuple[Offset, Offset]] | None] | None
        ) = None,
    ) -> None:
        """Run captured sweep legs, optionally with connector legs between them

        ``lead_in_legs`` are uncaptured chords before the first captured leg
        (e.g. circle warmup).

        ``between_leg_moves`` Uncaptured moves,  length ``len(legs) - 1``
                              between_leg_moves[i] is a sequence of chord legs that cumulatively move between legs[i] and legs[i+1]
                              or None when no connector is needed

        """
        for line_start, line_end in lead_in_legs or ():
            self.move_leg(line_start, line_end, speed)
        if between_leg_moves is not None and len(between_leg_moves) != len(legs) - 1:
            raise ValueError(
                "eddy_seek: between_leg_moves must have length len(legs) - 1"
            )
        for index, (line_start, line_end) in enumerate(legs):
            if between_leg_moves and index > 0:
                connector = between_leg_moves[index - 1]
                if connector:
                    for seg_start, seg_end in connector:
                        self.move_leg(seg_start, seg_end, speed)
            self.capture_leg(line_start, line_end, speed)
        self.th.wait_moves()
        self.th.get_last_move_time()

    def _manual_move(self, offset: Offset, speed: float) -> None:
        machine = self._origin + offset
        speed_mm_s = speed / 60.0
        self.th.limit_next_junction_speed(
            min(speed_mm_s, MAX_SCV)
        )  # jerky cornering leeds to odd behaviour and it is noisy
        manual_move_xy(self.th, machine, speed_mm_s)

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
            reactor.pause(systime + 0.010)  # ty: ignore[unresolved-attribute]
            self._process_buffered_data()

    def _sensor_outage(self, systime: float, end_time: float) -> bool:
        try:
            mcu = self._printer.lookup_object("mcu")
            est = mcu.estimated_print_time(systime)  # ty: ignore[unresolved-attribute]
            return est > end_time + 1.0
        except (KeyError, AttributeError):
            return False
        except Exception as exc:
            config_error = getattr(self._printer, "config_error", None)
            if config_error is not None and isinstance(exc, config_error):
                return False
            logger.exception("eddy_seek: sensor outage check failed")
            return False

    @overload
    def collect_samples(self, flat: Literal[True]) -> Sequence[MotionSample]: ...
    @overload
    def collect_samples(
        self, flat: Literal[False] = False
    ) -> Sequence[Sequence[MotionSample]]: ...
    def collect_samples(
        self, flat: bool = True
    ) -> Sequence[MotionSample] | Sequence[Sequence[MotionSample]]:
        self._wait_for_pending()
        if flat:
            flat_samples: list[MotionSample] = []
            for batch in self._results:
                flat_samples.extend(batch)
            self._results.clear()
            return flat_samples
        res_copy = self._results[::]
        self._results.clear()
        return res_copy
