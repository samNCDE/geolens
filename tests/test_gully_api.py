"""The gully pass in the run log, and reusing routing instead of repeating it.

``geolens.api`` wraps every public function so results land on disk without the
caller doing anything — and gullies were the one step not in it, because the
notebook called ``geolens.landscape.detect_gullies`` directly. The step whose
parameters most need a record of what was tried had no record at all.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "gapi_test_out"
FIX = ROOT / "gullies" / "gully_dem.tif"


@pytest.fixture(scope="module", autouse=True)
def _env():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ.pop("GEOLENS_PERSIST", None)      # persistence ON for this file


@pytest.fixture(scope="module")
def base():
    if not FIX.exists():
        pytest.skip("gully fixture not built")
    from geolens.landscape import build_terrain
    return build_terrain(str(FIX), stream_threshold_km2=0.2, out_prefix="ga_t")


def test_a_gully_run_lands_in_the_run_log(base):
    from geolens import api

    r = api.detect_gullies(base.as_dict(), out_prefix="ga_log")
    assert r["gully_count"] > 0
    assert "_run_record" in r, "the api wrapper did not persist this run"

    hist = api.run_history(function="detect_gullies")["runs"]
    assert hist, "detect_gullies is missing from the run log"
    saved = api.load_run(hist[0]["record"])
    assert saved["result"]["gully_count"] == r["gully_count"]
    # The arguments matter as much as the answer: "which threshold produced
    # these gullies" is the question this log exists to answer.
    assert "slope_std_min" in saved["arguments"]


def test_it_picks_the_threshold_and_says_how(base):
    """A threshold chosen for you is only acceptable if it shows its working."""
    from geolens import api

    r = api.detect_gullies(base.as_dict(), out_prefix="ga_auto")
    c = r["threshold_choice"]
    assert c and "slope_std_min" in c
    assert c["bimodal"] is True
    assert 0.0 < c["slope_std_min"] < 1.0
    assert "percentile" in c and "separation_decades" in c


def test_an_explicit_threshold_is_not_overridden(base):
    from geolens import api

    r = api.detect_gullies(base.as_dict(), slope_std_min=0.04,
                           out_prefix="ga_fixed")
    assert r["threshold_choice"] == {}, "it second-guessed an explicit value"
    assert r["parameters"]["slope_std_min"] == pytest.approx(0.04)


def test_reusing_a_terrain_result_does_not_route_again(base, monkeypatch):
    """The whole point of taking a result rather than a DEM path."""
    from geolens import api
    from geolens.landscape import terrain as T

    calls = []
    real = T.build_terrain

    def _spy(*a, **kw):
        calls.append(a[:1])
        return real(*a, **kw)

    monkeypatch.setattr(T, "build_terrain", _spy)
    api.detect_gullies(base.as_dict(), out_prefix="ga_reuse")
    assert not calls, f"it re-routed the DEM {len(calls)} time(s)"


def test_the_windows_are_scaled_to_the_cell_size(base):
    """A 40 m roughness window is barely one cell on a coarse DEM."""
    from geolens import api

    r = api.detect_gullies(base.as_dict(), out_prefix="ga_scale")
    p = r["parameters"]
    cell = base.cell_size
    assert p["window_m"] >= max(40.0, cell * 5) - 1e-9
    assert p["section_width_m"] >= max(60.0, cell * 14) - 1e-9


def test_the_auto_stream_threshold_is_in_real_square_kilometres():
    """The ftUS trap, in code written the same day the rest of it was fixed.

    Given a DEM path and no stream threshold, the wrapper picks one from the
    DEM's extent. The first version computed that extent as
    ``width * height * res[0] * res[1]`` — which on a CRS in US survey feet is
    square feet, and calling it square kilometres overstates the extent by
    10.76x. On Sam's grid (EPSG:6529, 2.400045 ftUS cells) a tile that is
    really 0.134 km2 reads as 1.440.

    Overstating the extent means choosing a stream threshold the DEM cannot
    support, which is the hard error this default exists to avoid.
    """
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    from geolens.units import unit_info

    n, cell_ft = 400, 2.400045
    p = OUT / "footgrid.tif"
    yy, xx = np.mgrid[0:n, 0:n].astype("float64")
    z = (6000.0 + 0.05 * yy + 0.02 * np.abs(xx - n / 2)).astype("float32")
    z -= np.clip(30.0 - 0.6 * np.abs(xx - n / 2), 0, None).astype("float32")
    with rasterio.open(str(p), "w", driver="GTiff", width=n, height=n, count=1,
                       dtype="float32", crs="EPSG:6529", nodata=-9999.0,
                       transform=from_origin(1500000, 1500000,
                                             cell_ft, cell_ft)) as dst:
        dst.write(z, 1)

    with rasterio.open(str(p)) as src:
        u = unit_info(src.crs, abs(src.res[0]), abs(src.res[1]))
        true_km2 = n * n * u.cell_area_m2 / 1e6
        raw_km2 = n * n * abs(src.res[0]) * abs(src.res[1]) / 1e6
    assert raw_km2 / true_km2 > 10, "fixture is not actually on a foot grid"

    from geolens import api
    r = api.detect_gullies(str(p), out_prefix="ga_ft")

    chosen = r["stream_threshold_km2"]
    assert chosen <= true_km2 / 50.0 + 1e-9, (
        f"stream threshold {chosen:.6f} km2 was chosen against an extent of "
        f"{true_km2:.4f} km2 — it used the raw {raw_km2:.4f} instead"
    )
