"""Raster / DEM inspection and point sampling."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Sequence

from .safety import Guard, GeolensError


def _jsonable(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (str, bool, int)):
        return v
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if hasattr(v, "item"):
        try:
            return _jsonable(v.item())
        except Exception:
            pass
    return str(v)


def describe_raster(
    path: str | Path,
    guard: Guard | None = None,
    stats: bool = True,
    max_stat_pixels: int = 4_000_000,
) -> dict[str, Any]:
    """Structural description of a raster: CRS, grid, bands, nodata, statistics.

    Statistics are computed on a decimated read when the raster is large, so
    this stays fast on multi-gigabyte DEMs. The decimation factor is reported.
    """
    import numpy as np
    import rasterio

    guard = guard or Guard.from_env()
    p = guard.read_path(path)

    try:
        src = rasterio.open(str(p))
    except Exception as exc:
        raise GeolensError(f"Could not open raster {p}: {exc}") from exc

    with src:
        crs = src.crs
        crs_info: dict[str, Any] = {"defined": crs is not None}
        if crs is not None:
            try:
                crs_info.update(
                    {
                        "name": crs.to_wkt().split('"')[1] if crs.to_wkt() else None,
                        "epsg": crs.to_epsg(),
                        "is_geographic": bool(crs.is_geographic),
                    }
                )
            except Exception:
                pass
            # The grid's cell size below is in THESE units. Reporting it
            # under a bare "m" made a 2.5 ftUS grid read as 2.5 m.
            try:
                unit, factor = crs.linear_units_factor
                crs_info["linear_units"] = unit
                crs_info["metres_per_unit"] = float(factor)
            except Exception:                                # noqa: BLE001
                pass
            if crs.is_geographic:
                crs_info["warning"] = (
                    "Geographic CRS (degrees). Cell size is in degrees, not metres — "
                    "reproject before computing slope, distance, or volume."
                )
        else:
            crs_info["warning"] = "No CRS defined. Spatial position cannot be trusted."

        res_x, res_y = src.res
        b = src.bounds
        bands: list[dict[str, Any]] = []

        decim = 1
        if stats:
            npix = src.width * src.height
            if npix > max_stat_pixels:
                decim = max(1, int(math.sqrt(npix / max_stat_pixels)) + 1)

        for i in range(1, src.count + 1):
            rec: dict[str, Any] = {
                "band": i,
                "dtype": src.dtypes[i - 1],
                "nodata": _jsonable(src.nodatavals[i - 1]),
            }
            try:
                rec["description"] = src.descriptions[i - 1]
            except Exception:
                rec["description"] = None

            if stats:
                try:
                    out_h = max(1, src.height // decim)
                    out_w = max(1, src.width // decim)
                    arr = src.read(i, out_shape=(out_h, out_w), masked=True)
                    if arr.count():
                        rec["stats"] = {
                            "min": _jsonable(float(arr.min())),
                            "max": _jsonable(float(arr.max())),
                            "mean": _jsonable(float(arr.mean())),
                            "std": _jsonable(float(arr.std())),
                            "valid_pixels_sampled": int(arr.count()),
                            "nodata_pixels_sampled": int(arr.mask.sum())
                            if np.ma.is_masked(arr)
                            else 0,
                        }
                    else:
                        rec["stats"] = None
                        rec["note"] = "All sampled pixels are nodata."
                except Exception as exc:
                    rec["stats"] = None
                    rec["note"] = f"Statistics unavailable: {exc}"
            bands.append(rec)

        result = {
            "path": str(p),
            "driver": src.driver,
            "crs": crs_info,
            "grid": {
                "width": src.width,
                "height": src.height,
                "pixel_count": src.width * src.height,
                "cell_size_x": _jsonable(res_x),
                "cell_size_y": _jsonable(abs(res_y)),
                "square_cells": bool(abs(abs(res_x) - abs(res_y)) < 1e-9),
            },
            "extent": {
                "minx": _jsonable(b.left),
                "miny": _jsonable(b.bottom),
                "maxx": _jsonable(b.right),
                "maxy": _jsonable(b.top),
                "width": _jsonable(b.right - b.left),
                "height": _jsonable(b.top - b.bottom),
            },
            "band_count": src.count,
            "bands": bands,
            "stats_decimation": decim,
        }

    warnings = []
    if crs_info.get("warning"):
        warnings.append(crs_info["warning"])
    if not result["grid"]["square_cells"]:
        warnings.append(
            "Cells are not square. Slope and hydrologic tools may misbehave; "
            "consider resampling to a square grid."
        )
    if decim > 1:
        warnings.append(
            f"Statistics computed on a 1/{decim} decimated read for speed — "
            "treat min/max as approximate."
        )
    result["warnings"] = warnings
    return result


def sample_points(
    path: str | Path,
    points: Sequence[Sequence[float]],
    band: int = 1,
    guard: Guard | None = None,
) -> dict[str, Any]:
    """Sample raster values at (x, y) coordinate pairs in the raster's own CRS."""
    import rasterio

    guard = guard or Guard.from_env()
    p = guard.read_path(path)

    coords = [(float(x), float(y)) for x, y in points]
    with rasterio.open(str(p)) as src:
        nodata = src.nodatavals[band - 1]
        vals = list(src.sample(coords, indexes=band))

    out = []
    for (x, y), v in zip(coords, vals):
        raw = float(v[0])
        is_nd = nodata is not None and (
            raw == nodata or (math.isnan(raw) and math.isnan(float(nodata)))
        )
        out.append(
            {"x": x, "y": y, "value": None if (is_nd or math.isnan(raw)) else raw}
        )

    valid = sum(1 for r in out if r["value"] is not None)
    return {
        "path": str(p),
        "band": band,
        "sampled": len(out),
        "valid": valid,
        "nodata_hits": len(out) - valid,
        "points": out,
    }


