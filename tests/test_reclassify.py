"""Changing the confinement breaks must not recompute the DEM.

The breaks are two thresholds on `confinement_ratio`, which the landscape pass
already computed and stored in the segments layer. The starting notebook told
you to call `landscape_pass` again to change them — conditioning, routing,
HAND, valley bottom and segmentation, all to relabel a column. On a
1.94-billion-cell DEM that is 2.7 hours per iteration, in the step whose entire
purpose is trying several values.
"""
from __future__ import annotations

import os
import time
import warnings
from pathlib import Path

import geopandas as gpd
import pytest

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "reclass_test_out"
FIX = ROOT / "landscape" / "landscape_dem.tif"


@pytest.fixture(scope="module", autouse=True)
def _env():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"


@pytest.fixture(scope="module")
def base():
    if not FIX.exists():
        pytest.skip("landscape fixture not built")
    from geolens import api

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = api.landscape_pass(str(FIX), stream_threshold_km2=0.05,
                               valley_types=False)
    return r["classified"]["segments"]


CM, PM = 3.0, 9.0


def test_reclassify_matches_a_full_rerun_exactly(base):
    """Identical, or the shortcut is not a shortcut."""
    from geolens import api

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        full = api.landscape_pass(str(FIX), stream_threshold_km2=0.05,
                                  valley_types=False,
                                  confined_max=CM, partly_confined_max=PM,
                                  out_prefix="rc_full")
        rc = api.reclassify_confinement(base, CM, PM, out_prefix="rc_cheap")

    a = gpd.read_file(full["classified"]["segments"])["confinement"].tolist()
    b = gpd.read_file(rc["segments"])["confinement"].tolist()
    assert len(a) == len(b)
    diff = sum(1 for x, y in zip(a, b) if x != y)
    assert diff == 0, f"{diff} of {len(a)} segments classified differently"

    # and the derived columns too
    fa = gpd.read_file(full["classified"]["segments"])
    fb = gpd.read_file(rc["segments"])
    assert fa["rosgen_candidates"].tolist() == fb["rosgen_candidates"].tolist()


def test_reclassify_is_much_faster_than_a_rerun(base):
    from geolens import api

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        t = time.monotonic()
        api.landscape_pass(str(FIX), stream_threshold_km2=0.05,
                           confined_max=CM, partly_confined_max=PM,
                           out_prefix="rc_t_full")
        t_full = time.monotonic() - t

        t = time.monotonic()
        api.reclassify_confinement(base, CM, PM, out_prefix="rc_t_cheap")
        t_rc = time.monotonic() - t

    assert t_rc < t_full / 5, (
        f"reclassify took {t_rc:.2f}s against {t_full:.2f}s for a full re-run — "
        f"it is recomputing something it should be reading"
    )


def test_reclassify_never_opens_the_dem(base, monkeypatch):
    """The decisive check: a relabel must not touch a raster at all."""
    import rasterio
    from geolens import api

    opened = []
    real = rasterio.open

    def spy(path, *a, **k):
        opened.append(str(path))
        return real(path, *a, **k)

    monkeypatch.setattr(rasterio, "open", spy)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        api.reclassify_confinement(base, CM, PM, out_prefix="rc_noraster")
    assert not opened, f"reclassify opened rasters: {opened[:3]}"


def test_the_sweep_compares_candidates_without_writing(base):
    from geolens import api

    before = set(p.name for p in OUT.rglob("*"))
    s = api.confinement_break_sweep(base)
    after = set(p.name for p in OUT.rglob("*"))

    assert s["n_segments"] > 0
    assert len(s["candidates"]) >= 4
    for row in s["candidates"]:
        assert row["confined_max"] < row["partly_confined_max"]
        assert sum(row["fraction_km"].values()) == pytest.approx(1.0, abs=0.02)
    assert after == before, "the sweep wrote files; it is supposed to be read-only"


def test_backwards_breaks_are_refused(base):
    from geolens import api
    from geolens.safety import GeolensError

    with pytest.raises(GeolensError, match="must be below"):
        api.reclassify_confinement(base, 9.0, 3.0, out_prefix="rc_bad")


def test_a_layer_without_the_ratio_column_says_so(tmp_path):
    from geolens import api
    from geolens.safety import GeolensError
    from shapely.geometry import LineString

    p = OUT / "no_ratio.gpkg"
    gpd.GeoDataFrame({"seg_id": [1]},
                     geometry=[LineString([(0, 0), (1, 1)])],
                     crs="EPSG:32612").to_file(str(p), driver="GPKG")
    with pytest.raises(GeolensError, match="confinement_ratio"):
        api.reclassify_confinement(str(p), 2.0, 5.0, out_prefix="rc_missing")
