"""Gully footprints from the ground, not from bank picks.

The footprint used to join the bank tops of a few cross-sections into a ring.
On the first real run at gully resolution -- Eagle Creek, 0.61 m, EPSG:6529 --
that gave a median of 2 usable sections per gully, footprints for 55 of 117
gullies, a median of 56% of each gully's own centreline inside its own
polygon (5% at worst), square-ended slabs beside the line, and one
figure-of-eight spanning two parallel incisions.

The replacement measures depth below the LOWER rim on a cross-profile every
two cells along the whole centreline, and burns the cells between the edges.
These tests hold it to a fixture of known gullies.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent
DEM = ROOT / "gullies" / "gully_dem.tif"
BUILT_WIDTH, BUILT_DEPTH = 14.0, 3.0          # tests/make_gullies.py


@pytest.fixture(scope="module")
def gullies(tmp_path_factory):
    if not DEM.exists():
        pytest.skip("gully fixture not built")
    out = tmp_path_factory.mktemp("gfp")
    os.environ["GEOLENS_OUT"] = str(out)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"
    import geopandas as gpd

    from geolens.landscape import (build_terrain, detect_gullies,
                                   suggest_slope_std_min, terrain_roughness)

    t = build_terrain(str(DEM), stream_threshold_km2=0.05, out_prefix="gfp")
    r = terrain_roughness(t, window_m=40.0)
    thr = suggest_slope_std_min(r.slope_std)["slope_std_min"]
    g = detect_gullies(t, r, polygons=True, slope_std_min=thr, max_drainage_km2=0.5,
                       out_prefix="gfpg")
    return (gpd.read_file(g.gully_lines), gpd.read_file(g.gully_polygons))


def test_every_gully_gets_a_footprint_from_the_ground(gullies):
    lines, polys = gullies
    assert len(polys) == len(lines)
    assert set(lines["footprint_method"]) == {"lower_rim_profiles"}


def test_the_footprint_contains_its_own_centreline(gullies):
    """The property the bank-pick ring failed on real terrain: 56% median."""
    lines, _ = gullies
    assert lines["centreline_inside"].min() > 0.95, (
        lines["centreline_inside"].describe()
    )


def test_width_depth_and_volume_match_the_built_gullies(gullies):
    """Parabolic gullies, 14 m wide and 3 m deep: width 14, mean depth 2/3 of
    3, cross-section area 2/3 x 14 x 3 = 28 m2. The bank-pick sections read
    16 m; before the edge was set at 5% of local depth, this read 17.5 m."""
    lines, _ = gullies
    w = float(lines["width_m"].median())
    d = float(lines["mean_depth_m"].median())
    mx = float(lines["max_depth_m"].median())
    assert w == pytest.approx(BUILT_WIDTH, rel=0.07), w
    assert d == pytest.approx(BUILT_DEPTH * 2 / 3, rel=0.07), d
    assert mx == pytest.approx(BUILT_DEPTH, rel=0.07), mx
    per_m = (lines["volume_m3"] / lines["length_m"]).median()
    assert per_m == pytest.approx(BUILT_WIDTH * BUILT_DEPTH * 2 / 3,
                                  rel=0.08), per_m


def test_the_section_numbers_are_kept_for_comparison(gullies):
    lines, _ = gullies
    for c in ("section_width_m", "section_depth_m", "footprint_quality"):
        assert c in lines.columns, c
    assert set(lines["footprint_quality"]) <= {"good", "partial", "doubtful",
                                               "none"}
    assert (lines["footprint_quality"] == "good").all()


def test_a_sharp_bend_does_not_fan():
    """A ring joined from left and right edge points fans out on the outside
    of every sharp bend, because the section normals diverge there. On Eagle
    Creek that drew wedges tens of metres across the slope. Cells are burned
    from each station's own profile, so the footprint of a right-angle gully
    stays the width of the gully."""
    import rasterio
    from rasterio.transform import from_origin
    from shapely.geometry import LineString

    from geolens.landscape.features import _gully_footprint

    n, cell = 300, 1.0
    yy, xx = np.mgrid[0:n, 0:n].astype(float)
    # An L-shaped trough 8 m wide, 2 m deep, turning 90 degrees at (150,150).
    d1 = np.where(xx <= 150, np.abs(yy - 150), np.hypot(xx - 150, yy - 150))
    d2 = np.where(yy >= 150, np.abs(xx - 150), np.hypot(xx - 150, yy - 150))
    dist = np.minimum(np.where(xx <= 150, np.abs(yy - 150), 1e9),
                      np.where(yy >= 150, np.abs(xx - 150), 1e9))
    dist = np.minimum(dist, np.minimum(d1, d2))
    z = 100.0 - 2.0 * np.clip(1 - (dist / 4.0) ** 2, 0, None)
    path = Path(os.environ.get("GEOLENS_OUT", ".")) / "bend.tif"
    with rasterio.open(str(path), "w", driver="GTiff", width=n, height=n,
                       count=1, dtype="float32", crs="EPSG:32613",
                       transform=from_origin(0, n, cell, cell)) as dst:
        dst.write(z.astype("float32"), 1)
    # centreline in map coords: row r -> y = n - r - 0.5
    line = LineString([(20.5, n - 150.5), (150.5, n - 150.5),
                       (150.5, n - 280.5)])
    with rasterio.open(str(path)) as src:
        poly, st = _gully_footprint(src, line, 20.0, 0.25, 1.0, 1.0)
    assert poly is not None, st
    assert st["footprint_width_m"] == pytest.approx(8.0, rel=0.15), st
    # Nothing far from the trough: every footprint vertex within 6 m of it.
    far = max(line.distance(__import__("shapely.geometry",
                                       fromlist=["Point"]).Point(c))
              for c in poly.exterior.coords) if poly.geom_type == "Polygon" \
        else max(line.distance(__import__("shapely.geometry",
                                          fromlist=["Point"]).Point(c))
                 for g in poly.geoms for c in g.exterior.coords)
    assert far < 6.0, f"footprint reaches {far:.1f} m from the gully"


def test_an_exclude_layer_sets_roads_aside_for_review(gullies, tmp_path):
    """Roads are incised, steep-sided and linear, and shape alone did not
    separate the one on Eagle Creek -- the detector follows a road's ditch,
    and a ditch is a small V. The honest filter is a map of the roads. Lines
    lying along it are set aside into their own layer, never silently
    dropped."""
    import geopandas as gpd

    from geolens.landscape import (build_terrain, detect_gullies,
                                   suggest_slope_std_min, terrain_roughness)

    lines, _ = gullies
    road = lines.iloc[[0]][["geometry"]].copy()       # pretend it is a road
    road_path = tmp_path / "roads.gpkg"
    road.to_file(str(road_path), driver="GPKG")

    t = build_terrain(str(DEM), stream_threshold_km2=0.05, out_prefix="gfx")
    r = terrain_roughness(t, window_m=40.0)
    thr = suggest_slope_std_min(r.slope_std)["slope_std_min"]
    g = detect_gullies(t, r, polygons=True, slope_std_min=thr, max_drainage_km2=0.5,
                       exclude=str(road_path), exclude_buffer_m=10.0,
                       out_prefix="gfxg")
    kept = gpd.read_file(g.gully_lines)
    assert len(kept) == len(lines) - 1, (len(kept), len(lines))
    assert g.parameters["n_excluded"] == 1
    set_aside = Path(g.gully_lines).parent / "gullies_excluded.gpkg"
    assert set_aside.exists(), "the excluded line was dropped, not set aside"
    ex = gpd.read_file(str(set_aside))
    assert len(ex) == 1 and ex["share_along_excluded"].iloc[0] > 0.5
    assert any("gullies_excluded.gpkg" in w for w in g.warnings)
