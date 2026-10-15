"""The watershed as the analysis unit, instead of a tile.

A rectangular tile cuts across drainage divides, so flow paths are truncated at
every edge and accumulation, HAND and everything derived from them are wrong
there. A basin derived from a coarsened copy of the DEM is better but still
approximate: its boundary is drawn at the coarse cell size, not the real
divide.

A complete watershed has no inflow across its boundary by definition, so
accumulation computed inside the clip is exactly right for every cell in it —
and it is the unit the work is actually about, valley bottom to the top of the
watershed.

``test_a_rectangle_is_not_a_watershed`` is the one that makes the case with a
number: a rectangle over the middle of the landscape fixture is missing
**365% of its own area** in upstream contribution.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "ws_test_out"
DEM = ROOT / "landscape" / "landscape_dem.tif"


@pytest.fixture(scope="module", autouse=True)
def _env():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"


@pytest.fixture(scope="module")
def outlet():
    """The real outlet: the cell with the largest contributing area."""
    if not DEM.exists():
        pytest.skip("landscape fixture not built")
    import rasterio
    from geolens.landscape import flow_routing

    fl = flow_routing(str(DEM), out_prefix="ws_fl")
    with rasterio.open(fl.accumulation) as src:
        acc = src.read(1)
        r, c = np.unravel_index(int(np.argmax(acc)), acc.shape)
        x, y = rasterio.transform.xy(src.transform, int(r), int(c))
    return float(x), float(y)


def test_a_pour_point_gives_a_complete_watershed(outlet):
    from geolens.landscape import watershed

    w = watershed(str(DEM), pour_point=outlet, out_prefix="ws_pp")
    assert w["boundary_source"] == "pour_point"
    assert w["inflow"]["is_complete_watershed"] is True
    assert w["area_km2"] > 100.0, "the outlet should drain most of the fixture"
    assert Path(w["dem"]).exists() and Path(w["boundary"]).exists()


def test_the_delineated_boundary_survives_a_round_trip(outlet):
    """Two independent code paths have to agree.

    ``pour_point`` claims completeness *by construction* — it is built from the
    flow graph. ``check_inflow`` works the other way round, by asking which
    outside cells drain in. Feeding the first's answer to the second is the
    only check here that is not self-referential.
    """
    from geolens.landscape import watershed

    w = watershed(str(DEM), pour_point=outlet, out_prefix="ws_rt1")
    back = watershed(str(DEM), boundary=w["boundary"], out_prefix="ws_rt2")
    assert back["inflow"]["inflow_cells"] == 0, (
        f"the inflow check found {back['inflow']['inflow_cells']} cells "
        f"draining into a watershed delineated from the flow graph — the two "
        f"disagree, and one of them is wrong"
    )
    assert back["area_km2"] == pytest.approx(w["area_km2"], rel=0.02)


def test_a_rectangle_is_not_a_watershed():
    """Why tiling does not work for this, as a number rather than an argument.

    Measured on the landscape fixture: a rectangle over the middle third has
    720 cells draining into it, carrying 3.65x the rectangle's own area in
    upstream contribution. Analyse that tile on its own and every
    contributing-area-derived quantity in it — the stream network, HAND,
    confinement, valley bottoms — is missing most of its input.
    """
    import geopandas as gpd
    import rasterio
    from shapely.geometry import box
    from geolens.landscape import watershed

    with rasterio.open(str(DEM)) as src:
        b, crs = src.bounds, src.crs
    rect = box(b.left + (b.right - b.left) * 0.3,
               b.bottom + (b.top - b.bottom) * 0.3,
               b.left + (b.right - b.left) * 0.7,
               b.bottom + (b.top - b.bottom) * 0.7)
    rp = OUT / "rect.gpkg"
    gpd.GeoDataFrame({"i": [0]}, geometry=[rect], crs=crs).to_file(
        str(rp), driver="GPKG")

    w = watershed(str(DEM), boundary=str(rp), out_prefix="ws_rect")
    info = w["inflow"]
    assert info["is_complete_watershed"] is False
    assert info["inflow_cells"] > 100
    assert info["inflow_fraction"] > 1.0, (
        f"a rectangle across a drainage network should be missing more than "
        f"its own area in upstream contribution; got "
        f"{info['inflow_fraction']:.2f}"
    )
    assert any("study area, not a complete watershed" in m
               for m in w["warnings"])


def test_a_pour_point_off_the_channel_is_snapped_and_says_so(outlet):
    """One cell off the channel gives a few hundred square metres.

    A coordinate typed from a map never lands on the routed cell. Without
    snapping this reads as a broken delineation rather than a misplaced point.
    """
    from geolens.landscape import watershed

    x, y = outlet
    off = (x - 120.0, y + 120.0)          # a few cells away
    w = watershed(str(DEM), pour_point=off, snap_radius_m=300.0,
                  out_prefix="ws_snap")
    assert w["pour_point_snap_m"] > 0.0, "the point did not move at all"
    assert w["area_km2"] > 100.0, (
        f"snapping failed to find the channel: {w['area_km2']:.4f} km2"
    )


def test_it_refuses_two_ways_of_naming_the_same_thing(outlet):
    from geolens.safety import GeolensError
    from geolens.landscape import watershed

    with pytest.raises(GeolensError, match="exactly one"):
        watershed(str(DEM), pour_point=outlet, huc="auto")
    with pytest.raises(GeolensError, match="exactly one"):
        watershed(str(DEM))


def test_a_boundary_with_no_crs_is_refused():
    """It would be placed against the DEM by accident of coordinate values."""
    import geopandas as gpd
    from shapely.geometry import box
    from geolens.safety import GeolensError
    from geolens.landscape import watershed

    p = OUT / "nocrs.gpkg"
    gpd.GeoDataFrame({"i": [0]}, geometry=[box(0, 0, 1, 1)]).to_file(
        str(p), driver="GPKG")
    with pytest.raises(GeolensError, match="no CRS"):
        watershed(str(DEM), boundary=str(p), out_prefix="ws_nocrs")


def test_the_huc_selftest_reports_rather_than_raises():
    """The network is not available here; the point is that it says so."""
    from geolens.landscape import huc_selftest

    out = huc_selftest()
    assert "ok" in out and "service" in out
    if not out["ok"]:
        assert "error" in out and "advice" in out


def test_a_pour_point_can_be_a_point_layer(outlet, tmp_path):
    """The ArcGIS tool hands over a point layer, not coordinates. It is read
    in its own CRS, so a lon/lat point works on a projected DEM."""
    import geopandas as gpd
    import rasterio
    from shapely.geometry import Point

    from geolens.landscape import watershed

    with rasterio.open(DEM) as src:
        crs = src.crs
    pts = gpd.GeoDataFrame(geometry=[Point(*outlet)], crs=crs).to_crs(4326)
    path = tmp_path / "pour.gpkg"
    pts.to_file(path, driver="GPKG")
    w = watershed(str(DEM), pour_point=str(path), out_prefix="ws_layer")
    ref = watershed(str(DEM), pour_point=outlet, out_prefix="ws_ref")
    assert abs(w["area_km2"] - ref["area_km2"]) < 0.01 * ref["area_km2"]
