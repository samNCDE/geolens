"""Split a landscape into watershed tiles, run each, merge the results.

**Why watersheds and not a grid.** Flow accumulation at a cell depends on every
cell upstream of it. Cut the DEM on an arbitrary rectangle and every flow path
crossing that edge is truncated: drainage areas come out too small near the
boundary, the stream network frays, and nothing warns you. A watershed divide is
the one boundary where a split is exactly correct, because by definition no flow
crosses it.

Two ways to get tiles:

* **Supply your own** — HUC12 polygons from the Watershed Boundary Dataset are
  the obvious choice and are easy to fetch. Preferred: they are consistent,
  named, and other people's data joins to them.
* **Derive them** — flow-route a *coarsened* copy of the DEM (cheap) and cut
  basins from that. Boundaries are approximate at the coarse cell size, which is
  why tiles are buffered before processing and clipped afterwards.

Each tile is processed independently, so peak memory is one tile's worth rather
than the whole landscape.

**The catch, stated plainly.** Splitting on a watershed divide is correct for
*routing direction* — no flow crosses a divide — but it does NOT preserve
contributing area for anything downstream of the cut. A mainstem entering a tile
from upstream arrives with zero accumulated area, so streams depending on that
area drop below the stream threshold and vanish. Measured on a test landscape:
12 subwatershed tiles recovered 6.4 km of a 187 km network.

So tile only when the DEM genuinely cannot be coarsened. For a landscape-scale
generalization, resampling 1 m lidar to 10 m cuts memory 100x, keeps the network
whole, and is the right answer almost every time.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from ..safety import Guard, GeolensError, note, progress
from ..raster import gtiff_profile


@dataclass
class TileSet:
    tiles: list[dict[str, Any]] = field(default_factory=list)
    source_dem: str = ""
    buffer_m: float = 0.0
    tile_dir: str = ""
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.tiles)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_dem": self.source_dem, "tile_count": len(self.tiles),
            "buffer_m": self.buffer_m, "tile_dir": self.tile_dir,
            "seconds": self.seconds, "warnings": self.warnings,
            "tiles": [
                {k: v for k, v in t.items() if k != "geometry"} for t in self.tiles
            ],
        }


# -- deriving basins ----------------------------------------------------------


def derive_basins(
    dem: str | Path,
    target_area_km2: float = 200.0,
    coarsen_to_m: float | None = None,
    guard: Guard | None = None,
    out_prefix: str = "basins",
) -> str:
    """Derive drainage basins by flow-routing a coarsened copy of the DEM.

    Routing the full-resolution grid to find basins would cost as much as the
    analysis itself, so the DEM is resampled down first. Basin boundaries are
    then approximate at that coarse cell size — acceptable, because tiles get
    buffered before processing and clipped after.

    ``target_area_km2`` sets roughly how big each basin should be; the stream
    threshold is derived from it.

    **These are approximate divides, not watershed boundaries.** They are drawn
    at the coarse cell size, so a basin's edge can be tens of cells off the
    real divide, and a clip to one is therefore not guaranteed to be free of
    inflow. For an exact unit use ``watershed()``, which delineates from the
    full-resolution flow graph and checks the result. Use this only when the
    whole extent genuinely has to be covered and approximate divides are
    acceptable — it is a last resort, not the first move.
    """
    import geopandas as gpd
    import rasterio
    from rasterio.enums import Resampling
    from rasterio import features as rfeat
    from scipy import ndimage
    from shapely.geometry import shape

    guard = guard or Guard.from_env()
    p = guard.read_path(dem)
    t0 = time.monotonic()

    with rasterio.open(str(p)) as src:
        cell = abs(src.res[0])
        crs = src.crs
        # Coarsen so the routing grid is a few million cells at most.
        target_cell = coarsen_to_m or max(cell, math.sqrt(
            (src.width * src.height * cell * cell) / 4_000_000.0
        ))
        factor = max(1, int(round(target_cell / cell)))
        w, h = max(1, src.width // factor), max(1, src.height // factor)
        note(f"coarsening {src.width}x{src.height} -> {w}x{h} "
             f"({cell:g} m -> {cell * factor:g} m) to derive basins")
        z = src.read(1, out_shape=(h, w), resampling=Resampling.average,
                     masked=True).astype("float32").filled(np.nan)
        transform = src.transform * src.transform.scale(
            src.width / w, src.height / h
        )

    coarse_cell = cell * factor
    tmp_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    coarse_path = tmp_dir / "coarse_dem.tif"
    prof = gtiff_profile(
        {"height": h, "width": w, "crs": crs, "transform": transform},
        dtype="float32", nodata=-9999.0,
    )
    with rasterio.open(str(coarse_path), "w", **prof) as dst:
        dst.write(np.nan_to_num(z, nan=-9999.0), 1)

    from .terrain import build_terrain

    thr = max(coarse_cell ** 2 / 1e6 * 4, target_area_km2 / 40.0)
    note(f"routing the coarse grid (stream threshold {thr:.3f} km²)")
    t = build_terrain(str(coarse_path), stream_threshold_km2=thr, guard=guard,
                      out_prefix=f"{out_prefix}_terrain")

    # Proper basins by pointer-jumping the D8 flow graph.
    #
    # An earlier version labelled connected components of "not a stream", which
    # is wrong: removing a stream tree from a rectangle leaves ONE connected
    # region (you can walk around the branch tips), so every landscape came back
    # as a single basin. Flow direction defines a forest — each cell points at
    # exactly one downstream neighbour — so following those pointers to a fixed
    # point gives the outlet each cell drains to, and cells sharing an outlet
    # are a basin by definition.
    with rasterio.open(t.flow_dir) as src:
        fdir = src.read(1)

    H, W = fdir.shape
    n = H * W
    # pysheds D8 encoding: N, NE, E, SE, S, SW, W, NW
    OFFSETS = {64: (-1, 0), 128: (-1, 1), 1: (0, 1), 2: (1, 1),
               4: (1, 0), 8: (1, -1), 16: (0, -1), 32: (-1, -1)}

    with rasterio.open(t.streams) as src:
        stream_mask = src.read(1).astype(bool)

    rows, cols = np.divmod(np.arange(n), W)
    nxt = np.arange(n)                      # default: drains to itself (outlet)
    for code, (dr, dc) in OFFSETS.items():
        m = (fdir.ravel() == code)
        if not m.any():
            continue
        r2, c2 = rows[m] + dr, cols[m] + dc
        inside = (r2 >= 0) & (r2 < H) & (c2 >= 0) & (c2 < W)
        idx = np.flatnonzero(m)
        nxt[idx[inside]] = r2[inside] * W + c2[inside]
        # Cells flowing off the edge stay self-pointing, i.e. they are outlets.

    # SUBWATERSHEDS, not terminal basins.
    #
    # Jumping all the way to the terminal outlet gives one basin per river
    # system — correct, but useless for tiling, since a single drainage comes
    # back as a single tile however large it is. Making every stream cell an
    # absorbing state instead means each hillslope cell resolves to the stream
    # cell it drains INTO, and grouping those by stream link gives proper
    # subwatersheds whose size is controlled by how finely the network is cut.
    stream_idx = np.flatnonzero(stream_mask.ravel())
    nxt[stream_idx] = stream_idx

    note("following flow pointers to the receiving stream cell")
    # Path doubling: log2(longest path) passes instead of walking step by step.
    for _ in range(int(np.ceil(np.log2(max(2, n)))) + 1):
        prev = nxt
        nxt = nxt[nxt]
        if np.array_equal(nxt, prev):
            break

    # Cut the stream network into links of the length that yields the wanted
    # basin area, then label each receiving stream cell by its link.
    cell_area_km2 = (coarse_cell ** 2) / 1e6
    want_cells = max(4, int(target_area_km2 / cell_area_km2))

    from skimage.morphology import skeletonize
    from .network import _trace_skeleton

    paths = _trace_skeleton(skeletonize(stream_mask))
    if not paths:
        raise GeolensError(
            "No stream network on the coarsened DEM — lower target_area_km2 or "
            "supply HUC polygons with split_by_basins(basins=...)."
        )
    # A link of length L drains roughly L * (basin width) — approximate the
    # width as sqrt(target area) and solve for L.
    link_cells = max(2, int(round(math.sqrt(target_area_km2) * 1000 / coarse_cell)))

    link_id = np.zeros(n, dtype="int32")
    lid = 0
    for path in paths:
        for i in range(0, len(path), link_cells):
            lid += 1
            for r, c in path[i:i + link_cells]:
                link_id[r * W + c] = lid
    note(f"{lid:,} stream links of ~{link_cells} cells each")

    # Stream cells the skeleton missed inherit their nearest labelled neighbour.
    lab_mask = (link_id > 0).reshape(H, W)
    if lab_mask.any() and not lab_mask.all():
        _, (jr, jc) = ndimage.distance_transform_edt(~lab_mask, return_indices=True)
        link_grid = link_id.reshape(H, W)[jr, jc]
    else:
        link_grid = link_id.reshape(H, W)

    filled = link_grid.ravel()[nxt].reshape(H, W).astype("int32")
    uniq = np.unique(filled)
    note(f"{uniq.size:,} subwatersheds before merging to ~{target_area_km2:g} km²")

    # Merge basins below target size into their nearest large neighbour, so tile
    # sizes come out roughly even instead of one huge basin and a thousand
    # headwater slivers.
    sizes = np.bincount(filled.ravel())
    big = np.flatnonzero(sizes >= want_cells)
    big = big[big > 0]
    if big.size == 0:
        # Every basin is small: keep the largest few rather than failing.
        order = np.argsort(-sizes)
        big = np.array([i for i in order if i > 0][:max(1, n // want_cells)])
    if big.size == 0:
        raise GeolensError(
            "Could not derive basins — try a larger target_area_km2, or supply "
            "HUC polygons with split_by_basins(basins=...)."
        )

    core = np.isin(filled, big)
    if not core.all():
        _, (ir, ic) = ndimage.distance_transform_edt(~core, return_indices=True)
        filled = filled[ir, ic]
    note(f"{big.size:,} basins after merging small ones")

    note("vectorizing basins")
    geoms = []
    for geom, val in rfeat.shapes(filled.astype("int32"), transform=transform):
        if val > 0:
            geoms.append((shape(geom), int(val)))

    gdf = gpd.GeoDataFrame(
        {"basin_id": [g[1] for g in geoms]},
        geometry=[g[0] for g in geoms], crs=crs,
    )
    gdf = gdf.dissolve(by="basin_id", as_index=False)
    gdf["area_km2"] = gdf.geometry.area / 1e6
    gdf = gdf[gdf["area_km2"] > target_area_km2 * 0.05].reset_index(drop=True)
    gdf["basin_id"] = range(len(gdf))

    out = tmp_dir / "basins.gpkg"
    gdf.to_file(str(out), driver="GPKG")
    note(f"{len(gdf)} basins, median {gdf['area_km2'].median():,.0f} km², "
         f"in {time.monotonic() - t0:.1f}s")
    return str(out)


# -- cutting tiles ------------------------------------------------------------


def split_by_basins(
    dem: str | Path,
    basins: str | Path | None = None,
    target_area_km2: float = 200.0,
    buffer_m: float | None = None,
    min_area_km2: float = 1.0,
    guard: Guard | None = None,
    out_prefix: str = "tiles",
) -> TileSet:
    """Cut a DEM into buffered watershed tiles.

    Parameters
    ----------
    basins
        Polygon layer to tile by — HUC12 from the Watershed Boundary Dataset is
        the natural choice. Omit it and basins are derived from a coarsened copy
        of the DEM instead.
    buffer_m
        Outward buffer on each tile before clipping the DEM. Flow routing needs
        no buffer inside a true watershed (nothing flows in), but the
        window-based steps — slope, TPI, slope standard deviation — read
        neighbours, so an unbuffered edge produces artefacts. Defaults to 30
        cells, which covers the largest default window.

    Tiles are written as separate GeoTIFFs. Nothing is held in memory across
    tiles, so peak usage is one tile.
    """
    import geopandas as gpd
    import rasterio
    from rasterio.mask import mask as rio_mask

    guard = guard or Guard.from_env()
    p = guard.read_path(dem)
    t0 = time.monotonic()
    warnings: list[str] = []

    with rasterio.open(str(p)) as src:
        cell = abs(src.res[0])
        crs = src.crs
    if buffer_m is None:
        buffer_m = cell * 30

    if basins is None:
        note("no basin layer supplied — deriving from a coarsened DEM")
        basins = derive_basins(p, target_area_km2=target_area_km2, guard=guard)
    else:
        basins = guard.read_path(basins)

    gdf = gpd.read_file(str(basins))
    if gdf.crs is not None and crs is not None and gdf.crs != crs:
        gdf = gdf.to_crs(crs)
        warnings.append("Basin layer was reprojected to the DEM CRS.")

    gdf = gdf[gdf.geometry.notna() & (gdf.geometry.area / 1e6 >= min_area_km2)]
    if gdf.empty:
        raise GeolensError(
            f"No basin polygons above {min_area_km2} km². Check the layer's CRS "
            "and that it overlaps the DEM."
        )

    tile_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    tile_dir.mkdir(parents=True, exist_ok=True)

    tiles: list[dict[str, Any]] = []
    skipped = 0
    for i, row in enumerate(progress(list(gdf.itertuples()), label="cutting tiles")):
        geom = row.geometry
        buffered = geom.buffer(buffer_m)
        try:
            with rasterio.open(str(p)) as src:
                arr, tr = rio_mask(src, [buffered], crop=True, filled=True,
                                   nodata=src.nodata if src.nodata is not None
                                   else -9999.0)
                prof = src.profile.copy()
        except ValueError:
            skipped += 1          # basin does not overlap the DEM
            continue

        if arr.size == 0 or not np.isfinite(arr).any():
            skipped += 1
            continue

        _nd = prof.get("nodata")
        prof = gtiff_profile(prof, dtype=prof.get("dtype", "float32"),
                             nodata=_nd if _nd is not None else -9999.0,
                             count=prof.get("count", 1),
                             height=arr.shape[1], width=arr.shape[2],
                             transform=tr)
        out_path = tile_dir / f"tile_{i:04d}.tif"
        with rasterio.open(str(out_path), "w", **prof) as dst:
            dst.write(arr[0], 1)

        tiles.append({
            "tile_id": i,
            "dem": str(out_path),
            "cells": int(arr.shape[1] * arr.shape[2]),
            "area_km2": float(geom.area / 1e6),
            "clip_geometry_wkt": geom.wkt,
        })

    if skipped:
        warnings.append(f"{skipped} basin(s) did not overlap the DEM and were skipped.")
    if not tiles:
        raise GeolensError("No tiles produced — basins may not overlap the DEM.")

    biggest = max(t["cells"] for t in tiles)
    note(f"{len(tiles)} tiles, largest {biggest:,} cells "
         f"({biggest / max(1, sum(t['cells'] for t in tiles)) * 100:.0f}% of total)")

    return TileSet(
        tiles=tiles, source_dem=str(p), buffer_m=float(buffer_m),
        tile_dir=str(tile_dir), seconds=time.monotonic() - t0, warnings=warnings,
    )


# -- running over tiles -------------------------------------------------------


def run_tiled(
    tileset: TileSet,
    fn: Callable[..., Any],
    clip_results: bool = True,
    skip_errors: bool = True,
    guard: Guard | None = None,
) -> dict[str, Any]:
    """Apply ``fn`` to every tile and collect the results.

    ``fn`` is called as ``fn(dem_path, guard)`` when it accepts two arguments,
    otherwise ``fn(dem_path)``.

    **Each tile gets its own output directory**, and taking the two-argument
    form is strongly preferred. Every analysis writes to fixed filenames under
    the guard's output directory, so tiles sharing one guard silently overwrite
    each other: every returned path then points at the last tile's files and the
    merge produces one tile's worth of features repeated N times. That failure
    is invisible — the paths differ per tile only if the guard does.

    With ``skip_errors`` a failing tile is recorded and the run continues — on a
    real landscape one tile in forty has no channel or sits entirely on nodata,
    and losing the other thirty-nine to it is not acceptable.
    """
    import inspect

    guard = guard or Guard.from_env()
    base_out = Path(guard.out_dir)
    try:
        takes_guard = len(inspect.signature(fn).parameters) >= 2
    except (TypeError, ValueError):
        takes_guard = False
    if not takes_guard:
        note("WARNING: fn takes one argument, so all tiles share one output "
             "directory and will overwrite each other. Accept (dem, guard).")

    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    t0 = time.monotonic()

    for tile in progress(tileset.tiles, label="processing tiles"):
        try:
            tile_guard = Guard(
                roots=list(guard.roots),
                out_dir=base_out / f"tile_{tile['tile_id']:04d}",
            )
            out = fn(tile["dem"], tile_guard) if takes_guard else fn(tile["dem"])
            results.append({"tile_id": tile["tile_id"], "result": out,
                            "area_km2": tile["area_km2"]})
        except Exception as exc:
            failures.append({"tile_id": tile["tile_id"],
                             "error": f"{type(exc).__name__}: {exc}"})
            if not skip_errors:
                raise

    elapsed = time.monotonic() - t0
    note(f"{len(results)}/{len(tileset.tiles)} tiles succeeded in {elapsed:.1f}s")
    return {
        "results": results,
        "failures": failures,
        "tile_count": len(tileset.tiles),
        "succeeded": len(results),
        "seconds": elapsed,
    }


def merge_vector_outputs(
    paths: Sequence[str],
    name: str = "merged.gpkg",
    clip_wkts: Sequence[str] | None = None,
    guard: Guard | None = None,
) -> dict[str, Any]:
    """Concatenate per-tile vector outputs into one layer.

    ``clip_wkts`` clips each tile's features back to its unbuffered basin, so
    the buffer used to avoid edge artefacts does not leave duplicate features in
    the overlap.
    """
    import geopandas as gpd
    import pandas as pd
    from shapely import wkt as shapely_wkt

    guard = guard or Guard.from_env()
    frames = []
    for i, pth in enumerate(progress(list(paths), label="merging tiles")):
        try:
            g = gpd.read_file(str(pth))
        except Exception:
            continue
        if g.empty:
            continue
        if clip_wkts is not None and i < len(clip_wkts) and clip_wkts[i]:
            try:
                g = gpd.clip(g, shapely_wkt.loads(clip_wkts[i]))
            except Exception:
                pass
        if g.empty:
            continue

        # Clipping shortens geometry but leaves stored attributes untouched, so
        # length_m still reports the pre-clip length and area-derived fields
        # still describe the pre-clip feature. Summing them then overstates the
        # total — measured at 117% of an untiled run before this was fixed.
        # Recompute what can be recomputed, and mark the rest.
        if g.geom_type.isin(["LineString", "MultiLineString"]).any():
            if "length_m" in g.columns:
                g["length_m_preclip"] = g["length_m"]
                g["length_m"] = g.geometry.length
                g["clipped"] = (
                    g["length_m"] < g["length_m_preclip"] - 1e-6
                )
                # Per-length metrics derived from an area/length ratio are no
                # longer self-consistent on a clipped feature.
                for col in ("valley_width_m", "core_width_m"):
                    if col in g.columns and "valley_area_m2" in g.columns:
                        g.loc[g["clipped"], col] = float("nan")
        elif g.geom_type.isin(["Polygon", "MultiPolygon"]).any():
            if "area_km2" in g.columns:
                g["area_km2_preclip"] = g["area_km2"]
                g["area_km2"] = g.geometry.area / 1e6
        g["tile_id"] = i
        frames.append(g)

    if not frames:
        raise GeolensError("Nothing to merge — every tile output was empty.")

    merged = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True),
                              crs=frames[0].crs)
    out = Path(guard.write_path(name))
    merged.to_file(str(out), driver="GPKG")
    info: dict[str, Any] = {
        "path": str(out), "features": len(merged), "tiles_merged": len(frames),
    }
    if "clipped" in merged.columns:
        n_clip = int(merged["clipped"].sum())
        info["clipped_features"] = n_clip
        info["note"] = (
            f"{n_clip} feature(s) were cut by a tile boundary. Their length_m "
            "was recomputed from the clipped geometry; width fields derived "
            "from an area/length ratio are set to NaN because they no longer "
            "describe the feature that remains. length_m_preclip keeps the "
            "original."
        )
    if "length_m" in merged.columns:
        info["total_length_km"] = round(float(merged["length_m"].sum()) / 1000, 3)
    return info


# -- grid tiling against precomputed flow -------------------------------------


# NOT for anything that depends on contributing area. Slope, TPI, roughness and
# NDVI are local operations and tile fine; flow accumulation, the stream
# network, HAND, confinement and valley bottoms do not, because a rectangular
# boundary truncates flow paths. Measured on the landscape fixture: a rectangle
# over the middle third is missing 3.65x its own area in upstream contribution.
# For those, use watershed().
def grid_tiles(
    dem: str | Path,
    tile_cells: int = 4000,
    buffer_cells: int = 128,
    guard: Guard | None = None,
    out_prefix: str = "gridtiles",
) -> TileSet:
    """Cut a DEM into overlapping square tiles.

    A plain grid is normally the *wrong* way to tile terrain analysis, because
    flow accumulation depends on everything upstream and a rectangle cuts flow
    paths. It becomes the *right* way once accumulation has already been
    computed over the full grid and is passed in
    (``build_terrain(..., flow=...)``): with the only global quantity solved,
    everything that remains — slope, TPI, distance transforms, region grows,
    cross-sections — is local, and a buffered square tile is exact.

    ``buffer_cells`` must exceed the largest analysis window used downstream
    (TPI defaults to 3x the roughness window), or the buffer will not cover the
    edge effects it exists to absorb.
    """
    import rasterio
    from rasterio.windows import Window

    guard = guard or Guard.from_env()
    p = guard.read_path(dem)
    t0 = time.monotonic()
    warnings: list[str] = []

    with rasterio.open(str(p)) as src:
        H, W = src.height, src.width
        profile = src.profile.copy()
        transform = src.transform
        nodata = src.nodata if src.nodata is not None else -9999.0

        tile_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
        tile_dir.mkdir(parents=True, exist_ok=True)

        nr = max(1, math.ceil(H / tile_cells))
        nc = max(1, math.ceil(W / tile_cells))
        note(f"{nr}x{nc} = {nr * nc} tiles of {tile_cells} cells "
             f"(+{buffer_cells} buffer)")

        tiles: list[dict[str, Any]] = []
        idx = 0
        for i in range(nr):
            for j in range(nc):
                r0, c0 = i * tile_cells, j * tile_cells
                r1, c1 = min(H, r0 + tile_cells), min(W, c0 + tile_cells)
                br0, bc0 = max(0, r0 - buffer_cells), max(0, c0 - buffer_cells)
                br1, bc1 = min(H, r1 + buffer_cells), min(W, c1 + buffer_cells)
                win = Window(bc0, br0, bc1 - bc0, br1 - br0)
                arr = src.read(1, window=win)
                if not np.isfinite(arr).any() or (arr == nodata).all():
                    continue

                pr = gtiff_profile(profile, dtype=profile.get("dtype", "float32"),
                                   nodata=nodata,
                                   height=arr.shape[0], width=arr.shape[1],
                                   transform=src.window_transform(win))
                out_path = tile_dir / f"tile_{idx:04d}.tif"
                with rasterio.open(str(out_path), "w", **pr) as dst:
                    dst.write(arr, 1)

                # The unbuffered core, in world coordinates, for clipping later.
                x0, y0 = rasterio.transform.xy(transform, r0, c0, offset="ul")
                x1, y1 = rasterio.transform.xy(transform, r1 - 1, c1 - 1,
                                               offset="lr")
                from shapely.geometry import box

                core = box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                tiles.append({
                    "tile_id": idx, "dem": str(out_path),
                    "cells": int(arr.size),
                    "area_km2": float(core.area / 1e6),
                    "clip_geometry_wkt": core.wkt,
                })
                idx += 1

    if not tiles:
        raise GeolensError("No tiles produced — the DEM may be entirely nodata.")
    note(f"{len(tiles)} tiles, largest {max(t['cells'] for t in tiles):,} cells")
    return TileSet(tiles=tiles, source_dem=str(p),
                   buffer_m=float(buffer_cells * abs(transform.a)),
                   tile_dir=str(tile_dir), seconds=time.monotonic() - t0,
                   warnings=warnings)
