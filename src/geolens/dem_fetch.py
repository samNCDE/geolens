"""USGS 3DEP elevation for an area: lidar where it exists, 1/3 arc-second
where it does not, as tiles and a mosaic in a folder you choose.

WHERE IT COMES FROM
-------------------
The National Map's product API (``tnmaccess.nationalmap.gov``) lists the
3DEP **1 m** DEM tiles -- 10 km squares, one per lidar project, in that
project's UTM zone -- that touch the area. They are GeoTIFFs on Amazon S3,
read through ``/vsicurl/`` so only the part over the area is fetched; if GDAL
cannot get through (a proxy with its own certificate), the whole tile is
downloaded with Python's HTTPS instead, the same fallback the NAIP reader
uses.

Where no 1 m project covers part of the area, the seamless **1/3
arc-second** (about 10 m) DEM fills it, resampled onto the same grid, and the
result says how much of the area that was. Its tiles are one degree square,
named by their north-west corner, so they need no search.

Where several lidar projects overlap, the most recently published wins,
cell by cell (a mosaic, not an average). A tile is skipped only when every
cell it could fill inside the area is already filled with data: tile
footprints are 10 km squares whatever the project flew, and a project near a
UTM zone line delivers the same squares in both zones, the data in one of
them. Until v0.45 a footprint covered by newer tiles was enough to skip a
tile, which left holes wherever the newer one had no data there (Kingman,
October 2026: the zone 11 tiles of a project straddling 114 W, and older
projects under newer ones; mosaics 14-70% filled).

UNITS
-----
3DEP elevations are metres (NAVD88). The output is in the lidar's own UTM
zone by default, metres throughout. Ask for another CRS and the grid is
built there; if that CRS is in feet the elevations are converted to feet
too, because geolens reads a DEM's elevations in its horizontal unit when
the file does not say otherwise -- metres of relief read as feet would be
3.28 times too flat.
"""
from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np

from .safety import Guard, GeolensError, note, progress

TNM_API = "https://tnmaccess.nationalmap.gov/api/v1/products"
DATASET_1M = "Digital Elevation Model (DEM) 1 meter"
NED13_URL = ("https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/"
             "TIFF/current/{tile}/USGS_13_{tile}.tif")
MAX_CELLS = 3_000_000_000
NODATA = -9999.0
# 2: tiles skipped by the data they would add, not their footprints (v0.45).
# A mosaic without it in dem_sources.json may have holes: fetch it again.
FETCH_VERSION = 2


def _get_json(url: str, timeout: int = 60, retries: int = 3) -> dict:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"Accept": "application/json",
                              "User-Agent": "geolens (+geomorphic analysis)"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:                               # noqa: BLE001
            last = e
            if attempt < retries - 1:
                time.sleep(2.0 * (attempt + 1))
    raise GeolensError(
        f"Could not reach The National Map after {retries} attempts.\n"
        f"  url:   {url}\n  error: {type(last).__name__}: {last}\n"
        f"The service is sometimes slow; try again, or download tiles from "
        f"apps.nationalmap.gov/downloader and give them as the DEM.") from last


def search_1m(bbox_wgs84: tuple[float, float, float, float],
              timeout: int = 60) -> list[dict]:
    """3DEP 1 m tiles touching a lon/lat box, most recently published
    first."""
    items: list[dict] = []
    offset = 0
    for _ in range(40):
        q = urllib.parse.urlencode({
            "datasets": DATASET_1M,
            "bbox": ",".join(f"{v:.6f}" for v in bbox_wgs84),
            "prodFormats": "GeoTIFF", "max": 500, "offset": offset,
            "outputFormat": "JSON"})
        doc = _get_json(f"{TNM_API}?{q}", timeout=timeout)
        got = doc.get("items") or []
        items.extend(got)
        total = int(doc.get("total") or len(items))
        if not got or len(items) >= total:
            break
        offset = len(items)
    items.sort(key=lambda i: (i.get("publicationDate") or "",
                              i.get("lastUpdated") or ""), reverse=True)
    return items


