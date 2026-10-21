"""Stream network extraction, segmentation into DGOs, and NHD conflation.

The unit of landscape-scale analysis is the **DGO** (discrete geographic object):
a fixed-length piece of stream plus the valley bottom that belongs to it. Every
metric is computed per DGO, which turns a continent-sized raster problem into a
table with tens of thousands of rows.

Valley cells are assigned to DGOs by nearest-segment allocation on a Euclidean
distance transform — O(cells), not O(segments x cells) — which is what keeps this
tractable at scale.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..safety import Guard, GeolensError, note, progress
from .terrain import TerrainResult


@dataclass
class NetworkResult:
    segments: str
    segment_raster: str
    segment_count: int
    total_length_km: float
    segment_length_m: float
    nhd_joined: int
    seconds: float
    warnings: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


# -- raster skeleton -> polylines ---------------------------------------------

_NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def prune_skeleton(skel: np.ndarray, min_spur_px: int = 8,
                   max_rounds: int = 12) -> np.ndarray:
    """Remove short side-spurs from a thinned skeleton.

    Skeletonizing a noisy band leaves small barbs hanging off the centreline.
    Each barb is a junction, and ``_trace_skeleton`` splits at junctions — so a
    single 1.2 km gully arrives as a dozen 150 m fragments unless the barbs go
    first. Pruning is iterative because removing one spur can expose another.
    """
    if min_spur_px <= 0:
        return skel
    sk = skel.copy()
    for _ in range(max_rounds):
        pts = {(int(r), int(c)) for r, c in np.argwhere(sk)}
        if not pts:
            break

        def nbrs(p):
            r, c = p
            return [(r + dr, c + dc) for dr, dc in _NB if (r + dr, c + dc) in pts]

        deg = {p: len(nbrs(p)) for p in pts}
        ends = [p for p, d in deg.items() if d == 1]
        if not ends:
            break

        removed = 0
        for e in ends:
            if e not in pts:
                continue
            branch = [e]
            prev, cur = None, e
            while True:
                nxt = [q for q in nbrs(cur) if q != prev]
                if len(nxt) != 1:
                    break
                prev, cur = cur, nxt[0]
                if deg.get(cur, 0) > 2:      # reached a junction
                    break
                branch.append(cur)
                if len(branch) > min_spur_px:
                    break
            if len(branch) <= min_spur_px:
                for q in branch:
                    sk[q] = False
                    pts.discard(q)
                removed += 1
        if removed == 0:
            break
    return sk


def _end_dirs(line, sample_m: float):
    """Outward unit vectors at each end of a line.

    "Outward" means pointing away from the line, so two fragments that continue
    each other have outward vectors that are roughly opposite.
    """
    import numpy as np

    xs, ys = line.xy
    pts = np.column_stack([xs, ys])
    if len(pts) < 2:
        return None

    def unit(a, b):
        v = a - b
        n = float(np.hypot(v[0], v[1]))
        return v / n if n > 0 else None

    # Walk in far enough to get a stable direction, but not past the line.
    k = 1
    total = 0.0
    while k < len(pts) - 1 and total < sample_m:
        total += float(np.hypot(*(pts[k] - pts[k - 1])))
        k += 1
    head = unit(pts[0], pts[min(k, len(pts) - 1)])

    k = 1
    total = 0.0
    while k < len(pts) - 1 and total < sample_m:
        total += float(np.hypot(*(pts[-k] - pts[-k - 1])))
        k += 1
    tail = unit(pts[-1], pts[max(-k - 1, -len(pts))])

    if head is None or tail is None:
        return None
    return (pts[0], head), (pts[-1], tail)


def merge_fragments(lines, min_length_m: float = 0.0, snap_tol_m: float = 0.0,
                    max_angle_deg: float = 40.0, sample_m: float = 0.0,
                    max_rounds: int = 8):
    """Join traced fragments into continuous features.

    Two passes, because they fix different things:

    1. ``shapely.ops.linemerge`` joins anything sharing an exact endpoint. Cheap
       and safe, but it refuses every line at a junction — three lines meet at
       one point and the continuation is ambiguous, so all three stay split.
    2. **Collinear joining.** Skeletonizing a noisy band leaves short barbs, and
       every barb is a junction that pass 1 cannot resolve. Here each fragment
       end gets an outward direction; two ends join when they are within
       ``snap_tol_m`` and their outward directions are roughly opposite, i.e.
       the fragments continue each other. The barb, pointing off at an angle,
       is left alone — which is exactly what should happen to it.

    Set ``snap_tol_m`` to 0 to skip pass 2.
    """
    import numpy as np
    from shapely.geometry import LineString, MultiLineString
    from shapely.ops import linemerge

    lines = [ln for ln in lines if ln is not None and ln.length > 0]
    if not lines:
        return []

    merged = linemerge(MultiLineString(lines)) if len(lines) > 1 else lines[0]
    parts = list(merged.geoms) if isinstance(merged, MultiLineString) else [merged]
    parts = [p for p in parts if isinstance(p, LineString) and p.length > 0]

    if snap_tol_m > 0 and len(parts) > 1:
        sample = sample_m if sample_m > 0 else max(snap_tol_m * 3, 1e-9)
        cos_max = np.cos(np.radians(180.0 - max_angle_deg))

        for _ in range(max_rounds):
            ends = []          # (part_index, which_end, point, outward_dir)
            for i, ln in enumerate(parts):
                d = _end_dirs(ln, sample)
                if d is None:
                    continue
                (p0, d0), (p1, d1) = d
                ends.append((i, 0, p0, d0))
                ends.append((i, 1, p1, d1))
            if len(ends) < 2:
                break

            # Only endpoints within snap_tol_m of each other can ever join, so
            # bucket them on a grid of that size and compare within the 3x3
            # neighbourhood instead of comparing every pair.
            #
            # The all-pairs version is O(n^2) and it is not a theoretical
            # concern: a real gully run on 10 m Ruidoso produced 21,018
            # fragments -- 42,036 endpoints, 8.8e8 pairs per round, several
            # rounds -- and sat in this loop for half an hour with nothing to
            # show. The fixtures produce a few dozen fragments, so it looked
            # instant in every test.
            cell = max(snap_tol_m, 1e-9)
            buckets: dict[tuple[int, int], list[int]] = {}
            for a, (_i, _e, pa, _d) in enumerate(ends):
                buckets.setdefault((int(pa[0] // cell), int(pa[1] // cell)),
                                   []).append(a)

            cands = []
            seen_pair: set[tuple[int, int]] = set()
            for a, (ia, ea, pa, da) in enumerate(ends):
                bx, by = int(pa[0] // cell), int(pa[1] // cell)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for b in buckets.get((bx + dx, by + dy), ()):
                            if b <= a:
                                continue
                            ib, eb, pb, db = ends[b]
                            if ia == ib:
                                continue
                            key = (a, b)
                            if key in seen_pair:
                                continue
                            seen_pair.add(key)
                            if float(np.hypot(*(pa - pb))) > snap_tol_m:
                                continue
                            # Opposite outward directions == collinear
                            # continuation.
                            dot = float(np.dot(da, db))
                            if dot > cos_max:
                                continue
                            cands.append((dot, a, b))
            if not cands:
                break

            cands.sort()                      # most opposite (best) first
            used_end: set[tuple[int, int]] = set()
            used_part: set[int] = set()
            joins = []
            for _dot, a, b in cands:
                ia, ea, _, _ = ends[a]
                ib, eb, _, _ = ends[b]
                if (ia, ea) in used_end or (ib, eb) in used_end:
                    continue
                if ia in used_part or ib in used_part:
                    continue
                used_end.add((ia, ea))
                used_end.add((ib, eb))
                used_part.add(ia)
                used_part.add(ib)
                joins.append((ia, ea, ib, eb))
            if not joins:
                break

            consumed: set[int] = set()
            new_parts = []
            for ia, ea, ib, eb in joins:
                ca = list(parts[ia].coords)
                cb = list(parts[ib].coords)
                if ea == 0:
                    ca.reverse()              # so ca ends at the join
                if eb == 1:
                    cb.reverse()              # so cb starts at the join
                new_parts.append(LineString(ca + cb[1:]))
                consumed.add(ia)
                consumed.add(ib)
            new_parts.extend(p for i, p in enumerate(parts) if i not in consumed)
            parts = new_parts

    if min_length_m > 0:
        parts = [p for p in parts if p.length >= min_length_m]
    return parts


def _trace_skeleton(skel: np.ndarray) -> list[list[tuple[int, int]]]:
    """Trace a thinned binary skeleton into polylines of (row, col) pixels.

    Splits at junctions so each returned path is a simple chain. Good enough for
    segmentation and length; not a topologically complete network model.
    """
    H, W = skel.shape
    pts = np.argwhere(skel)
    if pts.size == 0:
        return []
    ptset = {(int(r), int(c)) for r, c in pts}

    def nbrs(p):
        r, c = p
        return [
            (r + dr, c + dc)
            for dr, dc in _NB
            if (r + dr, c + dc) in ptset
        ]

    degree = {p: len(nbrs(p)) for p in ptset}
    nodes = {p for p, d in degree.items() if d != 2}   # endpoints and junctions
    visited_edges: set[frozenset] = set()
    paths: list[list[tuple[int, int]]] = []

    def walk(start, first):
        """Follow a chain of degree-2 cells until it reaches a node.

        A ring of degree-2 cells has no node to reach. This walked it forever,
        appending a cell per lap, and the only thing that stopped it was the
        machine running out of memory -- which is what killed an overnight run
        on a 1.94-billion-cell DEM, and what reproduced here as a 6 GB OOM on
        10 m Ruidoso. The synthetic fixtures never grew a closed loop, so the
        tests were all green while the bug was fatal on real terrain.

        Every cell already visited on THIS walk ends it. That is exactly the
        closed-loop case and it cannot fire on a simple chain, where no cell
        repeats.
        """
        path = [start, first]
        seen = {start, first}
        prev, cur = start, first
        while degree.get(cur, 0) == 2:
            nxt = [p for p in nbrs(cur) if p != prev]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            path.append(cur)
            if cur in seen:
                break                      # closed the ring; stop on the seam
            seen.add(cur)
        return path

    for n in nodes:
        for nb in nbrs(n):
            key = frozenset((n, nb))
            if key in visited_edges:
                continue
            p = walk(n, nb)
            for a, b in zip(p, p[1:]):
                visited_edges.add(frozenset((a, b)))
            if len(p) >= 2:
                paths.append(p)

    # Closed loops with no node on them.
    remaining = ptset - {p for path in paths for p in path}
    while remaining:
        start = remaining.pop()
        nb = [p for p in nbrs(start) if p in remaining or p in ptset]
        if not nb:
            continue
        p = walk(start, nb[0])
        paths.append(p)
        remaining -= set(p)

    return paths


def _absorb_short_connectors(paths, min_px: int, accumulation: str | None,
                             shape) -> tuple[list, int]:
    """Join every path shorter than ``min_px`` that runs between two
    junctions onto the neighbouring path that carries the most flow at the
    junction they share, until none is left; the short side of a loop is
    dropped instead. Returns the paths, how many were joined and how many
    sides dropped."""
    import rasterio

    node_paths: dict[tuple[int, int], set[int]] = {}
    alive = [True] * len(paths)

    def _add(i):
        for q in (paths[i][0], paths[i][-1]):
            node_paths.setdefault(q, set()).add(i)

    def _drop(i, ends_):
        for q in ends_:
            s = node_paths.get(q)
            if s:
                s.discard(i)

    for i in range(len(paths)):
        _add(i)

    acc_cache: dict[tuple[int, int], float] = {}

    def _flow(cells):
        """Accumulation at the cells next to the shared node, read once."""
        want = [c for c in cells if c not in acc_cache]
        if want and accumulation:
            try:
                with rasterio.open(accumulation) as src:
                    xy = [src.xy(r, c) for r, c in want]
                    for cell_, v in zip(want, src.sample(xy)):
                        acc_cache[cell_] = float(v[0])
            except Exception:                                # noqa: BLE001
                for cell_ in want:
                    acc_cache[cell_] = 0.0
        for cell_ in want:
            acc_cache.setdefault(cell_, 0.0)
        return [acc_cache[c] for c in cells]

    joined = dropped_sides = 0
    for _round in range(50):
        progress_made = False
        order = sorted((i for i in range(len(paths)) if alive[i]
                        and len(paths[i]) < min_px), key=lambda i: len(paths[i]))
        for i in order:
            if not alive[i] or len(paths[i]) >= min_px:
                continue
            p = paths[i]
            a, b = p[0], p[-1]
            at_a = [j for j in node_paths.get(a, ()) if j != i and alive[j]]
            at_b = [j for j in node_paths.get(b, ()) if j != i and alive[j]]
            if not at_a or not at_b:
                continue                         # a free end: a spur, left alone
            # The short side of a loop (another path between the same two
            # junctions) is the skeleton of a wide reach drawn twice: drop
            # it, the other side keeps the stream whole.
            if any(j in at_b and len(paths[j]) >= len(p) for j in at_a):
                _drop(i, (a, b))
                alive[i] = False
                dropped_sides += 1
                progress_made = True
                continue
            cands = []
            for node, js in ((a, at_a), (b, at_b)):
                for j in js:
                    q = paths[j]
                    # the cell of q beside the shared node
                    beside = q[1] if q[0] == node and len(q) > 1 else q[-2]
                    cands.append((node, j, beside))
            flows = _flow([c[2] for c in cands])
            node, j, _ = max(zip(cands, flows),
                             key=lambda t: (t[1], len(paths[t[0][1]])))[0]
            q = paths[j]
            if q[-1] == node:
                r = q + (p[1:] if p[0] == node else p[-2::-1])
            else:
                r = (p[:-1] if p[-1] == node else p[::-1][:-1]) + q
            _drop(i, (a, b))
            _drop(j, (q[0], q[-1]))
            alive[i] = False
            paths[j] = r
            _add(j)
            joined += 1
            progress_made = True
        if not progress_made:
            break
    return [p for i, p in enumerate(paths) if alive[i]], joined, dropped_sides


def extract_network(
    terrain: TerrainResult,
    segment_length_m: float = 500.0,
    min_branch_m: float = 300.0,
    nhd: str | Path | None = None,
    nhd_snap_m: float = 200.0,
    guard: Guard | None = None,
    out_prefix: str = "network",
) -> NetworkResult:
    """Vectorize the stream raster and cut it into fixed-length segments.

    Parameters
    ----------
    segment_length_m
        DGO length. 500 m suits 30 m data; longer smooths more, shorter resolves
        more but multiplies rows. Segments never exceed this; the tail of each
        branch is merged into the previous segment when shorter than half.
    min_branch_m
        Drop traced branches shorter than this — skeleton spurs, not streams.
    nhd
        Optional NHD flowline layer. Segments within ``nhd_snap_m`` of a flowline
        inherit its attributes, so DEM-derived geometry carries NHD identity.
    """
    import geopandas as gpd
    import rasterio
    from shapely.geometry import LineString
    from skimage.morphology import skeletonize

    guard = guard or Guard.from_env()
    t0 = time.monotonic()
    warnings: list[str] = []

    with rasterio.open(terrain.streams) as src:
        streams = src.read(1).astype(bool)
        transform = src.transform
        cell = abs(src.res[0])
        crs = src.crs
        shape = streams.shape
    with rasterio.open(terrain.conditioned) as src:
        dem = src.read(1).astype("float64")

    # Metres are not CRS units and elevations have a unit of their own.
    #
    # Every *_m parameter here -- segment_length_m, min_branch_m, nhd_snap_m --
    # was divided by a cell size in CRS units, and every *_m column written out
    # -- length_m, drop_m -- was a raw shapely length or elevation difference.
    # On EPSG:6529 that meant segments asked for at 500 m were cut at 500 ft =
    # 152 m, spurs under 300 ft were dropped instead of 300 m, and the reported
    # network length was in kilofeet under a column called km. Everything
    # downstream inherits the segment length: confinement, valley-form section
    # spacing, the lot.
    #
    # `gradient` was drop/length with both in feet, which happens to be right
    # on this grid and is wrong the moment a CRS declares a vertical unit that
    # differs from its horizontal one.
    _u = getattr(terrain, "units", None) or {}
    mpu = float(_u.get("metres_per_unit") or 1.0)
    vpu = float(_u.get("vertical_metres_per_unit") or mpu)
    cell_m = float(_u.get("cell_size_m") or terrain.cell_size or cell * mpu)

    def _u_from_m(metres: float) -> float:
        """A distance in metres, in the CRS's own units."""
        return float(metres) / mpu

    note("thinning stream raster")
    skel = skeletonize(streams)

    note("tracing network")
    paths = _trace_skeleton(skel)
    if not paths:
        raise GeolensError(
            "No stream network could be traced. Lower stream_threshold_km2."
        )

    min_px = max(2, int(round(_u_from_m(min_branch_m) / cell)))
    # Only a spur -- a path with a free end -- is dropped for being short. A
    # short path between two junctions is a piece of the stream itself (the
    # stubs of a junction cluster, and in a wide wash the stretches between
    # the side loops the skeleton grows), and dropping it cut the main stem
    # into pieces the network could not join: on the first Kingman run
    # 1,300 of 39,000 reaches had a gap of up to 400 m below them. Such a
    # path is joined onto the neighbouring path carrying the most flow (the
    # river goes on through the junction), never made a reach of its own:
    # a two-cell stub owns no cell once the junction cells go to the paths
    # that share them.
    n0 = len(paths)
    paths, joined, sides = _absorb_short_connectors(
        paths, min_px, terrain.accumulation, skel.shape)
    ends: dict[tuple[int, int], int] = {}
    for p in paths:
        for q in (p[0], p[-1]):
            ends[q] = ends.get(q, 0) + 1

    def _spur(p):
        return ends.get(p[0], 0) < 2 or ends.get(p[-1], 0) < 2
    n1 = len(paths)
    paths = [p for p in paths if len(p) >= min_px or not _spur(p)]
    note(f"{len(paths):,} branches after joining {joined:,} short connecting "
         f"paths onto their neighbours, dropping {sides:,} short sides of "
         f"loops and {n1 - len(paths):,} spurs under {min_branch_m:g} m "
         f"(traced {n0:,})")

    from rasterio.transform import xy as _rc_to_xy

    def to_xy_many(rcs):
        rows = [r for r, _ in rcs]
        cols = [c for _, c in rcs]
        xs_, ys_ = _rc_to_xy(transform, rows, cols, offset="center")
        return list(zip(xs_, ys_))

    seg_px = max(1, int(round(_u_from_m(segment_length_m) / cell)))
    records: list[dict[str, Any]] = []
    seg_pixels: list[list[tuple[int, int]]] = []

    for bi, path in enumerate(progress(paths, label="segmenting network")):
        chunks = [path[i : i + seg_px + 1] for i in range(0, len(path), seg_px)]
        # Merge a short tail into the previous chunk.
        if len(chunks) > 1 and len(chunks[-1]) < seg_px // 2:
            chunks[-2] = chunks[-2] + chunks[-1][1:]
            chunks.pop()
        for ci, chunk in enumerate(chunks):
            if len(chunk) < 2:
                continue
            coords = to_xy_many(chunk)
            line = LineString(coords)
            zs = [dem[r, c] for r, c in chunk]
            z0, z1 = float(zs[0]), float(zs[-1])
            length = float(line.length) * mpu          # true metres
            drop = (z0 - z1) * vpu                     # true metres
            records.append(
                {
                    "seg_id": len(records),
                    "branch": bi,
                    "pos": ci,
                    "length_m": length,
                    "z_start": z0,
                    "z_end": z1,
                    "drop_m": drop,
                    "gradient": drop / length if length > 0 else np.nan,
                    "geometry": line,
                }
            )
            seg_pixels.append(chunk)

    if not records:
        raise GeolensError("Network traced but no segments produced.")

    gdf = gpd.GeoDataFrame(records, crs=crs)
    # Gradient is signed by trace direction, which may run upstream. Use the
    # magnitude — direction is recovered from accumulation if ever needed.
    gdf["gradient"] = gdf["gradient"].abs()

    # --- segment id raster, for nearest-segment allocation -------------------
    note("rasterizing segment ids")
    seg_r = np.zeros(shape, dtype="int32")
    for rec, px in zip(records, seg_pixels):
        for r, c in px:
            seg_r[r, c] = rec["seg_id"] + 1  # 0 = background

    # --- NHD conflation -------------------------------------------------------
    joined = 0
    if nhd is not None:
        nhd_p = guard.read_path(nhd)
        nd = gpd.read_file(str(nhd_p))
        if nd.crs is not None and crs is not None and nd.crs != crs:
            nd = nd.to_crs(crs)
            warnings.append("NHD layer was reprojected to the DEM CRS.")
        note(f"joining NHD attributes within {nhd_snap_m:g} m")
        try:
            joined_gdf = gpd.sjoin_nearest(
                gdf, nd, how="left",
                max_distance=_u_from_m(nhd_snap_m),
                distance_col="nhd_dist_m",
            )
            # sjoin_nearest can duplicate rows when ties occur; keep the closest.
            joined_gdf = (
                joined_gdf.sort_values("nhd_dist_m")
                .groupby("seg_id", as_index=False)
                .first()
            )
            gdf = gpd.GeoDataFrame(joined_gdf, geometry="geometry", crs=crs)
            joined = int(gdf["nhd_dist_m"].notna().sum())
            note(f"{joined:,} of {len(gdf):,} segments matched an NHD flowline")
            if joined == 0:
                warnings.append(
                    f"No segments matched an NHD flowline within {nhd_snap_m:g} m. "
                    "Check the CRS and that the flowlines cover this extent."
                )
        except Exception as exc:
            warnings.append(f"NHD join failed ({exc}); segments carry no NHD identity.")

    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    seg_path = out_dir / "segments.gpkg"
    gdf.to_file(str(seg_path), driver="GPKG")

    import rasterio as rio

    prof = {
        "driver": "GTiff", "height": shape[0], "width": shape[1], "count": 1,
        "dtype": "int32", "crs": crs, "transform": transform, "nodata": 0,
        "compress": "deflate",
    }
    seg_r_path = out_dir / "segment_ids.tif"
    with rio.open(str(seg_r_path), "w", **prof) as dst:
        dst.write(seg_r, 1)

    elapsed = time.monotonic() - t0
    total_km = float(gdf["length_m"].sum()) / 1000.0
    note(f"network: {len(gdf):,} segments, {total_km:,.1f} km, {elapsed:.1f}s")

    return NetworkResult(
        segments=str(seg_path),
        segment_raster=str(seg_r_path),
        segment_count=len(gdf),
        total_length_km=total_km,
        segment_length_m=segment_length_m,
        nhd_joined=joined,
        seconds=elapsed,
        warnings=warnings,
    )
