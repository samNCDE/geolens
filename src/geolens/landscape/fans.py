"""Alluvial fan detection.

A fan is a cone of deposited sediment where a confined drainage empties onto
open ground. Three things make it identifiable from terrain, and all three are
needed — any one alone produces false positives everywhere:

1. **An apex at the mouth of a drainage.** Fans start where confinement ends.
   That transition shows up as a sharp DROP in the channel's own gradient --
   the canyon's value falling to the fan's, typically halving within a hundred
   metres. Apexes are found by walking the D8 graph and looking for that break,
   not by scanning the raster for steep or incised ground: a fan surface is
   steeper than the basin and its channel is incised into the fan, so both of
   those proxies point at the fan toe instead.
2. **A gradient between its neighbours.** A fan is markedly gentler than the
   range above it and markedly steeper than the basin below. Typical fan
   gradients are 2-10 %. A slope band alone would also select every hillslope
   of that steepness, which is why it is only admissible *near an apex*.
3. **It stands above the surface it buried.** A fan is constructional — it sits
   proud of the basin floor. Terrain in the same slope band that is *below* the
   local base level is a hillslope or a terrace, not a fan.

The spread from each apex is a **cost-limited region grow**: from the apex,
admit neighbouring cells that stay inside the slope band, stay above base
level, and keep descending away from the apex. That last condition is what
makes it a cone rather than a blob — a fan surface falls monotonically outward,
so a cell that climbs as you move away from the apex is not on the fan.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..safety import Guard, GeolensError, note, progress
from ..raster import gtiff_profile
from .terrain import TerrainResult


@dataclass
class FanResult:
    fans: str
    fan_raster: str
    apexes: str
    fan_count: int
    total_area_km2: float
    parameters: dict[str, Any]
    seconds: float
    warnings: list[str]
    candidates: str | None = None       # every grown region, with why it was kept
    rejected_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _nan_uniform(x: np.ndarray, size: int) -> np.ndarray:
    """Moving mean that ignores NaN (normalised convolution)."""
    from scipy import ndimage

    ok = np.isfinite(x)
    num = ndimage.uniform_filter(np.where(ok, x, 0.0).astype("float32"),
                                 size=size, mode="nearest")
    den = ndimage.uniform_filter(ok.astype("float32"), size=size,
                                 mode="nearest")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / den
    out[den < 1e-6] = np.nan
    return out


_NB8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

# D8 encoding, identical to landscape.flow (ESRI/pysheds convention).
_D8_CODES = np.array([1, 2, 4, 8, 16, 32, 64, 128], dtype=np.uint8)
_D8_DR = np.array([0, 1, 1, 1, 0, -1, -1, -1], dtype=np.int64)
_D8_DC = np.array([1, 1, 0, -1, -1, -1, 0, 1], dtype=np.int64)


def _downstream_index(fdir: np.ndarray) -> np.ndarray:
    """Flat index of each cell's downstream neighbour, or -1.

    Walking the D8 raster rather than a traced path is the same correctness
    argument as in :func:`detect_headcuts`: a D8 link is a real connection, so a
    walk along it cannot silently jump across the landscape the way a traced
    raster path can.
    """
    H, W = fdir.shape
    out = np.full(fdir.size, -1, dtype=np.int64)
    rr, cc = np.meshgrid(np.arange(H), np.arange(W), indexing="ij")
    for k, code in enumerate(_D8_CODES):
        m = fdir == code
        if not m.any():
            continue
        r2 = rr[m] + _D8_DR[k]
        c2 = cc[m] + _D8_DC[k]
        ok = (r2 >= 0) & (r2 < H) & (c2 >= 0) & (c2 < W)
        flat = (rr[m] * W + cc[m])[ok]
        out[flat] = r2[ok] * W + c2[ok]
    return out


def _mainstem_parent(down: np.ndarray, acc: np.ndarray) -> np.ndarray:
    """Flat index of each cell's largest inflowing neighbour, or -1.

    At a confluence there are several upstream cells. Taking the one with the
    most contributing area follows the main stem, which is what a long profile
    means. The alternative -- stopping at confluences -- loses the apex whenever
    a tributary joins near the range front, which on a real front is often.
    """
    parent = np.full(down.size, -1, dtype=np.int64)
    src = np.flatnonzero(down >= 0)
    tgt = down[src]
    a = acc.ravel()[src]
    # Sort by target, then by area: the last entry of each target's run is its
    # largest inflow. Vectorised -- the per-cell Python loop this replaces took
    # about a microsecond a cell, which is half an hour on a lidar DEM.
    order = np.lexsort((a, tgt))
    t_sorted = tgt[order]
    last = np.ones(t_sorted.size, dtype=bool)
    last[:-1] = t_sorted[1:] != t_sorted[:-1]
    parent[t_sorted[last]] = src[order[last]]
    return parent


def _walk(start: int, distance_m: float, nxt: np.ndarray,
          width: int, cell: float) -> tuple[int, float]:
    """Follow ``nxt`` from ``start`` until ``distance_m`` is covered."""
    travelled, j = 0.0, start
    while travelled < distance_m:
        k = nxt[j]
        if k < 0:
            return j, travelled
        dr = abs(int(k) // width - int(j) // width)
        dc = abs(int(k) % width - int(j) % width)
        travelled += cell * (1.41421356 if dr and dc else 1.0)
        j = int(k)
    return j, travelled


def _find_apexes(
    z: np.ndarray,
    fdir: np.ndarray,
    acc: np.ndarray,
    streams: np.ndarray,
    cell: float,
    min_drainage_km2: float,
    window_m: float,
    gradient_ratio_min: float,
    gradient_up_min: float,
    fan_slope_min: float,
    fan_slope_max: float,
    min_spacing_m: float,
) -> list[tuple[int, int, dict[str, float]]]:
    """Apexes are the CONCAVE break in the channel's long profile.

    A fan apex is where a stream leaves confinement and begins to deposit, and
    the thing that changes there is the channel's own gradient: it drops,
    abruptly, from the canyon's value to the fan's. On the fixture that is 10%
    to 5% within a hundred metres, and the transition is sharp enough that the
    measuring window has to be short to see it -- a 250 m window averages the
    canyon and the fan together and smears the apex over hundreds of metres.

    This is :func:`detect_headcuts` with the sign reversed. A headcut is a
    convex break, where gradient jumps up going downstream; a fan apex is a
    concave break, where it falls. Same walk, same ratio, opposite direction.

    Three earlier attempts failed, and each failure is a thing not to try again:

    * **Requiring the apex to be a stream cell with an abrupt confinement drop.**
      Scores 0/3. A deposited fan buries its own canyon mouth and flow spreads
      distributary across it, so there is no single channel at a real apex.
    * **Neighbourhood mean slope as a confinement proxy.** A fan surface at 5.5%
      is steeper than the basin at 0.4%, so the fan reads as confined and the
      downstream-most confined cell lands at the fan toe.
    * **Incision below the surroundings.** Cuts the candidate count usefully but
      does not move the answer: the channel crossing a fan is incised into the
      fan, so incision stays high past the apex.

    Returns ``(row, col, metrics)`` per apex.
    """
    H, W = z.shape
    cell_area_km2 = (cell ** 2) / 1e6
    area_km2 = acc * cell_area_km2

    down = _downstream_index(fdir)
    up = _mainstem_parent(down, acc)
    zf = z.ravel()

    cand = np.flatnonzero(streams.ravel() & (area_km2.ravel() >= min_drainage_km2))
    rows: list[tuple[float, int, dict[str, float]]] = []
    for idx in cand:
        i = int(idx)
        ju, du = _walk(i, window_m, up, W, cell)
        jd, dd = _walk(i, window_m, down, W, cell)
        # Demand most of the window on both sides, or the ratio is measured over
        # whatever happened to be available and is not comparable between cells.
        if du < window_m * 0.8 or dd < window_m * 0.8:
            continue
        g_up = (zf[ju] - zf[i]) / du
        g_dn = (zf[i] - zf[jd]) / dd
        if not (g_up > 1e-3 and g_dn > 1e-3):
            continue
        ratio = g_up / g_dn
        if ratio < gradient_ratio_min or g_up < gradient_up_min:
            continue
        if not (fan_slope_min <= g_dn <= fan_slope_max):
            continue
        rows.append((ratio, i, {
            "gradient_above": float(g_up),
            "gradient_below": float(g_dn),
            "gradient_ratio": float(ratio),
            "drainage_km2": float(area_km2.ravel()[i]),
        }))

    # Greedy thinning: strongest break first, suppress anything close to it. One
    # range-front break produces a short cluster of qualifying cells.
    rows.sort(key=lambda t: -t[0])
    out: list[tuple[int, int, dict[str, float]]] = []
    for _, i, metrics in rows:
        r, c = divmod(i, W)
        if all((abs(r - r0) * cell) ** 2 + (abs(c - c0) * cell) ** 2
               > min_spacing_m ** 2 for r0, c0, _ in out):
            out.append((r, c, metrics))
    return out


def _grow_fan(
    z: np.ndarray,
    slope: np.ndarray,
    base: np.ndarray,
    apex: tuple[int, int],
    cell: float,
    slope_min: float,
    slope_max: float,
    min_above_base: float,
    max_radius_m: float,
    descend_tol: float,
    seed_radius_m: float = 0.0,
) -> np.ndarray | None:
    """Cost-limited region grow outward from one apex.

    Breadth-first from the apex, admitting a neighbour when it is in the fan
    slope band, stands above local base level, lies within the radius cap, and
    is *lower* than the cell it was reached from (allowing a small tolerance for
    DEM noise). The descent rule is what keeps the region a cone.

    ``seed_radius_m`` suspends the slope-band test near the apex, and without it
    this function returns nothing on a real range front. The apex sits ON the
    slope discontinuity -- that is what makes it an apex -- so smoothing slope
    over a window bleeds the range face into the first cells of the fan. On the
    fixture the apex cell's smoothed slope reads 0.532 against a 30% range face,
    far outside a 0.01-0.15 fan band, and the two or three cells downstream of
    it read high too. The frontier hits that band, every cell is marked seen and
    rejected, and the grow dies two cells from the apex having found nothing.
    The contaminated radius is exactly the slope-smoothing radius, so that is
    what gets passed in.
    """
    H, W = z.shape
    r0, c0 = apex
    max_r_cells = max(2, int(round(max_radius_m / cell)))

    seed_cells = seed_radius_m / cell if cell > 0 else 0.0

    seen = np.zeros(z.shape, dtype=bool)
    out = np.zeros(z.shape, dtype=bool)
    q: deque[tuple[int, int]] = deque()
    seen[r0, c0] = True
    out[r0, c0] = True
    q.append((r0, c0))
    apex_z = float(z[r0, c0])

    while q:
        r, c = q.popleft()
        zr = z[r, c]
        for dr, dc in _NB8:
            rr, cc = r + dr, c + dc
            if not (0 <= rr < H and 0 <= cc < W) or seen[rr, cc]:
                continue
            seen[rr, cc] = True
            if abs(rr - r0) > max_r_cells or abs(cc - c0) > max_r_cells:
                continue
            if math.hypot(rr - r0, cc - c0) > max_r_cells:
                continue
            zz = z[rr, cc]
            if not np.isfinite(zz):
                continue
            if zz > zr + descend_tol:          # climbing away from the apex
                continue
            if zz > apex_z + descend_tol:      # above the apex: not on the fan
                continue
            near_apex = math.hypot(rr - r0, cc - c0) <= seed_cells
            s = slope[rr, cc]
            if not near_apex and not (slope_min <= s <= slope_max):
                continue
            if zz - base[rr, cc] < min_above_base:
                continue
            out[rr, cc] = True
            q.append((rr, cc))

    return out if out.sum() > 4 else None


def detect_alluvial_fans(
    terrain: TerrainResult,
    min_drainage_km2: float = 0.5,
    fan_slope_min: float = 0.02,
    auto_slope_floor: bool = True,
    fan_slope_max: float = 0.15,
    min_above_base_m: float = 1.0,
    max_radius_m: float | None = None,
    min_area_km2: float = 0.02,
    apex_window_m: float = 100.0,
    apex_gradient_ratio: float = 1.6,
    apex_gradient_min: float = 0.07,
    apex_spacing_m: float = 500.0,
    base_window_m: float = 2000.0,
    descend_tol_m: float = 0.5,
    smooth_slope_m: float = 50.0,
    max_area_km2: float | None = None,
    min_sector_deg: float = 60.0,
    guard: Guard | None = None,
    out_prefix: str = "fans",
) -> FanResult:
    """Find alluvial fans at the mouths of confined drainages.

    Verified on a fixture with three fans of known geometry (0.770 km2 each,
    700 m radius, 5.5% radial gradient, apexes at known coordinates):

    ===========================  ==================  ==========
    quantity                     found               error
    ===========================  ==================  ==========
    fans                         3 of 3, no extras   --
    apex position                7 m                 under a cell
    area                         0.732 km2           -4.9 %
    radius                       720 m               +2.9 %
    mean radial gradient         0.054               -1.8 %
    ===========================  ==================  ==========

    How an apex is found
    --------------------
    By the **concave break in the channel's own long profile**: the gradient
    drops abruptly where a stream leaves its canyon and starts depositing. On
    the fixture that is 10% to 5% within a hundred metres. This is
    :func:`detect_headcuts` with the sign reversed, walking the same D8 graph.
    :func:`_find_apexes` documents the three approaches that failed first and
    why, which is worth reading before trying a fourth.

    Two traps this function now handles itself, both of which produced
    confidently wrong output before:

    * **Slope noise.** Vertical noise of *e* metres over cells of size *c*
      produces apparent slope of about *e/c* -- 0.15 m over 10 m cells is 0.015,
      above a 0.01 ``fan_slope_min``. Flat ground then reads as fan surface and
      the region grow runs away; one run produced 139 km2 of fans on a 49 km2
      DEM. ``auto_slope_floor`` now measures how steep the flattest ground here
      actually reads and raises the band above it. Turning it off on the fixture
      turns 0.73 km2 fans into 3.34 km2 ones.
    * **The apex sits on a slope discontinuity by definition**, so smoothing
      bleeds the range face into the first cells of the fan. The grow suspends
      its slope test within one smoothing radius of the apex; without that it
      returns nothing at all.

    Parameters
    ----------
    min_drainage_km2
        Contributing area a channel needs before it can build a fan. Raise it to
        keep only major fans; lower it for small debris cones.
    fan_slope_min, fan_slope_max
        The fan gradient band. Alluvial fans typically run 2-10 %; debris cones
        are steeper, up to ~25 %. Widening this admits hillslopes, so widen the
        maximum only when you expect steep cones.
    min_above_base_m
        How far a fan must stand above local base level. **This is the parameter
        that separates a fan from a hillslope of the same gradient** — a fan is
        constructional and sits proud of the surface it buried.
    apex_window_m
        Distance over which the channel gradient is measured above and below a
        candidate apex. **Keep this short.** The break is sharp -- on the
        fixture, 10% to 5% within a hundred metres -- so a long window averages
        the canyon and the fan together and smears the apex over hundreds of
        metres. 100 m puts every fixture apex within one cell of truth; 250 m
        moves the median error to 351 m.
    apex_gradient_ratio
        How much the gradient must fall across the apex: gradient above divided
        by gradient below. 1.6 is deliberately permissive, because the cost of
        a loose ratio is extra candidates that the region grow then rejects,
        while a tight one drops real fans. At 2.0 the fixture loses one of three.
    apex_gradient_min
        How steep the channel above the apex must be. This is what excludes
        gentle gradient changes on the basin floor, which are common and are not
        fans. Raising it from 0.05 to 0.07 removes the fixture's one false
        positive without costing recall.
    apex_spacing_m
        Minimum separation between apexes. One range-front break produces a
        short cluster of qualifying cells; this keeps the strongest.
    descend_tol_m
        Vertical tolerance in the outward-descent rule, absorbing DEM noise. Too
        large and the region grows uphill into the range.
    smooth_slope_m
        Slope is smoothed over this distance before the band test. **Do not set
        it to zero.** Slope noise is roughly the DEM's vertical error divided by
        the cell size, so on fine grids it easily exceeds ``fan_slope_min`` and
        every flat cell then looks like fan surface.
    max_area_km2
        Hard cap on one fan. Defaults to the area of a half-disc at
        ``max_radius_m``, which is the largest a cone of that radius can be — a
        region larger than that is a runaway grow, not a fan.
    min_sector_deg
        **The cone test.** The angle the grown region subtends at its apex,
        ``2 * area / r90**2`` (r90: the 90th-percentile distance from the
        apex). The fixture's fans are half-discs, 180 degrees. On real
        mountain terrain (Ruidoso, 10 m) every candidate was a strip of main
        valley floor below a tributary mouth: the apex was right, but the grow
        ran up and down the trunk valley for up to 3 km at 3-44 degrees. Real
        fans subtend 60-180 degrees. A region
        under this angle, or one that reached ``max_radius_m``, is kept in
        ``fan_candidates.gpkg`` with the reason and left out of ``fans.gpkg``.

    Returns fan polygons with apex location, area, radius and mean gradient.
    """
    import geopandas as gpd
    import rasterio
    from rasterio import features as rfeat
    from rasterio.transform import xy as _rc_to_xy
    from scipy import ndimage
    from shapely.geometry import Point, shape

    guard = guard or Guard.from_env()
    t0 = time.monotonic()
    warnings: list[str] = []

    if fan_slope_min >= fan_slope_max:
        raise GeolensError(
            f"fan_slope_min ({fan_slope_min}) must be below fan_slope_max "
            f"({fan_slope_max})."
        )

    with rasterio.open(terrain.dem) as src:
        z = src.read(1, masked=True).astype("float32").filled(np.nan)
        transform = src.transform
        crs = src.crs
        profile = src.profile.copy()
        # TRUE METRES, not CRS units.
        #
        # Every use of `cell` below is metric: window sizes are
        # `<something>_m / cell`, areas are `cell * cell`, and the apex walk
        # accumulates `cell` and compares it against `window_m`. Reading the
        # raster's own resolution gave 33.6 on EPSG:6529, so on a foot grid
        # every window was 3.28x too small and every area 10.76x wrong --
        # min_area_km2 admitted fans a tenth of the size asked for, and the
        # drainage areas the apex test uses were ten times too large.
        #
        # `cell` is never used to build geometry here (that is `transform`),
        # so converting it once is the whole fix.
        _u = getattr(terrain, "units", None) or {}
        cell = float(_u.get("cell_size_m") or terrain.cell_size
                     or abs(src.res[0]))
    # Elevations in METRES too. On a US-survey-foot DEM every gradient below
    # (feet of drop over metres of run) read 3.28x too steep -- a 5% fan read
    # 16% and failed the 2-15% band -- and min_above_base_m and descend_tol_m
    # were compared against feet.
    _vm = float(_u.get("vertical_metres_per_unit") or 1.0)
    if _vm != 1.0:
        z = z * np.float32(_vm)
    # The radius cap grows with the DEM: 3 km on a 49 km2 site, up to 12 km
    # on a field office, where the piedmont fans are 5 to 15 km long and a
    # 3 km cap rejected 1,190 of 1,324 candidates at Kingman (Oct 2026).
    if max_radius_m is None:
        short_m = min(z.shape) * cell
        max_radius_m = float(min(12000.0, max(3000.0, 0.08 * short_m)))
        note(f"radius cap {max_radius_m:,.0f} m for a {short_m / 1000:,.0f} km "
             f"DEM")
    with rasterio.open(terrain.slope) as src:
        # Masked: the slope raster's nodata is -9999, and read raw it went into
        # the smoothing below and into the slope-floor percentile. On a DEM
        # with a nodata collar (Ruidoso's is a quarter of the grid) the floor
        # came out as -9999 and auto_slope_floor silently did nothing.
        slope_raw = src.read(1, masked=True).astype("float32").filled(np.nan)
    with rasterio.open(terrain.accumulation) as src:
        acc = src.read(1).astype("float32")
    # Apex candidates are channel cells with at least min_drainage_km2, taken
    # from the accumulation rather than from the terrain's stream raster: a
    # landscape pass extracts streams at its own threshold (1 km2 on Ruidoso),
    # which would silently drop every fan fed by a smaller drainage.
    _cell_km2 = float(_u.get("cell_area_m2") or cell * cell) / 1e6
    streams = np.isfinite(z) & (acc * _cell_km2 >= min_drainage_km2)

    # Slope from a raw DEM is dominated by noise at fine cell sizes: vertical
    # noise of e (metres) over cells of size c produces slope noise ~e/c. At
    # 0.15 m noise and 10 m cells that is 0.015 — ABOVE a 0.01 fan_slope_min, so
    # an unsmoothed slope puts flat ground inside the fan band and the region
    # grow runs away across the whole basin. Smooth before banding.
    swin = max(3, int(round(smooth_slope_m / cell)))
    if swin % 2 == 0:
        swin += 1
    slope = _nan_uniform(slope_raw, swin)

    # The floor that matters is not how much noise smoothing removed -- it is
    # how steep the FLATTEST ground here still reads after smoothing, because
    # that is what a fan_slope_min below it will admit. A low percentile of the
    # smoothed slope estimates it directly.
    finite = slope[np.isfinite(slope)]
    noise_floor = float(np.percentile(finite, 20)) if finite.size else 0.0
    requested_slope_min = fan_slope_min
    if auto_slope_floor and noise_floor >= fan_slope_max / 2:
        # Mountain terrain: a fifth of the DEM is steeper than half the fan
        # band, so the low percentile is measuring hillslopes, not noise on
        # flat ground. Raising the band above it (0.144 -> 0.216 on Ruidoso)
        # leaves an empty band and silently finds nothing.
        note(f"slope floor {noise_floor:.3f} is terrain, not noise (little "
             f"flat ground here); fan_slope_min left at {fan_slope_min:g}")
    elif auto_slope_floor and fan_slope_min < noise_floor * 1.5:
        # ... but never above 3%. On a Basin-and-Range field office a fifth
        # of the ground is bajada at 2 to 4%, which is fan surface, not
        # noise: Kingman (Oct 2026) had its floor raised to 4.9% and lost
        # the fans the band was there to find. Real fans start about 1.5%;
        # a floor of 3% keeps most of them and still excludes flat ground.
        fan_slope_min = float(min(noise_floor * 1.5, max(requested_slope_min,
                                                         0.03)))
        note(f"raised fan_slope_min {requested_slope_min:g} -> {fan_slope_min:.3f} "
             f"(flat ground here reads {noise_floor:.3f})")
        warnings.append(
            f"fan_slope_min was raised from {requested_slope_min:g} to "
            f"{fan_slope_min:.3f}. The flattest ground in this DEM still reads "
            f"a slope of {noise_floor:.3f} after smoothing, and a band that "
            f"starts below that admits flat ground -- which is how a previous "
            f"run produced 139 km2 of 'fans' on a 49 km2 DEM. Pass "
            f"auto_slope_floor=False to override, or raise smooth_slope_m to "
            f"lower the floor instead."
        )
    elif fan_slope_min < noise_floor:
        warnings.append(
            f"fan_slope_min ({fan_slope_min}) is below this DEM's slope floor "
            f"(~{noise_floor:.3f}) and auto_slope_floor is off. Flat ground "
            f"will read as in-band and fans will over-grow."
        )

    if cell > 60:
        warnings.append(
            f"DEM cell size is {cell:g} m. Fans smaller than a few hundred "
            "metres will not resolve; expect only the largest to be found."
        )

    # Local base level: a smoothed low envelope of the surface. A fan stands
    # above this; a hillslope of the same gradient does not.
    bwin = max(3, int(round(base_window_m / cell)))
    zf = np.where(np.isfinite(z), z, np.nanmean(z))
    note(f"base level from a {bwin}-cell ({bwin * cell:g} m) minimum filter")
    base = ndimage.minimum_filter(zf, size=bwin, mode="nearest")
    base = ndimage.uniform_filter(base, size=max(3, bwin // 3), mode="nearest")

    with rasterio.open(terrain.flow_dir) as src:
        fdir = src.read(1)

    note(f"locating apexes by gradient break (window {apex_window_m:g} m)")
    apex_records = _find_apexes(
        z, fdir, acc, streams, cell, min_drainage_km2,
        apex_window_m, apex_gradient_ratio, apex_gradient_min,
        fan_slope_min, fan_slope_max, apex_spacing_m,
    )
    apexes = [(r, c) for r, c, _ in apex_records]
    apex_metrics = {(r, c): m for r, c, m in apex_records}
    note(f"{len(apexes)} candidate apexes")
    if not apexes:
        warnings.append(
            "No fan apexes found. The apex test is a drop in the channel's own "
            "gradient: lower apex_gradient_ratio (how sharp the break must be) "
            "or apex_gradient_min (how steep the canyon above it must be), or "
            "lower min_drainage_km2. Also check that the stream network reaches "
            "the range front -- adjust stream_threshold_km2 in build_terrain if "
            "it stops short."
        )

    min_cells = max(5, int(round(min_area_km2 * 1e6 / (cell * cell))))
    cap_km2 = (max_area_km2 if max_area_km2 is not None
               else math.pi * (max_radius_m ** 2) / 2.0 / 1e6)
    max_cells = int(round(cap_km2 * 1e6 / (cell * cell)))
    fan_id = np.zeros(z.shape, dtype="int32")
    cand_id = np.zeros(z.shape, dtype="int32")
    records: list[dict[str, Any]] = []
    cands: list[dict[str, Any]] = []

    for apex in progress(apexes, label="growing fans"):
        region = _grow_fan(
            z, slope, base, apex, cell, fan_slope_min, fan_slope_max,
            min_above_base_m, max_radius_m, descend_tol_m,
            seed_radius_m=smooth_slope_m,
        )
        if region is None or region.sum() < min_cells:
            continue
        if region.sum() > max_cells:
            warnings.append(
                f"apex at row {apex[0]}, col {apex[1]} grew past the "
                f"{cap_km2:.2f} km² cap and was discarded as a runaway region — "
                "usually fan_slope_min sitting below the DEM's slope noise."
            )
            continue
        # Keep only the component containing the apex.
        lab, _ = ndimage.label(region, structure=np.ones((3, 3)))
        region = lab == lab[apex]
        if region.sum() < min_cells:
            continue

        rr, cc = np.nonzero(region)
        ax, ay = _rc_to_xy(transform, [apex[0]], [apex[1]], offset="center")
        dists = np.hypot(rr - apex[0], cc - apex[1]) * cell
        elevs = z[rr, cc]
        area_m2 = float(region.sum()) * cell * cell
        # Radial gradient from apex elevation, fitted over all fan cells.
        far = dists > cell
        grad = (
            float(np.median((z[apex] - elevs[far]) / dists[far])) if far.any()
            else float("nan")
        )
        # The cone test: the angle the region subtends at its apex. A fan is
        # a sector (the fixture's are half-discs, ~200 deg by this measure);
        # a strip of valley floor running away from a tributary mouth is a
        # few tens of degrees however long it is.
        r90 = float(np.percentile(dists, 90)) if dists.size else 0.0
        sector_deg = (math.degrees(2.0 * area_m2 / (r90 * r90))
                      if r90 > 0 else 0.0)
        if float(dists.max()) >= 0.98 * max_radius_m:
            reason = (f"reached the {max_radius_m:g} m radius cap: a runaway "
                      f"grow, usually along a valley floor")
        elif sector_deg < min_sector_deg:
            reason = (f"not a cone: {sector_deg:.0f} deg at the apex (under "
                      f"{min_sector_deg:g}), usually a strip of valley floor")
        else:
            reason = ""

        rec = {
            "apex_x": float(ax[0]),
            "apex_y": float(ay[0]),
            "apex_elev": float(z[apex]),
            "area_m2": area_m2,
            "area_km2": area_m2 / 1e6,
            "radius_m": float(dists.max()),
            "r90_m": r90,
            "sector_deg": sector_deg,
            "mean_radial_slope": grad,
            "relief_m": float(np.nanmax(elevs) - np.nanmin(elevs)),
            "mean_above_base_m": float(np.nanmean(elevs - base[rr, cc])),
            "drainage_km2": float(acc[apex] * _cell_km2),
            # The evidence for calling this an apex at all, carried through to
            # the output so a questionable fan can be judged rather than trusted.
            "gradient_above": apex_metrics.get(apex, {}).get("gradient_above"),
            "gradient_below": apex_metrics.get(apex, {}).get("gradient_below"),
            "gradient_ratio": apex_metrics.get(apex, {}).get("gradient_ratio"),
            "geometry": None,
        }
        k = len(cands) + 1
        cand_id[region & (cand_id == 0)] = k
        cands.append({**rec, "candidate_id": k, "accepted": not reason,
                      "reason": reason or "cone below a gradient break"})
        if reason:
            continue
        n = len(records) + 1
        fan_id[region & (fan_id == 0)] = n
        records.append({**rec, "fan_id": n, "candidate_id": k})

    note("vectorizing fans")

    def _polys(ids: np.ndarray) -> dict[int, Any]:
        out: dict[int, Any] = {}
        for geom, val in rfeat.shapes(ids, mask=ids > 0, transform=transform):
            v = int(val)
            g = shape(geom)
            if v not in out or g.area > out[v].area:
                out[v] = g
        return out

    geoms = _polys(fan_id)
    for rec in records:
        rec["geometry"] = geoms.get(rec["fan_id"])
    records = [r for r in records if r["geometry"] is not None]
    cgeoms = _polys(cand_id)
    for rec in cands:
        rec["geometry"] = cgeoms.get(rec["candidate_id"])
    cands = [r for r in cands if r["geometry"] is not None]
    rejected = sum(1 for r in cands if not r["accepted"])

    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Explicit column list, so adding a field to `records` without adding it
    # here silently drops it from the output. The gradient_* fields are the
    # EVIDENCE for calling each apex an apex; they belong with the fan.
    cols = ["fan_id", "apex_x", "apex_y", "apex_elev", "area_m2", "area_km2",
            "radius_m", "r90_m", "sector_deg", "mean_radial_slope", "relief_m",
            "mean_above_base_m", "drainage_km2", "gradient_above",
            "gradient_below", "gradient_ratio", "geometry"]
    gdf = gpd.GeoDataFrame(records, crs=crs, columns=cols)
    fan_path = out_dir / "fans.gpkg"
    gdf.to_file(str(fan_path), driver="GPKG")

    ccols = (["candidate_id", "accepted", "reason"]
             + [c for c in cols if c != "fan_id"])
    cand_path = out_dir / "fan_candidates.gpkg"
    gpd.GeoDataFrame(cands, crs=crs, columns=ccols).to_file(
        str(cand_path), driver="GPKG")

    apex_gdf = gpd.GeoDataFrame(
        {
            "fan_id": [r["fan_id"] for r in records],
            "gradient_above": [r.get("gradient_above") for r in records],
            "gradient_below": [r.get("gradient_below") for r in records],
            "gradient_ratio": [r.get("gradient_ratio") for r in records],
            "drainage_km2": [r.get("drainage_km2") for r in records],
        },
        geometry=[Point(r["apex_x"], r["apex_y"]) for r in records], crs=crs,
    )
    apex_path = out_dir / "fan_apexes.gpkg"
    apex_gdf.to_file(str(apex_path), driver="GPKG")

    prof = gtiff_profile(profile, dtype="int32", nodata=0)
    ras_path = out_dir / "fan_id.tif"
    with rasterio.open(str(ras_path), "w", **prof) as dst:
        dst.write(fan_id, 1)

    total = float(sum(r["area_km2"] for r in records))
    elapsed = time.monotonic() - t0
    note(f"{len(records)} fans, {total:.2f} km², in {elapsed:.1f}s"
         + (f"; {rejected} candidate(s) rejected by the cone test"
            if rejected else ""))

    if rejected:
        warnings.append(
            f"{rejected} of {len(cands)} grown fan candidates were rejected "
            f"as not cone-shaped (under {min_sector_deg:g} deg at the apex, "
            f"or reaching the {max_radius_m:g} m radius cap). On mountain "
            f"terrain these are usually strips of trunk-valley floor below a "
            f"tributary mouth: the apex is a real tributary mouth, the area is "
            f"not a fan. They are in {cand_path.name} with the reason, for "
            f"review; only accepted fans set Rosgen valley type III."
        )
    if records:
        grads = [r["mean_radial_slope"] for r in records
                 if np.isfinite(r["mean_radial_slope"])]
        if grads and np.median(grads) > fan_slope_max * 0.9:
            warnings.append(
                "Median fan gradient sits near fan_slope_max — the band may be "
                "clipping real fans. Raise fan_slope_max and re-check."
            )

    return FanResult(
        fans=str(fan_path),
        fan_raster=str(ras_path),
        apexes=str(apex_path),
        fan_count=len(records),
        total_area_km2=total,
        parameters={
            "min_drainage_km2": min_drainage_km2,
            "fan_slope_min": fan_slope_min, "fan_slope_max": fan_slope_max,
            "min_above_base_m": min_above_base_m, "max_radius_m": max_radius_m,
            "min_area_km2": min_area_km2,
            "fan_slope_min_requested": requested_slope_min,
            "fan_slope_min_used": fan_slope_min,
            "slope_floor_measured": noise_floor,
            "apex_window_m": apex_window_m,
            "apex_gradient_ratio": apex_gradient_ratio,
            "apex_gradient_min": apex_gradient_min,
            "apex_spacing_m": apex_spacing_m,
            "base_window_m": bwin * cell,
            "descend_tol_m": descend_tol_m, "cell_size": cell,
            "min_sector_deg": min_sector_deg,
            "stream_threshold_km2": terrain.stream_threshold_km2,
        },
        seconds=elapsed,
        warnings=warnings,
        candidates=str(cand_path),
        rejected_count=rejected,
    )