def ned13_tiles(bbox_wgs84: tuple[float, float, float, float]) -> list[dict]:
    """The 1/3 arc-second tiles covering a lon/lat box (no search needed)."""
    minx, miny, maxx, maxy = bbox_wgs84
    out = []
    for top in range(int(math.floor(miny)) + 1, int(math.ceil(maxy)) + 1):
        for west in range(int(math.floor(minx)), int(math.ceil(maxx))):
            ns = f"n{top:02d}" if top >= 0 else f"s{-top:02d}"
            ew = f"w{-west:03d}" if west < 0 else f"e{west:03d}"
            name = f"{ns}{ew}"
            out.append({
                "title": f"USGS 1/3 arc-second {name}",
                "downloadURL": NED13_URL.format(tile=name),
                "boundingBox": {"minX": west, "maxX": west + 1,
                                "minY": top - 1, "maxY": top},
                "source": "1/3 arc-second"})
    return out


def _inside(grid, window, geom):
    """Cells of ``window`` whose centres are inside ``geom``."""
    from rasterio.features import geometry_mask

    r0, c0, h, w = window
    return ~geometry_mask([geom.__geo_interface__], out_shape=(h, w),
                          transform=grid.sub_transform(r0, c0))


def _cells_to_fill(m, grid, window, part) -> bool:
    """Whether a tile could still fill a cell: an empty cell inside both
    the area and the tile's footprint (``part``)."""
    r0, c0, h, w = window
    empty = ~m.filled[r0:r0 + h, c0:c0 + w]
    if not empty.any():
        return False
    return bool((empty & _inside(grid, window, part)).any())


def _area_filled(m, grid, area) -> float:
    """The share of the area (its cells' centres) with data in the mosaic,
    on a thinned grid when the mosaic is large."""
    step = max(1, int(math.ceil(math.sqrt(grid.cells / 2.5e7))))
    filled = m.filled[::step, ::step]
    from rasterio.features import geometry_mask
    from rasterio.transform import from_origin

    tr = from_origin(grid.left, grid.top, grid.res * step, grid.res * step)
    inside = ~geometry_mask([area.__geo_interface__], out_shape=filled.shape,
                            transform=tr)
    n = int(inside.sum())
    return float((filled & inside).sum()) / n if n else 0.0


def _download(url: str, dest: Path, timeout: int = 300) -> Path:
    from .naip import _download as _dl

    return _dl(url, dest, timeout=timeout)


