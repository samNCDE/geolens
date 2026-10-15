"""Riparian vegetation as a floodplain proxy, for arid and intermittent systems.

WHY THIS EXISTS
---------------
:mod:`geolens.inundation` validates a floodplain against observed flood extent.
In the arid Southwest that fails twice over: a monsoon flood in an arroyo lasts
hours against a 6-12 day satellite revisit, and a dry sand bed is smooth at
C-band so it reads dark exactly like open water. Either the wash gets excluded
as a "water look-alike" or a bone-dry channel reads as flooded.

Where water is the limiting factor for plant growth, the vegetation is a far
better signal than the water. Phreatophytes -- mesquite, cottonwood, willow,
tamarisk -- root into the alluvial aquifer, so the green band marks the active
alluvial valley. It is there every growing season, not for six hours after a
storm, and it is visible to any optical sensor.

THE MEASUREMENT IS RELATIVE, NOT ABSOLUTE
-----------------------------------------
This does not threshold NDVI. An absolute threshold is meaningless across
regions, years and sensors: 0.35 is lush in the Mojave and bare ground in the
Willamette, and a drought year shifts everything down at once.

What it measures is **NDVI in excess of the local upland background** -- how much
greener this pixel is than the country around it. That is the same move as
``excess_drop_m`` in :func:`detect_headcuts` (drop minus the background
gradient) and ``min_above_base_m`` in the fan detector (height above local base
level). Subtracting the local background is what makes a threshold portable.

WHAT THIS IS NOT
----------------
**It is not the floodplain, and it is not flood extent.** Phreatophytes track
the water table, so the band maps where groundwater is shallow. That can be:

* **narrower** than the geomorphic floodplain -- an abandoned terrace floods in
  a large event but sits above the aquifer and stays bare;
* **wider** -- springs, seeps and irrigation return flows support vegetation
  outside anything the channel inundates;
* **displaced** -- on an incised, entrenched channel the water table has dropped
  and the riparian band dies back while the floodplain remains.

That last one is not a failure mode, it is a finding: a floodplain with no
riparian band on an incised reach is a real signal about the system.

THE FALSE POSITIVE THAT MATTERS: IRRIGATED AGRICULTURE
-------------------------------------------------------
In the arid West, irrigated fields are (a) intensely green, (b) sited on the
floodplain precisely because that is the flat ground with water rights, and
(c) therefore adjacent to and often contiguous with the real riparian band.
Nothing about NDVI distinguishes alfalfa from cottonwood.

This module does not pretend to solve that. It reports two things that help:
``patch_rectangularity`` -- fields are geometrically regular in a way riparian
corridors are not -- and ``connected_to_network``, since a field is frequently
green but hydrologically separate.

**Both are prompts to look, never filters.** A detached field is caught
reliably. A field CONTIGUOUS with the riparian band merges into one patch, and
whether either flag then fires depends on whether the field happens to dominate
that patch's shape -- sometimes it does, which is luck, not detection. The area
is inflated either way. Separating contiguous agriculture needs a land-cover
layer (NLCD cultivated crops, or LANDFIRE), not geometry. Until that is wired
in, treat the total as an upper bound in farmed valleys.

GETTING THE NDVI
----------------
Not implemented here yet; ``NDVI_SOURCES`` documents the intended route. A
growing-season median composite is what this expects -- a single date catches
one cloud, one senescence stage, one irrigation cycle.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .safety import Guard, GeolensError, note

NDVI_SOURCES = {
    "sentinel-2": {
        "stac": "https://planetarycomputer.microsoft.com/api/stac/v1",
        "collection": "sentinel-2-l2a",
        "bands": {"red": "B04", "nir": "B08"},
        "resolution_m": 10,
        "note": ("10 m, 5-day revisit, 2015 to now. Anonymous SAS tokens work. "
                 "Build a growing-season MEDIAN composite, not one date."),
    },
    "landsat": {
        "stac": "https://planetarycomputer.microsoft.com/api/stac/v1",
        "collection": "landsat-c2-l2",
        "bands": {"red": "red", "nir": "nir08"},
        "resolution_m": 30,
        "note": "30 m but back to 1984 — the only way to see riparian change.",
    },
}


@dataclass
class RiparianResult:
    band_raster: str
    band_polygons: str
    excess_raster: str
    n_patches: int
    riparian_km2: float
    connected_km2: float           # touching the stream network
    unconnected_km2: float         # green but hydrologically separate
    suspect_patches: list[dict[str, Any]]
    background_ndvi: float
    min_excess: float
    seconds: float
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _coarse_percentile(arr: np.ndarray, window_cells: int,
                       percentile: float) -> np.ndarray:
    """Local percentile, computed on a coarse grid and resampled back up.

    A moving percentile filter is a RANK filter: it sorts every cell in the
    window for every output pixel. At a 2 km window on 10 m cells that is a
    201x201 neighbourhood, 40,401 values sorted per pixel, and it raises
    MemoryError on a 400x300 test raster. On 1 m data the same window is
    2001x2001 — four million values per pixel. It is not slow, it is impossible.

    The background does not need that resolution. It is a smooth surface
    describing "how green is this part of the country", so it is computed by
    block-reducing to a coarse grid, taking a small percentile filter there, and
    zooming back. Cost drops from O(N * w^2) to roughly O(N), and the result is
    smoother than the moving filter was.
    """
    from scipy import ndimage

    h, w = arr.shape
    # Aim for a coarse grid on which the window spans about 8 cells.
    f = max(1, int(window_cells // 8))
    if f == 1:
        return ndimage.percentile_filter(arr, percentile=percentile,
                                         size=max(3, window_cells),
                                         mode="nearest")

    ph, pw = (-h) % f, (-w) % f
    padded = np.pad(arr, ((0, ph), (0, pw)), mode="edge")
    ch, cw = padded.shape[0] // f, padded.shape[1] // f
    blocks = padded.reshape(ch, f, cw, f)
    coarse = np.percentile(blocks, percentile, axis=(1, 3))

    coarse = ndimage.percentile_filter(coarse, percentile=percentile,
                                       size=max(3, int(window_cells // f) | 1),
                                       mode="nearest")
    back = ndimage.zoom(coarse, (padded.shape[0] / ch, padded.shape[1] / cw),
                        order=1, mode="nearest")
    return back[:h, :w]


def _rectangularity(geom) -> float:
    """Patch area divided by its minimum rotated bounding rectangle's area.

    A cultivated field approaches 1.0 — it *is* a rectangle. A riparian corridor
    is sinuous and elongated, so its bounding rectangle is mostly empty and the
    ratio falls well below. Not decisive on its own; a flag, not a filter.
    """
    try:
        mrr = geom.minimum_rotated_rectangle
        return float(geom.area / mrr.area) if mrr.area > 0 else 0.0
    except Exception:
        return 0.0


def _align(path: str | Path, shape: tuple[int, int], transform, crs,
           resampling: str, fill: float) -> tuple[np.ndarray, str | None]:
    """Read a raster onto another raster's grid.

    HAND and the stream network come off the DEM; NDVI normally comes off NAIP
    at 1 m. Requiring identical grids would mean either throwing away the NAIP
    resolution or silently dropping the HAND restriction -- and dropping it is
    the bad one, because without HAND every green mesa top is riparian and the
    result still looks like an answer.

    Returns the array plus a warning string, or None when nothing is worth
    saying. Resampling a coarse HAND up is honest: it is a smooth surface and
    bilinear interpolation of it is a better estimate of the height at a 1 m
    cell than refusing to look. A stream mask is categorical and goes nearest.
    """
    import rasterio
    from rasterio.warp import Resampling, reproject

    with rasterio.open(str(path)) as src:
        src_cell = abs(src.res[0])
        # Shape and CRS are NOT enough to call two rasters the same grid. Two
        # 40x40 EPSG:32612 rasters 100 km apart match on both and cover
        # completely different ground; taking the fast path there masks the
        # NDVI by heights sampled somewhere else entirely, with no warning.
        same_grid = (
            (src.height, src.width) == tuple(shape)
            and src.crs == crs
            and src.transform.almost_equals(transform, precision=1e-6)
        )
        if same_grid:
            return src.read(1, masked=True).astype("float32").filled(fill), None
        if src.crs is None:
            # Returning `fill` here is NOT "ignoring" the layer: the caller
            # passes fill=inf for HAND, and `band &= inf <= max_hand_m` is
            # False everywhere, so an un-georeferenced HAND silently deleted
            # the entire result and then blamed min_excess for it.
            neutral = (-np.inf if fill == np.inf else
                       (np.inf if fill == -np.inf else fill))
            return np.full(shape, neutral, "float32"), (
                f"{Path(path).name} has no CRS, so it cannot be aligned to the "
                f"NDVI grid. It was skipped, and the restriction it would have "
                f"applied is NOT in effect — give it a CRS, or the result is "
                f"unfiltered by it."
            )
        out = np.full(shape, fill, dtype="float32")
        reproject(
            source=rasterio.band(src, 1), destination=out,
            src_transform=src.transform, src_crs=src.crs,
            dst_transform=transform, dst_crs=crs,
            resampling=getattr(Resampling, resampling),
            src_nodata=src.nodata, dst_nodata=fill,
        )
    dst_cell = abs(transform.a)
    msg = None
    if src_cell > dst_cell * 1.5:
        msg = (f"{Path(path).name} is {src_cell:g} m against a {dst_cell:g} m "
               f"NDVI grid; it was resampled up. The cutoff is no sharper than "
               f"its native resolution -- the edge of the band is {src_cell:g} m "
               f"fuzzy however fine the NDVI is.")
    return out, msg


def riparian_band(
    ndvi: str | Path,
    dem: str | Path | None = None,
    hand: str | Path | None = None,
    streams: str | Path | None = None,
    background_window_m: float = 2000.0,
    background_percentile: float = 40.0,
    min_excess: float = 0.12,
    max_hand_m: float | None = 15.0,
    min_patch_km2: float = 0.001,
    rectangularity_flag: float = 0.75,
    out_prefix: str = "riparian",
    guard: Guard | None = None,
) -> RiparianResult:
    """Find the riparian band as NDVI in excess of the local upland background.

    Parameters
    ----------
    ndvi
        A growing-season NDVI composite. Values in [-1, 1]; scaled integers are
        detected and rescaled.
    hand
        Height above nearest drainage, from :func:`build_terrain`. Supplying it
        is what keeps a green hilltop — an irrigated mesa, a north-facing slope
        holding moisture — out of the result. Without it the band is whatever is
        green, wherever it is.
    background_window_m
        The neighbourhood that defines "local upland". Should be several times
        the valley width: too small and the riparian band raises its own
        background until it stops looking anomalous, which is the main way to
        get a mysteriously empty result.
    background_percentile
        Which percentile of that neighbourhood counts as background. The 40th,
        not the median, because in a narrow valley a meaningful share of the
        window is the green band itself.
    min_excess
        How far above background a pixel must sit. This is the sensitivity knob.
    max_hand_m
        Exclude ground more than this far above the drainage. ``None`` disables
        it, which is only sensible on terrain with no relief.
    """
    import geopandas as gpd
    import rasterio
    from rasterio import features as rfeat
    from scipy import ndimage
    from shapely.geometry import shape as shp_shape

    from .raster import gtiff_profile

    t0 = time.monotonic()
    guard = guard or Guard.from_env()
    warnings: list[str] = []

    with rasterio.open(str(guard.read_path(ndvi))) as src:
        arr = src.read(1, masked=True).astype("float32")
        profile = src.profile.copy()
        transform = src.transform
        crs = src.crs
        # TRUE metres. The NDVI grid is in the DEM's CRS -- US survey feet on
        # EPSG:6529 -- and every parameter here is metric: the background
        # window, the patch size, the HAND ceiling, and the areas reported.
        # All four were mixing units, so on a foot grid the background window
        # was a third of its size, min_patch_km2 admitted patches a tenth the
        # size asked for, and riparian_km2 was square feet over 1e6.
        try:
            mpu = float(crs.linear_units_factor[1]) if crs else 1.0
        except Exception:                                    # noqa: BLE001
            mpu = 1.0
        cell = abs(src.res[0]) * mpu
        cell_area_km2 = abs(src.res[0] * src.res[1]) * mpu * mpu / 1e6

    finite = arr.compressed()
    if finite.size == 0:
        raise GeolensError(f"{ndvi} has no valid data.")
    # Scaled integer NDVI is common (10000x in Sentinel-2 products).
    if np.nanmax(np.abs(finite)) > 1.5:
        scale = 10000.0 if np.nanmax(np.abs(finite)) > 100 else 100.0
        arr = arr / scale
        note(f"NDVI looked scaled; divided by {scale:g}")

    filled = np.asarray(arr.filled(np.nan), dtype="float32")
    valid = np.isfinite(filled)

    # --- local background ---------------------------------------------------
    win = max(3, int(round(background_window_m / cell)) | 1)
    if win >= min(filled.shape):
        win = max(3, (min(filled.shape) // 2) | 1)
        warnings.append(
            f"background_window_m exceeded the raster; reduced to {win * cell:.0f} m. "
            f"On a small extent the background is barely 'local' and the excess "
            f"is closer to an absolute threshold."
        )
    work = np.where(valid, filled, np.nanmedian(filled[valid]))
    background = _coarse_percentile(work, win, background_percentile)
    excess = np.where(valid, filled - background, np.nan)

    band = np.isfinite(excess) & (excess >= min_excess)

    # --- restrict to low ground --------------------------------------------
    if hand is not None and max_hand_m is not None:
        h, msg = _align(guard.read_path(hand), band.shape, transform, crs,
                        "bilinear", np.inf)
        if msg:
            warnings.append(msg)
        # HAND is in the DEM's vertical unit, which geolens takes to be the
        # horizontal one unless the CRS declares otherwise -- the same
        # assumption build_terrain states in its warnings. A 15 m ceiling was
        # applied as 15 ft.
        band &= h <= (max_hand_m / mpu)

    # --- clean and measure --------------------------------------------------
    min_cells = max(1, int(round(min_patch_km2 * 1e6 / (cell * cell))))
    lab, n = ndimage.label(band, structure=np.ones((3, 3)))
    if n:
        sizes = np.bincount(lab.ravel())
        too_small = np.flatnonzero(sizes < min_cells)
        band &= ~np.isin(lab, too_small)
        lab, n = ndimage.label(band, structure=np.ones((3, 3)))

    stream_mask = None
    if streams is not None:
        sm, msg = _align(guard.read_path(streams), band.shape, transform, crs,
                         "nearest", 0.0)
        if msg:
            warnings.append(msg)
        stream_mask = sm > 0
        if not stream_mask.any():
            warnings.append(
                "The stream raster has no cells on the NDVI extent, so every "
                "patch is reported as unconnected. Check they cover the same "
                "ground."
            )

    # --- which labels touch the stream network ------------------------------
    #
    # One pass over the label array, not one rasterisation per patch. The
    # per-patch version allocated a FULL-SIZE uint8 array for every polygon
    # just to ask whether it touched a stream, then two more full-size arrays
    # for the AND -- O(patches x H x W). At the module's own recommended input
    # (1 m NAIP NDVI) a 20 km valley is 400 MB per patch and thousands of
    # patches are routine, which is minutes of pure allocation. `lab` already
    # says which patch every cell belongs to.
    connected_labels: set[int] = set()
    if stream_mask is not None:
        touched = lab[stream_mask & (lab > 0)]
        connected_labels = set(np.unique(touched).tolist())

    # --- vectorise ----------------------------------------------------------
    #
    # One polygon per LABEL. Patches are 8-connected (ndimage.label above),
    # but rasterio's shapes() traces 4-connected regions, so a patch joined
    # through a diagonal came out as several polygons -- on Cherokee Bill
    # (v0.25) 1,523 of 1,838 "patches" were such fragments, median 11 m2,
    # every one touching another patch, and a one-cell fragment is a perfect
    # square: 939 of the 1,027 agriculture flags were fragments. The
    # min_patch_km2 filter never saw them, because it runs on labels.
    from shapely.ops import unary_union

    pieces: dict[int, list] = {}
    for gj, val in rfeat.shapes(lab.astype("int32"), mask=band,
                                transform=transform):
        if val:
            pieces.setdefault(int(val), []).append(shp_shape(gj))
    geoms, records = [], []
    connected_km2 = unconnected_km2 = 0.0
    suspect: list[dict[str, Any]] = []
    for val in sorted(pieces):
        parts = pieces[val]
        g = parts[0] if len(parts) == 1 else unary_union(parts)
        area_km2 = g.area * mpu * mpu / 1e6        # CRS units^2 -> km2
        rect = _rectangularity(g)
        idx = len(geoms)
        conn = True
        if stream_mask is not None:
            conn = int(val) in connected_labels
        connected_km2 += area_km2 if conn else 0.0
        unconnected_km2 += 0.0 if conn else area_km2
        rec = {
            "patch_id": idx,
            "area_km2": area_km2,
            "patch_rectangularity": rect,
            "connected_to_network": conn,
            "suspect_agriculture": bool(rect >= rectangularity_flag or not conn),
        }
        records.append(rec)
        geoms.append(g)
        if rec["suspect_agriculture"]:
            suspect.append(rec)

    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)

    band_path = out_dir / "riparian.tif"
    excess_path = out_dir / "ndvi_excess.tif"
    with rasterio.open(str(band_path), "w",
                       **gtiff_profile(profile, dtype="uint8", nodata=0)) as dst:
        dst.write(band.astype("uint8"), 1)
    with rasterio.open(str(excess_path), "w",
                       **gtiff_profile(profile, dtype="float32",
                                       nodata=-9999.0)) as dst:
        dst.write(np.nan_to_num(excess, nan=-9999.0).astype("float32"), 1)

    poly_path = out_dir / "riparian.gpkg"
    if geoms:
        gpd.GeoDataFrame(records, geometry=geoms, crs=crs).to_file(
            str(poly_path), driver="GPKG")

    total = float(band.sum()) * cell_area_km2
    if suspect:
        warnings.append(
            f"{len(suspect)} of {len(records)} patches are flagged as possible "
            f"irrigated agriculture — geometrically rectangular, or not touching "
            f"the stream network. In the arid West fields sit on the floodplain "
            f"by definition and NDVI cannot tell alfalfa from cottonwood. Look "
            f"at these before using the total."
        )
    if total <= 0:
        warnings.append(
            "No riparian band found. Before lowering min_excess, check "
            "background_window_m: if the window is not several times the valley "
            "width, the band raises its own background and stops looking "
            "anomalous."
        )

    return RiparianResult(
        band_raster=str(band_path),
        band_polygons=str(poly_path),
        excess_raster=str(excess_path),
        n_patches=len(records),
        riparian_km2=total,
        connected_km2=connected_km2,
        unconnected_km2=unconnected_km2,
        suspect_patches=suspect[:25],
        background_ndvi=float(np.nanmedian(background[valid])),
        min_excess=min_excess,
        seconds=time.monotonic() - t0,
        warnings=warnings,
    )
