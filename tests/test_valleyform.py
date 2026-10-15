"""Valley cross-section form, against valleys of known geometry.

Rosgen's valley types are distinguished by the shape of the whole valley, floor
to divide. This measures that, so every number here has an analytic truth:

    z(x) = 0                                      |x| <= half_floor      (floor)
         = relief * ((|x| - half_floor)/run)**n   within `run` of it     (wall)
         = relief                                 beyond                 (crest)

which gives, exactly:

    Vfw        = 2 * half_floor
    Vf         = 2 * Vfw / ((Eld - Esc) + (Erd - Esc)) = Vfw / relief
    side_slope = relief / run
    shape_n    = n     (1 = V-notch, 2 = glacial U)

Three valleys, chosen because they are the three Rosgen cases geometry alone
can actually separate: a V-notch canyon (I), a glacial trough (V), and a broad
gentle valley (VIII).
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import rasterio
import geopandas as gpd
from rasterio.transform import from_origin
from shapely.geometry import LineString

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "vform_test_out"

CELL = 5.0
CRS = "EPSG:32612"
X0, Y0 = 500000.0, 4000000.0
DOWN_GRADE = 0.01          # gentle downstream fall, so a thalweg exists


@pytest.fixture(scope="module", autouse=True)
def _env():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"


def _valley(name, half_floor_m, relief_m, run_m, n_exp, width_m=3000.0,
            length_m=2000.0):
    """Build a valley of exactly known cross-section, plus its centerline."""
    w = int(width_m / CELL)
    h = int(length_m / CELL)
    axis = w // 2
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.abs(xx - axis) * CELL                      # distance from axis, m

    wall = np.zeros_like(d, dtype="float64")
    m = d > half_floor_m
    t = np.clip((d - half_floor_m) / run_m, 0.0, 1.0)
    wall[m] = relief_m * (t[m] ** n_exp)

    z = (1500.0 - DOWN_GRADE * (yy * CELL) + wall).astype("float32")

    p = OUT / f"{name}.tif"
    prof = {"driver": "GTiff", "width": w, "height": h, "count": 1,
            "dtype": "float32", "crs": CRS, "nodata": -9999.0,
            "transform": from_origin(X0, Y0 + h * CELL, CELL, CELL)}
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(z, 1)

    # Centerline down the axis. Row 0 is the TOP of the raster (north), and
    # elevation falls with increasing row, so the line runs north to south.
    cx = X0 + (axis + 0.5) * CELL
    line = LineString([(cx, Y0 + h * CELL - 1.0), (cx, Y0 + 1.0)])
    cl = OUT / f"{name}_cl.gpkg"
    gpd.GeoDataFrame({"id": [1]}, geometry=[line], crs=CRS).to_file(
        str(cl), driver="GPKG")

    truth = {
        "vfw": 2 * half_floor_m,
        "relief": relief_m,
        "vf": (2 * half_floor_m) / relief_m,
        "side_slope": relief_m / run_m,
        "shape_n": n_exp,
    }
    return str(p), str(cl), truth


def _run(name, **kw):
    from geolens.landscape.valleyform import valley_form
    import pandas as pd

    dem, cl, truth = _valley(name, **kw)
    r = valley_form(dem, cl, spacing_m=200.0, max_search_m=1400.0,
                    min_rise_m=3.0, out_prefix=f"vf_{name}")
    df = pd.read_csv(r.table)
    return r, df[df["complete"]], truth


# -- the three cases ----------------------------------------------------------

def test_v_notch_canyon():
    """Rosgen I: narrow floor, steep walls, high relief. Vf must be small."""
    r, df, truth = _run("vnotch", half_floor_m=20.0, relief_m=300.0,
                        run_m=400.0, n_exp=1.0)
    assert len(df) >= 5, f"only {len(df)} complete sections"

    assert np.median(df["valley_floor_width_m"]) == pytest.approx(
        truth["vfw"], abs=3 * CELL)
    assert np.median(df["vf"]) == pytest.approx(truth["vf"], rel=0.30), (
        f"Vf {np.median(df['vf']):.3f} vs truth {truth['vf']:.3f}"
    )
    assert np.median(df["valley_relief_m"]) == pytest.approx(
        truth["relief"], rel=0.10)
    assert np.median(df["side_slope"]) == pytest.approx(
        truth["side_slope"], rel=0.20)
    assert np.median(df["shape_n"]) == pytest.approx(1.0, abs=0.25), (
        "a V-notch must fit n~1"
    )
    assert np.median(df["vf"]) < 0.5, "a V-notch must land in the v_shaped class"


def test_glacial_trough_is_separated_from_a_v_by_shape_alone():
    """Rosgen V vs I — the distinction the old crosswalk could not make.

    Same relief, same run, so the same side-slope gradient and a similar Vf.
    Only the exponent separates them, which is exactly why it was added.
    """
    _, v_df, _ = _run("v_cmp", half_floor_m=40.0, relief_m=300.0,
                      run_m=500.0, n_exp=1.0)
    _, u_df, _ = _run("u_cmp", half_floor_m=40.0, relief_m=300.0,
                      run_m=500.0, n_exp=2.0)

    v_n = float(np.median(v_df["shape_n"]))
    u_n = float(np.median(u_df["shape_n"]))
    assert v_n == pytest.approx(1.0, abs=0.3), f"V fitted n={v_n:.2f}"
    assert u_n == pytest.approx(2.0, abs=0.4), f"U fitted n={u_n:.2f}"
    assert u_n - v_n > 0.5, (
        f"U and V are not separable by shape here ({u_n:.2f} vs {v_n:.2f})"
    )


def test_broad_gentle_valley():
    """Rosgen VIII: wide floor, low relief, gentle walls. Vf must be large."""
    r, df, truth = _run("broad", half_floor_m=400.0, relief_m=60.0,
                        run_m=600.0, n_exp=1.0)
    assert len(df) >= 5
    assert np.median(df["vf"]) == pytest.approx(truth["vf"], rel=0.30)
    assert np.median(df["vf"]) > 4.0, "a broad valley must land in very_broad"
    assert np.median(df["side_slope"]) == pytest.approx(
        truth["side_slope"], rel=0.25)


# -- the properties that make it usable ---------------------------------------

def test_vf_separates_the_three_valleys():
    """The whole point: one dimensionless number ordering them correctly."""
    _, narrow, _ = _run("ord_v", half_floor_m=20.0, relief_m=300.0,
                        run_m=400.0, n_exp=1.0)
    _, mid, _ = _run("ord_m", half_floor_m=150.0, relief_m=150.0,
                     run_m=500.0, n_exp=1.0)
    _, broad, _ = _run("ord_b", half_floor_m=400.0, relief_m=60.0,
                       run_m=600.0, n_exp=1.0)

    a, b, c = (float(np.median(d["vf"])) for d in (narrow, mid, broad))
    assert a < b < c, f"Vf did not order the valleys: {a:.2f}, {b:.2f}, {c:.2f}"


def test_an_incomplete_search_is_reported_not_silently_averaged():
    """A section that never reached a divide is a lower bound, not a value."""
    from geolens.landscape.valleyform import valley_form
    import pandas as pd

    dem, cl, _ = _valley("wide_srch", half_floor_m=400.0, relief_m=60.0,
                         run_m=600.0, n_exp=1.0)
    r = valley_form(dem, cl, spacing_m=250.0, max_search_m=450.0,
                    min_rise_m=3.0, out_prefix="vf_short")
    df = pd.read_csv(r.table)
    assert (~df["complete"]).any(), "the truncated search was not flagged"
    assert any("max_search_m" in w for w in r.warnings), (
        "a mostly-incomplete run did not warn"
    )


def test_asymmetry_is_near_zero_on_a_symmetric_valley():
    """It flags structural control, so it must not fire on ordinary terrain."""
    _, df, _ = _run("sym", half_floor_m=50.0, relief_m=200.0,
                    run_m=400.0, n_exp=1.0)
    assert abs(float(np.median(df["asymmetry"]))) < 0.1


def test_asymmetry_detects_a_one_sided_valley():
    """One wall twice the other — the fault-controlled signature."""
    from geolens.landscape.valleyform import valley_form
    import pandas as pd

    w, h = 600, 400
    axis = w // 2
    yy, xx = np.mgrid[0:h, 0:w]
    d = (xx - axis) * CELL
    wall = np.where(d > 0,
                    np.clip(d / 400.0, 0, 1) * 300.0,          # steep right
                    np.clip(-d / 800.0, 0, 1) * 100.0)         # gentle left
    wall[np.abs(d) <= 50.0] = 0.0
    z = (1500.0 - DOWN_GRADE * (yy * CELL) + wall).astype("float32")

    p = OUT / "asym.tif"
    prof = {"driver": "GTiff", "width": w, "height": h, "count": 1,
            "dtype": "float32", "crs": CRS, "nodata": -9999.0,
            "transform": from_origin(X0, Y0 + h * CELL, CELL, CELL)}
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(z, 1)
    cx = X0 + (axis + 0.5) * CELL
    cl = OUT / "asym_cl.gpkg"
    gpd.GeoDataFrame({"id": [1]}, geometry=[
        LineString([(cx, Y0 + h * CELL - 1.0), (cx, Y0 + 1.0)])], crs=CRS
    ).to_file(str(cl), driver="GPKG")

    r = valley_form(str(p), str(cl), spacing_m=200.0, max_search_m=1200.0,
                    min_rise_m=3.0, out_prefix="vf_asym")
    df = pd.read_csv(r.segment_table)
    assert abs(float(df["asymmetry"].iloc[0])) > 0.35, (
        f"asymmetry {float(df['asymmetry'].iloc[0]):.2f} on a valley with one "
        f"wall 3x the other"
    )
    assert bool(df["persistent_asymmetry"].iloc[0])


def test_the_lines_on_the_map_run_divide_to_divide():
    """v0.28: each section line is the part that was measured. It used to be
    the whole cut -- 2 x max_search_m, 12 km on every Ruidoso section -- so the
    layer was a mesh of identical lines crossing the neighbouring valleys."""
    import geopandas as gpd
    from pathlib import Path

    r, df, truth = _run("vnotch_lines", half_floor_m=20.0, relief_m=300.0,
                        run_m=400.0, n_exp=1.0)
    g = gpd.read_file(r.sections).set_index("xs_id")
    done = df.set_index("xs_id")
    for xs, row in done.iterrows():
        want = row["divide_offset_right_m"] - row["divide_offset_left_m"]
        assert g.loc[xs].geometry.length == pytest.approx(want, abs=1e-6)
        assert want < 2 * 1400.0 * 0.9        # not the whole search
    fl = gpd.read_file(Path(r.sections).with_name("valley_form_floors.gpkg"))
    assert len(fl) > 0
    # the floor line spans the measured floor, give or take one sample
    f = fl.set_index("xs_id").loc[done.index[0]]
    assert f.geometry.length == pytest.approx(
        done.iloc[0]["valley_floor_width_m"], abs=2 * CELL)
