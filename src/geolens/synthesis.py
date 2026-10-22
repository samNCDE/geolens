"""Stream power, major streams and the HUC12 sensitivity table.

Three things a landscape plan asks of the reach layer once it exists:

**Stream power.** How much work the channel can do on its bed and banks.
Bankfull discharge comes from the regional curve (Moody et al. 2003:
Q = a A^b, A the drainage area in km²), the total stream power is
Omega = rho g Q S in W/m, and the unit (specific) stream power omega = Omega / W in W/m²
divides it over the regional bankfull width. Both are indices from a curve
and a 10 m slope, not measurements: compare reaches with them, do not
design with them.

**Major streams.** Each reach's ``drainage_km2`` is the area draining to
its downstream end. Walking from every outlet upstream, and taking the
larger drainage at each junction, gives the main stem of each stream and
its tributaries their own ``stream_id``; the stream's drainage area is the
area at its mouth. Streams draining more than a threshold at the mouth are
dissolved into ``major_streams``, one line each, with the stream's length,
gradient, stream power, dominant landscape type and fan share.

**Sensitivity.** ``huc12_summary`` has the measurements; this ranks them.
Each component of ``COMPONENTS`` (erodibility, runoff-prone soils, slope,
alluvial valley, unstable form, stream power, bare ground and its trend,
fire, storm depth) becomes a percentile rank among the project's HUC12s,
and the weighted mean of the ranks is the sensitivity score, cut into
thirds. The weights are a first guess, written beside the table so they
can be argued with; the per-component ranks say why a HUC12 scored as it
did.
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np

from .safety import GeolensError, note

RHO_G = 1000.0 * 9.81             # water density x gravity, N/m³

# (summary column, label, default weight). Every component points the same
# way: a higher value means a more sensitive subwatershed. A column that the
# summary lacks, or that is constant, drops out and the weights of the rest
# are renormalised.
COMPONENTS: tuple[tuple[str, str, float], ...] = (
    ("soil_k_mean", "soil erodibility (K)", 0.15),
    ("hsg_D_share", "runoff-prone soils (hydrologic group D)", 0.10),
    ("slope_mean_pct", "mean slope", 0.10),
    ("alluvial_landscape_share", "stream length in alluvial landscape types", 0.15),
    ("form_unstable_share", "unstable stream form (level B)", 0.15),
    ("unit_power_mean_w_m2", "unit stream power", 0.10),
    ("rap_bgr_per_decade", "bare ground trend (% per decade)", 0.10),
    ("rap_bgr_recent", "bare ground now (%)", 0.05),
    ("fire_burned_share_10yr", "burned in the last 10 years", 0.05),
    ("atlas14_24h_100yr_mm", "100-year 24-hour storm depth", 0.05),
)
DEFAULT_WEIGHTS = {col: w for col, _, w in COMPONENTS}
LABELS = {col: label for col, label, _ in COMPONENTS}
CLASSES = ("low", "moderate", "high")


def _read(layer):
    import geopandas as gpd

    if isinstance(layer, (str, Path)):
        return gpd.read_file(str(layer))
    return layer.copy()


# -- stream power ---------------------------------------------------------------------


def stream_power(segments, curve: str | dict | None = "arid_sw_az"):
    """Add ``q_bankfull_m3s``, ``stream_power_w_m`` and
    ``unit_stream_power_w_m2`` to a reach layer, from its ``drainage_km2``
    and ``gradient`` and a regional curve with a ``discharge`` relation
    (Moody et al. 2003 has one for each province; the generic US curve has
    none, and then only ``power_basis`` is written). Returns the layer."""
    import pandas as pd

    from .landscape.streamtype import _resolve_curve

    seg = _read(segments)
    for col in ("drainage_km2", "gradient"):
        if col not in seg.columns:
            raise GeolensError(f"stream power needs a reach layer with "
                               f"{col!r} (the valley types layer has it).")
    c = _resolve_curve(curve) or {}
    # After trace_streams, drainage_km2_used has the junction-cell
    # inheritance fixed (see there); before it, the reach's own column.
    a_col = "drainage_km2_used" if "drainage_km2_used" in seg.columns \
        else "drainage_km2"
    a_km2 = pd.to_numeric(seg[a_col], errors="coerce").to_numpy(float)
    s = np.abs(pd.to_numeric(seg["gradient"], errors="coerce").to_numpy(float))
    q = np.full(len(seg), np.nan)
    if c.get("discharge"):
        qa, qb = c["discharge"]
        with np.errstate(invalid="ignore"):
            q = qa * np.maximum(a_km2, 1e-6) ** qb
        wa, wb = c["width"]
        with np.errstate(invalid="ignore"):
            w = wa * np.maximum(a_km2, 1e-6) ** wb
        omega = RHO_G * q * s
        seg["q_bankfull_m3s"] = np.round(q, 3)
        seg["stream_power_w_m"] = np.round(omega, 1)
        seg["unit_stream_power_w_m2"] = np.round(omega / np.maximum(w, 0.1), 2)
        basis = (f"Q and bankfull width from {c.get('source', 'the curve')}, "
                 f"slope the reach gradient")
    else:
        seg["q_bankfull_m3s"] = np.nan
        seg["stream_power_w_m"] = np.nan
        seg["unit_stream_power_w_m2"] = np.nan
        basis = (f"not computed: {c.get('source', 'this curve')} has no "
                 f"bankfull discharge relation")
    seg["power_basis"] = basis
    return seg


# -- tracing streams -------------------------------------------------------------------


def _endpoints(geoms):
    starts = np.empty((len(geoms), 2))
    ends = np.empty((len(geoms), 2))
    for i, g in enumerate(geoms):
        if g is None or g.is_empty:
            starts[i] = ends[i] = np.nan
            continue
        if g.geom_type == "MultiLineString":
            first, last = g.geoms[0], g.geoms[-1]
        else:
            first = last = g
        starts[i] = first.coords[0][:2]
        ends[i] = last.coords[-1][:2]
    return starts, ends


def _downstream_is_end(seg, starts, ends, drainage, tol: float) -> np.ndarray:
    """Which end of each reach is downstream: True for the line's last
    vertex. A branch (the reaches between two junctions, drawn one way)
    goes by its fall from first vertex to last on the filled DEM, which
    never rises downstream; a flat branch by its drainage, which grows
    downstream; a single flat reach by whichever end sits beside bigger
    water."""
    import pandas as pd
    from scipy.spatial import cKDTree

    n = len(seg)
    die = np.full(n, -1, dtype=int)
    if "drop_m" in seg.columns:
        drop = pd.to_numeric(seg["drop_m"], errors="coerce").to_numpy(float)
    elif {"z_start", "z_end"} <= set(seg.columns):
        drop = (pd.to_numeric(seg["z_start"], errors="coerce")
                - pd.to_numeric(seg["z_end"], errors="coerce")).to_numpy(float)
    else:
        drop = np.zeros(n)
    drop = np.where(np.isfinite(drop), drop, 0.0)
    if {"branch", "pos"} <= set(seg.columns):
        br = seg["branch"].to_numpy()
        pos = seg["pos"].to_numpy()
        order = np.lexsort((pos, br))
        b_sorted = br[order]
        d_sorted = drainage[order]
        new = np.r_[True, b_sorted[1:] != b_sorted[:-1]]
        last = np.r_[b_sorted[1:] != b_sorted[:-1], True]
        fall = np.bincount(np.cumsum(new) - 1, weights=drop[order])
        d0, d1 = d_sorted[new], d_sorted[last]
        sign = np.where(fall > 0.01, 1, np.where(fall < -0.01, 0,
                        np.where(d1 > d0, 1, np.where(d1 < d0, 0, -1))))
        lookup = dict(zip(b_sorted[new], sign))
        die = np.array([lookup.get(b, -1) for b in br], dtype=int)
    unknown = die < 0
    die[unknown & (drop > 0.01)] = 1
    die[unknown & (drop < -0.01)] = 0
    unknown = np.where(die < 0)[0]
    if len(unknown):
        pts = np.vstack([starts, ends])
        own = np.r_[np.arange(n), np.arange(n)]
        dr2 = np.r_[drainage, drainage]
        tree = cKDTree(pts)
        for i in unknown:
            best = []
            for p in (starts[i], ends[i]):
                near = [k for k in tree.query_ball_point(p, tol) if own[k] != i]
                best.append(max((dr2[k] for k in near), default=-1.0))
            die[i] = 1 if best[1] >= best[0] else 0
    return die.astype(bool)


def _receivers(seg, down, up, drainage, tol: float, bridge_m: float):
    """For each reach, the reach its downstream end flows into (-1 for an
    outlet), and how it was matched: at a shared node, onto another reach's
    line, or across a gap in the network."""
    import geopandas as gpd
    from scipy.spatial import cKDTree
    from shapely.geometry import Point

    n = len(seg)
    recv = np.full(n, -1, dtype=int)
    kind = np.zeros(n, dtype=int)                # 0 outlet, 1 node, 2 line, 3 gap
    branch = seg["branch"].to_numpy() if "branch" in seg.columns \
        else np.arange(n)
    # Water joins bigger water: a receiver carries at least (nearly) all of
    # the drainage that reaches it. A reach whose line passes close by with
    # less drainage than ours is a side channel, not what we flow into.
    enough = 0.95
    tree = cKDTree(up)
    hits = tree.query_ball_point(down, tol)
    for i, cand in enumerate(hits):
        cand = [k for k in cand if k != i and drainage[k] >= enough * drainage[i]]
        if cand:
            recv[i] = max(cand, key=lambda k: drainage[k])
            kind[i] = 1
    rest = np.where(recv < 0)[0]
    if len(rest) == 0:
        return recv, kind
    sindex = seg.sindex
    for radius, code in ((tol, 2), (bridge_m, 3)):
        if radius is None or radius <= 0:
            continue
        rest = np.where(recv < 0)[0]
        if len(rest) == 0:
            break
        pts = gpd.GeoSeries([Point(*down[i]) for i in rest], crs=seg.crs)
        inp, tr = sindex.query(pts.buffer(radius), predicate="intersects")
        best: dict[int, tuple[float, int]] = {}
        for a, b in zip(inp, tr):
            i = rest[a]
            if b == i or branch[b] == branch[i]:
                continue
            if not drainage[b] >= enough * drainage[i]:
                continue
            d = seg.geometry.iloc[b].distance(pts.iloc[a])
            if d <= radius and (i not in best or (d, -drainage[b]) <
                                (best[i][0], -drainage[best[i][1]])):
                best[i] = (d, b)
        for i, (_, b) in best.items():
            recv[i] = b
            kind[i] = code
    return recv, kind


def _break_cycles(recv: np.ndarray, drainage: np.ndarray) -> list[int]:
    """Two reaches that end at the same confluence, with nothing traced
    below it, each lie on the other's line and receive each other; a ring
    in a wide wash's skeleton does the same. Every reach must reach an
    outlet, so each cycle is cut at its biggest reach, which becomes the
    outlet. Edits ``recv`` in place; returns the reaches cut."""
    n = len(recv)
    state = np.zeros(n, dtype=np.int8)        # 0 new, 1 on this walk, 2 settled
    cut: list[int] = []
    for s in range(n):
        if state[s]:
            continue
        walk: list[int] = []
        cur = s
        while cur >= 0 and state[cur] == 0:
            state[cur] = 1
            walk.append(cur)
            cur = recv[cur]
        if cur >= 0 and state[cur] == 1:       # closed on this walk
            ring = walk[walk.index(cur):]
            top = max(ring, key=lambda k: (drainage[k], -k))
            recv[top] = -1
            cut.append(top)
        for k in walk:
            state[k] = 2
    return cut


def trace_streams(segments, cell_m: float | None = None, snap_cells: float = 2.0,
                  bridge_m: float = 400.0):
    """Give every reach a ``stream_id``: the main stem of each stream, from
    its outlet up the larger drainage at every junction, and each tributary
    its own. Also ``dist_to_mouth_km`` (along the stream from its mouth to
    the reach's downstream end), ``flows_into`` (the receiving stream) and
    ``stream_km2_at_mouth``. Returns ``(layer, info)``; ``info`` counts how
    the reaches were joined (at a node, onto a line, across a gap) and the
    outlets found. A reach's downstream end joins the reach at the same node
    (within ``snap_cells`` cells), else the reach whose line passes that
    close, else, across a gap of up to ``bridge_m`` (the skeleton tracing
    dropped short connecting paths before v0.40), the nearest reach; in
    every case one carrying at least 95% of the reach's own drainage."""
    import pandas as pd

    seg = _read(segments)
    if "drainage_km2" not in seg.columns:
        raise GeolensError("tracing streams needs 'drainage_km2' on the reaches.")
    seg = seg[seg.geometry.notna() & ~seg.geometry.is_empty].reset_index(drop=True)
    n = len(seg)
    if n == 0:
        raise GeolensError("tracing streams: the reach layer is empty.")
    if cell_m is None:
        cell_m = float(pd.to_numeric(seg["dem_cell_m"], errors="coerce").median()) \
            if "dem_cell_m" in seg.columns else 10.0
        if not np.isfinite(cell_m) or cell_m <= 0:
            cell_m = 10.0
    tol = snap_cells * cell_m
    drainage = pd.to_numeric(seg["drainage_km2"], errors="coerce") \
        .fillna(0.0).to_numpy(float)
    length = pd.to_numeric(seg["length_m"], errors="coerce").to_numpy(float) \
        if "length_m" in seg.columns else seg.geometry.length.to_numpy(float)
    starts, ends = _endpoints(seg.geometry)
    die = _downstream_is_end(seg, starts, ends, drainage, tol)
    down = np.where(die[:, None], ends, starts)
    up = np.where(die[:, None], starts, ends)
    recv, kind = _receivers(seg, down, up, drainage, tol, bridge_m)
    cut = _break_cycles(recv, drainage)
    kind[cut] = 0

    tribs: list[list[int]] = [[] for _ in range(n)]
    for i, r in enumerate(recv):
        if r >= 0:
            tribs[r].append(i)
    km = np.where(np.isfinite(length), length, 0.0) / 1000.0
    outlets = sorted(np.where(recv < 0)[0], key=lambda k: -drainage[k])
    # The network above each reach, in km: a breadth-first order from the
    # outlets, summed from the headwaters down. (A ring is reached by none
    # of them and keeps 0.)
    order: list[int] = []
    seen = np.zeros(n, dtype=bool)
    todo: deque[int] = deque(outlets)
    seen[outlets] = True
    while todo:
        i = todo.popleft()
        order.append(i)
        for k in tribs[i]:
            if not seen[k]:
                seen[k] = True
                todo.append(k)
    upstream_km = np.zeros(n)
    for i in reversed(order):
        if recv[i] >= 0:
            upstream_km[recv[i]] += upstream_km[i] + km[i]

    def continuation(cands: list[int], r: int) -> list[int]:
        """The reaches joining ``r``, main stem first: the one with the
        most drainage, but among those within 10% of it the one with the
        longest network above -- a wide wash's skeleton has parallel
        threads and short side branches that inherit the river's
        accumulation at the junction cell, and the river is the one that
        goes on. A lone candidate carrying the river's whole drainage while
        the others together account for most of it is such a side branch
        at a real confluence, and goes last."""
        big = [k for k in cands if drainage[k] >= 0.95 * drainage[r]]
        demoted: set[int] = set()
        if len(big) == 1 and len(cands) > 1 and sum(
                drainage[k] for k in cands if k != big[0]) >= 0.5 * drainage[r]:
            demoted = {big[0]}
        top = max(drainage[k] for k in cands if k not in demoted)
        return sorted(cands, key=lambda k: (k in demoted, drainage[k] < 0.9 * top,
                                            -upstream_km[k], -drainage[k]))

    stream_id = np.full(n, -1, dtype=int)
    dist = np.full(n, np.nan)
    flows_into = np.full(n, -1, dtype=int)
    used = drainage.copy()                # drainage with the inherited values fixed
    inherited = np.zeros(n, dtype=bool)
    parallel = np.zeros(n, dtype=bool)

    def walk(start: int) -> tuple[list[int], list[tuple[int, int]]]:
        """The stem from ``start`` up the main continuation at every
        junction, and the side tributaries met on the way, each with the
        reach that went on as the stem beside it."""
        path, side, seen_here = [], [], set()
        cur = start
        while cur >= 0 and stream_id[cur] < 0 and cur not in seen_here:
            path.append(cur)
            seen_here.add(cur)
            if not tribs[cur]:
                break
            t = continuation(tribs[cur], cur)
            side.extend((k, t[0]) for k in t[1:])
            cur = t[0]
        return path, side

    queue: deque[tuple[int, int]] = deque((o, -1) for o in outlets)
    sid = 0
    while queue:
        start, sibling = queue.popleft()
        if stream_id[start] >= 0:
            continue
        path, side = walk(start)
        r = recv[start]
        if r >= 0 and stream_id[r] >= 0:
            # A tributary. Its mouth reach shares the junction cell with the
            # river, and the segment raster gave that cell to one of them:
            # a mouth reach carrying (nearly) the river's drainage inherited
            # it, and its own drainage is the reach above. A whole path
            # carrying what the stem beside it carries is a parallel thread
            # of the river (a wide wash's skeleton has them), and part of it.
            if sibling >= 0 and all(drainage[k] >= 0.9 * drainage[sibling]
                                    for k in path):
                this, into, d = stream_id[r], flows_into[r], dist[r]
                parallel[path] = True
            else:
                sid += 1
                this, into, d = sid, stream_id[r], 0.0
                if drainage[path[0]] >= 0.9 * drainage[r]:
                    inherited[path[0]] = True
                    above = [drainage[k] for k in (path[1:2] or tribs[path[0]])]
                    used[path[0]] = max(above) if above else np.nan
        else:
            sid += 1
            this, into, d = sid, -1, 0.0
        for k in path:
            stream_id[k] = this
            flows_into[k] = into
            dist[k] = d
            d += km[k]
        queue.extend(side)
    # A loop in the network can leave reaches unreached (every reach on a
    # ring receives from another): each becomes a stream of its own.
    for i in np.where(stream_id < 0)[0]:
        if stream_id[i] < 0:
            sid += 1
            path, side = walk(i)
            d = 0.0
            for k in path:
                stream_id[k] = sid
                flows_into[k] = -1
                dist[k] = d
                d += km[k]
    mouth = pd.Series(used).groupby(stream_id).max()
    seg["stream_id"] = stream_id
    seg["dist_to_mouth_km"] = np.round(dist, 2)
    seg["flows_into"] = np.where(flows_into > 0, flows_into, np.nan)
    seg["stream_km2_at_mouth"] = np.round(mouth.reindex(stream_id).to_numpy(), 2)
    seg["drainage_km2_used"] = np.round(used, 4)
    seg["drainage_inherited"] = inherited
    seg["parallel_thread"] = parallel
    seg["flow_reversed"] = ~die          # the line was digitised uphill
    info = {"streams": int(sid), "outlets": int(len(outlets)),
            "joined_at_node": int((kind == 1).sum()),
            "joined_on_line": int((kind == 2).sum()),
            "bridged": int((kind == 3).sum()),
            "snap_m": float(tol), "bridge_m": float(bridge_m or 0),
            "reversed": int((~die).sum()),
            "inherited_drainage": int(inherited.sum()),
            "parallel_threads": int(parallel.sum())}
    return seg, info


# -- major streams ---------------------------------------------------------------------


def _weighted_mean(values, weights):
    v = np.asarray(values, float)
    w = np.asarray(weights, float)
    ok = np.isfinite(v) & np.isfinite(w) & (w > 0)
    return float((v[ok] * w[ok]).sum() / w[ok].sum()) if ok.any() else np.nan


def major_streams(segments, min_drainage_km2: float = 25.0):
    """One line per stream whose drainage at the mouth is at least
    ``min_drainage_km2``, dissolved from a reach layer that
    :func:`trace_streams` has labelled, with the stream's length, gradient,
    stream power, dominant landscape type, fan and unconfined shares, and
    the HUC12 at its mouth."""
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import MultiLineString
    from shapely.ops import linemerge, unary_union

    seg = _read(segments)
    if "stream_id" not in seg.columns:
        seg, _ = trace_streams(seg)
    big = seg[pd.to_numeric(seg["stream_km2_at_mouth"], errors="coerce")
              >= float(min_drainage_km2)]
    landscape = "landscape_type" if "landscape_type" in seg.columns else \
        ("valley_type" if "valley_type" in seg.columns else None)
    rows = []
    for sid, g in big.groupby("stream_id"):
        km = pd.to_numeric(g["length_m"], errors="coerce").to_numpy(float) / 1000.0 \
            if "length_m" in g.columns else g.geometry.length.to_numpy(float) / 1000.0
        mouth = g.loc[g["dist_to_mouth_km"].idxmin()]
        mg = mouth.geometry
        if mg.geom_type == "MultiLineString":
            ends = (mg.geoms[0].coords[0], mg.geoms[-1].coords[-1])
        else:
            ends = (mg.coords[0], mg.coords[-1])
        mouth_xy = ends[0] if bool(mouth.get("flow_reversed", False)) else ends[1]
        geom = unary_union(list(g.geometry))
        if geom.geom_type == "MultiLineString":
            geom = linemerge(geom)
        if geom.geom_type == "LineString":
            geom = MultiLineString([geom])
        elif geom.geom_type != "MultiLineString":
            geom = MultiLineString([p for p in getattr(geom, "geoms", [geom])
                                    if p.geom_type == "LineString"])
        d_col = "drainage_km2_used" if "drainage_km2_used" in g.columns \
            else "drainage_km2"
        rec: dict[str, Any] = {
            "stream_id": int(sid),
            "drainage_km2": round(float(pd.to_numeric(g[d_col]).max()), 2),
            "length_km": round(float(np.nansum(km)), 2),
            "n_reaches": int(len(g)),
            "flows_into": (int(mouth["flows_into"])
                           if pd.notna(mouth.get("flows_into")) else None),
            "gradient_mean": round(_weighted_mean(g["gradient"], km), 4)
            if "gradient" in g.columns else None,
            "huc12_mouth": (str(mouth["huc12"]) if "huc12" in g.columns
                            and pd.notna(mouth["huc12"]) else None),
            "mouth_x": round(float(mouth_xy[0]), 1),
            "mouth_y": round(float(mouth_xy[1]), 1),
            "name": None,
        }
        for col, out in (("stream_power_w_m", "stream_power_mean_w_m"),
                         ("unit_stream_power_w_m2", "unit_power_mean_w_m2")):
            if col in g.columns:
                rec[out] = round(_weighted_mean(g[col], km), 2)
        if "unit_stream_power_w_m2" in g.columns:
            v = pd.to_numeric(g["unit_stream_power_w_m2"], errors="coerce")
            rec["unit_power_max_w_m2"] = round(float(v.max()), 2) \
                if v.notna().any() else None
        if landscape:
            share = pd.Series(km).groupby(g[landscape].fillna("unresolved")
                                          .to_numpy()).sum()
            share = share / share.sum() if share.sum() else share
            rec["landscape_main"] = str(share.idxmax()) if len(share) else None
            rec["landscape_main_share"] = round(float(share.max()), 3) \
                if len(share) else None
            on_fan = g[landscape].fillna("").str.contains("-AF|-IF", regex=True)
            rec["fan_share"] = round(float(km[on_fan.to_numpy()].sum()
                                           / km.sum()), 3) if km.sum() else None
        if "confinement" in g.columns:
            unc = (g["confinement"] == "unconfined").to_numpy()
            rec["unconfined_share"] = round(float(km[unc].sum() / km.sum()), 3) \
                if km.sum() else None
        rec["geometry"] = geom
        rows.append(rec)
    if not rows:
        return gpd.GeoDataFrame(columns=["stream_id", "drainage_km2", "geometry"],
                                geometry="geometry", crs=seg.crs)
    out = gpd.GeoDataFrame(rows, geometry="geometry", crs=seg.crs)
    return out.sort_values("drainage_km2", ascending=False).reset_index(drop=True)


def name_streams(streams, flowlines, within_m: float = 150.0,
                 min_share: float = 0.5):
    """Give the major streams their NHD names and the NHD's drainage at the
    mouth. For each stream, the share of its length lying within
    ``within_m`` of each GNIS-named flowline is measured -- with the
    stream's mouth zone (twice ``within_m`` from the mouth) left out, so a
    tributary does not take the river's name where it joins it -- and the
    name covering the most is taken when it covers at least ``min_share``
    (``name``, ``gnis_id``, ``name_share``; a stream the NHD names in two
    parts keeps the longer name, the other in ``other_names``). The
    flowline nearest the mouth (within ``within_m``) gives
    ``nhd_totdasqkm`` (its total drainage area), ``nhd_streamorde`` and
    ``nhd_qema_cfs``, and ``drainage_ratio`` is the traced drainage over
    the NHD's: near 1 where the two agree, well under 1 where a river
    enters from outside the project's HUC8s (its basin is not in the DEM).
    Returns the layer."""
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import Point

    ms = _read(streams)
    fl = _read(flowlines)
    for col in ("name", "gnis_id", "name_share", "other_names", "nhd_totdasqkm",
                "nhd_streamorde", "nhd_qema_cfs", "drainage_ratio"):
        ms[col] = None
    if len(ms) == 0 or len(fl) == 0:
        return ms
    if fl.crs != ms.crs:
        fl = fl.to_crs(ms.crs)
    fl = fl[fl.geometry.notna() & ~fl.geometry.is_empty].reset_index(drop=True)
    fl.columns = [c.lower() for c in fl.columns]
    named = fl[fl.get("gnis_name", pd.Series(dtype=object)).notna()] \
        if "gnis_name" in fl.columns else fl.iloc[0:0]
    named_idx = named.sindex if len(named) else None
    all_idx = fl.sindex
    names, ids, shares, others = [], [], [], []
    tot, order, qema, ratio = [], [], [], []
    have_mouth = {"mouth_x", "mouth_y"} <= set(ms.columns)
    for row in ms.itertuples():
        geom = row.geometry
        if geom.geom_type == "MultiLineString":
            ends = (geom.geoms[0].coords[0], geom.geoms[-1].coords[-1])
        else:
            ends = (geom.coords[0], geom.coords[-1])
        if have_mouth and pd.notna(row.mouth_x) and pd.notna(row.mouth_y):
            mouths = [Point(float(row.mouth_x), float(row.mouth_y))]
        else:
            mouths = [Point(e[:2]) for e in ends]
        name = gid = other = None
        share = None
        if named_idx is not None:
            # the stream without its mouth zone, where it meets what it
            # joins; a short stream is measured whole
            body = geom
            if len(mouths) == 1 and geom.length > 4 * within_m:
                body = geom.difference(mouths[0].buffer(2 * within_m))
            length = float(body.length) or 1.0
            hits = named.iloc[named_idx.query(body.buffer(within_m),
                                              predicate="intersects")]
            cover: dict[tuple[str, str], float] = {}
            for h in hits.itertuples():
                part = body.intersection(h.geometry.buffer(within_m))
                if part.is_empty:
                    continue
                key = (str(h.gnis_name), str(getattr(h, "gnis_id", "") or ""))
                cover[key] = cover.get(key, 0.0) + float(part.length)
            if cover:
                ranked = sorted(cover.items(), key=lambda kv: -kv[1])
                (best, best_id), best_len = ranked[0]
                sh = min(best_len / length, 1.0)
                if sh >= min_share:
                    name, gid, share = best, best_id or None, round(sh, 3)
                    rest = [k[0] for k, v in ranked[1:]
                            if v / length >= min_share / 2 and k[0] != best]
                    other = "; ".join(rest) if rest else None
        mouth = None
        for p in mouths:
            near = fl.iloc[all_idx.query(p.buffer(within_m), predicate="intersects")]
            if len(near):
                d = near.geometry.distance(p)
                k = d.idxmin()
                if d[k] <= within_m:
                    f = near.loc[k]
                    t = pd.to_numeric(f.get("totdasqkm"), errors="coerce")
                    if mouth is None or (pd.notna(t) and (mouth[0] is None
                                                          or t > mouth[0])):
                        mouth = (float(t) if pd.notna(t) else None,
                                 f.get("streamorde"), f.get("qema"))
        names.append(name); ids.append(gid); shares.append(share); others.append(other)
        if mouth:
            tot.append(mouth[0])
            order.append(int(mouth[1]) if pd.notna(mouth[1]) else None)
            qema.append(round(float(mouth[2]), 1) if pd.notna(mouth[2]) else None)
            ratio.append(round(float(row.drainage_km2) / mouth[0], 2)
                         if mouth[0] else None)
        else:
            tot.append(None); order.append(None); qema.append(None); ratio.append(None)
    ms["name"], ms["gnis_id"], ms["name_share"], ms["other_names"] = names, ids, shares, others
    ms["nhd_totdasqkm"], ms["nhd_streamorde"] = tot, order
    ms["nhd_qema_cfs"], ms["drainage_ratio"] = qema, ratio
    return ms


# -- the HUC12 sensitivity table ----------------------------------------------------------


def huc12_power(segments) -> "pd.DataFrame":
    """Per HUC12: length-weighted mean and 90th percentile of unit stream
    power, and the mean total stream power, from a reach layer with
    ``huc12`` and the stream power columns."""
    import pandas as pd

    seg = _read(segments)
    if "huc12" not in seg.columns or "unit_stream_power_w_m2" not in seg.columns:
        return pd.DataFrame()
    seg = seg[seg["huc12"].notna()].copy()
    seg["huc12"] = seg["huc12"].astype(str)
    km = pd.to_numeric(seg["length_m"], errors="coerce") / 1000.0 \
        if "length_m" in seg.columns else seg.geometry.length / 1000.0
    rows = {}
    for code, idx in seg.groupby("huc12").groups.items():
        g = seg.loc[idx]
        w = km.loc[idx].to_numpy(float)
        u = pd.to_numeric(g["unit_stream_power_w_m2"], errors="coerce")
        rows[code] = {
            "unit_power_mean_w_m2": round(_weighted_mean(u, w), 2),
            "unit_power_p90_w_m2": round(float(u.quantile(0.9)), 2)
            if u.notna().any() else None,
            "stream_power_mean_w_m": round(_weighted_mean(
                pd.to_numeric(g["stream_power_w_m"], errors="coerce"), w), 1),
        }
    t = pd.DataFrame.from_dict(rows, orient="index")
    t.index.name = "huc12"
    return t


def _weights(weights) -> dict[str, float]:
    if weights is None:
        return dict(DEFAULT_WEIGHTS)
    if isinstance(weights, (str, Path)):
        weights = json.loads(Path(weights).read_text(encoding="utf-8"))
    out = {}
    for k, v in dict(weights).items():
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise GeolensError(f"sensitivity weight for {k!r} is not a number.")
        if v < 0:
            raise GeolensError(f"sensitivity weight for {k!r} is negative.")
        if v > 0:
            out[str(k)] = v
    if not out:
        raise GeolensError("every sensitivity weight is zero.")
    return out


def huc12_sensitivity(summary, weights: dict | str | Path | None = None,
                      power: "pd.DataFrame | None" = None,
                      min_weight_share: float = 0.5):
    """Rank the HUC12s of ``summary`` (``huc12_summary.csv`` or its table)
    by sensitivity: each weighted component becomes a percentile rank
    (0 = least, 1 = most, among these HUC12s), the score is the weighted
    mean of the ranks a HUC12 has, and the class cuts the scored HUC12s
    into thirds. A HUC12 with less than ``min_weight_share`` of the weight
    measured is ``insufficient data``. ``weights`` maps summary columns to
    weights (default ``DEFAULT_WEIGHTS``; a JSON file does too); any
    column of the summary can be one. Returns ``(table, info)``."""
    import pandas as pd

    if isinstance(summary, (str, Path)):
        t = pd.read_csv(summary, dtype={"huc12": str}).set_index("huc12")
    else:
        t = summary.copy()
        if "huc12" in t.columns:
            t = t.set_index("huc12")
    t.index = t.index.astype(str)
    if power is not None and len(power):
        p = power.copy()
        p.index = p.index.astype(str)
        for col in p.columns:
            t[col] = p[col].reindex(t.index)
    form = [c for c in t.columns if c.startswith("form_") and c.endswith("_km")]
    if form and "form_unstable_km" in t.columns:
        total = t[form].sum(axis=1)
        t["form_unstable_share"] = (t["form_unstable_km"].fillna(0.0) / total) \
            .where(total > 0)
    w = _weights(weights)
    used, dropped = {}, {}
    ranks = pd.DataFrame(index=t.index)
    for col, weight in w.items():
        if col not in t.columns:
            dropped[col] = "not in the summary"
            continue
        v = pd.to_numeric(t[col], errors="coerce")
        if v.notna().sum() < 3:
            dropped[col] = "fewer than 3 HUC12s have it"
            continue
        if v.nunique(dropna=True) < 2:
            dropped[col] = "the same in every HUC12"
            continue
        ranks[f"rank_{col}"] = v.rank(pct=True, method="average")
        used[col] = weight
    if not used:
        raise GeolensError("No sensitivity component could be ranked: "
                           + "; ".join(f"{k} ({v})" for k, v in dropped.items()))
    total_w = sum(used.values())
    num = pd.Series(0.0, index=t.index)
    have = pd.Series(0.0, index=t.index)
    for col, weight in used.items():
        r = ranks[f"rank_{col}"]
        num += r.fillna(0.0) * weight
        have += r.notna() * weight
    score = (num / have).where(have > 0)
    share = have / total_w
    out = pd.DataFrame(index=t.index)
    for col in ("name", "km2", "in_boundary_share"):
        if col in t.columns:
            out[col] = t[col]
    out["sensitivity_score"] = score.round(3)
    out["weight_measured"] = share.round(2)
    enough = share >= min_weight_share
    cls = pd.Series("insufficient data", index=t.index, dtype=object)
    scored = score[enough & score.notna()]
    if len(scored) >= 3:
        q = scored.rank(pct=True, method="average")
        cls.loc[scored.index] = np.where(q > 2 / 3, "high",
                                         np.where(q > 1 / 3, "moderate", "low"))
    elif len(scored):
        cls.loc[scored.index] = "moderate"
    out["sensitivity_class"] = cls

    def drivers(code):
        r = ranks.loc[code].dropna().sort_values(ascending=False)
        top = [(c[5:], v) for c, v in r.items() if v >= 2 / 3][:3]
        return ", ".join(f"{LABELS.get(c, c)} {v:.2f}" for c, v in top) or None

    out["sensitivity_drivers"] = [drivers(c) for c in out.index]
    for col in used:
        out[f"rank_{col}"] = ranks[f"rank_{col}"].round(3)
    for col in used:
        out[col] = t[col]
    out.index.name = "huc12"
    info = {"weights": {k: round(v / total_w, 3) for k, v in used.items()},
            "asked": w, "dropped": dropped, "huc12s": int(len(out)),
            "scored": int(len(scored)),
            "classes": {c: int((cls == c).sum()) for c in (*CLASSES, "insufficient data")}}
    return out, info


# -- the whole step ------------------------------------------------------------------------


def synthesize(out_dir: str | Path, valley_types, curve="arid_sw_az",
               merged=None, summary_csv=None, hucs=None,
               min_drainage_km2: float = 25.0, weights=None,
               cell_m: float | None = None, flowlines=None) -> dict[str, Any]:
    """Stream power and stream ids on the reaches, the major streams layer
    and the HUC12 sensitivity table, written to ``out_dir``.

    ``valley_types`` is the reach layer the streams are traced on (the whole
    HUC12 set, so streams are whole); ``merged`` the layer clipped to the
    boundary that gets the new columns by ``seg_id`` and is rewritten (when
    None, ``valley_types`` itself is written as ``reaches_power.gpkg``).
    ``summary_csv`` is ``huc12_summary.csv``; ``hucs`` the HUC12 polygons
    (for ``huc12_sensitivity.gpkg``); ``flowlines`` the NHDPlus HR
    flowlines (``wsdata.nhd_flowlines``) that name the major streams."""
    import geopandas as gpd
    import pandas as pd

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {}

    note("synthesis: tracing streams")
    full, info = trace_streams(valley_types, cell_m=cell_m)
    result["tracing"] = info
    note("synthesis: stream power")
    full = stream_power(full, curve)
    result["power_basis"] = str(full["power_basis"].iloc[0]) if len(full) else ""
    new_cols = ["stream_id", "dist_to_mouth_km", "flows_into",
                "stream_km2_at_mouth", "drainage_km2_used", "drainage_inherited",
                "parallel_thread", "flow_reversed", "q_bankfull_m3s",
                "stream_power_w_m", "unit_stream_power_w_m2", "power_basis"]

    if merged is not None:
        m = _read(merged)
        m = m.drop(columns=[c for c in new_cols if c in m.columns])
        m = m.merge(full[["seg_id", *new_cols]], on="seg_id", how="left")
        m = gpd.GeoDataFrame(m, geometry="geometry", crs=full.crs)
        path = Path(merged) if isinstance(merged, (str, Path)) \
            else out_dir / "valley_types.gpkg"
        m.to_file(str(path), driver="GPKG", layer="valley_types")
        result["reaches"] = str(path)
        inside = m
    else:
        path = out_dir / "reaches_power.gpkg"
        full.to_file(str(path), driver="GPKG", layer="reaches_power")
        result["reaches"] = str(path)
        inside = full

    note("synthesis: major streams")
    ms = major_streams(full, min_drainage_km2)
    if flowlines is not None and Path(str(flowlines)).exists() and len(ms):
        note("synthesis: naming the major streams from the NHD")
        try:
            ms = name_streams(ms, flowlines)
            result["named"] = int(ms["name"].notna().sum())
            r = pd.to_numeric(ms["drainage_ratio"], errors="coerce")
            result["drainage_ratio_median"] = round(float(r.median()), 2) \
                if r.notna().any() else None
            result["entering_from_outside"] = int((r < 0.5).sum())
        except Exception as exc:                             # noqa: BLE001
            result["naming_failed"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    ms_path = out_dir / "major_streams.gpkg"
    ms.to_file(str(ms_path), driver="GPKG", layer="major_streams")
    result["major_streams"] = str(ms_path)
    result["major_streams_n"] = int(len(ms))
    result["min_drainage_km2"] = float(min_drainage_km2)
    if len(ms):
        result["largest"] = [
            {"stream_id": int(r.stream_id), "drainage_km2": float(r.drainage_km2),
             "length_km": float(r.length_km),
             "landscape_main": getattr(r, "landscape_main", None),
             "huc12_mouth": getattr(r, "huc12_mouth", None),
             "name": getattr(r, "name", None),
             "nhd_totdasqkm": getattr(r, "nhd_totdasqkm", None)}
            for r in ms.head(10).itertuples()]

    power = huc12_power(inside)
    if len(power):
        power.to_csv(out_dir / "huc12_power.csv")
        result["huc12_power"] = str(out_dir / "huc12_power.csv")

    if summary_csv and Path(summary_csv).exists():
        note("synthesis: HUC12 sensitivity")
        try:
            table, sinfo = huc12_sensitivity(summary_csv, weights, power=power)
        except GeolensError as exc:
            # Too few HUC12s to rank (a ranch), or nothing measured yet.
            result["sensitivity"] = {"skipped": str(exc)}
            return result
        csv = out_dir / "huc12_sensitivity.csv"
        table.to_csv(csv)
        result["sensitivity_csv"] = str(csv)
        result["sensitivity"] = sinfo
        (out_dir / "sensitivity_weights.json").write_text(
            json.dumps({"weights": sinfo["weights"], "labels": {
                k: LABELS.get(k, k) for k in sinfo["weights"]},
                "note": "Edit the weights and run the project again "
                        "(run_project(sensitivity_weights=...), or the "
                        "toolbox's Sensitivity weights file) to re-rank."},
                       indent=2), encoding="utf-8")
        if hucs is not None:
            h = _read(hucs)
            h["huc12"] = h["huc12"].astype(str)
            g = gpd.GeoDataFrame(
                table.reset_index().merge(h[["huc12", "geometry"]], on="huc12",
                                          how="left"),
                geometry="geometry", crs=h.crs)
            gpkg = out_dir / "huc12_sensitivity.gpkg"
            g.to_file(str(gpkg), driver="GPKG", layer="huc12_sensitivity")
            result["sensitivity_layer"] = str(gpkg)
        top = table[table["sensitivity_class"] == "high"] \
            .sort_values("sensitivity_score", ascending=False).head(10)
        result["most_sensitive"] = [
            {"huc12": str(i), "name": str(r.get("name", "")),
             "score": float(r["sensitivity_score"]),
             "drivers": r.get("sensitivity_drivers")}
            for i, r in top.iterrows()]
    return result
