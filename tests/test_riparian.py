"""Riparian band detection from NDVI excess.

Synthetic scene: dry uplands, a sinuous green corridor along the channel, a
green hilltop, and a rectangular irrigated field on the floodplain. Each of
those exists to catch a specific failure.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "rip_test_out"

CELL = 10.0
W, H = 400, 300
CRS = "EPSG:32612"
TRANSFORM = from_origin(500000, 4000000, CELL, CELL)

UPLAND_NDVI = 0.14
RIPARIAN_NDVI = 0.62
FIELD_NDVI = 0.70


@pytest.fixture(scope="module", autouse=True)
def _env():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"


def _write(name, arr, dtype="float32", nodata=-9999.0):
    p = OUT / name
    prof = {"driver": "GTiff", "width": W, "height": H, "count": 1,
            "dtype": dtype, "crs": CRS, "transform": TRANSFORM,
            "nodata": nodata}
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(arr.astype(dtype), 1)
    return str(p)


@pytest.fixture(scope="module")
def scene():
    """NDVI, HAND and streams for a synthetic arid valley."""
    xx, yy = np.meshgrid(np.arange(W), np.arange(H))

    # A sinuous channel across the middle.
    centre = H / 2 + 28 * np.sin(2 * np.pi * xx / 160.0)
    dist = np.abs(yy - centre)

    ndvi = np.full((H, W), UPLAND_NDVI, dtype="float32")
    riparian = dist <= 6                       # ~120 m corridor
    ndvi[riparian] = RIPARIAN_NDVI

    # An irrigated field on low ground but DETACHED from the corridor — the
    # case the flags can actually catch. A field contiguous with the riparian
    # band merges into one patch and is not separable this way; see
    # test_a_contiguous_field_is_not_separable.
    field = (slice(200, 225), slice(250, 330))
    ndvi[field] = FIELD_NDVI
    field_mask = np.zeros((H, W), bool)
    field_mask[field] = True

    # A green hilltop, far above the drainage.
    hilltop = (slice(20, 45), slice(40, 90))
    ndvi[hilltop] = 0.66
    hill_mask = np.zeros((H, W), bool)
    hill_mask[hilltop] = True

    rng = np.random.default_rng(4)
    ndvi = np.clip(ndvi + rng.normal(0, 0.012, ndvi.shape), -1, 1)

    hand = (dist * 1.2).astype("float32")      # rises away from the channel
    # A field on the floodplain IS low ground — that is why it is farmed. If the
    # fixture puts it above the HAND cutoff the agriculture test passes for the
    # wrong reason: the field is filtered out before it can be flagged.
    hand[field] = 5.0
    hand[hilltop] = 80.0                        # the hilltop is high ground
    streams = (dist <= 1).astype("uint8")

    return {
        "ndvi": _write("ndvi.tif", ndvi),
        "hand": _write("hand.tif", hand),
        "streams": _write("streams.tif", streams, dtype="uint8", nodata=0),
        "riparian_mask": riparian,
        "field_mask": field_mask,
        "hill_mask": hill_mask,
    }


def _band(result):
    with rasterio.open(result.band_raster) as src:
        return src.read(1).astype(bool)


# -- the core behaviour -------------------------------------------------------

def test_finds_the_riparian_corridor(scene):
    from geolens.riparian import riparian_band

    r = riparian_band(scene["ndvi"], hand=scene["hand"],
                      streams=scene["streams"], out_prefix="rip1")
    band = _band(r)
    truth = scene["riparian_mask"]
    recall = float((band & truth).sum() / truth.sum())
    assert recall > 0.85, f"only {recall:.0%} of the corridor was found"
    assert r.riparian_km2 > 0


def test_a_green_hilltop_is_excluded_by_hand(scene):
    """Greenness alone is not riparian. North-facing slopes and mesa-top
    irrigation are green and are not floodplain."""
    from geolens.riparian import riparian_band

    r = riparian_band(scene["ndvi"], hand=scene["hand"],
                      streams=scene["streams"], max_hand_m=15.0,
                      out_prefix="rip2")
    band = _band(r)
    hill = scene["hill_mask"]
    assert (band & hill).sum() == 0, "a green hilltop was called riparian"


def test_without_hand_the_hilltop_leaks_in(scene):
    """Stated plainly so the HAND argument is understood as load-bearing."""
    from geolens.riparian import riparian_band

    r = riparian_band(scene["ndvi"], hand=None, max_hand_m=None,
                      out_prefix="rip3")
    band = _band(r)
    assert (band & scene["hill_mask"]).sum() > 0


def test_the_irrigated_field_is_flagged(scene):
    """It will be detected — nothing in NDVI separates alfalfa from cottonwood.

    What matters is that it does not pass silently.
    """
    from geolens.riparian import riparian_band

    r = riparian_band(scene["ndvi"], hand=scene["hand"],
                      streams=scene["streams"], out_prefix="rip4")
    assert r.suspect_patches, "the rectangular field was not flagged"
    assert any("agriculture" in w for w in r.warnings)


def test_a_contiguous_field_inflates_the_area(scene):
    """The honest limitation, asserted rather than described.

    A field touching the riparian corridor merges into one patch, and its area
    is counted as riparian. That harm is unavoidable here — nothing in NDVI
    separates alfalfa from cottonwood, and in the arid West fields sit on the
    floodplain by definition, so this is the common case rather than the edge
    case.

    Whether a flag fires on the merged patch is luck: it depends on whether the
    field happens to dominate that patch's shape or its connectivity. So this
    asserts the inflation, which is certain, and deliberately does NOT assert
    that the flags catch it. Separating contiguous agriculture needs a
    land-cover layer, not geometry.
    """
    from geolens.riparian import riparian_band

    with rasterio.open(scene["ndvi"]) as src:
        arr = src.read(1)
    # Paste a field directly against the corridor.
    arr[150:172, 60:140] = FIELD_NDVI
    touching = _write("ndvi_touching.tif", arr)

    with rasterio.open(scene["hand"]) as src:
        h = src.read(1)
    h[150:172, 60:140] = 5.0
    touching_hand = _write("hand_touching.tif", h)

    clean = riparian_band(scene["ndvi"], hand=scene["hand"],
                          streams=scene["streams"], out_prefix="rip_clean")
    r = riparian_band(touching, hand=touching_hand, streams=scene["streams"],
                      out_prefix="rip_touch")

    assert r.riparian_km2 > clean.riparian_km2 * 1.05, (
        "the contiguous field did not inflate the area — check the fixture"
    )
    assert isinstance(r.suspect_patches, list)


def test_rectangularity_separates_a_field_from_a_corridor():
    from shapely.geometry import box, Polygon
    from geolens.riparian import _rectangularity

    field = box(0, 0, 100, 60)
    assert _rectangularity(field) > 0.95

    # A sinuous corridor: its bounding rectangle is mostly empty.
    xs = np.linspace(0, 400, 60)
    ys = 30 * np.sin(xs / 40.0)
    top = list(zip(xs, ys + 8))
    bot = list(zip(xs[::-1], ys[::-1] - 8))
    corridor = Polygon(top + bot)
    assert _rectangularity(corridor) < 0.6


# -- the relative measurement -------------------------------------------------

def test_it_measures_excess_not_absolute_ndvi(scene):
    """Shifting the whole scene must not change the answer.

    A drought year, a different sensor or a different region moves absolute NDVI
    wholesale. The band is defined against the local background precisely so
    that a fixed threshold stays meaningful.
    """
    from geolens.riparian import riparian_band

    with rasterio.open(scene["ndvi"]) as src:
        arr = src.read(1)
    shifted = _write("ndvi_dry.tif", np.clip(arr - 0.08, -1, 1))

    a = riparian_band(scene["ndvi"], hand=scene["hand"], out_prefix="rip5")
    b = riparian_band(shifted, hand=scene["hand"], out_prefix="rip6")
    assert abs(a.riparian_km2 - b.riparian_km2) / a.riparian_km2 < 0.10, (
        "an absolute shift changed the result — the measurement is not relative"
    )


def test_scaled_integer_ndvi_is_detected(scene):
    """Sentinel-2 products routinely ship NDVI scaled by 10000."""
    from geolens.riparian import riparian_band

    with rasterio.open(scene["ndvi"]) as src:
        arr = src.read(1)
    scaled = _write("ndvi_scaled.tif", arr * 10000.0)

    a = riparian_band(scene["ndvi"], hand=scene["hand"], out_prefix="rip7")
    b = riparian_band(scaled, hand=scene["hand"], out_prefix="rip8")
    assert abs(a.riparian_km2 - b.riparian_km2) / a.riparian_km2 < 0.10


def test_too_small_a_background_window_says_so(scene):
    """The main way to get a mysteriously empty result.

    With a window near the valley width, the band raises its own background and
    stops looking anomalous. The failure must explain itself.
    """
    from geolens.riparian import riparian_band

    r = riparian_band(scene["ndvi"], hand=scene["hand"],
                      background_window_m=60.0, out_prefix="rip9")
    if r.riparian_km2 == 0:
        assert any("background_window_m" in w for w in r.warnings)
    else:
        # Even if something survives, it must be far less than the real corridor.
        full = riparian_band(scene["ndvi"], hand=scene["hand"],
                             out_prefix="rip10")
        assert r.riparian_km2 < full.riparian_km2


def test_outputs_land_on_disk(scene):
    from geolens.riparian import riparian_band

    r = riparian_band(scene["ndvi"], hand=scene["hand"],
                      streams=scene["streams"], out_prefix="rip11")
    assert Path(r.band_raster).exists()
    assert Path(r.excess_raster).exists()
    assert Path(r.band_polygons).exists()


def test_empty_ndvi_is_a_clear_error():
    from geolens.riparian import riparian_band
    from geolens.safety import GeolensError

    empty = _write("empty.tif", np.full((H, W), -9999.0))
    with pytest.raises(GeolensError, match="no valid data"):
        riparian_band(empty, out_prefix="rip12")


# -- mixed resolutions: the real case -----------------------------------------
#
# NDVI comes off NAIP at 1 m; HAND comes off the DEM at 10 m. Requiring
# identical grids would mean either building NDVI on the DEM grid (throwing away
# the resolution NAIP was streamed for) or dropping the HAND cutoff (which is
# worse: without it every green mesa top is riparian and the answer still looks
# like an answer).

def _coarsen(path, factor, name):
    """Block-average a raster to a coarser grid, as a real DEM would be."""
    import rasterio
    from rasterio.transform import Affine

    with rasterio.open(path) as src:
        a = src.read(1).astype("float32")
        t = src.transform
    h, w = a.shape[0] // factor, a.shape[1] // factor
    small = a[:h * factor, :w * factor].reshape(h, factor, w, factor).mean((1, 3))
    prof = {"driver": "GTiff", "width": w, "height": h, "count": 1,
            "dtype": "float32", "crs": CRS, "nodata": -9999.0,
            "transform": Affine(t.a * factor, 0, t.c, 0, t.e * factor, t.f)}
    out = OUT / name
    with rasterio.open(str(out), "w", **prof) as dst:
        dst.write(small.astype("float32"), 1)
    return str(out)


def test_a_coarse_hand_still_excludes_the_hilltop(scene):
    """The whole point of resampling instead of skipping."""
    from geolens.riparian import riparian_band

    coarse_hand = _coarsen(scene["hand"], 5, "hand_50m.tif")   # 10 m -> 50 m
    r = riparian_band(scene["ndvi"], hand=coarse_hand,
                      streams=scene["streams"], max_hand_m=15.0,
                      out_prefix="rip_mixed")
    band = _band(r)
    assert (band & scene["hill_mask"]).sum() == 0, (
        "a coarse HAND was ignored and the green hilltop came back as riparian"
    )
    recall = float((band & scene["riparian_mask"]).sum()
                   / scene["riparian_mask"].sum())
    assert recall > 0.80, f"only {recall:.0%} of the corridor survived"


def test_resampling_a_coarse_layer_is_stated_not_silent(scene):
    """A 50 m HAND gives a 50 m-fuzzy edge however fine the NDVI is."""
    from geolens.riparian import riparian_band

    coarse_hand = _coarsen(scene["hand"], 5, "hand_50m_b.tif")
    r = riparian_band(scene["ndvi"], hand=coarse_hand, out_prefix="rip_mixed2")
    assert any("resampled up" in w for w in r.warnings)


def test_a_stream_layer_covering_other_ground_says_so(scene):
    """Streams from the wrong tile make everything 'unconnected' — silently."""
    import rasterio
    from rasterio.transform import from_origin
    from geolens.riparian import riparian_band

    p = OUT / "streams_elsewhere.tif"
    prof = {"driver": "GTiff", "width": 50, "height": 50, "count": 1,
            "dtype": "uint8", "crs": CRS, "nodata": 0,
            "transform": from_origin(600000, 4100000, CELL, CELL)}
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(np.ones((50, 50), "uint8"), 1)

    r = riparian_band(scene["ndvi"], hand=scene["hand"], streams=str(p),
                      out_prefix="rip_mixed3")
    assert any("no cells on the NDVI extent" in w for w in r.warnings)


def test_a_hand_raster_covering_other_ground_is_not_accepted(scene):
    """Same shape, same CRS, 100 km away — the fast path used to take it.

    It matched on (height, width, crs) and never compared the transform, so the
    NDVI got masked by heights sampled somewhere else, silently.
    """
    import rasterio
    from rasterio.transform import from_origin
    from geolens.riparian import riparian_band

    with rasterio.open(scene["ndvi"]) as src:
        h, w = src.height, src.width

    p = OUT / "hand_elsewhere.tif"
    prof = {"driver": "GTiff", "width": w, "height": h, "count": 1,
            "dtype": "float32", "crs": CRS, "nodata": -9999.0,
            "transform": from_origin(600000, 4100000, CELL, CELL)}
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(np.full((h, w), 2.0, "float32"), 1)   # "everything is low"

    r = riparian_band(scene["ndvi"], hand=str(p), max_hand_m=15.0,
                      out_prefix="rip_elsewhere")
    band = _band(r)
    assert (band & scene["hill_mask"]).sum() == 0, (
        "a HAND raster from 100 km away was used as if it were this grid, so "
        "the green hilltop passed the height test"
    )


def test_a_hand_without_a_crs_does_not_silently_delete_the_band(scene):
    """It used to fill with +inf, so `h <= max_hand_m` was False everywhere.

    The warning said the layer "was ignored". It had in fact removed 100% of
    the result, and the follow-up warning then blamed min_excess.
    """
    import rasterio
    from geolens.riparian import riparian_band

    with rasterio.open(scene["hand"]) as src:
        h = src.read(1)
        prof = src.profile.copy()
    prof["crs"] = None
    p = OUT / "hand_nocrs.tif"
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(h, 1)

    r = riparian_band(scene["ndvi"], hand=str(p), streams=scene["streams"],
                      max_hand_m=15.0, out_prefix="rip_nocrs")
    assert r.riparian_km2 > 0, (
        "a CRS-less HAND deleted the entire band while reporting it as ignored"
    )
    assert any("NOT in effect" in w for w in r.warnings), (
        "the skipped restriction was not stated plainly"
    )


def test_a_patch_joined_through_diagonals_is_one_polygon():
    """8-connected patches must come out as one polygon each.

    v0.25 traced them 4-connected: on Cherokee Bill 1,523 of 1,838 patches
    were fragments of bigger ones (median 11 m2), each touching another, and
    one-cell fragments are perfect squares, so they took 939 of the 1,027
    agriculture flags. Here: ten 2x2 blocks in a staircase, each touching
    the next only at a corner. One patch of 4,000 m2, not ten squares of 400.
    """
    import geopandas as gpd

    from geolens.riparian import riparian_band

    ndvi = np.full((H, W), UPLAND_NDVI, dtype="float32")
    for k in range(10):
        r0, c0 = 100 + 2 * k, 100 + 2 * k
        ndvi[r0:r0 + 2, c0:c0 + 2] = RIPARIAN_NDVI
    p = _write("stairs.tif", ndvi)
    r = riparian_band(p, out_prefix="rip_stairs")
    g = gpd.read_file(r.band_polygons)
    assert len(g) == 1, f"{len(g)} polygons for one 8-connected patch"
    assert abs(g["area_km2"].iloc[0] - 40 * CELL * CELL / 1e6) < 1e-9
    # a diagonal staircase is not a field
    assert not bool(g["suspect_agriculture"].iloc[0])
