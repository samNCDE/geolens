"""Two bugs that only real terrain produces, and that fixtures hid completely.

Both were found by running on a 1,036 km2 DEM of the arid Southwest after an
overnight run on 1.94 billion cells died with nothing to show. Neither could
be found any other way: the synthetic gully fixtures are a handful of straight
incisions, and both bugs need a network with structure.
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from geolens.landscape.network import _trace_skeleton


def _ring(n: int = 41, r: int = 15) -> np.ndarray:
    """A closed ring on which EVERY cell has exactly two 8-neighbours.

    That is the case with no endpoint and no junction anywhere, which is what
    the tracer had no way to leave.
    """
    sk = np.zeros((n, n), bool)
    c = n // 2
    for i in range(n):
        for j in range(n):
            if abs(i - c) + abs(j - c) == r:
                sk[i, j] = True
    return sk


def test_the_ring_really_has_no_endpoint_or_junction():
    """Guard on the fixture: if this stops being all-degree-2 the regression
    below stops testing anything."""
    sk = _ring()
    degs = set()
    for i, j in np.argwhere(sk):
        degs.add(int(sum(sk[i + a, j + b] for a in (-1, 0, 1)
                         for b in (-1, 0, 1) if (a or b))))
    assert degs == {2}, degs


def test_a_closed_loop_terminates(monkeypatch):
    """The overnight crash.

    The tracer followed chains of degree-2 cells until it reached a junction
    or an endpoint. A ring has neither, so it walked round and round appending
    one cell per lap, and the only thing that ended it was the machine running
    out of memory -- 6 GB on a 9.9 M cell DEM, reproduced, and on 1.94 billion
    cells it ran all night.
    """
    sk = _ring()
    n_cells = int(sk.sum())

    t0 = time.monotonic()
    paths = _trace_skeleton(sk)
    elapsed = time.monotonic() - t0

    assert elapsed < 10.0, f"took {elapsed:.1f}s on {n_cells} cells"
    longest = max(len(p) for p in paths)
    assert longest <= n_cells + 1, (
        f"a path of {longest} cells was traced through a {n_cells}-cell ring "
        f"— it went round more than once"
    )
    assert sum(len(p) for p in paths) < 4 * n_cells


def test_a_simple_chain_is_still_one_path():
    sk = np.zeros((5, 9), bool)
    sk[2, 1:8] = True
    paths = _trace_skeleton(sk)
    assert [len(p) for p in paths] == [7]


def test_merging_fragments_does_not_scale_quadratically():
    """The second half of the same crash.

    Fragment merging compared every pair of endpoints. A real gully run
    produced 21,018 fragments — 42,036 endpoints, 8.8e8 pairs per round, and
    several rounds — and sat there for half an hour. The fixtures produce a
    few dozen fragments, so it looked instant in every test.

    Doubling the fragment count must not quadruple the time. The bound is
    loose on purpose: this is guarding an order of growth, not a runtime.
    """
    from shapely.geometry import LineString

    from geolens.landscape.network import merge_fragments as _merge

    def build(n):
        # Scattered, far apart: nothing can join, so every pair is examined
        # and the cost is the comparison itself.
        return [LineString([(i * 1000.0, 0.0), (i * 1000.0 + 50.0, 0.0)])
                for i in range(n)]

    def timed(n):
        lines = build(n)
        t0 = time.monotonic()
        _merge(lines, snap_tol_m=5.0)
        return time.monotonic() - t0

    small = max(timed(1500), 1e-3)
    large = timed(6000)                       # 4x the fragments
    assert large < small * 8.0, (
        f"4x the fragments cost {large / small:.1f}x the time "
        f"({small*1000:.0f} ms -> {large*1000:.0f} ms) — the endpoint "
        f"comparison is back to all-pairs"
    )
