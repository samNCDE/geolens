"""Roughness in strips is the whole-grid computation, cell for cell.

v0.27 moved terrain_roughness from ~50 bytes a cell for the whole grid (90+ GB
on Ruidoso's 1.785 billion cells, where the overnight run died) to one strip
at a time. The only way that is safe is if the answer does not change, so
this compares a one-strip run with a many-strip run on a grid with nodata
holes and edges, where an off-by-one in the overlap would show.
"""
from __future__ import annotations

import os
import types
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin


@pytest.fixture(scope="module")
def grid(tmp_path_factory):
    d = tmp_path_factory.mktemp("rough")
    os.environ["GEOLENS_OUT"] = str(d / "out")
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"
    h, w = 530, 410
    rng = np.random.default_rng(3)
    yy, xx = np.mgrid[0:h, 0:w]
    z = (2000 + 0.05 * yy + 3 * np.sin(xx / 17.0) * np.cos(yy / 23.0)
         + rng.normal(0, 0.15, (h, w))).astype("float32")
    z[200:215, 100:130] = -9999.0           # a hole straddling a strip edge
    z[:, :3] = -9999.0                       # a ragged edge
    gy, gx = np.gradient(np.where(z == -9999.0, np.nan, z), 1.0)
    s = np.hypot(gx, gy).astype("float32")
    s[~np.isfinite(s)] = -9999.0
    prof = dict(driver="GTiff", width=w, height=h, count=1, dtype="float32",
                crs="EPSG:32613", transform=from_origin(0, h, 1.0, 1.0),
                nodata=-9999.0)
    for name, a in (("dem.tif", z), ("slope.tif", s)):
        with rasterio.open(d / name, "w", **prof) as dst:
            dst.write(a, 1)
    return types.SimpleNamespace(dem=str(d / "dem.tif"),
                                 slope=str(d / "slope.tif"),
                                 units={"metres_per_unit": 1.0})


def test_strips_equal_the_whole_grid(grid):
    from geolens.landscape.features import terrain_roughness

    one = terrain_roughness(grid, window_m=9.0, out_prefix="r_one")
    many = terrain_roughness(grid, window_m=9.0, out_prefix="r_many",
                             block_rows=37)          # 15 strips, odd size
    assert one.parameters["block_rows"] >= 530       # really one strip
    for name in ("slope_std", "tpi", "curvature"):
        a = rasterio.open(getattr(one, name)).read(1)
        b = rasterio.open(getattr(many, name)).read(1)
        assert np.array_equal(a, b), name
        assert (a == -9999.0).sum() > 0              # the holes stayed holes


def test_the_strip_size_follows_the_memory_budget(grid, monkeypatch):
    from geolens.landscape import features

    monkeypatch.setattr(features, "ROUGHNESS_BLOCK_BYTES", 1e6)
    r = features.terrain_roughness(grid, window_m=9.0, out_prefix="r_budget")
    assert r.parameters["block_rows"] == 256      # the floor, not the grid
    assert r.parameters["halo_rows"] == 27 // 2 + 1   # TPI window 27 cells
