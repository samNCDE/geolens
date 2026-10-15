"""Gully detection on a grid too large to hold.

The mask criteria are four elementwise thresholds on four rasters. Read whole
and cast to float64 — which is what this did — that is 8 bytes x N cells x 4,
or 62 GB on a 1.94-billion-cell DEM, before anything else. Windowed, the peak is
one block per raster plus a bool mask.

Tiling is only worth doing if it gives the identical answer, so that is what is
asserted here, not the memory.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "gmem_test_out"

N = 300
CELL = 2.0


@pytest.fixture(scope="module", autouse=True)
def _env():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"


def _w(name, arr, nodata=-9999.0):
    p = OUT / name
    prof = {"driver": "GTiff", "width": arr.shape[1], "height": arr.shape[0],
            "count": 1, "dtype": "float32", "crs": "EPSG:32612",
            "nodata": nodata,
            "transform": from_origin(500000, 4000000, CELL, CELL)}
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(arr.astype("float32"), 1)
    return str(p)


@pytest.fixture(scope="module")
def rasters():
    rng = np.random.default_rng(11)
    sstd = rng.gamma(2.0, 0.03, (N, N)).astype("float32")
    tpi = rng.normal(-0.1, 0.5, (N, N)).astype("float32")
    slope = rng.gamma(2.0, 0.05, (N, N)).astype("float32")
    acc = rng.gamma(2.0, 400.0, (N, N)).astype("float32")
    # a hole, so nodata handling is exercised too
    for a in (sstd, tpi, slope, acc):
        a[10:40, 10:40] = -9999.0
    return {"sstd": _w("sstd.tif", sstd), "tpi": _w("tpi.tif", tpi),
            "slope": _w("slope.tif", slope), "acc": _w("acc.tif", acc),
            "arrays": (sstd, tpi, slope, acc)}


def _naive(arrays, cell_area_km2, sstd_min, tpi_max, slope_min, dmin, dmax):
    """The full-array version, as it was, for comparison."""
    sstd, tpi, slope, acc = (np.ma.masked_equal(a, -9999.0) for a in arrays)
    area = np.maximum(acc, 1.0) * cell_area_km2
    m = (np.ma.filled(sstd >= sstd_min, False)
         & np.ma.filled(tpi <= tpi_max, False)
         & np.ma.filled(slope >= slope_min, False)
         & np.ma.filled((area >= dmin) & (area <= dmax), False))
    return np.asarray(m)


@pytest.mark.parametrize("block", [64, 128, 4096])
def test_the_windowed_mask_is_identical_to_a_full_read(rasters, block):
    from geolens.landscape.features import _windowed_gully_mask

    cell_area_km2 = (CELL ** 2) / 1e6
    kw = dict(slope_std_min=0.05, tpi_max=-0.3, min_slope=0.05,
              cell_area_km2=cell_area_km2, min_drainage_km2=0.0005,
              max_drainage_km2=float("inf"))

    got, counts = _windowed_gully_mask(
        rasters["sstd"], rasters["tpi"], rasters["slope"], rasters["acc"],
        block=block, **kw)
    want = _naive(rasters["arrays"], cell_area_km2, 0.05, -0.3, 0.05,
                  0.0005, float("inf"))

    assert got.sum() > 0, "the fixture admits nothing; the test proves nothing"
    assert np.array_equal(got, want), (
        f"block={block}: windowed mask differs from a full read in "
        f"{int((got != want).sum())} cells"
    )
    assert counts["all"] == int(got.sum())


def test_block_size_does_not_change_the_answer(rasters):
    """A tiling that depends on its block size is not a tiling."""
    from geolens.landscape.features import _windowed_gully_mask

    kw = dict(slope_std_min=0.05, tpi_max=-0.3, min_slope=0.05,
              cell_area_km2=(CELL ** 2) / 1e6, min_drainage_km2=0.0005,
              max_drainage_km2=float("inf"))
    a, _ = _windowed_gully_mask(rasters["sstd"], rasters["tpi"],
                                rasters["slope"], rasters["acc"],
                                block=37, **kw)
    b, _ = _windowed_gully_mask(rasters["sstd"], rasters["tpi"],
                                rasters["slope"], rasters["acc"],
                                block=4096, **kw)
    assert np.array_equal(a, b)


def test_nodata_never_passes_a_criterion(rasters):
    from geolens.landscape.features import _windowed_gully_mask

    got, _ = _windowed_gully_mask(
        rasters["sstd"], rasters["tpi"], rasters["slope"], rasters["acc"],
        slope_std_min=-1e9, tpi_max=1e9, min_slope=-1e9,
        cell_area_km2=(CELL ** 2) / 1e6, min_drainage_km2=-1.0,
        max_drainage_km2=float("inf"))
    assert not got[10:40, 10:40].any(), (
        "nodata cells passed every criterion — the fill value was compared as "
        "though it were a measurement"
    )


def test_per_criterion_counts_name_the_tightest_test(rasters):
    """'No gullies found' is nearly always one criterion doing the rejecting."""
    from geolens.landscape.features import _windowed_gully_mask

    _, counts = _windowed_gully_mask(
        rasters["sstd"], rasters["tpi"], rasters["slope"], rasters["acc"],
        slope_std_min=99.0,                 # impossible
        tpi_max=1e9, min_slope=-1e9,
        cell_area_km2=(CELL ** 2) / 1e6, min_drainage_km2=-1.0,
        max_drainage_km2=float("inf"))
    assert counts["slope_std"] == 0
    assert counts["tpi"] > 0 and counts["slope"] > 0
    assert min(counts, key=counts.get) == "slope_std"


def test_no_full_grid_float64_reads_in_gully_detection():
    """A source guard, like the BigTIFF one.

    The bug was `src.read(1).astype("float64")` on four rasters. It is an easy
    line to reintroduce and impossible to notice on a test fixture, because
    every fixture here is a few hundred cells across.
    """
    src = Path("src/geolens/landscape/features.py").read_text()
    body = src[src.index("def detect_gullies("):]
    offenders = [ln.strip() for ln in body.splitlines()
                 if 'astype("float64")' in ln and "window" not in ln]
    assert not offenders, (
        "full-grid float64 reads are back in detect_gullies: " + str(offenders)
    )
