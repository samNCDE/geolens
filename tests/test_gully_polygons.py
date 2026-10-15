"""Gullies as 2D footprints, built from the measured bank tops.

The detector already cuts cross-sections along every centreline and runs the
verified bank detection on them. Those bank positions used to be reduced to a
median width and discarded. Closing a polygon through them gives a footprint
with measured edges instead of a buffered line — and because it comes off the
same sections, it carries the same gully_id and joins straight back.

The fixture has gullies of known width (14 m) and depth (3 m), so the polygon
area has a truth to be scored against.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import geopandas as gpd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "gpoly_test_out"
FIX = ROOT / "gullies"

TRUE_WIDTH_M = 14.0


@pytest.fixture(scope="module", autouse=True)
def _env():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"


@pytest.fixture(scope="module")
def detected():
    if not (FIX / "gully_dem.tif").exists():
        pytest.skip("gully fixture not built — run tests/make_gullies.py")
    from geolens.landscape import build_terrain, terrain_roughness, detect_gullies

    t = build_terrain(str(FIX / "gully_dem.tif"), stream_threshold_km2=0.2,
                      out_prefix="gp_t")
    rg = terrain_roughness(t, window_m=40.0)
    g = detect_gullies(t, polygons=True, roughness=rg, slope_std_min=0.04, tpi_max=-0.3,
                       min_length_m=100.0, section_width_m=60.0,
                       out_prefix="gp_g")
    return g


def test_polygons_are_produced_for_the_detected_gullies(detected):
    assert detected.gully_polygons, "no polygon layer was written"
    pg = gpd.read_file(detected.gully_polygons)
    assert len(pg) > 0
    assert len(pg) >= detected.gully_count - 1, (
        f"{len(pg)} polygons for {detected.gully_count} gullies"
    )


def test_every_polygon_is_valid_geometry(detected):
    pg = gpd.read_file(detected.gully_polygons)
    bad = [int(r.gully_id) for r in pg.itertuples() if not r.geometry.is_valid]
    assert not bad, f"invalid polygons for gullies {bad} — banks crossed over"
    assert (pg.geometry.area > 0).all()


def test_polygon_ids_join_back_to_the_lines(detected):
    """The point of doing it this way rather than as a separate detector."""
    lines = gpd.read_file(detected.gully_lines)
    polys = gpd.read_file(detected.gully_polygons)
    joined = lines.merge(polys[["gully_id", "measured_area_m2"]],
                         on="gully_id", how="inner", suffixes=("", "_p"))
    assert len(joined) == len(polys), "gully_id does not join the two layers"


def test_polygon_width_matches_the_known_gully_width(detected):
    """area / length is the mean width, and the fixture's truth is 14 m."""
    pg = gpd.read_file(detected.gully_polygons)
    implied = pg.geometry.area / pg["length_m"]
    med = float(np.median(implied))
    assert med == pytest.approx(TRUE_WIDTH_M, abs=4.0), (
        f"polygon implies a mean width of {med:.1f} m against a known "
        f"{TRUE_WIDTH_M} m"
    )


def test_the_detector_mask_is_not_the_gully_footprint(detected):
    """Written after this test failed and the failure turned out to be right.

    ``mask_area_m2`` was being reported as the gully's area. It is not: the
    detector's thresholds (slope variability + negative TPI) select the
    steepest, most incised part of the section, which is a ribbon near the
    centreline. On this fixture of KNOWN 14 m gullies the mask implies a width
    of 2.45 m — about one cell — while the measured polygon implies 15.75 m.

    So the mask understates gullied area roughly six-fold, and anything derived
    from it (area eroded, extent maps, volume per unit area) inherited that.
    The relationship is systematic, so it is pinned here rather than papered
    over.
    """
    pg = gpd.read_file(detected.gully_polygons)
    ok = pg[(pg["mask_area_m2"] > 0) & np.isfinite(pg["measured_area_m2"])]
    assert len(ok) > 0
    ratio = float(np.median(ok["measured_area_m2"] / ok["mask_area_m2"]))
    assert ratio > 2.0, (
        f"the mask and the measured footprint now agree ({ratio:.2f}x). If the "
        f"detector changed this may be good news — recheck which one matches "
        f"the known 14 m width before relaxing this test."
    )

    implied_mask_w = float((ok["mask_area_m2"] / ok["length_m"]).median())
    implied_poly_w = float((ok["measured_area_m2"] / ok["length_m"]).median())
    assert implied_mask_w < 0.5 * TRUE_WIDTH_M
    assert implied_poly_w == pytest.approx(TRUE_WIDTH_M, abs=4.0)


def test_gullied_area_uses_the_measured_footprint(detected):
    """The headline number must not be the ribbon."""
    pg = gpd.read_file(detected.gully_polygons)
    measured_km2 = float(np.nansum(pg["measured_area_m2"])) / 1e6
    assert detected.gullied_area_km2 == pytest.approx(measured_km2, rel=0.02), (
        f"gullied_area_km2 is {detected.gullied_area_km2:.5f} against "
        f"{measured_km2:.5f} km2 of measured footprint — it is still using the "
        f"detector mask"
    )


def test_polygon_is_not_just_a_buffered_line(detected):
    """A buffer has constant width; a measured footprint does not."""
    pg = gpd.read_file(detected.gully_polygons)
    from shapely.geometry import Polygon

    g = pg.geometry.iloc[0]
    poly = g if isinstance(g, Polygon) else max(g.geoms, key=lambda p: p.area)
    xs, ys = poly.exterior.coords.xy
    # Width varies along a real gully; a buffer's does not. Measure the spread
    # of the polygon's cross-stream extent in a few bands along it.
    pts = np.column_stack([np.asarray(xs), np.asarray(ys)])
    order = np.argsort(pts[:, 0])
    pts = pts[order]
    bands = np.array_split(pts, 6)
    widths = [float(b[:, 1].max() - b[:, 1].min()) for b in bands if len(b) > 2]
    assert len(widths) >= 4
    assert float(np.std(widths)) > 0.05, (
        "the footprint has constant width, which a measured one should not"
    )


def test_polygons_are_opt_in(gully_fixture_terrain=None):
    """v0.27: footprints climb the walls of steep hollows (Cherokee Bill:
    median 31 m wide, 11 m deep, drainage < 0.5 km2), so the polygon layer is
    written only when asked for. The switch is captured before the body
    builds a list with the same name."""
    import inspect

    from geolens.landscape import features
    src = inspect.getsource(features.detect_gullies)
    assert "polygons: bool = False" in src
    assert src.index("write_polygons = bool(polygons)") < src.index(
        "polygons: list[Any] = []")
