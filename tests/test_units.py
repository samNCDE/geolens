"""Linear units, and the HAND invariant.

Both of these came off one real run: a 2.4 ft DEM on NAD83(2011) / New Mexico
Central (ftUS), read as though it were 2.4 m. Every number in this file is
taken from that run.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "units_test_out"

# The real values, so a regression is recognisable rather than abstract.
FT_US = 0.30480060960121924
CELL_FT = 2.400045177320985
Z_MIN, Z_MAX = 6065.6357421875, 11974.024414062      # feet: Ruidoso to Sierra Blanca


@pytest.fixture(scope="module", autouse=True)
def _env():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"


def _crs(epsg):
    import pyproj
    return pyproj.CRS.from_epsg(epsg)


# -- the unit bug -------------------------------------------------------------

def test_a_foot_grid_is_not_read_as_metres():
    """The 10.8x error, exactly as it occurred."""
    from geolens.units import unit_info

    u = unit_info(_crs(6529), CELL_FT, CELL_FT, (Z_MIN, Z_MAX))
    assert not u.is_metric
    assert u.cell_size_m == pytest.approx(0.7315, abs=1e-3)

    wrong = max(1, int(round(1.0 * 1e6 / CELL_FT ** 2)))     # what it used to do
    right = u.cells_for_area_km2(1.0)
    assert wrong == pytest.approx(173_605, abs=50)
    assert right == pytest.approx(1_868_657, rel=1e-3)
    assert right / wrong == pytest.approx(10.76, abs=0.05), (
        "a 1 km2 stream threshold was 10.8x too low on this DEM"
    )


def test_a_metric_grid_is_unchanged():
    """The fix must not move anything for the common case."""
    from geolens.units import unit_info

    u = unit_info(_crs(32612), 2.0, 2.0, (1500.0, 1800.0))
    assert u.is_metric
    assert u.cell_size_m == 2.0
    assert u.cells_for_area_km2(1.0) == 250_000
    assert u.z_from_metres(15.0) == 15.0
    assert not u.warnings


def test_the_unit_is_reported_not_just_corrected():
    """Silent correction is its own trap: the user reads the raster in a GIS."""
    from geolens.units import unit_info

    u = unit_info(_crs(6529), CELL_FT, CELL_FT, (Z_MIN, Z_MAX))
    assert any("not metres" in w for w in u.warnings)
    # The relief must be shown converted, so a wrong z assumption is visible.
    joined = " ".join(u.warnings)
    assert "1,849" in joined and "3,650" in joined, (
        "the relief was not restated in metres, so the vertical assumption "
        "cannot be sanity-checked by eye"
    )


def test_vertical_thresholds_convert():
    from geolens.units import unit_info

    u = unit_info(_crs(6529), CELL_FT, CELL_FT, (Z_MIN, Z_MAX))
    assert u.vertical_assumed, "no vertical CRS here, so this must be flagged"
    assert u.z_from_metres(15.0) == pytest.approx(49.21, abs=0.05)
    assert u.z_to_metres(100.0) == pytest.approx(30.48, abs=0.01)


def test_an_explicit_vertical_unit_wins():
    """A DEM in a ftUS CRS whose z really is metres — it happens."""
    from geolens.units import unit_info

    u = unit_info(_crs(6529), CELL_FT, CELL_FT, (1849.0, 3650.0),
                  vertical_unit="m")
    assert not u.vertical_assumed
    assert u.z_from_metres(15.0) == 15.0


def test_non_square_cells_use_true_cell_area():
    from geolens.units import unit_info

    u = unit_info(_crs(32612), 2.0, 3.0, None)
    assert not u.square_cells
    assert u.cell_area_m2 == pytest.approx(6.0)
    assert u.cells_for_area_km2(1.0) == pytest.approx(166_667, rel=1e-3), (
        "area was computed from cell_size**2 rather than dx*dy"
    )
    assert any("not square" in w for w in u.warnings)


def test_build_terrain_uses_true_metres_on_a_foot_grid():
    from geolens.landscape import build_terrain

    n = 100
    yy, xx = np.mgrid[0:n, 0:n]
    z = (7000.0 + 0.9 * np.abs(xx - n // 2) - 0.4 * yy).astype("float32")
    p = OUT / "ft.tif"
    prof = {"driver": "GTiff", "width": n, "height": n, "count": 1,
            "dtype": "float32", "crs": _crs(6529).to_string(), "nodata": -9999.0,
            "transform": from_origin(500000, 4000000, CELL_FT, CELL_FT)}
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(z, 1)

    t = build_terrain(str(p), stream_threshold_km2=0.0005, out_prefix="ftterr")
    assert t.cell_size == pytest.approx(0.7315, abs=1e-3), (
        f"cell_size is {t.cell_size}, which is CRS units, not metres — "
        f"every downstream window size in metres inherits the error"
    )
    assert t.cell_size_crs == pytest.approx(CELL_FT, abs=1e-6)
    assert t.units["horizontal_unit"] == "US survey foot"
    assert any("not metres" in w for w in t.warnings)


# -- the HAND invariant -------------------------------------------------------

def test_hand_cannot_exceed_the_dems_relief():
    """HAND of 20,628 on a DEM with 5,908 of relief — the real symptom.

    The excess was exactly the nodata value: flow ran off into nodata and the
    height was taken above -9999 instead of above a stream.
    """
    from geolens.landscape.terrain import _sanitise_hand

    n = 50
    z = np.linspace(6000.0, 6500.0, n * n).reshape(n, n).astype("float32")
    p = OUT / "relief.tif"
    prof = {"driver": "GTiff", "width": n, "height": n, "count": 1,
            "dtype": "float32", "crs": "EPSG:32612", "nodata": -9999.0,
            "transform": from_origin(500000, 4000000, 2.0, 2.0)}
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(z, 1)

    hand = np.full((n, n), 12.0)
    hand[0, :5] = z[0, :5] + 9999.0          # the real failure mode
    warnings: list[str] = []
    out = _sanitise_hand(hand, p, warnings)

    assert (out[0, :5] == -9999.0).all(), "impossible HAND values survived"
    assert out.max() <= 500.0 * 1.001 + 1e-6
    joined = " ".join(warnings)
    assert "impossible" in joined and "nodata" in joined, (
        "the failure was masked but not explained"
    )


def test_hand_is_masked_to_the_dems_valid_cells():
    """HAND had 10.5 million MORE valid cells than the DEM it came from."""
    from geolens.landscape.terrain import _sanitise_hand

    n = 40
    yy, xx = np.mgrid[0:n, 0:n]
    z = (1500.0 + 0.5 * yy).astype("float32")     # real relief, so the
    z[:4, :] = -9999.0                            # invariant is not degenerate
    p = OUT / "masked.tif"
    prof = {"driver": "GTiff", "width": n, "height": n, "count": 1,
            "dtype": "float32", "crs": "EPSG:32612", "nodata": -9999.0,
            "transform": from_origin(500000, 4000000, 2.0, 2.0)}
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(z, 1)

    hand = np.full((n, n), 3.0)              # a value everywhere, including nodata
    warnings: list[str] = []
    out = _sanitise_hand(hand, p, warnings)

    assert (out[:4, :] == -9999.0).all()
    assert int((out != -9999.0).sum()) == (n - 4) * n
    assert any("no data" in w for w in warnings)
