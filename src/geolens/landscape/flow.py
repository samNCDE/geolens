"""D8 flow direction, accumulation and true watersheds — memory-efficient.

Written because the general-purpose libraries hold far more memory than the
algorithms need, and at 1-2 m over a large area that is the difference between
running and being killed. Every array here is the smallest dtype that carries
the information:

===================  ========  =================================================
array                dtype     bytes/cell
===================  ========  =================================================
flow direction       uint8     1     (8 directions + 0 for nodata/outlet)
in-degree            uint8     1     (at most 8 upstream neighbours)
accumulation         uint32    4     (cell counts -- EXACT; see _accumulate)
filled elevation     float32   4     (reused in place where possible)
===================  ========  =================================================

At 2 m over 3 million acres — 3.0 billion cells — that is about 30 GB for the
whole routing step, which fits in 128 GB with room to work. The same job in
float64 throughout needs over 100 GB and dies.

**Why this module exists at all: only accumulation has a global dependency.**
A cell's contributing area depends on every cell upstream of it, so accumulation
cannot be tiled without either including the whole upstream area or injecting
what flows in. Everything else in the package — slope, TPI, distance
transforms, region grows, cross-sections — is *local* and tiles freely. So the
right architecture is: route once over the full grid here, then tile the local
work against these outputs.

The three algorithms are all standard and all O(n) or O(n log n):

* **Priority-flood** depression filling (Barnes et al. 2014) — one heap pass
  from the edges inward, no iteration to convergence.
* **Topological accumulation** (Kahn's algorithm) — process cells only once
  every upstream neighbour has contributed. No recursion, so no stack limit and
  no repeated traversal.
* **Reverse traversal** for catchments — walk upstream from a pour point.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..safety import Guard, GeolensError, note
from ..raster import gtiff_profile

# D8 encoding matches pysheds/ESRI so outputs interoperate:
#   32  64  128
#   16   0    1
#    8   4    2
_CODES = np.array([1, 2, 4, 8, 16, 32, 64, 128], dtype=np.uint8)
_DR = np.array([0, 1, 1, 1, 0, -1, -1, -1], dtype=np.int64)
_DC = np.array([1, 1, 0, -1, -1, -1, 0, 1], dtype=np.int64)


def _numba():
    try:
        import numba

        return numba
    except ImportError:
        return None


@dataclass
class FlowResult:
    filled: str
    flow_dir: str
    accumulation: str
    cells: int
    cell_size: float
    crs: str | None
    depressions_filled: int
    max_accumulation_km2: float
    seconds: float
    peak_arrays_gb: float
    warnings: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


# -- priority-flood depression filling ----------------------------------------


def _priority_flood(z: np.ndarray, nodata_mask: np.ndarray) -> tuple[np.ndarray, int]:
    """Fill depressions by flooding inward from the edges (Barnes et al. 2014).

    Pops the lowest cell on the boundary of the processed region, raises each
    unprocessed neighbour to at least that elevation, and pushes it. Every cell
    is touched once, so this is O(n log n) with a heap and needs no
    convergence loop — unlike iterative filling, which can take many passes over
    the whole grid.
    """
    import heapq

    nb = _numba()
    H, W = z.shape
    out = z.copy()
    closed = nodata_mask.copy()

    heap: list[tuple[float, int, int]] = []
    # Seed with the grid edge and the border of any nodata region: these are the
    # places water can leave from.
    for r in range(H):
        for c in (0, W - 1):
            if not closed[r, c]:
                heapq.heappush(heap, (float(out[r, c]), r, c))
                closed[r, c] = True
    for c in range(W):
        for r in (0, H - 1):
            if not closed[r, c]:
                heapq.heappush(heap, (float(out[r, c]), r, c))
                closed[r, c] = True
    if nodata_mask.any():
        from scipy import ndimage

        edge = ndimage.binary_dilation(nodata_mask) & ~nodata_mask
        for r, c in np.argwhere(edge):
            if not closed[r, c]:
                heapq.heappush(heap, (float(out[r, c]), int(r), int(c)))
                closed[r, c] = True

    filled = 0
    while heap:
        zc, r, c = heapq.heappop(heap)
        for k in range(8):
            rr, cc = r + int(_DR[k]), c + int(_DC[k])
            if not (0 <= rr < H and 0 <= cc < W) or closed[rr, cc]:
                continue
            closed[rr, cc] = True
            if out[rr, cc] <= zc:
                # See the note in the numba path: the increment must clear
                # float32 spacing at this elevation. nextafter guarantees it.
                step = np.nextafter(np.float32(zc), np.float32(np.inf))
                out[rr, cc] = max(step, np.float32(zc + abs(zc) * 9.5e-7))
                filled += 1
            heapq.heappush(heap, (float(out[rr, cc]), rr, cc))
    return out, filled


def _priority_flood_fast(z: np.ndarray, nodata_mask: np.ndarray):
    """numba-compiled priority flood, falling back to the pure-Python version."""
    nb = _numba()
    if nb is None:
        return _priority_flood(z, nodata_mask)

    from numba import njit

    @njit(cache=True)
    def _pf(z, closed, seed, dr, dc):
        H, W = z.shape
        n = H * W
        # Binary heap over (elevation, linear index), as parallel arrays — a
        # list of tuples would allocate per push and dominate the runtime.
        hz = np.empty(n, dtype=np.float32)
        hi = np.empty(n, dtype=np.int64)
        size = 0

        def push(size, val, idx):
            hz[size] = val
            hi[size] = idx
            i = size
            while i > 0:
                p = (i - 1) // 2
                if hz[p] <= hz[i]:
                    break
                hz[p], hz[i] = hz[i], hz[p]
                hi[p], hi[i] = hi[i], hi[p]
                i = p
            return size + 1

        for r in range(H):
            for c in range(W):
                if seed[r, c] and not closed[r, c]:
                    closed[r, c] = True
                    size = push(size, z[r, c], r * W + c)

        filled = 0
        while size > 0:
            zc = hz[0]
            idx = hi[0]
            size -= 1
            hz[0] = hz[size]
            hi[0] = hi[size]
            i = 0
            while True:
                l, rgt, sm = 2 * i + 1, 2 * i + 2, i
                if l < size and hz[l] < hz[sm]:
                    sm = l
                if rgt < size and hz[rgt] < hz[sm]:
                    sm = rgt
                if sm == i:
                    break
                hz[sm], hz[i] = hz[i], hz[sm]
                hi[sm], hi[i] = hi[i], hi[sm]
                i = sm

            r, c = idx // W, idx % W
            for k in range(8):
                rr = r + dr[k]
                cc = c + dc[k]
                if rr < 0 or rr >= H or cc < 0 or cc >= W:
                    continue
                if closed[rr, cc]:
                    continue
                closed[rr, cc] = True
                if z[rr, cc] <= zc:
                    # The increment MUST exceed float32 spacing at this
                    # elevation or the addition is a no-op and the "filled"
                    # cell stays exactly level with its neighbour. float32
                    # spacing near 1900 m is 1.2e-4, so a fixed 1e-5 silently
                    # does nothing: the flat never gains a gradient, every cell
                    # in it ends up with no downslope neighbour, and each
                    # becomes a spurious outlet that severs the network.
                    # Scale with magnitude: 2**-23 is float32's relative
                    # precision, and 8x that is a comfortable margin.
                    eps = np.float32(abs(zc) * 9.5e-7 + 1e-6)
                    z[rr, cc] = np.float32(zc) + eps
                    filled += 1
                size = push(size, z[rr, cc], rr * W + cc)
        return filled

    # np.ascontiguousarray returns the SAME array when the input is already
    # float32 and contiguous, so without the copy this mutates the caller's DEM
    # in place -- while the pure-Python path copies. Two implementations of one
    # function must not differ on whether they destroy their input.
    out = np.ascontiguousarray(z, dtype=np.float32).copy()
    closed = np.ascontiguousarray(nodata_mask.copy())

    # Seeds: every place water can LEAVE the grid. That is the outer ring, and
    # also the valid cells bordering any nodata region.
    #
    # The nodata border is not a refinement, it is the whole thing on real data.
    # A DEM clipped to a watershed or an AOI has nodata all the way around, so
    # every outer-ring cell is closed before the loop starts. Seeding the ring
    # alone then seeds NOTHING: the heap is empty, the while loop never runs,
    # and priority-flood returns the DEM unfilled with filled=0 and no error.
    # Every depression survives, each becomes a cell with no downslope
    # neighbour, accumulation shatters into thousands of tiny basins, and HAND
    # is computed against the fragments. It looks like a result.
    seed = np.zeros(out.shape, dtype=bool)
    seed[0, :] = seed[-1, :] = True
    seed[:, 0] = seed[:, -1] = True
    if nodata_mask.any():
        from scipy import ndimage

        seed |= ndimage.binary_dilation(nodata_mask) & ~nodata_mask
    seed &= ~nodata_mask

    if not seed.any():
        raise GeolensError(
            "Priority-flood found no outlet: every cell on the grid edge and "
            "every cell bordering nodata is itself nodata. Depression filling "
            "cannot run, so flow routing would return a shattered network. "
            "Check the DEM actually contains data."
        )

    filled = _pf(out, closed, np.ascontiguousarray(seed), _DR, _DC)
    return out, int(filled)


# -- D8 direction -------------------------------------------------------------


def _d8_direction(z: np.ndarray, nodata: np.ndarray, cell: float) -> np.ndarray:
    """Steepest-descent D8, vectorized over the eight neighbour shifts.

    Diagonal drops are divided by sqrt(2) so a diagonal is not preferred purely
    for covering more ground — the classic D8 mistake.
    """
    H, W = z.shape
    best = np.zeros((H, W), dtype=np.float32)
    code = np.zeros((H, W), dtype=np.uint8)

    for k in range(8):
        dr, dc = int(_DR[k]), int(_DC[k])
        dist = cell * (np.sqrt(2.0) if dr != 0 and dc != 0 else 1.0)

        shifted = np.full((H, W), np.inf, dtype=np.float32)
        r0, r1 = max(0, -dr), H - max(0, dr)
        c0, c1 = max(0, -dc), W - max(0, dc)
        shifted[r0:r1, c0:c1] = z[r0 + dr:r1 + dr, c0 + dc:c1 + dc]

        drop = (z - shifted) / np.float32(dist)
        better = (drop > best) & np.isfinite(drop)
        best = np.where(better, drop, best)
        code = np.where(better, _CODES[k], code)
        del shifted, drop, better

    code[nodata] = 0
    return code


# -- accumulation via topological sweep ---------------------------------------


def _accumulate(fdir: np.ndarray, weights: np.ndarray | None = None) -> np.ndarray:
    """Flow accumulation by Kahn's algorithm — each cell processed exactly once.

    Count how many neighbours drain into each cell, seed a queue with the ones
    nobody drains into (ridges), and push each cell's total downstream as it is
    finished. No recursion, no repeated traversal, and the queue never holds
    more than the grid.
    """
    nb = _numba()
    H, W = fdir.shape
    # uint32, not float32. Accumulation is a count of cells, and float32 carries
    # only 24 bits of mantissa: above 2**24 = 16.7M cells the spacing exceeds 1,
    # so `acc += 1` is a no-op and every hillslope cell draining into a trunk
    # contributes nothing. At 2 m that ceiling is 67 km2 -- an ordinary
    # watershed -- and above ~5e8 cells the spacing is 64, which silently
    # discards whole tributaries. uint32 is the same 4 bytes and is EXACT to
    # 4.29 billion cells, more than any grid this will run on.
    acc = (np.ones((H, W), dtype=np.uint32) if weights is None
           else weights.astype(np.float64, copy=True))

    if nb is None:
        raise GeolensError(
            "numba is required for flow accumulation at this scale. "
            "pip install numba"
        )
    from numba import njit

    @njit(cache=True)
    def _run(fdir, acc, codes, dr, dc):
        H, W = fdir.shape
        indeg = np.zeros((H, W), dtype=np.uint8)

        # Which neighbour does each cell drain to, and how many drain into it?
        for r in range(H):
            for c in range(W):
                f = fdir[r, c]
                if f == 0:
                    continue
                for k in range(8):
                    if f == codes[k]:
                        rr = r + dr[k]
                        cc = c + dc[k]
                        if 0 <= rr < H and 0 <= cc < W:
                            indeg[rr, cc] += 1
                        break

        queue = np.empty(H * W, dtype=np.int64)
        head = 0
        tail = 0
        for r in range(H):
            for c in range(W):
                if indeg[r, c] == 0 and fdir[r, c] != 0:
                    queue[tail] = r * W + c
                    tail += 1

        processed = 0
        while head < tail:
            idx = queue[head]
            head += 1
            processed += 1
            r, c = idx // W, idx % W
            f = fdir[r, c]
            if f == 0:
                continue
            for k in range(8):
                if f == codes[k]:
                    rr = r + dr[k]
                    cc = c + dc[k]
                    if 0 <= rr < H and 0 <= cc < W:
                        acc[rr, cc] += acc[r, c]
                        indeg[rr, cc] -= 1
                        if indeg[rr, cc] == 0 and fdir[rr, cc] != 0:
                            queue[tail] = rr * W + cc
                            tail += 1
                    break
        return processed

    processed = _run(fdir, acc, _CODES, _DR, _DC)
    total = int((fdir != 0).sum())
    if processed < total:
        # Unprocessed cells sit in a cycle — only possible if depressions were
        # not fully resolved, so flag it rather than returning quiet nonsense.
        raise GeolensError(
            f"Flow accumulation left {total - processed:,} cells unprocessed, "
            "which means the flow graph contains a cycle. The DEM was not fully "
            "depression-filled — report this with the DEM if it persists."
        )
    return acc


# -- public -------------------------------------------------------------------


def flow_routing(
    dem: str | Path,
    fill_depressions: bool = True,
    guard: Guard | None = None,
    out_prefix: str = "flow",
) -> FlowResult:
    """Compute D8 flow direction and accumulation over the whole grid.

    These are useful products in their own right — flow direction and
    contributing area answer a lot of questions on their own — and they are the
    foundation every tiling scheme needs, because accumulation is the one
    quantity that cannot be computed tile-by-tile without either the full
    upstream area or injected inflow.

    Outputs: filled DEM (float32), flow direction (uint8, ESRI/pysheds D8
    encoding), and accumulation (uint32, in cells — multiply by cell area for
    contributing area).
    """
    import rasterio

    guard = guard or Guard.from_env()
    p = guard.read_path(dem)
    t0 = time.monotonic()
    warnings: list[str] = []

    with rasterio.open(str(p)) as src:
        z = src.read(1, masked=True)
        profile = src.profile.copy()
        cell = abs(src.res[0])
        crs = src.crs.to_string() if src.crs else None
        if src.crs is None:
            warnings.append("DEM has no CRS — drainage areas are in raw cells only.")
        elif src.crs.is_geographic:
            warnings.append(
                "DEM is in a geographic CRS (degrees). Flow directions are "
                "computed on a distorted grid and drainage areas are wrong. "
                "Reproject before using these outputs."
            )

    nodata = np.ma.getmaskarray(z)
    zf = z.filled(np.float32(1e30)).astype(np.float32)
    H, W = zf.shape
    cells = H * W
    note(f"{cells:,} cells @ {cell:g} m")

    filled_count = 0
    if fill_depressions:
        note("filling depressions (priority flood)")
        zf, filled_count = _priority_flood_fast(zf, nodata)
        note(f"raised {filled_count:,} cells")

    note("computing D8 flow direction")
    fdir = _d8_direction(zf, nodata, cell)

    note("computing flow accumulation (topological sweep)")
    acc = _accumulate(fdir)

    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)

    def _write(arr, name, dtype, nd):
        pr = gtiff_profile(profile, dtype=dtype, nodata=nd)
        path = out_dir / name
        with rasterio.open(str(path), "w", **pr) as dst:
            dst.write(arr.astype(dtype), 1)
        return str(path)

    zf[nodata] = profile.get("nodata", -9999.0) or -9999.0
    paths = {
        "filled": _write(zf, "filled.tif", "float32", profile.get("nodata", -9999.0)),
        "flow_dir": _write(fdir, "flow_dir.tif", "uint8", 0),
        "accumulation": _write(acc, "accumulation.tif", str(acc.dtype), None),
    }

    max_acc_km2 = float(acc.max()) * (cell ** 2) / 1e6
    peak_gb = cells * (4 + 1 + 4 + 1) / 1e9
    elapsed = time.monotonic() - t0
    note(f"flow routing complete in {elapsed:.1f}s "
         f"(max drainage {max_acc_km2:,.2f} km²)")

    return FlowResult(
        filled=paths["filled"], flow_dir=paths["flow_dir"],
        accumulation=paths["accumulation"], cells=cells, cell_size=cell, crs=crs,
        depressions_filled=filled_count, max_accumulation_km2=max_acc_km2,
        seconds=elapsed, peak_arrays_gb=round(peak_gb, 2), warnings=warnings,
    )


def catchment(
    fdir: np.ndarray, pour_r: int, pour_c: int, out: np.ndarray | None = None
) -> np.ndarray:
    """Every cell draining to (pour_r, pour_c) — the COMPLETE watershed.

    This is the tiling unit that carries no truncation: a complete watershed has
    no inflow across its boundary by definition, so accumulation computed inside
    the clip is exactly right for every cell in it.

    Walks upstream by checking which of the eight neighbours point at each cell.
    """
    nb = _numba()
    if nb is None:
        raise GeolensError("numba is required. pip install numba")
    from numba import njit

    @njit(cache=True)
    def _up(fdir, r0, c0, mask, codes, dr, dc):
        H, W = fdir.shape
        stack = np.empty(H * W, dtype=np.int64)
        top = 0
        stack[top] = r0 * W + c0
        top += 1
        mask[r0, c0] = True
        n = 1
        while top > 0:
            top -= 1
            idx = stack[top]
            r, c = idx // W, idx % W
            for k in range(8):
                rr = r + dr[k]
                cc = c + dc[k]
                if rr < 0 or rr >= H or cc < 0 or cc >= W or mask[rr, cc]:
                    continue
                # Does this neighbour drain INTO (r, c)? Its code must point the
                # opposite way, i.e. index (k + 4) mod 8.
                if fdir[rr, cc] == codes[(k + 4) % 8]:
                    mask[rr, cc] = True
                    stack[top] = rr * W + cc
                    top += 1
                    n += 1
        return n

    mask = out if out is not None else np.zeros(fdir.shape, dtype=bool)
    _up(fdir, int(pour_r), int(pour_c), mask, _CODES, _DR, _DC)
    return mask


def complete_watersheds(
    flow: FlowResult,
    target_area_km2: float = 50.0,
    guard: Guard | None = None,
    out_prefix: str = "watersheds",
) -> str:
    """Partition the landscape into COMPLETE watersheds of roughly a target size.

    Pour points are chosen where accumulation first exceeds the target area,
    working from the largest drainage down. Each pour point's full catchment is
    claimed, and cells already claimed by a larger watershed are not re-taken —
    so the result partitions the grid, and **every polygon is a complete
    watershed of the cells it contains**.

    This is what makes tiling safe: clip the DEM to one of these and its
    accumulation is exact, with no truncated inflow. Contrast with cutting the
    network into reach-length slices, which are not complete watersheds and do
    truncate.
    """
    import geopandas as gpd
    import rasterio
    from rasterio import features as rfeat
    from shapely.geometry import shape

    guard = guard or Guard.from_env()
    t0 = time.monotonic()

    with rasterio.open(flow.flow_dir) as src:
        fdir = src.read(1)
        transform = src.transform
        crs = src.crs
    with rasterio.open(flow.accumulation) as src:
        acc = src.read(1)

    cell = flow.cell_size
    cell_area_km2 = (cell ** 2) / 1e6
    want = target_area_km2 / cell_area_km2
    if want < 4:
        raise GeolensError(
            f"target_area_km2 ({target_area_km2}) is under 4 cells at "
            f"{cell:g} m resolution."
        )

    note(f"selecting pour points at ~{want:,.0f} cells ({target_area_km2:g} km²)")
    claimed = np.zeros(fdir.shape, dtype=bool)
    label = np.zeros(fdir.shape, dtype="int32")

    # Candidates: cells whose accumulation is at least the target. Taking the
    # largest first means each new watershed is the residual of what is left,
    # which keeps sizes near the target instead of nesting.
    cand = np.flatnonzero(acc.ravel() >= want)
    if cand.size == 0:
        raise GeolensError(
            f"No cell reaches {target_area_km2:g} km² of drainage. Lower "
            "target_area_km2."
        )
    order = cand[np.argsort(-acc.ravel()[cand])]

    W = fdir.shape[1]
    n = 0
    scratch = np.zeros(fdir.shape, dtype=bool)
    for idx in order:
        r, c = int(idx // W), int(idx % W)
        if claimed[r, c]:
            continue
        scratch[:] = False
        catchment(fdir, r, c, out=scratch)
        new = scratch & ~claimed
        if new.sum() < want * 0.25:        # mostly already claimed: skip
            continue
        n += 1
        label[new] = n
        claimed |= new

    # Anything unclaimed (small coastal/edge drainages) joins its nearest
    # labelled neighbour so the partition is complete.
    if not claimed.all():
        from scipy import ndimage

        # return_distances=False. Without it scipy allocates a full-size
        # float64 distance array and hands it back only to be discarded --
        # 15.5 GB on a 1.94-billion-cell grid, for a number nothing here reads.
        ir, ic = ndimage.distance_transform_edt(
            ~claimed, return_indices=True, return_distances=False)
        label = label[ir, ic]

    note(f"{n} complete watersheds")
    geoms = []
    for geom, val in rfeat.shapes(label, mask=label > 0, transform=transform):
        geoms.append((shape(geom), int(val)))
    gdf = gpd.GeoDataFrame({"ws_id": [g[1] for g in geoms]},
                           geometry=[g[0] for g in geoms], crs=crs)
    gdf = gdf.dissolve(by="ws_id", as_index=False)
    gdf["area_km2"] = gdf.geometry.area / 1e6

    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "watersheds.gpkg"
    gdf.to_file(str(out), driver="GPKG")
    note(f"watersheds written in {time.monotonic() - t0:.1f}s "
         f"(median {gdf['area_km2'].median():,.1f} km²)")
    return str(out)
