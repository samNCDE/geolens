"""Nodata holes in a DEM, and the fabricated terrain they used to produce.

A real DEM has holes: masked water bodies, lidar voids, the collar left by a
clip. Everything in this file was silent — no error, no warning, just terrain
that is not there being reported as measured.

Measured on a real 1.94-billion-cell DEM: slope.tif had 1,936,812,500 valid
cells against the DEM's 1,305,630,482. 630 million fabricated zeros.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from scipy import ndimage

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "holes_test_out"

CELL = 2.0
TRUE_SLOPE = 0.60


@pytest.fixture(scope="module", autouse=True)
def _env():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"


@pytest.fixture(scope="module")
def holed():
    """A uniform hillslope with a masked void in the middle.

    Uniform on purpose: every real cell has exactly one correct slope, so any
    departure is fabricated rather than arguable. The void sits far below the
    surrounding ground in elevation terms, which is what a masked lake or a
    lidar dropout looks like.
    """
    n = 80
    yy, xx = np.mgrid[0:n, 0:n]
    z = (1000.0 + TRUE_SLOPE * CELL * xx).astype("float32")
    hole = np.zeros((n, n), bool)
    hole[30:45, 30:45] = True
    z[hole] = -9999.0

    p = OUT / "holed.tif"
    prof = {"driver": "GTiff", "width": n, "height": n, "count": 1,
            "dtype": "float32", "crs": "EPSG:32612", "nodata": -9999.0,
            "transform": from_origin(500000, 4000000, CELL, CELL)}
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(z, 1)
    return {"path": str(p), "hole": hole, "n": n,
            "ring": ndimage.binary_dilation(hole) & ~hole}


def _read(path):
    with rasterio.open(path) as src:
        return src.read(1, masked=True), src.nodata


# -- slope --------------------------------------------------------------------

def test_slope_does_not_invent_valid_cells(holed):
    from geolens.landscape.terrain import _slope

    out = _slope(Path(holed["path"]), OUT / "slope.tif")
    s, nd = _read(out)

    assert nd is not None, "slope was written with no nodata tag"
    n_valid = int((~np.ma.getmaskarray(s)).sum())
    expected = holed["n"] ** 2 - int(holed["hole"].sum())
    assert n_valid == expected, (
        f"slope has {n_valid} valid cells where the DEM has {expected} — "
        f"{n_valid - expected} were fabricated"
    )


def test_the_ring_around_a_hole_keeps_its_real_slope(holed):
    """The inversion that mattered.

    NaN propagates one cell through a centred difference, so the ring of VALID
    cells around every hole used to read slope 0.0 — and valley._fuzzy_high
    reads 0.0 as MAXIMUM evidence for "flat valley floor". A closed ring of
    strongest-possible valley evidence around every void, which binary_fill_holes
    can then swallow whole.
    """
    from geolens.landscape.terrain import _slope

    out = _slope(Path(holed["path"]), OUT / "slope2.tif")
    s, _ = _read(out)
    ring = s[holed["ring"]].compressed()

    assert ring.size > 0
    assert np.allclose(ring, TRUE_SLOPE, atol=0.02), (
        f"ring slope is {np.unique(np.round(ring, 3))[:5]}, not {TRUE_SLOPE}. "
        f"0.0 means NaN propagation; {TRUE_SLOPE/2} means a nearest-fill "
        f"halving the one-sided difference"
    )


# -- TPI and curvature --------------------------------------------------------

def test_tpi_near_a_hole_is_not_a_fabricated_anomaly(holed):
    """A void filled with the GLOBAL MEAN elevation becomes a plateau.

    Every real cell within the TPI window then reads a huge anomaly and sails
    past any `tpi <= -0.3` test — a ring of confident false gullies around
    every hole, each with a reported volume.
    """
    from geolens.landscape import build_terrain, terrain_roughness

    t = build_terrain(holed["path"], stream_threshold_km2=0.0002,
                      out_prefix="holes_t")
    rg = terrain_roughness(t, window_m=20.0)

    tpi, nd = _read(rg.tpi)
    assert nd is not None, "TPI was written with no nodata tag"
    assert np.ma.getmaskarray(tpi)[holed["hole"]].all(), (
        "TPI has values inside the hole"
    )

    near = ndimage.binary_dilation(holed["hole"], iterations=6) & ~holed["hole"]
    far = ~ndimage.binary_dilation(holed["hole"], iterations=12)
    near_v = np.abs(tpi[near].compressed())
    far_v = np.abs(tpi[far].compressed())
    assert near_v.size and far_v.size

    # On a uniform plane TPI is ~0 everywhere. Near the hole it must not be
    # wildly larger than far from it.
    assert float(np.nanmax(near_v)) < max(1.0, 10 * float(np.nanmax(far_v))), (
        f"TPI near the hole reaches {np.nanmax(near_v):.2f} against "
        f"{np.nanmax(far_v):.2f} far away — the void is being read as terrain"
    )


def test_no_ring_of_false_gullies_around_a_void(holed):
    """The user-visible consequence, asserted directly."""
    from geolens.landscape import build_terrain, terrain_roughness, detect_gullies

    t = build_terrain(holed["path"], stream_threshold_km2=0.0002,
                      out_prefix="holes_t2")
    rg = terrain_roughness(t, window_m=20.0)
    g = detect_gullies(t, roughness=rg, slope_std_min=0.01, tpi_max=-0.3,
                       min_length_m=20.0, section_width_m=40.0,
                       out_prefix="holes_g")

    assert g.gully_count == 0, (
        f"{g.gully_count} gullies found on a uniform plane with one hole in it "
        f"— they are the rim of the void"
    )