# -- writing large rasters ----------------------------------------------------
#
# Every raster this package writes goes through gtiff_profile(). It exists
# because of one failure mode that only appears on real data:
#
#   CPLE_AppDefinedError: TIFFAppendToStrip: Maximum TIFF file size exceeded.
#   Use BIGTIFF=YES creation option.
#
# A classic TIFF cannot exceed 4 GB — the format's offsets are 32-bit. A float32
# raster crosses that at about 1.07 billion cells, which is a 32,000 x 32,000
# grid: roughly 64 km square at 2 m, or 32 km square at 1 m. In other words it
# is not an exotic case, it is an ordinary lidar county. Small test fixtures
# never reach it, so this is exactly the class of bug that passes every test and
# fails on the first real DEM.
#
# BIGTIFF=IF_SAFER rather than IF_NEEDED: with compression GDAL cannot predict
# the output size, and IF_NEEDED decides from an estimate that a compressed
# write can overshoot. IF_SAFER switches at half the limit and takes the
# guesswork out. BigTIFF is read fine by GDAL, QGIS and ArcGIS Pro.

BIGTIFF_CELL_HINT = 1_000_000  # above this, write tiled as well

# Tile dimension must be a multiple of 16 for GTiff. 512 is the usual choice:
# it makes windowed reads in a GIS cheap, which matters when the user opens a
# multi-gigabyte raster and pans around it.
_TILE = 512


def gtiff_profile(
    base: dict,
    dtype: str,
    nodata: Any = None,
    count: int = 1,
    compress: str = "deflate",
    **extra: Any,
) -> dict:
    """Build a GeoTIFF creation profile that survives a large raster.

    Pass the source dataset's profile as ``base``; this returns a copy with the
    dtype, nodata and creation options set. Always use this rather than
    ``profile.update(...)`` directly, or the 4 GB limit comes back.
    """
    p = dict(base)
    p.update(driver="GTiff", dtype=dtype, count=count, nodata=nodata,
             compress=compress, BIGTIFF="IF_SAFER")

    width = int(p.get("width") or 0)
    height = int(p.get("height") or 0)
    if width * height >= BIGTIFF_CELL_HINT and width >= _TILE and height >= _TILE:
        p.update(tiled=True, blockxsize=_TILE, blockysize=_TILE)
    else:
        # Striped. Drop any tiling keys inherited from the source profile, or
        # GDAL errors on a raster too small for its own block size.
        for k in ("tiled", "blockxsize", "blockysize"):
            p.pop(k, None)

    p.update(extra)
    return p


# -- statistics without materialising the grid --------------------------------

PERCENTILE_SAMPLE_CELLS = 20_000_000


def percentiles(path: str, qs: "list[float]",
                max_cells: int = PERCENTILE_SAMPLE_CELLS,
                band: int = 1) -> "dict[str, float]":
    """Percentiles of a raster, read decimated rather than whole.

    Choosing a threshold from a distribution does NOT require every cell. A
    percentile estimated from 20 million cells is identical, to the precision
    anyone acts on, to one from two billion -- but reading the whole grid costs
    two arrays the size of the raster, because ``np.percentile`` partitions a
    copy.

    That is not a theoretical concern. The starting notebook used to do::

        with rasterio.open(rg.slope_std) as s:
            ss = s.read(1)
        pcts = {f"p{q}": np.percentile(ss, q) for q in (50, 75, 90, 95)}

    On a 1.94-billion-cell DEM that is a 7.7 GB read plus a 7.7 GB partition,
    four times over, in one notebook cell -- and it killed an overnight run
    after the two expensive steps before it had already succeeded.

    rasterio's ``out_shape`` uses the raster's overviews when it has them, so
    this is also far faster than a full read even when memory is not the
    constraint.
    """
    import numpy as np
    import rasterio

    with rasterio.open(path) as src:
        h, w = src.height, src.width
        total = h * w
        step = 1 if total <= max_cells else int(np.ceil(np.sqrt(total / max_cells)))
        arr = src.read(band, masked=True,
                       out_shape=(max(1, h // step), max(1, w // step)))

    vals = np.asarray(arr.compressed(), dtype="float64")
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        raise GeolensError(f"{path} has no valid data to take percentiles of.")
    out = {f"p{q:g}": float(np.percentile(vals, q)) for q in qs}
    out["n_sampled"] = int(vals.size)
    out["decimation"] = int(step)
    return out
