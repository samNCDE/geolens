"""Inline display.

The test that matters is the decimation one. Everything here works on a
300-cell fixture whether or not it reads efficiently; the property that decides
whether it survives a real 1 m DEM is that it never materialises the full array,
and that is invisible unless you assert it.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

import matplotlib
matplotlib.use("Agg")

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "view_test_out"


@pytest.fixture(scope="module", autouse=True)
def _env():
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"


@pytest.fixture(scope="module")
def fan_dem():
    """The shipped fan DEM, or a synthetic hill if it was not generated."""
    import rasterio
    from rasterio.transform import from_origin

    p = ROOT / "fans" / "fan_dem.tif"
    if p.exists():
        return str(p)
    q = OUT / "mini_dem.tif"
    if not q.exists():
        yy, xx = np.mgrid[0:120, 0:160]
        z = (200.0 - 0.05 * xx + 3.0 * np.sin(yy / 9.0)).astype("float32")
        prof = {"driver": "GTiff", "width": 160, "height": 120, "count": 1,
                "dtype": "float32", "crs": "EPSG:32612", "nodata": -9999.0,
                "transform": from_origin(500000, 4000000, 10.0, 10.0)}
        with rasterio.open(str(q), "w", **prof) as dst:
            dst.write(z, 1)
    return str(q)


@pytest.fixture(scope="module")
def blob_mask(fan_dem):
    import rasterio
    from geolens.raster import gtiff_profile

    p = OUT / "blob_mask.tif"
    with rasterio.open(fan_dem) as src:
        h, w = src.height, src.width
        profile = src.profile.copy()
    m = np.zeros((h, w), "uint8")
    yy, xx = np.mgrid[0:h, 0:w]
    m[((yy - h / 2) ** 2 + (xx - w / 2) ** 2) < (min(h, w) / 4) ** 2] = 1
    with rasterio.open(str(p), "w",
                       **gtiff_profile(profile, dtype="uint8", nodata=0)) as dst:
        dst.write(m, 1)
    return str(p)


@pytest.fixture(scope="module")
def blob_polygons(blob_mask):
    import geopandas as gpd
    import rasterio
    from rasterio import features as rfeat
    from shapely.geometry import shape as shp_shape

    p = OUT / "blobs.gpkg"
    with rasterio.open(blob_mask) as src:
        arr = src.read(1)
        geoms = [shp_shape(g) for g, v in
                 rfeat.shapes(arr, mask=arr > 0, transform=src.transform) if v]
        crs = src.crs
    gpd.GeoDataFrame({"fan_id": range(len(geoms))}, geometry=geoms,
                     crs=crs).to_file(str(p), driver="GPKG")
    return str(p)

@pytest.fixture(scope="module")
def big_raster():
    """A raster far larger than any display, to prove reads are decimated."""
    import rasterio
    from rasterio.transform import from_origin

    p = OUT / "big.tif"
    if not p.exists():
        w = h = 4000                      # 16M cells
        prof = {
            "driver": "GTiff", "width": w, "height": h, "count": 1,
            "dtype": "float32", "crs": "EPSG:32612",
            "transform": from_origin(500000, 4000000, 1.0, 1.0),
            "nodata": -9999.0, "compress": "deflate",
        }
        yy, xx = np.mgrid[0:h, 0:w]
        z = (1000 + 0.01 * xx + 5 * np.sin(yy / 200.0)).astype("float32")
        with rasterio.open(str(p), "w", **prof) as dst:
            dst.write(z, 1)
    return str(p)


# -- the property that matters ------------------------------------------------

def test_reads_are_decimated_not_full(big_raster):
    """A 16-million-cell raster must come back as roughly display size.

    If this ever reads the full array, it works here and kills the kernel on a
    real DEM, where the same code path would allocate gigabytes to draw a
    thousand pixels.
    """
    from geolens.view import _read_decimated

    arr, extent, crs = _read_decimated(big_raster, max_px=500)
    assert max(arr.shape) <= 520, f"read back {arr.shape} — not decimated"
    assert arr.size < 300_000, f"{arr.size} cells for a 500 px display"
    # The extent must still be the FULL raster, or the picture is a lie.
    assert extent[1] - extent[0] == pytest.approx(4000.0)


def test_decimation_respects_max_px(big_raster):
    from geolens.view import _read_decimated

    small, _, _ = _read_decimated(big_raster, max_px=200)
    large, _, _ = _read_decimated(big_raster, max_px=800)
    assert max(small.shape) < max(large.shape)
    assert max(small.shape) <= 220


def test_small_rasters_are_not_upsampled():
    """Decimation only ever shrinks."""
    from geolens.view import _read_decimated

    p = ROOT / "compound" / "compound_dem.tif"
    if not p.exists():
        pytest.skip("compound fixture not built")
    import rasterio
    with rasterio.open(str(p)) as src:
        native = (src.height, src.width)
    arr, _, _ = _read_decimated(str(p), max_px=10_000)
    assert arr.shape == native


# -- figures ------------------------------------------------------------------

def test_show_raster_returns_a_figure(big_raster):
    from geolens.view import show_raster

    fig = show_raster(big_raster, title="test", max_px=300)
    assert fig.axes
    assert fig.axes[0].get_title(loc="left") == "test"


def test_percentile_stretch_survives_an_outlier(big_raster):
    """One spike must not flatten the whole image to a single colour.

    A min/max stretch on a raster with one bad cell renders everything else
    identical, which reads as "the algorithm did nothing".
    """
    import rasterio
    from geolens.view import _read_decimated, show_raster

    spiked = OUT / "spiked.tif"
    with rasterio.open(big_raster) as src:
        prof = src.profile.copy()
        arr = src.read(1)
    arr[0, 0] = 1e9
    with rasterio.open(str(spiked), "w", **prof) as dst:
        dst.write(arr, 1)

    fig = show_raster(str(spiked), max_px=200)
    im = fig.axes[0].images[0]
    vmin, vmax = im.get_clim()
    assert vmax < 1e8, "the outlier set the colour scale"
    assert vmax > vmin


def test_show_mask_over_a_dem(fan_dem, blob_mask):
    """Builds its own mask. It used to read one from ``tests/e2e_out/``, which
    is a previous run's output — so on a clean checkout this skipped silently
    and the mask path was untested for anyone but the author."""
    from geolens.view import show_mask

    fig = show_mask(blob_mask, dem=fan_dem, title="Fans", max_px=300)
    assert "of frame" in fig.axes[0].get_title(loc="left")


def test_categorical_colours_are_stable():
    """The same class must be the same colour in every figure.

    Randomly assigned category colours make two runs impossible to compare,
    which defeats the point of looking at them.
    """
    from geolens.view import CONFINEMENT_COLORS, VALLEY_TYPE_COLORS

    assert CONFINEMENT_COLORS["confined"] != CONFINEMENT_COLORS["unconfined"]
    assert set(VALLEY_TYPE_COLORS) == {
        "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI"
    }


def test_sections_use_the_real_structure():
    """Guards against drift between cross_sections output and the plotter.

    The first version of this read `offsets`/`elevations`, which the API does
    not produce — it produces `points`. It rendered nothing and raised nothing.
    """
    from geolens.view import show_sections

    dem = ROOT / "compound" / "compound_dem.tif"
    line = ROOT / "compound" / "centerline.shp"
    if not (dem.exists() and line.exists()):
        pytest.skip("compound fixture not built")

    from geolens import api
    from geolens.morphology import delineate
    from geolens.sections import extract_cross_sections

    xs = api.cross_sections(str(dem), str(line), spacing=50, width=80)
    d = delineate(extract_cross_sections(str(dem), str(line), spacing=50,
                                         width=80))
    fig = show_sections(xs, delineation=d, n=4)
    drawn = [a for a in fig.axes if a.lines]
    assert len(drawn) >= 3, "sections did not draw"
    # Bank tops are vertical lines; at least one panel should have them.
    assert any(len(a.lines) > 1 for a in drawn), "no bank markers drawn"


def test_dispatch_picks_the_right_plotter(blob_polygons):
    from geolens.view import show

    assert show(blob_polygons).axes


def test_unknown_input_is_a_clear_error():
    from geolens.view import show
    from geolens.safety import GeolensError

    with pytest.raises(GeolensError, match="Do not know how to plot"):
        show("something.xyz")
    with pytest.raises(GeolensError, match="Nothing plottable"):
        show({"seconds": 1.0, "warnings": []})


def test_one_bad_panel_does_not_kill_the_figure():
    """A missing layer should cost one panel, not the whole summary."""
    from geolens.view import show_landscape

    dem = ROOT / "fans" / "fan_dem.tif"
    if not dem.exists():
        pytest.skip("fan fixture not built")
    result = {
        "terrain": {"dem": str(dem), "hand": str(ROOT / "does_not_exist.tif")},
        "valley": {},
        "classified": {},
    }
    fig = show_landscape(result, dem=str(dem))
    assert fig.axes, "the figure collapsed because one layer was missing"
