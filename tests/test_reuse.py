"""Reusing the routing, and reading statistics without materialising the grid.

Both came off one overnight run on a 1.94-billion-cell DEM that re-routed the
whole grid for a second time and then died taking percentiles.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "reuse_test_out"


@pytest.fixture(scope="module", autouse=True)
def _env():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"


@pytest.fixture(scope="module")
def dem():
    n = 140
    yy, xx = np.mgrid[0:n, 0:n]
    axis = n // 2
    z = (1800.0 + 0.6 * np.abs(xx - axis) - 0.25 * yy).astype("float32")
    z += np.random.default_rng(3).normal(0, 0.05, z.shape)
    p = OUT / "dem.tif"
    prof = {"driver": "GTiff", "width": n, "height": n, "count": 1,
            "dtype": "float32", "crs": "EPSG:32612", "nodata": -9999.0,
            "transform": from_origin(500000, 4000000, 2.0, 2.0)}
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(z.astype("float32"), 1)
    return str(p)


# -- restream -----------------------------------------------------------------

def test_restream_matches_a_full_rebuild(dem):
    """The whole claim: changing the threshold needs no second routing pass."""
    from geolens.landscape import build_terrain, restream

    base = build_terrain(dem, stream_threshold_km2=0.0002, out_prefix="rs_base")
    rebuilt = build_terrain(dem, stream_threshold_km2=0.001, out_prefix="rs_full")
    reused = restream(base, stream_threshold_km2=0.001, out_prefix="rs_cheap")

    with rasterio.open(rebuilt.streams) as a, rasterio.open(reused.streams) as b:
        sa, sb = a.read(1) > 0, b.read(1) > 0
    assert sa.sum() > 0
    agree = float((sa == sb).mean())
    assert agree > 0.999, f"stream masks differ ({agree:.4%} agreement)"

    with rasterio.open(rebuilt.hand) as a, rasterio.open(reused.hand) as b:
        ha = a.read(1, masked=True)
        hb = b.read(1, masked=True)
    both = ~(np.ma.getmaskarray(ha) | np.ma.getmaskarray(hb))
    assert np.allclose(ha.data[both], hb.data[both], atol=0.01), (
        "HAND differs between a rebuild and a restream"
    )

    # Agreement on the intersection is not equivalence. This test compared only
    # where BOTH rasters had data, so it was blind to the two producing
    # different amounts of it -- and they did: build_terrain used pysheds'
    # compute_hand while restream used geolens' own tracer, and on the
    # compound fixture that was 89.8% of ground against 95.3%. Coverage is
    # part of the answer, so compare it.
    na = int((~np.ma.getmaskarray(ha)).sum())
    nb = int((~np.ma.getmaskarray(hb)).sum())
    assert abs(na - nb) <= max(1, na // 1000), (
        f"a rebuild resolved HAND for {na:,} cells and a restream for {nb:,} — "
        f"they are not using the same HAND implementation"
    )


def test_restream_does_not_reroute(dem, monkeypatch):
    """If it ever calls a router again, the whole point is lost."""
    from geolens.landscape import build_terrain, restream
    from geolens.landscape import terrain as T

    base = build_terrain(dem, stream_threshold_km2=0.0002, out_prefix="rs2")

    called = {"n": 0}
    real = T._run_pysheds

    def spy(*a, **k):
        called["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(T, "_run_pysheds", spy)
    restream(base, stream_threshold_km2=0.001, out_prefix="rs2b")
    assert called["n"] == 0, "restream re-ran the flow router"


def test_restream_accepts_what_landscape_pass_returns(dem):
    """The reuse path has to take the dict the pipeline actually hands back."""
    from geolens.landscape import build_terrain, restream, terrain_from_result

    base = build_terrain(dem, stream_threshold_km2=0.0002, out_prefix="rs3")
    as_dict = base.as_dict()
    assert terrain_from_result(as_dict).accumulation == base.accumulation
    # and wrapped the way run_landscape returns it
    assert terrain_from_result({"terrain": as_dict}).hand == base.hand
    r = restream({"terrain": as_dict}, stream_threshold_km2=0.001,
                 out_prefix="rs3b")
    assert Path(r.streams).exists()


def test_restream_says_so_when_the_threshold_is_impossible(dem):
    from geolens.landscape import build_terrain, restream
    from geolens.safety import GeolensError

    base = build_terrain(dem, stream_threshold_km2=0.0002, out_prefix="rs4")
    with pytest.raises(GeolensError, match="largest contributing area"):
        restream(base, stream_threshold_km2=500.0, out_prefix="rs4b")


# -- decimated percentiles ----------------------------------------------------

def test_percentiles_match_a_full_read():
    from geolens.raster import percentiles

    rng = np.random.default_rng(0)
    a = rng.normal(0.3, 0.1, (1200, 1200)).astype("float32")
    a[:100, :] = -9999.0
    p = OUT / "pct.tif"
    prof = {"driver": "GTiff", "width": 1200, "height": 1200, "count": 1,
            "dtype": "float32", "crs": "EPSG:32612", "nodata": -9999.0,
            "transform": from_origin(0, 0, 2, 2)}
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(a, 1)

    truth = {f"p{q:g}": float(np.percentile(a[a > -9998], q))
             for q in (50, 75, 90, 95)}
    got = percentiles(str(p), [50, 75, 90, 95], max_cells=100_000)

    assert got["decimation"] > 1, "nothing was decimated; the test proves nothing"
    for k, v in truth.items():
        assert abs(got[k] - v) < 0.005, f"{k}: {got[k]} vs {v}"


def test_percentiles_exclude_nodata():
    """A -9999 fill dragged into a percentile moves every threshold."""
    from geolens.raster import percentiles

    a = np.full((400, 400), 0.5, dtype="float32")
    a[:200, :] = -9999.0
    p = OUT / "pct_nd.tif"
    prof = {"driver": "GTiff", "width": 400, "height": 400, "count": 1,
            "dtype": "float32", "crs": "EPSG:32612", "nodata": -9999.0,
            "transform": from_origin(0, 0, 2, 2)}
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(a, 1)

    got = percentiles(str(p), [50], max_cells=10_000_000)
    assert got["p50"] == pytest.approx(0.5), (
        "nodata was included; the median came back as the fill value"
    )


def test_percentiles_on_an_empty_raster_is_a_clear_error():
    from geolens.raster import percentiles
    from geolens.safety import GeolensError

    a = np.full((50, 50), -9999.0, dtype="float32")
    p = OUT / "pct_empty.tif"
    prof = {"driver": "GTiff", "width": 50, "height": 50, "count": 1,
            "dtype": "float32", "crs": "EPSG:32612", "nodata": -9999.0,
            "transform": from_origin(0, 0, 2, 2)}
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(a, 1)
    with pytest.raises(GeolensError, match="no valid data"):
        percentiles(str(p), [50])
