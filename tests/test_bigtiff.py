"""Large rasters must not hit the 4 GB classic-TIFF limit.

The failure this guards against, reported from a real DEM:

    CPLE_AppDefinedError: TIFFAppendToStrip: Maximum TIFF file size exceeded.
    Use BIGTIFF=YES creation option.

A classic TIFF cannot exceed 4 GB because its offsets are 32-bit. float32
crosses that at about 1.07 billion cells — a 32,000 x 32,000 grid, which is
64 km square at 2 m. Every test fixture in this repository is a few hundred
cells across, so nothing here would ever have caught it. That is the point of
these tests: they check the creation options rather than the output size.
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from geolens.raster import BIGTIFF_CELL_HINT, gtiff_profile


def _tiff_version(path: Path) -> int:
    """42 for a classic TIFF, 43 for a BigTIFF. Read straight from the header."""
    with open(path, "rb") as fh:
        head = fh.read(4)
    endian = "<" if head[:2] == b"II" else ">"
    return struct.unpack(endian + "H", head[2:4])[0]


def _base(width=64, height=64):
    return {
        "driver": "GTiff", "width": width, "height": height, "count": 1,
        "crs": "EPSG:32612", "transform": from_origin(500000, 4000000, 10, 10),
    }


def test_bigtiff_is_always_requested():
    """Every profile this package writes asks for BigTIFF when it might help."""
    p = gtiff_profile(_base(), dtype="float32", nodata=-9999.0)
    assert p["BIGTIFF"] == "IF_SAFER"


def test_if_safer_not_if_needed():
    """IF_NEEDED decides from an estimate that a compressed write can overshoot.

    That is exactly how a compressed raster ends up over 4 GB anyway. IF_SAFER
    switches at half the limit instead.
    """
    p = gtiff_profile(_base(), dtype="float32")
    assert p["BIGTIFF"] != "IF_NEEDED"
    assert p["compress"] == "deflate"


def test_the_option_actually_reaches_gdal(tmp_path):
    """Proves the plumbing, not just the dict.

    A creation option that rasterio silently drops would leave every assertion
    above passing and the real bug in place. Forcing BIGTIFF=YES and reading the
    magic number back is the only check that the option took effect.
    """
    out = tmp_path / "forced.tif"
    prof = gtiff_profile(_base(), dtype="float32", nodata=-9999.0, BIGTIFF="YES")
    with rasterio.open(str(out), "w", **prof) as dst:
        dst.write(np.zeros((64, 64), dtype="float32"), 1)
    assert _tiff_version(out) == 43, "BIGTIFF=YES did not produce a BigTIFF"


def test_small_rasters_stay_classic(tmp_path):
    """IF_SAFER must not promote everything — small files stay classic TIFF.

    Not cosmetic: it keeps the fixtures readable by anything, and confirms
    IF_SAFER is being evaluated rather than treated as YES.
    """
    out = tmp_path / "small.tif"
    prof = gtiff_profile(_base(), dtype="float32", nodata=-9999.0)
    with rasterio.open(str(out), "w", **prof) as dst:
        dst.write(np.zeros((64, 64), dtype="float32"), 1)
    assert _tiff_version(out) == 42


def test_large_rasters_are_tiled_small_ones_are_not():
    """Tiled blocks make panning a multi-gigabyte raster in a GIS cheap.

    Striped rasters force a full-width read for any window, which is what makes
    a big DEM feel frozen in ArcGIS Pro.
    """
    side = int(BIGTIFF_CELL_HINT ** 0.5) + 100
    big = gtiff_profile(_base(side, side), dtype="float32")
    assert big.get("tiled") is True
    assert big["blockxsize"] == 512 and big["blockysize"] == 512
    # Multiple of 16, which GTiff requires.
    assert big["blockxsize"] % 16 == 0

    small = gtiff_profile(_base(64, 64), dtype="float32")
    assert not small.get("tiled")
    assert "blockxsize" not in small


def test_inherited_tiling_is_dropped_for_small_output(tmp_path):
    """A tiled source profile must not force tiling on a small derived raster.

    GDAL errors when the block size exceeds the raster, and derived products
    (a clipped tile, a coarsened DEM) are routinely smaller than their source.
    """
    src_profile = _base(64, 64)
    src_profile.update(tiled=True, blockxsize=512, blockysize=512)
    p = gtiff_profile(src_profile, dtype="float32", nodata=-9999.0)
    assert "blockxsize" not in p

    out = tmp_path / "derived.tif"
    with rasterio.open(str(out), "w", **p) as dst:
        dst.write(np.zeros((64, 64), dtype="float32"), 1)
    assert out.exists()


def test_extra_options_win(tmp_path):
    """Callers can still override — width/height/transform come through."""
    p = gtiff_profile(_base(), dtype="int32", nodata=0,
                      width=10, height=20, compress="lzw")
    assert p["width"] == 10 and p["height"] == 20
    assert p["compress"] == "lzw"
    assert p["dtype"] == "int32" and p["nodata"] == 0


def test_every_write_in_the_package_is_routed():
    """No raster write may bypass gtiff_profile.

    Thirteen call sites had the same missing option. A new one added without
    the helper would reintroduce the bug on exactly the DEMs that matter, so
    check the source rather than trusting the convention.
    """
    import geolens

    root = Path(geolens.__file__).resolve().parent
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text()
        if 'compress="deflate"' in text and "def gtiff_profile" not in text:
            offenders.append(str(path.relative_to(root)))
    assert not offenders, (
        f"these files build a GeoTIFF profile by hand instead of calling "
        f"gtiff_profile: {offenders}"
    )
