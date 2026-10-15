"""Tile-and-mosaic writing for fetched rasters (NAIP imagery, 3DEP DEMs).

Remote imagery and elevation come as tiles: NAIP quarter-quads, 3DEP 1 m
10 km squares. Both fetchers work the same way:

1. An **output grid** over the area of interest: a CRS, a cell size in true
   metres, and an origin every tile snaps to, so tiles line up exactly.
2. For each tile, **newest first**, the part of the grid its footprint
   covers is read (streamed where the server allows it) and saved as its own
   GeoTIFF in the tiles folder. The tiles are kept: they are the imagery, and
   a second run over the same ground reads them from disk.
3. The tile is written into the **mosaic** only where no newer tile has been
   written yet. A mosaic, not an average: every cell keeps one acquisition.

Memory is one tile plus a one-byte-a-cell coverage mask, whatever the size
of the area; the mosaic itself is written window by window to a tiled
BigTIFF.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .raster import gtiff_profile


@dataclass
class Grid:
    """An output grid: CRS, top-left origin, cell size in CRS units, shape."""

    crs: Any
    left: float
    top: float
    res: float                      # CRS units
    width: int
    height: int
    metres_per_unit: float = 1.0

    @property
    def transform(self):
        from rasterio.transform import from_origin

        return from_origin(self.left, self.top, self.res, self.res)

    @property
    def cells(self) -> int:
        return self.width * self.height

    @property
    def cell_m(self) -> float:
        return self.res * self.metres_per_unit

    def bounds(self) -> tuple[float, float, float, float]:
        return (self.left, self.top - self.height * self.res,
                self.left + self.width * self.res, self.top)

    def window_for(self, bounds: tuple[float, float, float, float]):
        """The (row, col, height, width) of this grid covering ``bounds``
        (minx, miny, maxx, maxy in the grid's CRS), clipped to the grid;
        None when they do not overlap."""
        minx, miny, maxx, maxy = bounds
        c0 = max(0, int(math.floor((minx - self.left) / self.res)))
        c1 = min(self.width, int(math.ceil((maxx - self.left) / self.res)))
        r0 = max(0, int(math.floor((self.top - maxy) / self.res)))
        r1 = min(self.height, int(math.ceil((self.top - miny) / self.res)))
        if c1 <= c0 or r1 <= r0:
            return None
        return r0, c0, r1 - r0, c1 - c0

    def sub_transform(self, r0: int, c0: int):
        from rasterio.transform import from_origin

        return from_origin(self.left + c0 * self.res, self.top - r0 * self.res,
                           self.res, self.res)


def grid_over(bounds: tuple[float, float, float, float], crs: Any,
              cell_m: float, metres_per_unit: float = 1.0) -> Grid:
    """A grid covering ``bounds`` (in ``crs``) at ``cell_m`` true metres,
    origin snapped to a multiple of the cell so repeated runs line up."""
    res = cell_m / metres_per_unit
    minx, miny, maxx, maxy = bounds
    left = math.floor(minx / res) * res
    top = math.ceil(maxy / res) * res
    width = int(math.ceil((maxx - left) / res))
    height = int(math.ceil((top - miny) / res))
    return Grid(crs=crs, left=left, top=top, res=res, width=width,
                height=height, metres_per_unit=metres_per_unit)


class Mosaic:
    """A BigTIFF mosaic on ``grid``, filled newest-first, window by window."""

    def __init__(self, path: str | Path, grid: Grid, dtype: str, count: int,
                 nodata: float, band_names: list[str] | None = None,
                 predictor: int | None = None):
        import rasterio

        self.path = str(path)
        self.grid = grid
        self.nodata = nodata
        self.count = count
        self.filled = np.zeros((grid.height, grid.width), dtype=bool)
        prof = gtiff_profile({"width": grid.width, "height": grid.height,
                              "crs": grid.crs, "transform": grid.transform},
                             dtype=dtype, nodata=nodata, count=count)
        if predictor:
            prof["predictor"] = predictor
        # Always tiled: the mosaic is written in tile-sized windows, and a
        # striped file would rewrite whole strips for each one.
        if grid.width >= 256 and grid.height >= 256:
            prof.update(tiled=True, blockxsize=256, blockysize=256)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._dst = rasterio.open(self.path, "w+", **prof)
        if band_names:
            for i, name in enumerate(band_names, 1):
                self._dst.set_band_description(i, name)

    def add(self, window: tuple[int, int, int, int], data: np.ndarray,
            valid: np.ndarray) -> int:
        """Write ``data`` (count, h, w) into ``window`` where ``valid`` and
        nothing newer is there yet. Returns the number of cells filled."""
        from rasterio.windows import Window

        r0, c0, h, w = window
        win = Window(c0, r0, w, h)
        filled = self.filled[r0:r0 + h, c0:c0 + w]
        take = valid & ~filled
        n = int(take.sum())
        if n == 0:
            return 0
        cur = self._dst.read(window=win)
        cur = np.where(filled[None], cur, self.nodata).astype(cur.dtype)
        data = data if data.ndim == 3 else data[None]
        cur[:, take] = data[:, take].astype(cur.dtype)
        self._dst.write(cur, window=win)
        filled |= take
        return n

    @property
    def coverage(self) -> float:
        return float(self.filled.mean()) if self.filled.size else 0.0

    def close(self) -> None:
        self._dst.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def write_tile(path: str | Path, grid: Grid, window, data: np.ndarray,
               dtype: str, nodata: float,
               band_names: list[str] | None = None) -> str:
    """One tile, on the grid, as its own GeoTIFF."""
    import rasterio

    r0, c0, h, w = window
    data = data if data.ndim == 3 else data[None]
    prof = gtiff_profile({"width": w, "height": h, "crs": grid.crs,
                          "transform": grid.sub_transform(r0, c0)},
                         dtype=dtype, nodata=nodata, count=data.shape[0])
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(str(path), "w", **prof) as dst:
        dst.write(data.astype(dtype))
        if band_names:
            for i, name in enumerate(band_names, 1):
                dst.set_band_description(i, name)
    return str(path)


def read_onto(src_path: str, bands: list[int], grid: Grid, window,
              resampling: str = "bilinear") -> tuple[np.ndarray, Any]:
    """Read ``bands`` of a raster (a URL is streamed through /vsicurl/) onto
    one window of ``grid``. Returns ``(array, source nodata)``; cells the
    source does not cover come back as NaN."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    r0, c0, h, w = window
    path = (f"/vsicurl/{src_path}" if str(src_path).startswith(("http://",
                                                                 "https://"))
            else str(src_path))
    out = np.full((len(bands), h, w), np.nan, dtype="float32")
    with rasterio.open(path) as src:
        for i, b in enumerate(bands):
            reproject(source=rasterio.band(src, b), destination=out[i],
                      src_transform=src.transform, src_crs=src.crs,
                      dst_transform=grid.sub_transform(r0, c0),
                      dst_crs=grid.crs,
                      resampling=getattr(Resampling, resampling),
                      src_nodata=src.nodata, dst_nodata=np.nan)
        nodata = src.nodata
    return out, nodata
