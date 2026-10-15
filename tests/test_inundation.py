"""Flood-extent validation.

The network call cannot be tested here — outbound HTTPS to EODC is blocked in
the environment this was written in, the same as the geology module. So the
search payload, the raster accumulation, the metric and the label export are
tested against constructed inputs, and the live service is covered by
``inundation_selftest``, which has to run somewhere with internet access.

The tests that matter most are the ones about what the metric REFUSES to do.
Scoring a floodplain against observed flood extent as if it were ground truth
is the way to get a plausible number that biases calibration toward
under-delineation, and several of these tests exist purely to stop that
creeping back in.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "inun_test_out"

CELL = 20.0
W = H = 100
CRS = "EPSG:32612"
TRANSFORM = from_origin(500000, 4000000, CELL, CELL)
CELL_KM2 = (CELL * CELL) / 1e6


@pytest.fixture(scope="module", autouse=True)
def _env():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"


def _write_mask(path: Path, arr: np.ndarray):
    prof = {
        "driver": "GTiff", "width": W, "height": H, "count": 1, "dtype": "uint8",
        "crs": CRS, "transform": TRANSFORM, "nodata": 0,
    }
    with rasterio.open(str(path), "w", **prof) as dst:
        dst.write(arr.astype("uint8"), 1)
    return str(path)


def _scenario(tmp_path, flood: np.ndarray, valid: np.ndarray):
    """Build an InundationResult-shaped dict from two masks."""
    return {
        "flood_envelope": _write_mask(tmp_path / "flood.tif", flood),
        "valid_mask": _write_mask(tmp_path / "valid.tif", valid),
        "valid_fraction": float(valid.mean()),
    }


def _floodplain(tmp_path, mask: np.ndarray, name="fp.gpkg"):
    import geopandas as gpd
    from rasterio import features as rfeat
    from shapely.geometry import shape

    geoms = [shape(g) for g, v in
             rfeat.shapes(mask.astype("uint8"), mask=mask.astype(bool),
                          transform=TRANSFORM) if v]
    p = tmp_path / name
    gpd.GeoDataFrame({"id": range(len(geoms))}, geometry=geoms,
                     crs=CRS).to_file(str(p), driver="GPKG")
    return str(p)


# -- the metric ---------------------------------------------------------------

def test_a_floodplain_containing_all_observed_flood_scores_one(tmp_path):
    from geolens.inundation import validate_floodplain

    flood = np.zeros((H, W), bool); flood[40:60, 10:90] = True
    valid = np.ones((H, W), bool)
    fp = np.zeros((H, W), bool); fp[30:70, 5:95] = True     # wider than the flood

    r = validate_floodplain(_floodplain(tmp_path, fp),
                            _scenario(tmp_path, flood, valid))
    assert r.recall == pytest.approx(1.0)
    # The floodplain is much larger than the observed flood. That is CORRECT,
    # and must not be penalised anywhere in the result.
    assert r.unconfirmed_km2 > 0
    assert r.floodplain_km2 > r.observed_flood_km2


def test_a_floodplain_missing_half_scores_half(tmp_path):
    from geolens.inundation import validate_floodplain

    flood = np.zeros((H, W), bool); flood[40:60, 10:90] = True
    valid = np.ones((H, W), bool)
    fp = np.zeros((H, W), bool); fp[40:60, 10:50] = True    # covers half

    r = validate_floodplain(_floodplain(tmp_path, fp),
                            _scenario(tmp_path, flood, valid))
    assert r.recall == pytest.approx(0.5, abs=0.02)
    assert r.missed_km2 == pytest.approx(r.observed_flood_km2 * 0.5, rel=0.05)


def test_flood_outside_the_valid_region_is_excluded_from_the_score(tmp_path):
    """The single most important behaviour here.

    A reach SAR could not see must not count against the delineation. If
    unobservable pixels entered the denominator, every confined valley — where
    radar shadow and layover are worst, and where the confinement cutoff is
    most interesting — would score badly for a reason that has nothing to do
    with the delineation.
    """
    from geolens.inundation import validate_floodplain

    flood = np.zeros((H, W), bool)
    flood[40:60, 10:50] = True       # inside the floodplain, observable
    flood[40:60, 50:90] = True       # NOT observable
    valid = np.zeros((H, W), bool); valid[:, :50] = True
    fp = np.zeros((H, W), bool); fp[40:60, 10:50] = True

    r = validate_floodplain(_floodplain(tmp_path, fp),
                            _scenario(tmp_path, flood, valid))
    assert r.recall == pytest.approx(1.0), (
        "unobservable flood pixels leaked into the denominator"
    )
    assert r.valid_fraction == pytest.approx(0.5, abs=0.02)


def test_precision_and_iou_are_not_reported(tmp_path):
    """Refusing to compute them is a design decision, not an omission.

    A floodplain legitimately exceeds any observed extent. Reporting precision
    would invite optimising it, and optimising it means shrinking the
    floodplain until it matches whatever SAR happened to catch.
    """
    from geolens.inundation import validate_floodplain

    flood = np.zeros((H, W), bool); flood[40:60, 10:90] = True
    valid = np.ones((H, W), bool)
    fp = np.zeros((H, W), bool); fp[20:80, 5:95] = True

    r = validate_floodplain(_floodplain(tmp_path, fp),
                            _scenario(tmp_path, flood, valid)).as_dict()
    for forbidden in ("precision", "iou", "jaccard", "f1", "accuracy"):
        assert forbidden not in r, f"{forbidden} must not be reported"
    assert any("Precision and IoU are not reported" in n for n in r["notes"])


def test_low_observability_is_flagged(tmp_path):
    from geolens.inundation import validate_floodplain

    flood = np.zeros((H, W), bool); flood[40:60, 10:30] = True
    valid = np.zeros((H, W), bool); valid[:, :30] = True     # 30% observable
    fp = np.zeros((H, W), bool); fp[40:60, 10:30] = True

    r = validate_floodplain(_floodplain(tmp_path, fp),
                            _scenario(tmp_path, flood, valid))
    assert any("observable" in n for n in r.notes)


def test_no_observations_says_so_rather_than_scoring_zero(tmp_path):
    """Nothing observed is not the same as nothing captured."""
    from geolens.inundation import validate_floodplain

    flood = np.zeros((H, W), bool)
    valid = np.ones((H, W), bool)
    fp = np.zeros((H, W), bool); fp[40:60, 10:90] = True

    r = validate_floodplain(_floodplain(tmp_path, fp),
                            _scenario(tmp_path, flood, valid))
    assert r.recall is None
    assert "No usable observations" in r.verdict


# -- labels -------------------------------------------------------------------

def test_unobserved_stays_unlabelled_not_negative(tmp_path):
    """The mistake that would quietly wreck a calibration.

    A 6-12 day revisit almost never catches a flashy stream at peak. If every
    pixel not seen wet became a negative, the model would learn that floodplains
    are whatever SAR happened to catch.
    """
    from geolens.inundation import export_labels

    flood = np.zeros((H, W), bool); flood[40:60, 10:50] = True
    valid = np.ones((H, W), bool)

    r = export_labels(_scenario(tmp_path, flood, valid), out_prefix="lab1")
    with rasterio.open(r["labels"]) as src:
        lab = src.read(1)

    assert (lab == 1).sum() == flood.sum()
    assert (lab == 2).sum() == 0, "negatives invented where none were supplied"
    assert (lab == 0).sum() == (~flood).sum()
    assert "Unlabelled is not negative" in r["note"]


def test_above_confinement_supplies_the_negatives(tmp_path):
    from geolens.inundation import export_labels

    flood = np.zeros((H, W), bool); flood[40:60, 10:50] = True
    valid = np.ones((H, W), bool)
    above = np.zeros((H, W), bool); above[:20, :] = True

    scen = _scenario(tmp_path, flood, valid)
    r = export_labels(scen, above_confinement=_write_mask(tmp_path / "above.tif",
                                                          above),
                      out_prefix="lab2")
    with rasterio.open(r["labels"]) as src:
        lab = src.read(1)
    assert (lab == 2).sum() == above.sum()
    assert r["negative_cells"] == int(above.sum())


def test_a_pixel_is_never_both_positive_and_negative(tmp_path):
    """If terrain above confinement was observed flooding, the confinement
    cutoff is wrong there — and the pixel must not be labelled as negative."""
    from geolens.inundation import export_labels

    flood = np.zeros((H, W), bool); flood[10:30, :] = True
    valid = np.ones((H, W), bool)
    above = np.zeros((H, W), bool); above[:20, :] = True     # overlaps the flood

    r = export_labels(_scenario(tmp_path, flood, valid),
                      above_confinement=_write_mask(tmp_path / "ab.tif", above),
                      out_prefix="lab3")
    with rasterio.open(r["labels"]) as src:
        lab = src.read(1)
    overlap = above & flood
    assert (lab[overlap] == 1).all(), "observed flooding was overwritten as dry"


# -- search plumbing ----------------------------------------------------------

def test_search_asks_for_the_right_collection(monkeypatch):
    from geolens import inundation

    seen = {}

    def fake(url, payload, timeout=60, retries=3):
        seen["url"] = url
        seen["payload"] = payload
        return {"features": [{"id": "x"}], "links": []}

    monkeypatch.setattr(inundation, "_post_json", fake)
    items = inundation.search_scenes((-83.5, 35.4, -82.0, 36.4),
                                     "2024-09-26", "2024-10-06")
    assert len(items) == 1
    assert seen["payload"]["collections"] == ["GFM"]
    assert seen["payload"]["datetime"] == "2024-09-26/2024-10-06"
    assert seen["payload"]["bbox"] == [-83.5, 35.4, -82.0, 36.4]
    assert seen["url"].endswith("/search")


def test_search_follows_pagination(monkeypatch):
    """A decade of flood scenes over one catchment is more than one page."""
    from geolens import inundation

    pages = [
        {"features": [{"id": f"a{i}"} for i in range(100)],
         "links": [{"rel": "next", "href": "http://x/search", "body": {}}]},
        {"features": [{"id": "b0"}], "links": []},
    ]
    calls = {"n": 0}

    def fake(url, payload, timeout=60, retries=3):
        p = pages[min(calls["n"], len(pages) - 1)]
        calls["n"] += 1
        return p

    monkeypatch.setattr(inundation, "_post_json", fake)
    items = inundation.search_scenes((-1, -1, 1, 1), "2015-01-01", "2026-01-01")
    assert len(items) == 101, "pagination stopped at the first page"


def test_unreachable_service_is_a_clear_error(monkeypatch):
    from geolens import inundation
    from geolens.safety import GeolensError

    def boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(GeolensError, match="Could not reach"):
        inundation._post_json("http://x", {}, timeout=1, retries=1)
