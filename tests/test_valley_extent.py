"""The valley as an AREA, floor to ridgetop.

Three constructions were tried. The first put the Rosgen type on the stream
centreline, which is a classification of a valley drawn as a line. The second
walked each cross-section out to its measured divide and stitched the divide
points into a ribbon; on five straight parallel troughs that looked right, and
on real terrain it was 2,186 triangular shards covering 55% of the DEM --
every summary statistic said "tiles perfectly, 1.00x overlap", because that is
true of shards too.

The third is the definition: a reach's valley is the ground that drains to it.
Its boundary is the drainage divide, which is what floor-to-ridgetop means.
These tests are about the properties that definition guarantees, because those
are exactly what the ribbon could not deliver.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "vext_out"
DEM = ROOT / "landscape" / "landscape_dem.tif"


@pytest.fixture(scope="module")
def built():
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"
    if not DEM.exists():
        pytest.skip("landscape fixture not built")

    import geopandas as gpd

    from geolens.landscape import (build_terrain, extract_network,
                                   valley_catchments)

    t = build_terrain(str(DEM), stream_threshold_km2=0.3, out_prefix="ve_t")
    n = extract_network(t, segment_length_m=300.0, out_prefix="ve_n")
    path = valley_catchments(t.flow_dir, n.segments, streams=t.streams)
    return t, n, gpd.read_file(path)


def test_every_reach_gets_an_area_not_a_line(built):
    _t, n, g = built
    import geopandas as gpd

    assert len(g) > 0
    assert set(g.geom_type) <= {"Polygon", "MultiPolygon"}, (
        f"valley extent came back as {set(g.geom_type)} — a valley type on a "
        f"line is a classification of a valley, not a valley"
    )
    seg = gpd.read_file(n.segments)
    assert len(g) >= 0.5 * len(seg), (
        f"only {len(g)} of {len(seg)} reaches got a valley"
    )


def test_the_valleys_tile_with_no_overlap(built):
    """Every cell flows to exactly one reach, so this is guaranteed by
    construction rather than cleaned up afterwards."""
    _t, _n, g = built
    total = float(g.geometry.area.sum())
    union = float(g.geometry.union_all().area)
    assert total == pytest.approx(union, rel=1e-6), (
        f"{total / union:.3f}x overlap — the valleys are piling on top of "
        f"each other"
    )


def test_they_cover_essentially_the_whole_dem(built):
    """The ribbon covered 55% and left holes everywhere; a partition of flow
    leaves out only what drains off the edge."""
    import rasterio

    t, _n, g = built
    with rasterio.open(t.dem) as src:
        b = src.bounds
        extent = (b.right - b.left) * (b.top - b.bottom)
    covered = float(g.geometry.union_all().area) / extent
    assert covered > 0.9, f"valleys cover only {covered:.0%} of the DEM"


def test_every_geometry_is_valid(built):
    """Polygonised cells meet at corners, which shapely calls invalid and most
    GIS refuses to clip or dissolve."""
    _t, _n, g = built
    bad = g[~g.is_valid]
    assert bad.empty, f"{len(bad)} invalid valley polygons"


def test_a_valley_contains_its_own_reach(built):
    """The obvious property, and the one the ribbon failed: the polygon has to
    be around the channel it belongs to."""
    import geopandas as gpd

    _t, n, g = built
    seg = gpd.read_file(n.segments)
    key = "seg_id" if "seg_id" in g.columns else "segment_id"
    j = g.merge(seg[[key, "geometry"]].rename(columns={"geometry": "line"}),
                on=key, how="inner")
    assert len(j) > 10
    inside = [row.line.intersection(row.geometry).length / max(row.line.length, 1e-9)
              for row in j.itertuples()]
    frac = float(np.median(inside))
    assert frac > 0.8, (
        f"the median reach lies only {frac:.0%} inside its own valley polygon"
    )


def test_the_area_column_is_metres_on_a_foot_grid(tmp_path):
    """CRS units squared are not square metres, and the difference on the grid
    this package is used on is 10.76."""
    import geopandas as gpd
    import rasterio
    from rasterio.crs import CRS

    from geolens.landscape import (build_terrain, extract_network,
                                   valley_catchments)

    src_path = DEM
    ft = tmp_path / "ft.tif"
    with rasterio.open(str(src_path)) as src:
        prof, z = src.profile.copy(), src.read(1)
    # Same grid, relabelled as US survey feet: cells and areas change meaning.
    prof.update(crs=CRS.from_epsg(6529))
    with rasterio.open(str(ft), "w", **prof) as dst:
        dst.write(z, 1)

    os.environ["GEOLENS_OUT"] = str(tmp_path / "o")
    (tmp_path / "o").mkdir()
    t = build_terrain(str(ft), stream_threshold_km2=0.3, out_prefix="ft_t")
    n = extract_network(t, segment_length_m=300.0, out_prefix="ft_n")
    g = gpd.read_file(valley_catchments(t.flow_dir, n.segments))
    os.environ["GEOLENS_OUT"] = str(OUT)

    crs_units2 = float(g.geometry.area.sum())
    reported_m2 = float(g["valley_area_m2"].sum())
    assert reported_m2 == pytest.approx(crs_units2 * 0.30480060960121924 ** 2,
                                        rel=1e-6), (
        "valley_area_m2 is holding square feet"
    )
