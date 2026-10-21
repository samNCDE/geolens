"""Alluvial fan detection, against three fans of exactly known geometry.

The fixture is 0.770 km2 per fan, 700 m radius, 5.5% radial gradient, apexes at
known coordinates. Every number below is checked against those, because this
detector has produced confident nonsense three separate times — zero fans,
seventeen false fans, and 139 km2 of "fans" on a 49 km2 DEM — and in each case
nothing failed loudly.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent
FIX = ROOT / "fans"
OUT = ROOT / "fan_test_out"

TRUE_AREA_KM2 = 0.770
TRUE_RADIUS_M = 700.0
TRUE_GRADIENT = 0.055
N_FANS = 3


@pytest.fixture(scope="module", autouse=True)
def fixtures():
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    if not (FIX / "fan_dem.tif").exists():
        subprocess.run([sys.executable, str(ROOT / "make_fans.py")], check=True)


@pytest.fixture(scope="module")
def truth():
    import pandas as pd
    return pd.read_csv(FIX / "fan_truth.csv")


@pytest.fixture(scope="module")
def terrain():
    from geolens.landscape.terrain import build_terrain
    # 0.1 km2: small enough that the network reaches up into the canyons. Too
    # large and there is no channel above the apex to measure a gradient on.
    return build_terrain(str(FIX / "fan_dem.tif"), stream_threshold_km2=0.1,
                         backend="pysheds", out_prefix="ft_terrain")


@pytest.fixture(scope="module")
def fans(terrain):
    from geolens.landscape.fans import detect_alluvial_fans
    return detect_alluvial_fans(terrain, max_radius_m=1500.0,
                                out_prefix="ft_fans").as_dict()


def _gdf(result):
    import geopandas as gpd
    return gpd.read_file(result["fans"])


# -- the fixture itself -------------------------------------------------------

def test_fixture_canyon_is_not_dammed():
    """The apex must be graded to the canyon that feeds it.

    This is the bug that cost a session: the cone was built at
    ``basin_elevation + radius x slope``, putting it 87 m ABOVE the canyon floor
    behind it. Flow could not cross the apex, the filled DEM routed around the
    fan margin, and every apex detector landed on that margin channel 500-800 m
    away in y. It read like a tuning problem and it was a dam.
    """
    import rasterio

    with rasterio.open(FIX / "fan_dem.tif") as src:
        z = src.read(1)
        T = src.transform
        r, _ = rasterio.transform.rowcol(T, 202500, 3703600)
        _, c_in = rasterio.transform.rowcol(T, 202440, 3703600)
        _, c_out = rasterio.transform.rowcol(T, 202560, 3703600)
    step = float(z[r, c_out] - z[r, c_in])
    assert step < 5.0, f"{step:+.1f} m step across the canyon mouth — fan dams its canyon"


def test_fixture_range_actually_drains(terrain):
    """There must be a channel above each apex, or there is nothing to measure.

    An earlier fixture left the range sheeting east past its own canyons; the
    whole range held nine stream cells and no apex had a confined reach above
    it.
    """
    import rasterio

    with rasterio.open(terrain.streams) as src:
        streams = src.read(1) > 0
        T = src.transform
    cols = np.arange(streams.shape[1])
    x = T.c + (cols + 0.5) * abs(T.a)
    west = int(streams[:, x < 202500].sum())
    assert west > 500, f"only {west} stream cells in the whole range"


# -- detection ----------------------------------------------------------------

def test_finds_every_fan_and_nothing_else(fans):
    assert fans["fan_count"] == N_FANS, (
        f"{fans['fan_count']} fans found, {N_FANS} exist"
    )


def test_apexes_are_within_a_cell(fans, truth):
    gdf = _gdf(fans)
    miss = [
        float(np.hypot(truth.apex_x_world - f.apex_x,
                       truth.apex_y_world - f.apex_y).min())
        for _, f in gdf.iterrows()
    ]
    # 10 m cells; 15 m is one cell plus rounding.
    assert max(miss) < 15.0, f"apex position errors {[round(m) for m in miss]} m"


def test_area_is_right(fans):
    gdf = _gdf(fans)
    for a in gdf["area_km2"]:
        err = abs(a - TRUE_AREA_KM2) / TRUE_AREA_KM2
        assert err < 0.15, f"area {a:.3f} km2 vs truth {TRUE_AREA_KM2} ({err:.0%})"


def test_radius_and_gradient_are_right(fans):
    gdf = _gdf(fans)
    for _, f in gdf.iterrows():
        assert abs(f.radius_m - TRUE_RADIUS_M) / TRUE_RADIUS_M < 0.15, (
            f"radius {f.radius_m:.0f} m vs truth {TRUE_RADIUS_M:.0f}"
        )
        assert abs(f.mean_radial_slope - TRUE_GRADIENT) / TRUE_GRADIENT < 0.15, (
            f"gradient {f.mean_radial_slope:.3f} vs truth {TRUE_GRADIENT}"
        )


def test_gradient_break_evidence_is_reported(fans):
    """The reason each apex was called an apex travels with it.

    A fan you cannot interrogate is a fan you have to take on faith.
    """
    gdf = _gdf(fans)
    assert {"gradient_above", "gradient_below", "gradient_ratio"} <= set(gdf.columns)
    for _, f in gdf.iterrows():
        assert f.gradient_above > f.gradient_below, "not a concave break"
        assert f.gradient_ratio >= 1.6


def test_outputs_land_on_disk(fans):
    assert Path(fans["fans"]).exists()
    assert Path(fans["apexes"]).exists()
    assert Path(fans["fan_raster"]).exists()


# -- the runaway guard --------------------------------------------------------

def test_auto_slope_floor_prevents_the_runaway(terrain):
    """The regression test for 139 km2 of fans on a 49 km2 DEM.

    With the floor off and a band starting below the DEM's own slope noise, flat
    basin reads as fan surface and the grow spreads until it hits the radius
    cap. Same DEM, same apexes, 4.5x the area.
    """
    from geolens.landscape.fans import detect_alluvial_fans

    guarded = detect_alluvial_fans(terrain, fan_slope_min=0.01,
                                   auto_slope_floor=True, max_radius_m=1500.0,
                                   out_prefix="ft_guard").as_dict()
    loose = detect_alluvial_fans(terrain, fan_slope_min=0.01,
                                 auto_slope_floor=False, max_radius_m=1500.0,
                                 out_prefix="ft_loose").as_dict()

    assert guarded["parameters"]["fan_slope_min_used"] > 0.01, "floor did not engage"
    assert guarded["total_area_km2"] < 3.0
    # Unguarded, the grow still runs away -- and since v0.29 the cone test
    # catches what the floor did not: the runaway regions are candidates, not
    # fans.
    import geopandas as gpd

    lc = gpd.read_file(loose["candidates"])
    assert lc.area_km2.sum() > 3 * guarded["total_area_km2"], (
        "the unguarded grow should blow up; if it does not, this test has "
        "stopped testing anything"
    )
    assert not lc[lc.area_km2 > 1.5].accepted.any(), (
        "a runaway region was accepted as a fan"
    )
    assert loose["total_area_km2"] < 3.0
    assert any("139 km2" in w or "raised" in w for w in guarded["warnings"]), (
        "raising the floor must be reported, not done silently"
    )


def test_slope_floor_is_reported_either_way(fans):
    p = fans["parameters"]
    assert "fan_slope_min_requested" in p
    assert "fan_slope_min_used" in p
    assert "slope_floor_measured" in p


# -- apex finder in isolation -------------------------------------------------

def test_apex_finder_needs_a_concave_break(terrain, truth):
    """Raising the ratio past what the fixture has must find nothing.

    Guards against a detector that reports apexes regardless of the evidence.
    """
    import rasterio
    from geolens.landscape.fans import _find_apexes

    with rasterio.open(FIX / "fan_dem.tif") as s:
        z = s.read(1).astype("float32")
        cell = abs(s.res[0])
    with rasterio.open(terrain.flow_dir) as s:
        fdir = s.read(1)
    with rasterio.open(terrain.accumulation) as s:
        acc = s.read(1).astype("float32")
    with rasterio.open(terrain.streams) as s:
        streams = s.read(1).astype(bool)

    found = _find_apexes(z, fdir, acc, streams, cell, 0.5, 100.0,
                         1.6, 0.07, 0.01, 0.15, 500.0)
    assert len(found) == N_FANS

    # The fixture's breaks are ratio ~2.0. Nothing should survive a demand of 5.
    none = _find_apexes(z, fdir, acc, streams, cell, 0.5, 100.0,
                        5.0, 0.07, 0.01, 0.15, 500.0)
    assert len(none) == 0


# -- in the landscape pass ------------------------------------------------------

def test_the_landscape_pass_searches_for_fans_and_types_them_iii(tmp_path,
                                                                monkeypatch):
    """The fan search existed from v0.16 and no pass ever called it, so no
    reach was ever Type III. It runs inside the pass now."""
    import geopandas as gpd

    from geolens.landscape.pipeline import run_landscape

    monkeypatch.setenv("GEOLENS_OUT", str(tmp_path))
    out = run_landscape(str(FIX / "fan_dem.tif"), stream_threshold_km2=0.1,
                        stream_types=False)
    assert out["fans"]["fan_count"] == 3
    vt = gpd.read_file(out["valley_types"]["segments"])
    on_fans = vt[vt.valley_type_1996 == "III"]
    assert len(on_fans) >= 3, vt.valley_type_1996.value_counts().to_dict()
    assert (on_fans.basis_1996.str.contains("alluvial fan")).all()
    # on the 2022 card a detected fan is an active fan with the inactive
    # one open, not a blank
    assert (on_fans.valley_type == "U-AL-AF").all()
    assert (on_fans.alternatives == "U-AL-IF").all()


def test_a_big_dem_is_searched_on_a_coarser_copy(tmp_path, monkeypatch,
                                                 terrain):
    """Above FAN_MAX_CELLS the search runs on an averaged copy, and still
    finds the fixture's fans (they are 1.4 km across)."""
    from geolens.landscape import pipeline
    from geolens.safety import Guard

    monkeypatch.setenv("GEOLENS_OUT", str(tmp_path))
    monkeypatch.setattr(pipeline, "FAN_MAX_CELLS", terrain.cells // 3)
    monkeypatch.setattr(pipeline, "FAN_MIN_CELL_M", 0.0)
    fr = pipeline._fans_for_pass(terrain, Guard.from_env())
    assert "fans_dem_dir" in str(list(tmp_path.iterdir()))
    assert fr.parameters["cell_size"] > terrain.cell_size * 1.5
    assert fr.fan_count == 3
