"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fakes import FakeGcmd, FakePrinter, ok_seek_result

from _eddy_seek.accuracy_test import run_accuracy_test
from _eddy_seek.common import Offset
from _eddy_seek.config import SeekConfig
from _eddy_seek.session import compute_accuracy_stats


def test_mock_does_not_inflate_reference_relative_spread():
    """Session-relative corrections vary with mock; found center from test start does not."""
    saved = Offset.zero()
    true_center = Offset(0.05, -0.03)
    mocks = [Offset(0.2, 0.1), Offset(-0.15, 0.25), Offset(0.1, -0.2)]

    session_relative = [(true_center - saved) - mock for mock in mocks]
    session_stats = compute_accuracy_stats(session_relative)
    assert session_stats.max_pair > 0.3

    reference_relative = [
        mock + offset for mock, offset in zip(mocks, session_relative)
    ]
    ref_stats = compute_accuracy_stats(reference_relative)
    assert ref_stats.max_pair == pytest.approx(0.0)
    assert ref_stats.mean.x == pytest.approx(true_center.x)
    assert ref_stats.mean.y == pytest.approx(true_center.y)


def test_run_accuracy_test_records_reference_relative_offsets_with_mock():
    true_center = Offset(0.05, -0.03)
    mocks = [Offset(0.2, 0.1), Offset(-0.15, 0.25)]
    seek_corrections = [true_center - mock for mock in mocks]

    host = MagicMock()
    host.printer = FakePrinter()
    host.seek_config = SeekConfig(save_plots=False)

    console = MagicMock()
    recorded: list[Offset] = []

    def capture_stats(_console, offsets, *, durations_s=None):
        recorded.extend(offsets)

    seek_results = [ok_seek_result(offset=offset) for offset in seek_corrections]

    with (
        patch(
            "_eddy_seek.accuracy_test._apply_mock_offset",
            side_effect=mocks,
        ),
        patch(
            "_eddy_seek.accuracy_test.SeekSession",
            side_effect=lambda *args, **kwargs: MagicMock(
                run=MagicMock(return_value=seek_results.pop(0))
            ),
        ),
        patch(
            "_eddy_seek.accuracy_test.report_accuracy_stats", side_effect=capture_stats
        ),
    ):
        run_accuracy_test(
            host,
            FakeGcmd(),
            console=console,
            repeats=2,
            mock_enabled=True,
        )

    assert len(recorded) == 2
    assert recorded[0].x == pytest.approx(true_center.x)
    assert recorded[0].y == pytest.approx(true_center.y)
    assert recorded[1].x == pytest.approx(true_center.x)
    assert recorded[1].y == pytest.approx(true_center.y)
