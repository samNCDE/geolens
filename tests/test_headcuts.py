"""Headcut detection, against a fixture with known headcut positions.

The gully fixture puts six headcuts at known world coordinates, each gaining
2.2 m of depth over an 8 m ramp on a 4.5% hillslope. That is the whole point of
it: the previous implementation of this function reported 380 m drops on a DEM
with 85 m of relief and nothing caught it, because nothing checked the numbers
against anything.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent
FIX = ROOT / "gullies"
OUT = ROOT / "hc_test_out"

pytest.importorskip("topotoolbox", reason="headcuts need topotoolbox")

# How close a reported headcut has to be to a true one to count. The fixture's
# gullies are 200 m apart, so 40 m cannot accidentally match the wrong gully.
MATCH_M = 40.0
TRUE_DROP_M = 2.2


@pytest.fixture(scope="module", autouse=True)
def fixtures():
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    if not (FIX / "gully_dem.tif").exists():
        subprocess.run([sys.executable, str(ROOT / "make_gullies.py")], check=True)


@pytest.fixture(scope="module")
def truth():
    import pandas as pd
    return pd.read_csv(FIX / "gully_truth.csv")


@pytest.fixture(scope="module")
def gully_lines():
    """Run the gully detector, whose output is what headcuts are found on."""
    from geolens.landscape.terrain import build_terrain
    from geolens.landscape.features import detect_gullies

    tr = build_terrain(str(FIX / "gully_dem.tif"), stream_threshold_km2=0.005,
                       backend="pysheds", out_prefix="hct_terrain")
    g = detect_gullies(tr, out_prefix="hct_gullies").as_dict()
    assert g["gully_count"] == 6, f"fixture regression: {g['gully_count']} gullies"
    return g["gully_lines"]


@pytest.fixture(scope="module")
def result(gully_lines):
    from geolens.landscape.features import detect_headcuts
    return detect_headcuts(str(FIX / "gully_dem.tif"), lines=gully_lines,
                           out_prefix="hct").as_dict()


def _misses(gdf, truth):
    return [
        float(np.hypot(truth.headcut_x_world - r.x,
                       truth.headcut_y_world - r.y).min())
        for _, r in gdf.iterrows()
    ]


def test_finds_every_headcut_and_nothing_else(result, truth):
    """6 of 6, with no false positives."""
    import geopandas as gpd

    gdf = gpd.read_file(result["headcuts"])
    miss = _misses(gdf, truth)
    hits = sum(1 for m in miss if m < MATCH_M)
    assert hits == 6, f"recall {hits}/6 (misses: {[round(m) for m in miss]})"
    assert len(gdf) == 6, f"precision: {len(gdf)} reported for 6 real headcuts"


def test_positions_are_accurate(result, truth):
    """Not just the right count — the right places."""
    import geopandas as gpd

    gdf = gpd.read_file(result["headcuts"])
    miss = _misses(gdf, truth)
    assert np.median(miss) < 10.0, f"median position error {np.median(miss):.0f} m"
    assert max(miss) < MATCH_M


def test_excess_drop_recovers_the_real_step(result):
    """``excess_drop_m`` must land near the true 2.2 m, not the raw drop.

    Raw drop reads ~3.1 m here because a 12 m window on a 4.5% hillslope
    collects about 0.5 m of hillslope fall on top of the step. Subtracting the
    background gradient is what makes the number mean "headcut height".
    """
    import geopandas as gpd

    gdf = gpd.read_file(result["headcuts"])
    med = float(gdf["excess_drop_m"].median())
    err = abs(med - TRUE_DROP_M) / TRUE_DROP_M
    assert err < 0.30, f"excess_drop_m median {med:.2f} m vs truth {TRUE_DROP_M} m"
    # And it must be closer to truth than the uncorrected drop is.
    raw = float(gdf["drop_m"].median())
    assert abs(med - TRUE_DROP_M) < abs(raw - TRUE_DROP_M), (
        f"background correction made it worse: excess {med:.2f}, raw {raw:.2f}"
    )


def test_no_absurd_drops(result):
    """The regression guard for the bug that motivated this rewrite.

    The old implementation reported ~380 m drops on a DEM with 85 m of relief,
    because it measured gradient across spatial jumps in a traced path. Walking
    the stream graph makes that impossible by construction — an edge is a real
    connection — but assert it anyway, because it shipped once.
    """
    import geopandas as gpd
    import rasterio

    with rasterio.open(FIX / "gully_dem.tif") as src:
        band = src.read(1, masked=True)
        relief = float(band.max() - band.min())

    gdf = gpd.read_file(result["headcuts"])
    assert gdf["drop_m"].abs().max() < relief, (
        f"a headcut dropped more than the DEM's entire relief ({relief:.0f} m)"
    )
    # Tighter: no single step should exceed a modest fraction of total relief.
    assert gdf["drop_m"].abs().max() < 0.25 * relief


def test_candidates_are_written_too(result):
    """Everything above threshold lands on disk, not just the selection.

    Knickpoint finding is the slow part of this call; keeping the candidates
    means changing your mind about a threshold does not mean recomputing.
    """
    cand = Path(result["summary"]["candidates_file"])
    assert cand.exists()
    assert result["summary"]["passed_threshold"] >= result["summary"]["reported"]


def test_unrestricted_is_honest_about_precision(gully_lines):
    """Without `lines`, recall holds but precision drops — and it says so."""
    import geopandas as gpd
    from geolens.landscape.features import detect_headcuts

    r = detect_headcuts(str(FIX / "gully_dem.tif"), out_prefix="hct_all").as_dict()
    gdf = gpd.read_file(r["headcuts"])
    assert len(gdf) > 6, "expected many candidates without a line restriction"
    assert any("precision" in w for w in r["warnings"]), (
        "the unrestricted mode must warn that it over-reports"
    )


def test_missing_topotoolbox_gives_a_useful_error(monkeypatch):
    """Not a bare ImportError several frames deep."""
    import builtins

    from geolens.landscape.features import detect_headcuts
    from geolens.safety import GeolensError

    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "topotoolbox":
            raise ImportError("no topotoolbox")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(GeolensError, match="pip install topotoolbox"):
        detect_headcuts(str(FIX / "gully_dem.tif"), out_prefix="hct_err")