def fetch_dem(
    aoi: str | Path,
    source: str = "best",
    resolution_m: float | None = None,
    crs: str | None = None,
    buffer_m: float = 0.0,
    timeout: int = 60,
    guard: Guard | None = None,
) -> dict[str, Any]:
    """3DEP elevation over ``aoi`` into the output folder (``GEOLENS_OUT``):

    ``dem_tiles/``        each source tile, clipped to the area, on the grid
    ``dem_mosaic.tif``    all of them, newest lidar on top
    ``dem_sources.json``  which tiles, which projects, how much was lidar

    ``aoi``: a polygon layer, or a raster whose extent is the area.
    ``source``: ``"best"`` (1 m lidar, 1/3 arc-second where there is none),
    ``"1m"`` (lidar only) or ``"1/3"`` (the ~10 m seamless DEM only).
    ``resolution_m``: default 1 m, or 10 m for ``"1/3"``.
    ``crs``: the output CRS (EPSG code, WKT or a file to copy it from); by
    default the lidar's own UTM zone, or the area's CRS for ``"1/3"``.
    """
    import pyproj
    from shapely.geometry import box
    from shapely.ops import transform as _transform

    from .mosaic import Mosaic, grid_over, read_onto, write_tile
    from .naip import _aoi_geometry

    t0 = time.monotonic()
    guard = guard or Guard.from_env()
    warnings: list[str] = []
    source = (source or "best").lower().replace(" ", "")
    if source not in ("best", "1m", "1/3"):
        raise GeolensError("source must be 'best', '1m' or '1/3'.")

    geom, aoi_crs, _ = _aoi_geometry(str(aoi), guard)
    if buffer_m:
        # Buffer in metres: in a projected CRS directly, else via UTM.
        pc = pyproj.CRS.from_user_input(aoi_crs)
        mpu0 = 1.0 if pc.is_geographic else float(
            pc.axis_info[0].unit_conversion_factor)
        if pc.is_geographic:
            raise GeolensError("Give the area in a projected CRS to buffer it.")
        geom = geom.buffer(buffer_m / mpu0)
    to_wgs = pyproj.Transformer.from_crs(aoi_crs, "EPSG:4326", always_xy=True)
    area_wgs = _transform(to_wgs.transform, geom)
    bbox = area_wgs.bounds

    lidar = search_1m(bbox, timeout) if source in ("best", "1m") else []
    note(f"{len(lidar)} 3DEP 1 m tile(s) touch the area")

    # The output CRS: asked for, else the newest lidar tile's own, else the
    # area's.
    out_crs = None
    if crs:
        p = Path(str(crs))
        if p.exists():
            import rasterio

            try:
                with rasterio.open(str(p)) as src:
                    out_crs = src.crs
            except Exception:                                # noqa: BLE001
                out_crs = _aoi_geometry(str(p), guard)[1]
        else:
            out_crs = crs
    elif lidar:
        import rasterio

        try:
            first = lidar[0]["downloadURL"]
            path = (f"/vsicurl/{first}" if first.startswith("http") else first)
            with rasterio.open(path) as src:
                out_crs = src.crs
        except Exception:                                    # noqa: BLE001
            out_crs = None
        if out_crs is None:
            lon = (bbox[0] + bbox[2]) / 2
            zone = int((lon + 180) // 6) + 1
            out_crs = f"EPSG:{26900 + zone}"                 # NAD83 / UTM
    else:
        out_crs = aoi_crs
    pcrs = pyproj.CRS.from_user_input(out_crs)
    if pcrs.is_geographic:
        raise GeolensError("Give a projected output CRS: the grid is built "
                           "in metres.")
    mpu = float(pcrs.axis_info[0].unit_conversion_factor)
    to_out = pyproj.Transformer.from_crs("EPSG:4326", pcrs, always_xy=True)
    area = _transform(to_out.transform, area_wgs)
    cell_m = float(resolution_m or (10.0 if source == "1/3" else 1.0))
    grid = grid_over(area.bounds, pcrs, cell_m, mpu)
    if grid.cells > MAX_CELLS:
        raise GeolensError(
            f"A {cell_m:g} m grid over this area is {grid.width:,} x "
            f"{grid.height:,} = {grid.cells / 1e9:.1f} billion cells. Use a "
            f"coarser resolution or a smaller area.")
    z_factor = 1.0 / mpu            # metres of elevation -> the CRS's unit
    z_unit = pcrs.axis_info[0].unit_name if mpu != 1.0 else "metre"
    if mpu != 1.0:
        note(f"elevations converted from metres to {z_unit} to match the CRS")

    tiles = list(lidar)
    # "best": the 1/3 arc-second DEM fills whatever the lidar leaves empty;
    # a lidar tile's footprint says little about where it has data, so it
    # is offered over the whole area and fills only the cells still empty.
    if source == "1/3" or source == "best":
        tiles += ned13_tiles(bbox)
        if source == "best" and not lidar:
            warnings.append("No 3DEP 1 m lidar covers this area; the DEM is "
                            "the 1/3 arc-second (about 10 m) product.")

    out = Path(guard.out_dir)
    tile_dir = Path(guard.write_path("dem_tiles"))
    tile_dir.mkdir(parents=True, exist_ok=True)
    import hashlib

    key = hashlib.sha1(f"{pcrs.to_wkt()}|{grid.res:.6f}|{grid.left:.3f}|"
                       f"{grid.top:.3f}".encode()).hexdigest()[:8]
    mosaic_path = str(out / "dem_mosaic.tif")
    sources: list[dict] = []
    covered = None
    lidar_cells = 0
    lidar_share = None
    with Mosaic(mosaic_path, grid, "float32", 1, NODATA, ["elevation"],
                predictor=3) as m:
        for it in progress(tiles, label="DEM tiles"):
            if it.get("source") == "1/3 arc-second" and lidar_share is None:
                lidar_share = _area_filled(m, grid, area)
            bb = it.get("boundingBox") or {}
            try:
                fp = _transform(to_out.transform,
                                box(bb["minX"], bb["minY"], bb["maxX"], bb["maxY"]))
                part = fp.intersection(area)
            except Exception:                                # noqa: BLE001
                part = area
            if part.is_empty:
                continue
            window = grid.window_for(part.bounds)
            if window is None:
                continue
            if covered is not None and not _cells_to_fill(m, grid, window, part):
                sources.append({"title": it.get("title"), "used": False,
                                "why": "its cells are filled already"})
                continue
            url = it.get("downloadURL") or ""
            name = Path(urllib.parse.urlparse(url).path).stem or "tile"
            tile_path = tile_dir / f"{name}_{key}.tif"
            how = "disk"
            try:
                if tile_path.exists():
                    arr, _ = read_onto(str(tile_path), [1], grid, window)
                    z = arr[0]
                else:
                    try:
                        arr, _ = read_onto(url, [1], grid, window)
                        how = "streamed"
                    except Exception as e1:                  # noqa: BLE001
                        if not url.startswith("http"):
                            raise
                        note(f"  streaming failed for {name} "
                             f"({type(e1).__name__}); downloading it")
                        whole = tile_dir / "source" / f"{name}.tif"
                        _download(url, whole, timeout=max(timeout, 600))
                        arr, _ = read_onto(str(whole), [1], grid, window)
                        how = "downloaded"
                    z = arr[0] * np.float32(z_factor)
                    write_tile(tile_path, grid, window,
                               np.nan_to_num(z, nan=NODATA), "float32", NODATA,
                               ["elevation"])
            except Exception as e:                           # noqa: BLE001
                warnings.append(f"skipped {name}: {type(e).__name__}: {e}")
                sources.append({"title": it.get("title"), "used": False,
                                "error": f"{type(e).__name__}: {e}"})
                continue
            valid = np.isfinite(z) & (z > NODATA + 1)
            n = m.add(window, z[None], valid)
            if it.get("source") != "1/3 arc-second":
                lidar_cells += n
            covered = part if covered is None else covered.union(part)
            sources.append({"title": it.get("title"), "used": True, "how": how,
                            "url": url, "published": it.get("publicationDate"),
                            "tile": str(tile_path), "cells": n})
        coverage = m.coverage
        area_coverage = _area_filled(m, grid, area)
        if lidar_share is None:
            lidar_share = area_coverage if source != "1/3" else 0.0
    filled = max(1, int(m.filled.sum()))
    lidar_frac = lidar_cells / filled
    if source == "1m" and area_coverage < 0.995:
        warnings.append(f"{1 - area_coverage:.0%} of the area has no 3DEP 1 m "
                        f"lidar data and is left as nodata.")
    elif source == "best" and lidar and lidar_share < 0.995:
        warnings.append(
            f"{1 - lidar_share:.0%} of the area has no 3DEP 1 m lidar data; it "
            f"is filled from the 1/3 arc-second DEM (about 10 m), resampled to "
            f"{cell_m:g} m. Channel measurements there are not lidar.")
    if coverage < 0.95:
        warnings.append(f"The mosaic covers {coverage:.0%} of its grid (the "
                        f"rest is outside the area or has no DEM).")
    sources_path = str(out / "dem_sources.json")
    with open(sources_path, "w", encoding="utf-8") as fh:
        json.dump({"aoi": str(aoi), "bbox_wgs84": list(bbox), "source": source,
                   "crs": pcrs.to_string(), "cell_size_m": cell_m,
                   "z_unit": z_unit, "lidar_fraction": lidar_frac,
                   "coverage": coverage, "area_coverage": area_coverage,
                   "lidar_share": lidar_share, "fetch_version": FETCH_VERSION,
                   "tiles": sources, "warnings": warnings}, fh, indent=1,
                  default=str)
    note(f"DEM: {sum(1 for s in sources if s.get('used'))} tile(s), "
         f"{lidar_frac:.0%} lidar, {grid.width:,} x {grid.height:,} at "
         f"{cell_m:g} m")
    return {"dem": mosaic_path, "tiles_dir": str(tile_dir),
            "sources": sources_path, "crs": pcrs.to_string(),
            "cell_size_m": cell_m, "z_unit": z_unit,
            "lidar_fraction": lidar_frac, "coverage": coverage,
            "area_coverage": area_coverage, "lidar_share": lidar_share,
            "fetch_version": FETCH_VERSION,
            "n_tiles": sum(1 for s in sources if s.get("used")),
            "seconds": time.monotonic() - t0, "warnings": warnings}
