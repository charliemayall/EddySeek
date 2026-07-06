"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

import json

from _eddy_seek.common import Offset
from _eddy_seek.harmonic import HarmonicFit
from _eddy_seek.movement.handler import MotionSample
from _eddy_seek.plotting.primitives import (
    PASS_COLORS,
    AccuracyRepeatRecord,
    BinnedProfile,
    Bounds,
    BoxRecord,
    CentroidPassRecord,
    CircleHarmonicPassRecord,
    HeatmapRecord,
    MarkerRecord,
    PassMove,
    ProbeRecord,
    ScatterRecord,
    StatsRecord,
    SweepCentroidPassRecord,
    XYCloud,
    pass_color,
    record_pass_num,
)


def test_primitives_serialization():
    scatter = ScatterRecord(
        1,
        "pts",
        XYCloud((1.0, 2.0), (3.0, 4.0), (100.0, 101.0)),
    )
    scatter_no_freqs = ScatterRecord(1, "pts", XYCloud((1.0,), (2.0,)))
    marker = MarkerRecord(1, "best", Offset(1.0, 2.0), "star")
    box = BoxRecord(1, Bounds.from_box((0.0, 1.0, 0.0, 1.0)))
    stats = StatsRecord("title", (("k", "K"),), ({"k": "v"},))
    probe = ProbeRecord(Offset(1.0, 2.0), 100.0, (99.0, 101.0))

    assert scatter.to_dict()["type"] == "scatter"
    assert scatter.to_dict()["cloud"]["freqs"] == [100.0, 101.0]
    assert "freqs" not in scatter_no_freqs.to_dict()["cloud"]
    assert marker.to_dict()["at"] == {"x": 1.0, "y": 2.0}
    assert box.to_dict()["bounds"]["lo"] == {"x": 0.0, "y": 0.0}
    assert probe.to_dict()["mean_hz"] == 100.0

    for record in (scatter, scatter_no_freqs, marker, box, stats, probe):
        json.dumps(record.to_dict())

    assert pass_color(1) == PASS_COLORS[0]
    assert PassMove.compute(Offset.zero(), Offset(1.0, 0.0)).moved.x == 1.0
    assert record_pass_num(AccuracyRepeatRecord(2, Offset.zero())) == 2

    samples = [
        MotionSample(Offset(1.0, 2.0), 100.0, 0.0),
        MotionSample(Offset(3.0, 4.0), 101.0, 0.1),
    ]
    cloud = XYCloud.from_samples(samples)
    assert cloud == XYCloud((1.0, 3.0), (2.0, 4.0), (100.0, 101.0))
    probes = [(Offset(1.0, 2.0), 100.0), (Offset(3.0, 4.0), 101.0)]
    assert XYCloud.from_probes(probes) == cloud
    assert XYCloud.from_probes(probes, freqs=False) == XYCloud((1.0, 3.0), (2.0, 4.0))

    circle = CircleHarmonicPassRecord(
        2,
        Offset(0.1, 0.2),
        1.0,
        PassMove.compute(Offset(0.1, 0.2), Offset(0.2, 0.3)),
        XYCloud((0.0,), (0.0,), (100.0,)),
        BinnedProfile((0.0,), (100.0,)),
        HarmonicFit(100.0, 1.0, 0.0, 1.0, 0.1, 1),
        rejected=False,
    )
    trace = circle.to_trace_dict()
    assert trace["type"] == "circle_pass"
    assert trace["harmonic"]["amp"] == 1.0
    assert "samples" not in trace

    sweep = SweepCentroidPassRecord(
        1,
        "coarse",
        PassMove.compute(Offset.zero(), Offset(0.1, 0.0)),
        Bounds.from_box((-1.0, 1.0, -1.0, 1.0)),
        XYCloud((0.0,), (0.0,), (100.0,)),
    )
    sweep_trace = sweep.to_trace_dict()
    assert sweep_trace["type"] == "sweep_centroid"
    assert sweep_trace["sample_count"] == 1
    assert "samples" not in sweep_trace

    centroid = CentroidPassRecord(
        1,
        PassMove.compute(Offset.zero(), Offset(0.1, 0.0)),
        XYCloud((0.0,), (0.0,), (100.0,)),
    )
    assert centroid.to_trace_dict()["sample_count"] == 1

    heatmap = HeatmapRecord(
        PassMove.compute(Offset.zero(), Offset(0.0, 0.0)),
        Bounds.from_box((-1.0, 1.0, -1.0, 1.0)),
        ((100.0,),),
        (0.0,),
        (0.0,),
        XYCloud((0.0,), (0.0,), (100.0,)),
    )
    assert heatmap.to_trace_dict()["type"] == "debug_scan"
