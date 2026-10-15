"""The same ground in two units must give the same physical answer.

Every unit bug this package has had was invisible to its own test suite, and
for one reason: the fixtures were metric, so ``metres_per_unit`` was 1.0 and
every missing conversion was a no-op. The bugs only appeared on EPSG:6529
(NAD83(2011) New Mexico Central, US survey feet), which is the grid it is
actually used on. Found that way, in one session:

* gully cross-sections cut at ``section_width_m`` CRS units -- 60 m asked for,
  60 ft = 18.3 m cut, "2 sections x 3 points" on a real run;
* ``min_depth_m`` compared against foot elevations, three times shallower
  than asked for;
* gully ``width_m``, ``mean_depth_m``, ``volume_m3`` and ``measured_area_m2``
  written straight out of CRS units into columns named for metres;
* valley extent area in square feet under ``valley_area_m2``;
* a width/depth ratio mixing a horizontal unit with a vertical one.

``tests/make_terrain.py`` writes one landscape-evolution surface twice -- once
in US survey feet, once in metres -- describing the same ground. Anything
reported in metres, square metres or as a dimensionless ratio has to agree
between the two. A conversion that is missing on one path cannot survive that,
and no amount of staring at the code finds them as reliably.

The tolerance is not tight for its own sake: the two grids are resampled the
same way but flow routing on a 10 m grid and a 32.8084 ftUS grid is not
bit-identical, because the cell centres land on different floating-point
coordinates. What must not differ is the ANSWER, by more than a percent or
two. A missing conversion is wrong by 3.28 or 10.76, which is not subtle.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "units_out"
FT = ROOT / "terrain" / "terrain_small_dem.tif"
M = ROOT / "terrain" / "terrain_small_dem_m.tif"
FT_US = 1200.0 / 3937.0


@pytest.fixture(scope="module")
def passes():
    if not (FT.exists() and M.exists()):
        subprocess.run([sys.executable, str(ROOT / "make_terrain.py"),
                        "--small"], check=True)
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"

    from geolens import api

    kw = dict(stream_threshold_km2=0.2, segment_length_m=400, geology=None)
    # One output folder per pass. They shared one, so the metric pass
    # overwrote the foot pass's files and every test that read a layer back
    # compared the metric result with itself.
    out = []
    for dem, sub in ((FT, "ft"), (M, "m")):
        os.environ["GEOLENS_OUT"] = str(OUT / sub)
        (OUT / sub).mkdir(parents=True, exist_ok=True)
        out.append(api.landscape_pass(str(dem), **kw))
    return tuple(out)


def _close(a, b, tol=0.03):
    if a is None or b is None:
        return False
    if not (np.isfinite(a) and np.isfinite(b)):
        return False
    return abs(a - b) <= tol * max(abs(a), abs(b), 1e-12)


def test_the_two_fixtures_really_are_the_same_ground():
    """Guard on the fixture. If these drift apart the rest tests nothing."""
    import rasterio

    with rasterio.open(str(FT)) as f, rasterio.open(str(M)) as m:
        assert f.shape == m.shape
        assert f.crs.linear_units_factor[1] == pytest.approx(FT_US, rel=1e-9)
        assert m.crs.linear_units_factor[1] == pytest.approx(1.0)
        assert abs(f.transform.a) * FT_US == pytest.approx(abs(m.transform.a),
                                                           rel=1e-9)
        zf, zm = f.read(1), m.read(1)
        assert np.allclose(zf * FT_US, zm, atol=1e-2), (
            "the foot DEM's elevations are not the metric one's in feet"
        )


def test_network_length_agrees(passes):
    ft, m = passes
    a, b = ft["network"]["total_length_km"], m["network"]["total_length_km"]
    assert _close(a, b), f"network {a:.2f} km on feet vs {b:.2f} km on metres"


def test_valley_bottom_area_agrees(passes):
    ft, m = passes
    a, b = ft["valley"]["valley_area_km2"], m["valley"]["valley_area_km2"]
    assert _close(a, b, 0.05), f"valley bottom {a:.2f} vs {b:.2f} km2"


def test_valley_extent_area_agrees(passes):
    """``valley_area_m2`` held square feet before this existed."""
    import geopandas as gpd

    ft, m = passes
    a = gpd.read_file(ft["valley_extent"])["valley_area_km2"].sum()
    b = gpd.read_file(m["valley_extent"])["valley_area_km2"].sum()
    assert _close(a, b, 0.05), f"valley extent {a:.2f} vs {b:.2f} km2"


def test_valley_form_measurements_agree(passes):
    """Widths and reliefs in metres, and two dimensionless shape numbers."""
    import pandas as pd

    ft, m = passes
    a = pd.read_csv(ft["valley_form"]["table"])
    b = pd.read_csv(m["valley_form"]["table"])
    for col, tol in (("valley_top_width_m", 0.06),
                     ("valley_floor_width_m", 0.15),
                     ("valley_relief_m", 0.06),
                     ("vf", 0.10),
                     ("shape_n", 0.10),
                     ("side_slope", 0.06)):
        if col not in a.columns or col not in b.columns:
            continue
        # Floor width is quantised to the 10 m cell, and its median sits
        # between the 30 and 50 m steps: one section of 62 moving by a cell
        # flips it from 40 to 50. A unit bug is x3.28; compare the mean.
        stat = "mean" if col == "valley_floor_width_m" else "median"
        ma, mb = float(getattr(a[col], stat)()), float(getattr(b[col], stat)())
        assert _close(ma, mb, tol), (
            f"{col}: {ma:.4g} on feet vs {mb:.4g} on metres — a factor of "
            f"{max(ma, mb) / max(min(ma, mb), 1e-12):.2f}"
        )


def test_channel_and_confinement_agree(passes):
    import geopandas as gpd

    ft, m = passes
    a = gpd.read_file(ft["classified"]["segments"])
    b = gpd.read_file(m["classified"]["segments"])
    for col in ("channel_width_m", "valley_width_m", "confinement_ratio",
                "drainage_km2", "gradient"):
        if col not in a.columns:
            continue
        ma, mb = float(a[col].median()), float(b[col].median())
        assert _close(ma, mb, 0.08), f"{col}: {ma:.4g} vs {mb:.4g}"


def test_stream_type_discriminators_agree(passes):
    """Slope and sinuosity are dimensionless; width/depth is only
    dimensionless once the horizontal and vertical units are reconciled."""
    import geopandas as gpd

    ft, m = passes
    if "stream_types" not in ft or "stream_types" not in m:
        pytest.skip("stream types not produced")
    a = gpd.read_file(ft["stream_types"]["segments"])
    b = gpd.read_file(m["stream_types"]["segments"])
    for col in ("sinuosity", "slope"):
        ma, mb = float(a[col].median()), float(b[col].median())
        assert _close(ma, mb, 0.05), f"{col}: {ma:.4g} vs {mb:.4g}"
    assert a["stream_candidates"].value_counts().head(3).index.tolist() == \
        b["stream_candidates"].value_counts().head(3).index.tolist(), (
            "the commonest stream-type candidate sets differ between the two "
            "unit systems"
        )


def test_valley_type_counts_agree(passes):
    ft, m = passes
    a = ft["valley_types"]["type_counts"]
    b = m["valley_types"]["type_counts"]
    assert set(a) == set(b), f"different valley types: {a} vs {b}"
    # Compare the DISTRIBUTION, not raw counts. The two grids do not produce
    # bit-identical networks -- cell centres land on different floating-point
    # coordinates -- so a type with nine reaches on one and eleven on the
    # other is sampling noise, while a unit bug moves whole classes.
    na, nb = sum(a.values()), sum(b.values())
    for t in a:
        fa, fb = a[t] / max(na, 1), b[t] / max(nb, 1)
        assert abs(fa - fb) < 0.08, (
            f"type {t}: {fa:.0%} of reaches on feet vs {fb:.0%} on metres "
            f"({a[t]} vs {b[t]})"
        )


def test_gully_measurements_agree(passes):
    """The path where two unit bugs were sitting at once.

    Sections were cut 3.28x too narrow AND the minimum bank height was 3.28x
    too permissive, and then width, depth, volume and footprint area were
    written out of CRS units into columns named for metres.
    """
    import geopandas as gpd

    from geolens.landscape import (detect_gullies, suggest_slope_std_min,
                                   terrain_from_result, terrain_roughness)

    ft, m = passes
    got = {}
    for tag, res in (("ft", ft), ("m", m)):
        t = terrain_from_result(res["terrain"])
        rr = terrain_roughness(t, window_m=60.0, out_prefix=f"up_r_{tag}")
        thr = suggest_slope_std_min(rr.slope_std)["slope_std_min"]
        g = detect_gullies(t, rr, slope_std_min=thr, max_drainage_km2=0.2,
                           out_prefix=f"up_g_{tag}")
        d = g.as_dict()
        gdf = gpd.read_file(d["gullies"]) if d.get("gullies") else None
        got[tag] = (d, gdf)

    (da, ga), (db, gb) = got["ft"], got["m"]
    assert _close(float(da["total_length_km"]), float(db["total_length_km"]),
                  0.12), (
        f"gully length {da['total_length_km']:.2f} vs "
        f"{db['total_length_km']:.2f} km")
    if ga is not None and gb is not None and len(ga) and len(gb):
        for col, tol in (("width_m", 0.20), ("mean_depth_m", 0.25),
                         ("measured_area_m2", 0.30)):
            if col not in ga.columns:
                continue
            ma = float(np.nanmedian(ga[col].astype(float)))
            mb = float(np.nanmedian(gb[col].astype(float)))
            if not (np.isfinite(ma) and np.isfinite(mb)):
                continue
            assert _close(ma, mb, tol), (
                f"gully {col}: {ma:.4g} on feet vs {mb:.4g} on metres — a "
                f"factor of {max(ma, mb) / max(min(ma, mb), 1e-12):.2f}"
            )


def test_riparian_areas_agree(passes, tmp_path):
    """Four unit mixes in one function: the background window, the minimum
    patch size, the HAND ceiling and the reported areas. On a foot grid the
    window was a third of its size, patches a tenth the size asked for got
    through, and riparian_km2 was square feet divided by a million."""
    import rasterio

    from geolens import api

    ft, m = passes
    got = {}
    for tag, res, dem in (("ft", ft, FT), ("m", m, M)):
        # The same synthetic NDVI on each grid: green wherever HAND is low,
        # which is what riparian vegetation in dry country looks like.
        with rasterio.open(res["terrain"]["hand"]) as src:
            h = src.read(1).astype("float32")
            prof = src.profile.copy()
            zunit = FT_US if tag == "ft" else 1.0
        h_m = np.where(np.isfinite(h), h * zunit, 999.0)
        ndvi = np.where(h_m < 2.0, 0.55, 0.12).astype("float32")
        prof.update(dtype="float32", nodata=-9999.0, count=1)
        p = tmp_path / f"ndvi_{tag}.tif"
        with rasterio.open(str(p), "w", **prof) as dst:
            dst.write(ndvi, 1)
        got[tag] = api.riparian_band(
            str(p), hand=res["terrain"]["hand"],
            streams=res["terrain"]["streams"],
            background_window_m=600.0, max_hand_m=5.0, min_excess=0.12,
            out_prefix=f"rip_{tag}")

    a, b = got["ft"]["riparian_km2"], got["m"]["riparian_km2"]
    assert a > 0 and b > 0, (a, b)
    assert _close(a, b, 0.08), (
        f"riparian {a:.4f} km2 on feet vs {b:.4f} km2 on metres — a factor "
        f"of {max(a, b) / max(min(a, b), 1e-12):.2f}"
    )


def test_the_run_planner_reports_metres_on_a_foot_grid():
    """Sam's full Ruidoso plan read "1,785,000,000 cells @ 2.5 m, 11,156 km2"
    for a 2.5 ftUS grid over 1,036 km2. The cell count and the RAM estimate
    were right; the size and area were in feet under metre labels."""
    from geolens.landscape.plan import plan_run
    from geolens.raster import describe_raster

    if not (FT.exists() and M.exists()):
        pytest.skip("terrain fixture not built")
    a, b = plan_run(FT), plan_run(M)
    assert a.cell_size == pytest.approx(b.cell_size, rel=1e-3)
    assert a.area_km2 == pytest.approx(b.area_km2, rel=1e-3)
    d = describe_raster(FT)
    assert d["crs"]["metres_per_unit"] == pytest.approx(FT_US, rel=1e-9)
