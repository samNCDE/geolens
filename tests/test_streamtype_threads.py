"""Braided (D) needs more than one channel at bankfull (v0.38).

Kingman's lidar pilots had 48% of their length typed D on width/depth alone:
at the regional depth (0.2 m) a 16 m sandy bed has W/D 80, whether it is
one channel or five. The lidar section says which: the threads below the
bankfull stage, parted by bars above it.
"""
from __future__ import annotations

import numpy as np

from geolens.landscape.streamtype import _threads, classify_stream_type


def _section(bed):
    o = np.arange(len(bed), dtype=float)
    return o, np.array(bed, dtype=float)


def test_threads_are_counted_between_the_banks():
    # bank | thread | bar | thread | bank, stage 1.0
    o, z = _section([3, 2, 0.5, 0.2, 0.4, 1.4, 1.5, 0.3, 0.1, 0.6, 2, 3])
    assert _threads(o, z, 1.0, 1.0, 10.0, min_width=1.0, min_bar=0.2) == 2
    # a bar below the stage does not part the threads
    o, z = _section([3, 2, 0.5, 0.2, 0.4, 0.9, 0.95, 0.3, 0.1, 0.6, 2, 3])
    assert _threads(o, z, 1.0, 1.0, 10.0, min_width=1.0, min_bar=0.2) == 1
    # a flat wide bed is one thread however wide
    o, z = _section([3] + [0.2] * 30 + [3])
    assert _threads(o, z, 0.5, 0.0, 31.0, min_width=1.0, min_bar=0.1) == 1
    # a sliver narrower than min_width is not a thread
    o, z = _section([3, 0.2, 1.5, 1.6, 0.2, 0.2, 0.2, 3])
    assert _threads(o, z, 1.0, 0.0, 7.0, min_width=2.0, min_bar=0.2) == 1
    assert _threads(o, z, 1.0, 0.0, 7.0, min_width=1.0, min_bar=0.2) == 2
    assert _threads(o, z, 1.0, 0.0, 7.0, min_width=1.0, min_bar=0.8) == 1
    assert _threads(o[:0], z[:0], 1.0, 0.0, 7.0, 1.0, 0.2) == 0


def test_a_single_thread_is_not_braided():
    wide = dict(entrenchment=4.0, width_depth=85.0, sinuosity=1.08, slope=0.02)
    assert classify_stream_type(**wide)["stream_letter"] == "D"
    assert classify_stream_type(threads=3, **wide)["stream_letter"] == "D"
    one = classify_stream_type(threads=1, **wide)
    assert one["stream_letter"] != "D" and one["single_thread"] is True
    assert one["braided_possible"] is False
    assert one["stream_letter"] in ("C", "F", None)
    # unknown threads leave the width/depth rule as it was
    assert classify_stream_type(threads=None, **wide)["stream_letter"] == "D"
    assert classify_stream_type(threads=float("nan"), **wide)["stream_letter"] == "D"
