"""Flow routing on a DEM that is clipped to a watershed.

This is the shape of every real DEM: a polygon clip leaves nodata all the way
around the raster. It is NOT an edge case, and every failure in this file was
silent — wrong numbers, no exception, no warning.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "flow_nd_out"

CELL = 2.0
CRS = "EPSG:32612"


@pytest.fixture(scope="module", autouse=True)
def _env():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"


def _collared(n=60, collar=3, pit=True):
    """A tilted plane with a nodata collar and a pit in the middle.

    The collar is what a clip to a watershed boundary leaves behind.
    """
    yy, xx = np.mgrid[0:n, 0:n]
    z = (1900.0 - 0.4 * xx - 0.05 * yy).astype("float32")
    if pit:
        z[n // 2 - 2:n // 2 + 2, n // 2 - 2:n // 2 + 2] = 1700.0
    nod = np.zeros((n, n), bool)
    nod[:collar, :] = nod[-collar:, :] = True
    nod[:, :collar] = nod[:, -collar:] = True
    # One gap in the collar, so the basin has a real outlet — as a clipped
    # watershed does at its pour point.
    nod[-collar:, n // 2 - 1:n // 2 + 1] = False
    z[nod] = -9999.0
    return z, nod


def _write(z, name="dem.tif"):
    p = OUT / name
    prof = {"driver": "GTiff", "width": z.shape[1], "height": z.shape[0],
            "count": 1, "dtype": "float32", "crs": CRS, "nodata": -9999.0,
            "transform": from_origin(500000, 4000000, CELL, CELL)}
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(z, 1)
    return str(p)


# -- the bug that shipped -----------------------------------------------------

def test_a_nodata_collar_does_not_disable_depression_filling():
    """The numba path used to seed only the outer ring of the array.

    With a collar every ring cell is nodata, so nothing was seeded, the heap
    was empty, the loop never ran, and priority-flood returned the DEM
    UNFILLED with filled=0 and no error. numba is the default, so the path
    that worked was the one that almost never ran.
    """
    from geolens.landscape.flow import _priority_flood_fast

    z, nod = _collared()
    out, filled = _priority_flood_fast(z.copy(), nod)
    assert filled > 0, (
        "no depressions filled on a collared DEM — the pit survived, so every "
        "cell in it becomes a spurious outlet and the network shatters"
    )
    interior = ~nod
    assert out[interior].min() > 1699.0, "the pit was left at its original depth"


def test_the_two_priority_flood_paths_agree():
    """They are supposed to be the same algorithm. They were not."""
    from geolens.landscape.flow import _priority_flood, _priority_flood_fast

    z, nod = _collared()
    slow_z, slow_n = _priority_flood(z.copy(), nod)
    fast_z, fast_n = _priority_flood_fast(z.copy(), nod)

    assert slow_n == fast_n, f"filled {slow_n} cells vs {fast_n}"
    valid = ~nod
    assert np.allclose(slow_z[valid], fast_z[valid], atol=1e-3)


def test_a_dem_with_no_outlet_at_all_is_an_error_not_a_silent_pass():
    """Fully enclosed by nodata: there is nowhere for water to leave.

    Returning an unfilled DEM here is what produced the original bug's
    symptoms, so it must raise rather than proceed.
    """
    from geolens.landscape.flow import _priority_flood_fast
    from geolens.safety import GeolensError

    z, nod = _collared(pit=False)
    nod[:] = True                       # every cell nodata
    with pytest.raises(GeolensError, match="no outlet"):
        _priority_flood_fast(z.copy(), nod)


# -- accumulation must be exact -----------------------------------------------

def test_accumulation_is_exact_above_float32s_mantissa():
    """float32 holds 24 bits. Accumulation is a CELL COUNT.

    Above 2**24 = 16,777,216 the float32 spacing exceeds 1, so `acc += 1` is a
    no-op: every hillslope cell draining into a large trunk contributes
    nothing. At 2 m that ceiling is a 67 km2 watershed, which is ordinary.
    """
    from geolens.landscape.flow import _accumulate

    big = 2 ** 24
    assert np.float32(big) == np.float32(big + 1), (
        "premise wrong: float32 can still separate these"
    )
    assert np.uint32(big) != np.uint32(big + 1)

    # A single downslope column: the bottom cell's accumulation must equal the
    # number of cells above it, exactly.
    n = 200
    acc = _accumulate(_column_fdir(n))
    assert acc.dtype == np.uint32, f"accumulation is {acc.dtype}, not an exact type"
    assert int(acc[-1, 0]) == n, f"bottom cell has {int(acc[-1,0])} of {n} cells"


def _column_fdir(n):
    """Every cell drains to the one below it; the last drains nowhere."""
    from geolens.landscape.flow import _CODES, _DR, _DC
    south = None
    for k in range(8):
        if _DR[k] == 1 and _DC[k] == 0:
            south = _CODES[k]
    assert south is not None
    f = np.full((n, 1), south, dtype="uint8")
    f[-1, 0] = 0
    return f


# -- end to end ---------------------------------------------------------------

def _valley_watershed(n=120, collar=4):
    """A V-shaped valley draining south through one gap in the collar.

    A tilted plane is the wrong fixture here: its water genuinely leaves
    through the whole downslope edge, so a fragmented accumulation is the
    CORRECT answer and the test would pass on broken code. This basin has one
    outlet, so the outlet must collect most of the grid.
    """
    yy, xx = np.mgrid[0:n, 0:n]
    axis = n // 2
    z = (1800.0 + 0.8 * np.abs(xx - axis) - 0.30 * yy).astype("float32")
    nod = np.zeros((n, n), bool)
    nod[:collar, :] = nod[-collar:, :] = True
    nod[:, :collar] = nod[:, -collar:] = True
    nod[-collar:, axis - 1:axis + 1] = False        # the pour point
    z[nod] = -9999.0
    return z, nod


def test_hand_on_a_collared_dem_is_not_shattered():
    """The user-visible symptom: 'the HAND output doesn't look like it worked'.

    With filling disabled by the collar, accumulation fragments into thousands
    of tiny basins, almost nothing crosses the stream threshold, and HAND is
    measured against whatever fragments remain.
    """
    from geolens.landscape import build_terrain

    z, nod = _valley_watershed()
    dem = _write(z, "collared.tif")
    t = build_terrain(dem, stream_threshold_km2=0.0001, out_prefix="collar")

    with rasterio.open(t.accumulation) as src:
        acc = src.read(1)
    with rasterio.open(t.hand) as src:
        hand = src.read(1, masked=True)

    valid = ~nod
    # A single tilted plane draining to one gap: the outlet must collect a
    # large share of the grid, not a handful of cells.
    assert acc[valid].max() > 0.25 * valid.sum(), (
        f"max accumulation {acc[valid].max()} of {valid.sum()} valid cells — "
        f"the network is fragmented"
    )
    h = hand.compressed()
    assert h.size, "HAND is empty"
    assert np.isfinite(h).all(), "HAND has non-finite values"
    assert h.min() >= -0.01, f"HAND is negative ({h.min()}) — below its own drainage"
