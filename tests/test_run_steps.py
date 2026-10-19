"""Each step of the landscape pass can be run on its own, on an earlier run.

The ArcGIS toolbox has a tool per step as well as one for the whole routine;
these are the api calls behind the step tools. The point is the
"preprocessing" (routing, valley bottom, confinement) runs once and every
later step reads it from the run folder.
"""
from pathlib import Path

import geopandas as gpd
import pytest

ROOT = Path(__file__).resolve().parents[1]
DEM = str(ROOT / "tests" / "landscape" / "landscape_dem.tif")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    import os

    out = tmp_path_factory.mktemp("steps")
    old = os.environ.get("GEOLENS_OUT")
    os.environ["GEOLENS_OUT"] = str(out)
    # The later steps read the run record; other test files turn records off.
    persist = os.environ.pop("GEOLENS_PERSIST", None)
    from geolens import api
    api.landscape_pass(DEM, stream_threshold_km2=3.0, segment_length_m=800.0,
                       valley_types=False, stream_types=False)
    yield out
    if persist is not None:
        os.environ["GEOLENS_PERSIST"] = persist
    if old is None:
        os.environ.pop("GEOLENS_OUT", None)
    else:
        os.environ["GEOLENS_OUT"] = old


def test_preprocessing_alone_stops_before_valley_types(run):
    assert (run / "classified_dir" / "segments_classified.gpkg").exists()
    assert not (run / "valley_types_dir").exists()
    assert list((run / "runs").glob("*_landscape_pass.json"))


def test_valley_types_for_run(run, monkeypatch):
    monkeypatch.setenv("GEOLENS_OUT", str(run))
    from geolens import api
    v = api.valley_types_for_run(str(run), geology=None, fans=True)
    vt = gpd.read_file(v["valley_types"]["segments"])
    assert len(vt) > 0 and "valley_type" in vt.columns
    assert "fans" in v and Path(v["fans"]["candidates"]).exists()
    assert Path(v["valley_extent"]).exists()


def test_stream_types_for_run_and_stability(run, monkeypatch):
    monkeypatch.setenv("GEOLENS_OUT", str(run))
    from geolens import api
    s = api.stream_types_for_run(str(run), regional_curve="arid_sw_nm",
                                 section_plots=False,
                                 out_prefix="stream_types_v29")
    assert Path(s["stream_types"]["segments"]).exists()
    assert "stream_types_v29_dir" in s["stream_types"]["segments"]
    stab = s["stability"]
    assert "stability_v29_dir" in stab["stability"]
    assert sum(stab["counts"].values()) > 0


def test_a_folder_that_is_not_a_run_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("GEOLENS_OUT", str(tmp_path))
    from geolens import api
    from geolens.safety import GeolensError
    with pytest.raises(GeolensError, match="landscape pass"):
        api.stream_types_for_run(str(tmp_path), dem=DEM)
    with pytest.raises(GeolensError, match="landscape pass"):
        api.valley_types_for_run(str(tmp_path))


def test_riparian_for_run_uses_the_runs_hand(run, monkeypatch, tmp_path):
    """NAIP is stubbed with a synthetic NDVI: green along the fixture's
    channels, dry elsewhere. The band must follow the channels, because the
    run's HAND limits it to ground near one."""
    import numpy as np
    import rasterio

    monkeypatch.setenv("GEOLENS_OUT", str(run))
    from geolens import api

    terrain = api._run_terrain(run)
    with rasterio.open(terrain.hand) as src:
        hand = src.read(1, masked=True).filled(np.nan)
        prof = src.profile.copy()
    ndvi = np.where(np.isfinite(hand) & (hand < 2.0), 0.55, 0.12).astype("float32")
    path = tmp_path / "ndvi.tif"
    prof.update(dtype="float32", nodata=-9999.0, count=1)
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(ndvi, 1)

    def fake_naip(aoi, **kw):
        return {"ndvi": str(path), "rgb": None, "warnings": []}

    monkeypatch.setattr(api, "naip_imagery", fake_naip)
    res = api.riparian_for_run(str(run))
    rip = res["riparian"]
    assert rip["n_patches"] > 0
    assert Path(rip["band_polygons"]).exists()


def test_calibrating_confinement_for_a_run(run, monkeypatch):
    """No breaks: a report. Both breaks: calibrated reaches, valleys re-typed
    from the existing measurements, and later steps using them."""
    monkeypatch.setenv("GEOLENS_OUT", str(run))
    from geolens import api

    rep = api.calibrate_confinement_for_run(str(run))
    assert "reclassified" not in rep
    pct = rep["sweep"]["confinement_ratio_percentiles"]
    assert len(rep["sweep"]["candidates"]) >= 3

    lo, hi = pct[25], pct[66]
    res = api.calibrate_confinement_for_run(str(run), lo, hi)
    seg = Path(res["reclassified"]["segments"])
    assert seg.exists() and seg.parent.name == "reclassified_dir"
    g = gpd.read_file(seg)
    # "unknown": reaches with no valley bottom were measured as nothing,
    # not as confined (v0.33).
    assert set(g["confinement"]) <= {"confined", "partly confined",
                                     "unconfined", "unknown"}
    if (run / "valley_form_dir" / "valley_form_segments.csv").exists():
        assert Path(res["valley_types"]["segments"]).exists()
    assert api._run_segments(run) == seg
