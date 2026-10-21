"""Valley cross-section form: valley floor to drainage divide.

WHY THIS EXISTS
---------------
The Rosgen valley types are defined by the shape of the **whole valley**, from
the floor up to the bounding divides. Read his descriptions and every
discriminator is a hillslope property, not a channel property:

* I   -- "V-notched" canyons          -> cross-section SHAPE
* II  -- "gentler side slopes"        -> SIDE-SLOPE gradient
* V   -- "U-shaped glacial troughs"   -> cross-section SHAPE
* VII -- "high-relief alluvial slopes"-> RELIEF, floor to divide
* VIII/IX/X -- "wide" / "broad"       -> valley WIDTH at the divides

The first version of :mod:`geolens.landscape.rosgen` fed that crosswalk two
channel-centric proxies instead: the confinement ratio (valley-bottom width
divided by **channel** width) and the **channel** gradient. Those are the right
inputs for a stream-type classification and the wrong ones here. A 20 m channel
in a 200 m valley bottom scores the same confinement whether the valley walls
rise 30 m at 8% or 600 m at 70% -- which is the difference between Rosgen VIII
and Rosgen I.

So this module measures the valley as a landform. For each segment it cuts long
cross-sections, walks outward and upward from the floor to the drainage divide
on each side, and measures what the classification actually turns on.

WHAT IT MEASURES
----------------
``vf``
    The **valley floor width to height ratio** (Bull & McFadden 1977), the
    standard morphometric index for exactly this question::

        Vf = 2 * Vfw / ((Eld - Esc) + (Erd - Esc))

    where ``Vfw`` is valley floor width, ``Eld``/``Erd`` are the left and right
    divide elevations and ``Esc`` is the valley floor elevation. Low Vf (< ~0.5)
    is a narrow V-shaped valley actively incising; high Vf (> ~2) is a broad
    flat-floored valley. It is dimensionless, so it compares across basins and
    across cell sizes, and it is widely used in tectonic geomorphology, which
    means a reader can check it against published values for their region.

``shape_n``
    The exponent of ``z ~ |x|^n`` fitted to the section above the floor.
    ``n ~ 1`` is a V; ``n ~ 2`` is a U (a parabola). This is what separates a
    glacial trough (V) from a fluvial V-notch (I) at similar side-slope
    gradients, and it is the single measurement the old crosswalk most needed.

``side_slope_left`` / ``side_slope_right``, ``side_slope``
    Rise over run from the edge of the valley floor to the divide. This is
    Rosgen's "steep" / "moderately steep" / "gentle side slopes", which is a
    different number from the valley's downstream gradient.

``valley_relief_m``
    Divide elevation minus floor elevation, the mean of the two sides.

``asymmetry``
    ``(left_relief - right_relief) / (left_relief + right_relief)``, in
    ``[-1, 1]``. A persistently one-sided valley is the classic signature of
    structural control -- a fault or a resistant dipping bed holding one wall
    up. That does not *prove* Rosgen VI, which is defined by structure, but it
    is the first DEM-derived evidence for it this package has had, so VI moves
    from "never assertable" to "flagged as a candidate where the asymmetry is
    persistent along the reach".

``divide_found_left`` / ``divide_found_right``
    Whether a divide was actually reached inside ``max_search_m``. **A section
    that ran out of search distance is not a measurement**, and Vf computed from
    it is a lower bound. Both are reported so a broad valley is distinguishable
    from a truncated search.

WHAT IT DOES NOT DO
-------------------
It does not classify. It measures the form and hands it to
:func:`geolens.landscape.rosgen.rosgen_valley_types`, which owns the crosswalk.
Keeping those apart is deliberate: the measurements are checkable against
published Vf values, and the crosswalk is the part that is an opinion.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..safety import Guard, GeolensError, note, progress

# A divide search that has to run further than this is not describing a valley
# side any more, it is crossing into the next drainage. Scaled by the segment's
# own valley width where one is known.
DEFAULT_MAX_SEARCH_M = 3000.0

# How far above the floor a cell has to sit before it counts as "wall" rather
# than "floor". Below this the section is still on the valley bottom, and
# including it in the side-slope fit drags the gradient toward zero.
FLOOR_TOLERANCE_M = 2.0

# Rosgen's descriptive side-slope classes, encoded. Like GRADIENT_BREAKS in
# rosgen.py these are an encoding, not published values -- calibrate them.
SIDE_SLOPE_BREAKS: dict[str, float] = {
    "gentle": 0.10,
    "moderate": 0.25,
    "steep": 0.60,
    # above `steep` is very_steep
}

# Vf breaks. Bull & McFadden's own usage puts actively incising V-shaped
# valleys below ~0.5 and broad aggrading valleys above ~1.0.
VF_BREAKS: dict[str, float] = {
    "v_shaped": 0.5,
    "moderate": 1.0,
    "broad": 4.0,
    # above `broad` is very_broad
}


@dataclass
class ValleyFormResult:
    sections: str                  # cross-section traces, as a GPKG
    table: str                     # per-section measurements, as CSV
    segment_table: str             # aggregated to segments
    n_sections: int
    n_measured: int                # sections where both divides were found
    median_vf: float
    median_side_slope: float
    median_shape_n: float
    seconds: float
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def side_slope_class(s: float | None) -> str:
    if s is None or not np.isfinite(s):
        return "unknown"
    if s < SIDE_SLOPE_BREAKS["gentle"]:
        return "gentle"
    if s < SIDE_SLOPE_BREAKS["moderate"]:
        return "moderate"
    if s < SIDE_SLOPE_BREAKS["steep"]:
        return "steep"
    return "very_steep"


def vf_class(vf: float | None) -> str:
    if vf is None or not np.isfinite(vf):
        return "unknown"
    if vf < VF_BREAKS["v_shaped"]:
        return "v_shaped"
    if vf < VF_BREAKS["moderate"]:
        return "moderate"
    if vf < VF_BREAKS["broad"]:
        return "broad"
    return "very_broad"


def _find_divide(z: np.ndarray, floor_z: float,
                 min_rise_m: float) -> tuple[int | None, bool]:
    """Index of the drainage divide walking outward from the floor.

    The divide is the first point where elevation stops rising: a local maximum
    that is not immediately exceeded further out. Walking to the single highest
    point instead is wrong -- on a section that crosses into the next drainage
    the highest point can be a peak two valleys over, which would make the
    valley look far deeper and far wider than it is.

    Returns ``(index, True)`` when a real crest was found, or
    ``(index_of_max, False)`` when the search ran out of section first. The
    caller needs that distinction: the second case is a lower bound, not a
    measurement.
    """
    if z.size < 3 or not np.isfinite(z).any():
        # A section that runs off the edge of the DEM has a side with no data
        # at all. Synthetic fixtures are covered edge to edge and never produce
        # this; a real network reaches the raster boundary constantly, and
        # nanargmax on an all-NaN slice raises rather than returning nothing.
        return None, False

    # Smooth lightly so single-cell noise on a hillslope is not a "crest".
    k = max(3, int(round(z.size * 0.02)) | 1)
    if k < z.size:
        pad = k // 2
        sm = np.convolve(np.pad(z, pad, mode="edge"), np.ones(k) / k, mode="valid")
    else:
        sm = z
    if not np.isfinite(sm).any():
        return None, False

    best_i = int(np.nanargmax(sm))
    if sm[best_i] - floor_z < min_rise_m:
        # Never climbed far enough above the floor to call anything a divide.
        return (best_i if np.isfinite(sm[best_i]) else None), False

    # Walk out from the floor; stop at the first point that is not exceeded
    # within a reasonable look-ahead.
    look = max(3, int(round(z.size * 0.10)))
    for i in range(1, sm.size - 1):
        if sm[i] - floor_z < min_rise_m:
            continue
        ahead = sm[i + 1:i + 1 + look]
        if ahead.size == 0:
            break
        if not np.isfinite(ahead).any():
            # Ran off the edge of the DEM. Nothing beyond here to compare
            # against, so this is a lower bound, not a crest.
            return i, False
        if np.nanmax(ahead) <= sm[i]:
            return i, True
    # The fall-through: no crest was found, so this is the highest point seen,
    # and it is a LOWER BOUND by construction. Returning True here whenever the
    # profile happened to peak away from the last sample handed out a
    # "complete" flag for a section that found nothing -- and `complete` is the
    # one thing this module's honesty rests on.
    return best_i, False


def _shape_exponent(dist: np.ndarray, rise: np.ndarray,
                    floor_tolerance_m: float, toe_fraction: float = 0.05) -> float:
    """Fit ``rise = a * (dist + x0)**n`` up a valley wall and return n.

    A V-shaped valley is linear in distance (n ~ 1); a glacial trough is
    parabolic (n ~ 2). That is the measurement separating Rosgen valley types
    at the same side-slope gradient, so it has to be right.

    **It was not.** The previous version fitted ``rise + tol`` rather than
    ``rise``, to keep the logarithm finite near the floor. That additive term
    is not part of a power law, and it flattens the bottom of the curve, which
    the fit compensates for with a steeper slope. Measured on clean synthetic
    flanks: a true 2.0 came back as **2.60** and a true 1.6 as 1.87. Include
    the valley floor in the samples, as a real cross-section does, and a true
    2.0 read **2.96** and a true 1.6 read **3.96** — a broad U reported as
    something with no physical meaning at all. Both of the wide valleys in the
    fixture were being classified on that number.

    Two changes fix it:

    * **Fit ``log(rise)`` directly**, with points at or below the floor
      tolerance dropped rather than shifted. Those points carry no shape
      information; including them only distorts the ones that do.
    * **Drop the toe.** Within a few metres of the floor the rise is a metre or
      two, so in log space it is mostly noise, and noise there drags the slope
      up hard: with 1.5 m of noise a true 2.0 fitted to 2.89 even after the
      first fix. Samples below ``toe_fraction`` of the wall's own height are
      excluded.

    **Why the offset x0 is solved rather than assumed zero.** The wall is
    measured outward from the detected edge of the valley floor, and that edge
    is found by an elevation tolerance. A parabola is genuinely flat near its
    vertex, so a 2 m tolerance on a 300 m trough calls the first 41 m of the
    wall "floor" — the toe of the curve is cut off, and with it the part that
    carries the n = 2 signal. The offset recovers where the wall really began,
    and it is allowed to be negative because the floor edge can be detected
    late as well as early.

    Measured after the fix, against built values:

    ==========  ======  ======  ======  ======
    true n         1.0     1.5     2.0     2.5
    clean        1.009   1.502   1.989   2.516
    +1.5 m noise 1.004     --    2.092     --
    +wide floor    --      --    2.025     --
    ==========  ======  ======  ======  ======
    """
    m0 = np.isfinite(dist) & np.isfinite(rise) & (dist > 0)
    if m0.sum() < 8:
        return float("nan")
    d = np.asarray(dist[m0], dtype="float64")
    r = np.asarray(rise[m0], dtype="float64")

    top = float(np.nanmax(r)) if r.size else float("nan")
    if not np.isfinite(top) or top <= 0:
        return float("nan")

    tol = max(float(floor_tolerance_m), 1e-3)
    keep = r > max(tol, toe_fraction * top)
    if keep.sum() < 8:
        return float("nan")
    d, r = d[keep], r[keep]
    order = np.argsort(d)
    d, r = d[order], r[order]

    y = np.log(r)
    span = float(d[-1])
    if span <= 0:
        return float("nan")

    best_n, best_res = float("nan"), np.inf
    for x0 in np.linspace(-0.9 * float(d[0]), 0.5 * span, 96):
        x = np.log(d + x0)
        if not np.all(np.isfinite(x)) or np.ptp(x) < 1e-9:
            continue
        A = np.vstack([x, np.ones_like(x)]).T
        sol, res, _, _ = np.linalg.lstsq(A, y, rcond=None)
        resid = float(res[0]) if res.size else float(((A @ sol - y) ** 2).sum())
        if resid < best_res:
            best_res, best_n = resid, float(sol[0])

    # Outside this is a fit artefact, not a valley shape.
    return best_n if 0.2 <= best_n <= 5.0 else float("nan")


def _thalweg_index(offsets: np.ndarray, z: np.ndarray,
                   search_m: float) -> int | None:
    """The valley floor NEAR THE CENTRELINE, not the lowest point in range.

    A valley section is deliberately long -- it has to reach the divides -- so
    at ``max_search_m = 2500`` it is 5 km wide and routinely crosses the divide
    into the next drainage. Taking ``nanargmin`` over the whole transect then
    finds the lowest ground anywhere in 5 km, which on real terrain is often a
    DIFFERENT VALLEY.

    Measured on the fan fixture: the thalweg came back at offset -2494 m, the
    far edge of the section, 886 m elevation against a local channel around
    1100 m. Everything downstream is then measured from the wrong origin -- a
    446 m "valley floor" in the neighbouring basin, no divide found on one
    side, and a nonsense Vf. It affected 436 of 445 sections.

    The section origin sits on the mapped channel, so the floor is at or near
    offset 0. Search a window around it wide enough to absorb centreline
    imprecision and no wider.
    """
    near = np.abs(offsets) <= search_m
    cand = near & np.isfinite(z)
    if not cand.any():
        cand = np.isfinite(z)
        if not cand.any():
            return None
    idx = np.flatnonzero(cand)
    return int(idx[np.argmin(z[idx])])


def _section_offsets(half_width_m: float, point_spacing_m: float,
                     fine_half_m: float | None = None,
                     coarse_samples: int = 300) -> np.ndarray:
    """Sample offsets across a section: fine near the axis, coarse further out.

    A uniform grid at the DEM's own cell size is enormously wasteful here, and
    on a real dataset it is the difference between a step that runs and one
    that does not. Finding a divide six kilometres away does not need 0.73 m
    sampling: at 2.4 ftUS cells that is 16,405 points per section, and across
    a 1,767 km network at 500 m spacing it comes to **116 million samples**.

    But coarsening uniformly breaks the other half of the measurement. The
    valley floor, the incised channel and the toe of the wall — everything
    that decides Vf and the shape exponent — live within a few hundred metres
    of the axis and need the resolution. Only the reach out to the divide can
    afford to be coarse.

    So: cell spacing inside ``fine_half_m``, and beyond it whatever gives
    roughly ``coarse_samples`` points across the rest. Same run: about 1,100
    points per section instead of 16,405.

    ``_measure_section`` works off these offsets rather than assuming a single
    step, which is what makes non-uniform sampling safe.
    """
    step = max(float(point_spacing_m), 1e-9)
    half = float(half_width_m)
    fine_half = (float(fine_half_m) if fine_half_m is not None
                 else min(half, max(200.0, 25.0 * step)))
    fine = np.arange(-fine_half, fine_half + step, step)
    if fine_half >= half:
        return fine

    coarse_step = max(step, (half - fine_half) / max(coarse_samples // 2, 1))
    pos = np.arange(fine_half + coarse_step, half + coarse_step, coarse_step)
    if pos.size == 0:
        return fine
    return np.concatenate([-pos[::-1], fine, pos])


def _measure_section(offsets: np.ndarray, z: np.ndarray, thalweg_i: int,
                     floor_tolerance_m: float,
                     min_rise_m: float,
                     channel_max_width_m: float = 100.0,
                     floor_search_m: float = 1000.0) -> dict[str, Any] | None:
    """Everything the crosswalk needs, from one floor-to-divide section.

    ``channel_max_width_m`` is how wide the incised channel may be, and
    ``floor_search_m`` how far beyond it to look for the valley-floor
    elevation. Together they define an annulus around the thalweg: outside
    this channel, but not so far out that it reaches the next valley's. The
    floor's own width is then measured across the whole section.
    """
    if z.size < 7 or thalweg_i is None or not np.isfinite(z[thalweg_i]):
        return None
    # Sampling is NOT uniform: fine near the axis where the floor and the
    # channel are, coarse further out where only the divide matters. So an
    # index cannot be derived by dividing metres by a single step, and a width
    # cannot be derived by multiplying samples by one either. Everything below
    # works off the offsets themselves.
    def _idx_at(metres: float, direction: int) -> int:
        """Index `metres` away from the thalweg, going `direction`."""
        target = float(offsets[thalweg_i]) + direction * float(metres)
        return int(np.clip(np.searchsorted(offsets, target), 0, offsets.size - 1))

    def _local_step(i: int) -> float:
        a = max(0, i - 1)
        b = min(offsets.size - 1, i + 1)
        gaps = b - a
        return (float(offsets[b] - offsets[a]) / gaps) if gaps else 1.0

    step_m = _local_step(thalweg_i)
    thalweg_z = float(z[thalweg_i])
    if not np.isfinite(thalweg_z):
        return None

    # -- the valley floor is not the channel ---------------------------------
    #
    # Taking the floor elevation at the thalweg means the tolerance band sits
    # inside the incised channel, and the "valley floor" comes back as the
    # channel's own width. Measured on a fixture with built floors of 80, 180,
    # 900 and 1800 m, the thalweg version returned **20, 0, 0 and 40 m** — the
    # 1800 m alluvial floor read as 40 m, so Vf was ~0.15 for every valley and
    # all four classified as V-shaped whatever their form. That is the
    # difference between measuring a valley and measuring its channel.
    #
    # The channel is distinguishable by being NARROW. Estimating the floor
    # elevation from outside a channel-width window steps over it: the same
    # four valleys then measure 120, 220, 1220 and 1960 m.
    #
    # Those are all biased high, by +50%, +22%, +36% and +9%, and that bias is
    # real rather than a defect to tune away: a valley flank is genuinely flat
    # near its toe — most of all a parabolic one — so any tolerance band
    # includes some of it. The bias is largest where the floor is narrowest,
    # i.e. where it is a couple of cells wide and the resolution is the limit.
    # Vf should be read as an upper bound on a narrow floor.
    # Estimated from an ANNULUS around the thalweg -- outside the channel, but
    # still nearby. Taking the minimum over the whole section instead reached
    # into the NEXT valley's channel, which is lower, and dragged floor_z below
    # this valley's floor: the tolerance band then fitted inside this channel
    # again and the 1800 m floor read 40 m, the same failure by a different
    # route. A section is several kilometres wide; "outside the channel" has to
    # mean outside THIS channel, not anywhere else on the profile.
    i_ch_lo = _idx_at(channel_max_width_m / 2.0, -1)
    i_ch_hi = _idx_at(channel_max_width_m / 2.0, +1)
    i_fl_lo = _idx_at(channel_max_width_m / 2.0 + floor_search_m, -1)
    i_fl_hi = _idx_at(channel_max_width_m / 2.0 + floor_search_m, +1)
    half = max(1, thalweg_i - i_ch_lo, i_ch_hi - thalweg_i)
    left = z[i_fl_lo:i_ch_lo]
    right = z[i_ch_hi + 1:i_fl_hi + 1]
    outside = np.concatenate([left, right])
    outside = outside[np.isfinite(outside)]
    annulus_z = float(np.min(outside)) if outside.size else thalweg_z

    # Only step over a channel that is THERE. Stepping out unconditionally
    # assumes an incised channel, and a V-notch does not have one: on a
    # perfect V at 0.5 gradient the step landed 25 m up the flank, the
    # tolerance band was taken around that, and a one-cell floor measured
    # 114 m. Both directions of this mistake are the same mistake — a floor
    # measured somewhere other than the floor.
    #
    # A channel is a STEP: elevation climbs out of the notch and then flattens.
    # A V keeps climbing at the same rate. Comparing the rise across the
    # channel window with the rise across the next window of equal width tells
    # the two apart without assuming either.
    # Compare GRADIENTS over equal distances in metres, not over equal numbers
    # of samples. Index-counted windows move with the sampling, and when one
    # of them landed on the flank instead of the floor a real channel read as
    # no channel and a 180 m floor measured 50 m. Metres are what the terrain
    # is shaped in; samples are an implementation detail.
    def _grad(d0: float, d1: float) -> float:
        """Mean gradient between two distances out from the thalweg."""
        best = np.inf
        for direction in (-1, +1):
            a, b = _idx_at(d0, direction), _idx_at(d1, direction)
            if a == b or not (np.isfinite(z[a]) and np.isfinite(z[b])):
                continue
            span = abs(float(offsets[b] - offsets[a]))
            if span <= 0:
                continue
            best = min(best, abs(float(z[b] - z[a])) / span)
        return best

    w = max(channel_max_width_m / 2.0, step_m)
    grad_in = _grad(0.0, w)
    grad_out = _grad(w, 2.0 * w)
    # A channel is a STEP: climb out of the notch, then flatten. A V-notch
    # keeps climbing at the same rate, so its two gradients match and it is
    # correctly left alone — stepping over a channel it does not have put the
    # floor 25 m up the flank and measured a one-cell floor as 114 m.
    incised = (np.isfinite(grad_in) and np.isfinite(grad_out)
               and grad_in > 2.0 * max(grad_out, 1e-9))

    floor_z = annulus_z if incised else thalweg_z
    if floor_z < thalweg_z:
        floor_z = thalweg_z

    near_floor = np.isfinite(z) & (z <= floor_z + floor_tolerance_m)
    lo = hi = thalweg_i
    while lo > 0 and near_floor[lo - 1]:
        lo -= 1
    while hi < z.size - 1 and near_floor[hi + 1]:
        hi += 1
    # The floor spans lo..hi INCLUSIVE, so its width is (hi - lo + 1) samples,
    # not the distance between the two end samples. With a one-cell floor --
    # which is exactly what a V-notch canyon has, and therefore the case Vf is
    # most often asked about -- the old form gave 0.0 m and Vf 0.0 for every
    # incised section.
    # Span between the outermost floor samples, plus one sample's width so a
    # single-cell floor is one cell wide rather than zero — which is exactly
    # what a V-notch has, and therefore the case Vf is most often asked about.
    if hi > lo:
        span = float(offsets[hi] - offsets[lo])
        vfw = span + span / (hi - lo)
    else:
        vfw = _local_step(thalweg_i)

    out: dict[str, Any] = {"floor_z": floor_z, "thalweg_z": thalweg_z,
                           "incision_m": floor_z - thalweg_z,
                           "valley_floor_width_m": vfw,
                           # Where the floor starts and ends on the section,
                           # so it can be drawn.
                           "floor_offset_left_m": float(offsets[lo]),
                           "floor_offset_right_m": float(offsets[hi])}

    sides = {}
    for name, sl_idx, sl_off, sl_z in (
        ("left", lo, offsets[:lo + 1][::-1], z[:lo + 1][::-1]),
        ("right", hi, offsets[hi:], z[hi:]),
    ):
        if sl_z.size < 3:
            sides[name] = None
            continue
        di, found = _find_divide(sl_z, floor_z, min_rise_m)
        if di is None:
            sides[name] = None
            continue
        run = float(abs(sl_off[di] - sl_off[0]))
        rise = float(sl_z[di] - floor_z)
        sides[name] = {
            # WHERE the divide is, not just how far and how high. Without this
            # the whole measurement stays a number in a table: the ridge line
            # cannot be drawn, and neither can the valley it bounds. "Valley
            # types" then renders as a coloured stream centreline, which is a
            # classification OF a valley rather than a map of one.
            "divide_offset_m": float(sl_off[di]),
            "divide_z": float(sl_z[di]),
            "relief_m": rise,
            "run_m": run,
            "slope": (rise / run) if run > 0 else float("nan"),
            "found": bool(found),
            "shape_n": _shape_exponent(
                np.abs(sl_off[:di + 1] - sl_off[0]),
                np.asarray(sl_z[:di + 1], dtype=float) - floor_z,
                floor_tolerance_m,
            ),
        }

    L, R = sides.get("left"), sides.get("right")
    for name, s in (("left", L), ("right", R)):
        out[f"side_slope_{name}"] = s["slope"] if s else float("nan")
        out[f"relief_{name}_m"] = s["relief_m"] if s else float("nan")
        out[f"divide_found_{name}"] = bool(s["found"]) if s else False
        out[f"shape_n_{name}"] = s["shape_n"] if s else float("nan")

    have = [x for x in (L, R) if x]

    # A search that ran out of section is NOT a missing measurement -- it is a
    # lower bound, and on real terrain it is the common case: a segment on a
    # basin floor has no divide within kilometres. Measured on the fan fixture,
    # only 12 of 394 sections reached a crest on both sides, so requiring both
    # threw away 97% of the data and collapsed segment coverage from 222 to 5.
    #
    # Worse, it threw away the informative ones. "No divide within 2 km and
    # only 0.5 m of rise" says the valley is at least 4 km wide and flat, which
    # is precisely what separates Rosgen VIII/IX/X from I/II. That is a result.
    out["unbounded"] = bool(
        have and not any(h["found"] for h in have)
        and max(h["relief_m"] for h in have) < max(2.0 * min_rise_m, 10.0)
    )

    # Combine from whichever sides exist.
    #
    # These used to be computed only inside `if L and R`, so one side running
    # off the edge of the DEM nulled the other side too: on the fan fixture
    # side_slope existed for 23 of 222 segments, and the crosswalk fell back to
    # channel proxies for the other 199. A section with one good wall still
    # measures a side slope and a shape -- it just cannot measure a Vf or an
    # asymmetry, which need both divides by definition.
    if have:
        with np.errstate(invalid="ignore"):
            import warnings as _w
            with _w.catch_warnings():
                _w.simplefilter("ignore", RuntimeWarning)
                out["side_slope"] = float(np.nanmean([h["slope"] for h in have]))
                out["shape_n"] = float(np.nanmean([h["shape_n"] for h in have]))
                out["valley_relief_m"] = float(
                    np.nanmean([h["relief_m"] for h in have]))
    else:
        for k in ("side_slope", "shape_n", "valley_relief_m"):
            out[k] = float("nan")

    out["divide_offset_left_m"] = (float(L["divide_offset_m"]) if L
                                   else float("nan"))
    out["divide_offset_right_m"] = (float(R["divide_offset_m"]) if R
                                    else float("nan"))

    if L and R:
        eld, erd = L["divide_z"], R["divide_z"]
        denom = (eld - floor_z) + (erd - floor_z)
        # Vf is undefined on a valley with no relief, and a near-zero
        # denominator produces a huge number that looks like a very broad
        # valley. Report it as missing instead.
        out["vf"] = (2.0 * vfw / denom) if denom > 1e-6 else float("nan")
        out["valley_relief_m"] = 0.5 * denom
        tot = L["relief_m"] + R["relief_m"]
        out["asymmetry"] = ((L["relief_m"] - R["relief_m"]) / tot
                            if tot > 1e-6 else float("nan"))
        out["valley_top_width_m"] = vfw + L["run_m"] + R["run_m"]
        out["complete"] = bool(L["found"] and R["found"])
    else:
        # Vf needs both divides; asymmetry needs both walls.
        for k in ("vf", "asymmetry"):
            out[k] = float("nan")
        out["valley_top_width_m"] = (
            vfw + sum(h["run_m"] for h in have) * (2.0 / len(have))
            if have else float("nan"))
        out["complete"] = False

    out["side_slope_class"] = side_slope_class(out.get("side_slope"))
    # Vf from a truncated search is a LOWER bound: the real floor-width ratio
    # can only be larger, because the divides are further out than measured.
    out["vf_is_lower_bound"] = bool(not out.get("complete", False))
    if out["unbounded"]:
        out["vf_class"] = "very_broad"
    else:
        out["vf_class"] = vf_class(out.get("vf"))
    return out


def _cut_sections(dem_path: str, lines, ids, spacing_m: float,
                  half_width_m: float, point_spacing_m: float):
    """Cut sections along EACH line feature, keeping that feature's id.

    ``extract_cross_sections`` merges a multi-feature layer into a single
    centreline. That is right for one reach and wrong for a network: on a
    222-segment fixture it produced **20 sections covering 9 segments**, and
    every one of them carried the same id, so the join to the segments matched
    nothing. A branching network cannot be one line.

    So this walks the features itself, reusing the exact geometry convention
    ``extract_cross_sections`` uses (origin on the line, left-hand normal from
    the local tangent) so offsets, bearings and the resulting polygons stay
    interchangeable between the two. One DEM handle for the whole network,
    rather than one open per feature.
    """
    import math

    import rasterio
    from shapely.geometry import LineString, MultiLineString
    from shapely.ops import linemerge

    out: list[dict[str, Any]] = []
    offsets = _section_offsets(half_width_m, point_spacing_m)
    xs_id = 0

    with rasterio.open(dem_path) as src:
        nodata = src.nodata
        for geom, sid in zip(lines, ids):
            if geom is None or geom.is_empty:
                continue
            parts = ([geom] if isinstance(geom, LineString)
                     else list(getattr(linemerge(geom), "geoms", [geom]))
                     if isinstance(geom, MultiLineString) else [])
            for part in parts:
                if not isinstance(part, LineString) or part.length <= 0:
                    continue
                # A segment shorter than the spacing still deserves one
                # section at its midpoint, or short reaches vanish entirely.
                if part.length < spacing_m:
                    stations = [part.length / 2.0]
                else:
                    stations = list(np.arange(spacing_m / 2.0, part.length,
                                              spacing_m))
                # Tangent baseline. Lengthening this to fight raster
                # staircase noise was tried and measured: it fixes nothing the
                # median case cares about (bearing spread within a reach is
                # already 0.0 degrees at every baseline tested) and it costs a
                # third of all measurements, because an over-smoothed tangent
                # is no longer perpendicular to a sinuous valley and the
                # divide search goes off at an angle.
                #
                #   baseline        bearing spread    sides measured
                #   one cell            0.0 deg            95%
                #   3x cell             0.0 deg            87%
                #   spacing/4           0.0 deg            75%
                #   spacing/2           0.0 deg            62%
                #
                # The handful of reaches that DO swing wildly are ones holding
                # pieces of two branches; that is handled where it belongs, in
                # valley_polygons, which un-twists a pair rather than blurring
                # every section in the dataset to hide it.
                delta = max(point_spacing_m, part.length / 1000.0)

                for st in stations:
                    origin = part.interpolate(float(st))
                    a = part.interpolate(max(0.0, st - delta))
                    b = part.interpolate(min(part.length, st + delta))
                    tx, ty = b.x - a.x, b.y - a.y
                    n = math.hypot(tx, ty)
                    if n == 0:
                        continue
                    tx, ty = tx / n, ty / n
                    nx, ny = -ty, tx                  # left-hand normal, as cut
                    coords = [(origin.x + nx * o, origin.y + ny * o)
                              for o in offsets]
                    vals = []
                    for v in src.sample(coords, indexes=1):
                        raw = float(v[0])
                        bad = math.isnan(raw) or (
                            nodata is not None and raw == nodata)
                        vals.append(np.nan if bad else raw)
                    out.append({
                        "xs_id": xs_id,
                        "station": float(st),
                        "segment_id": sid,
                        "center_x": origin.x,
                        "center_y": origin.y,
                        "bearing_deg": round(
                            math.degrees(math.atan2(tx, ty)) % 360, 2),
                        "offsets": offsets,
                        "z": np.asarray(vals, dtype=float),
                        "coords": coords,
                    })
                    xs_id += 1
    return out


def _offset_line(coords, cx: float, cy: float, off_a_m, off_b_m,
                 mpu: float):
    """The piece of a section between two offsets given in metres."""
    import math

    from shapely.geometry import LineString

    try:
        a_m, b_m = float(off_a_m), float(off_b_m)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(a_m) and math.isfinite(b_m)) or len(coords) < 2:
        return None
    (x0, y0), (x1, y1) = coords[0], coords[-1]
    n = math.hypot(x1 - x0, y1 - y0)
    if n == 0:
        return None
    ux, uy = (x1 - x0) / n, (y1 - y0) / n        # towards +offset
    a, b = a_m / mpu, b_m / mpu
    if abs(b - a) < 1e-9:
        b = a + 1e-3
    return LineString([(cx + ux * a, cy + uy * a), (cx + ux * b, cy + uy * b)])


def _measured_line(coords, cx: float, cy: float, m: dict, mpu: float):
    """Divide to divide; a side without a divide runs to the search limit."""
    import math

    (x0, y0), (x1, y1) = coords[0], coords[-1]
    half_m = math.hypot(x1 - x0, y1 - y0) * mpu / 2.0
    left = m.get("divide_offset_left_m")
    right = m.get("divide_offset_right_m")
    left = left if left is not None and math.isfinite(left) else -half_m
    right = right if right is not None and math.isfinite(right) else half_m
    return _offset_line(coords, cx, cy, left, right, mpu)


def valley_catchments(flow_dir: str | Path,
                      segments: str | Path,
                      id_field: str | None = None,
                      attributes: str | Path | None = None,
                      streams: str | Path | None = None,
                      min_area_m2: float | None = None,
                      out_prefix: str = "valley_extent",
                      guard: Guard | None = None) -> str:
    """The valley of a reach: every cell that drains to it. Floor to ridgetop.

    The first attempt at this walked each cross-section out to its measured
    divide and stitched the divide points into a ribbon. On five straight
    parallel troughs that looked right. On real terrain it is shards: the
    divide distance jumps between neighbouring sections -- 300 m here, 2500 m
    two hundred metres downstream -- so joining them across the gap between
    sections makes long thin triangles. 2,186 of them covering 55% of the
    Ruidoso DEM, and every summary statistic said it was fine, because
    "tiles perfectly, 1.00x overlap" is true of shards too. Drawing it was
    what showed it.

    A valley does not need to be stitched, because it already has a
    definition: the ground that drains to the reach. Its boundary IS the
    drainage divide, which is what "valley bottom to ridgetop" means, and it
    comes off the flow-direction raster that conditioning already paid for.

    * The boundary is a real ridgeline, not a line between two measurements.
    * Reaches tile with no overlap and no gaps, because every cell flows to
      exactly one of them.
    * Nothing has to be un-twisted, repaired, or resolved afterwards.

    What this does NOT do is measure the valley -- that is
    :func:`valley_form`, and its cross-sections are still what decide the
    Rosgen type. This decides the shape the type is drawn on.

    A cell is assigned to the reach its flow path reaches first. Cells that
    never reach one (they leave the DEM, or hit a flat with no direction) are
    left out and the fraction is reported rather than quietly assigned.
    """
    import geopandas as gpd
    import numpy as np
    import pandas as pd
    import rasterio
    from rasterio import features

    guard = guard or Guard.from_env()
    seg = gpd.read_file(str(guard.read_path(segments)))
    if id_field is None:
        for cand in ("seg_id", "segment_id", "valley_id"):
            if cand in seg.columns:
                id_field = cand
                break
    if id_field is None:
        raise GeolensError(
            f"{segments} has none of seg_id / segment_id / valley_id, so its "
            f"reaches cannot be identified."
        )

    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)

    with rasterio.open(str(guard.read_path(flow_dir))) as src:
        fdir = np.ascontiguousarray(src.read(1), dtype=np.int32)
        transform, crs, (H, W) = src.transform, src.crs, fdir.shape
        cell_area = abs(transform.a * transform.e)

    # Burn the reaches onto the grid. `all_touched` because a segment line is
    # a vectorised stream and can run along a cell edge; missing the cell it
    # belongs to would send everything upstream of it hunting for a label it
    # never finds.
    ids = list(seg[id_field])
    codes = {v: i + 1 for i, v in enumerate(ids)}          # 0 means unlabelled
    label = features.rasterize(
        ((g, codes[v]) for g, v in zip(seg.geometry, ids)
         if g is not None and not g.is_empty),
        out_shape=(H, W), transform=transform, fill=0,
        all_touched=True, dtype="int32")

    # A reach burned onto cells the flow never visits is a reach with no
    # valley. Intersecting with the stream raster is not the fix -- it would
    # drop exactly the off-by-one-cell case `all_touched` is there to catch --
    # but it IS worth knowing about.
    if streams is not None:
        with rasterio.open(str(guard.read_path(streams))) as src:
            sr = src.read(1)
        stray = int(((label > 0) & (sr <= 0)).sum())
        if stray > 0.5 * int((label > 0).sum()):
            note(f"  {stray:,} of {int((label>0).sum()):,} burned reach cells "
                 f"are not on the stream raster — the segments layer and the "
                 f"flow grid may not be from the same run")

    label = _assign_to_reach(fdir, label)

    assigned = label > 0
    frac = float(assigned.mean())
    if frac <= 0:
        raise GeolensError(
            "No cell drains to any reach. The segments layer and the flow "
            "direction raster are probably not from the same DEM."
        )

    floor = (min_area_m2 if min_area_m2 is not None else 4.0 * cell_area)
    rows: dict[int, list] = {}
    for geom, val in features.shapes(label, mask=assigned,
                                     transform=transform, connectivity=8):
        rows.setdefault(int(val), []).append(geom)

    from shapely.geometry import shape
    from shapely.ops import unary_union

    back = {v: k for k, v in codes.items()}
    m2 = _crs_m2(crs)
    recs, geoms, mended = [], [], 0
    for code, shapes_ in rows.items():
        parts = [shape(g) for g in shapes_]
        parts = [g for g in parts if g.area >= floor] or parts
        g = unary_union(parts)
        if not g.is_valid:
            # Polygonised cell boundaries meet at single points on a diagonal,
            # which is a self-touching ring. Shapely calls that invalid and
            # most GIS software will refuse to clip or dissolve it, so it is
            # repaired here rather than left for the user to discover.
            from shapely.validation import make_valid
            g = make_valid(g)
            mended += 1
        if g.is_empty or g.area <= 0:
            continue
        # CRS units squared are not square metres: this grid is US survey
        # feet, where the difference is a factor of 10.76.
        recs.append({id_field: back[code],
                     "valley_area_m2": float(g.area) * m2})
        geoms.append(g)

    gdf = gpd.GeoDataFrame(pd.DataFrame(recs), geometry=geoms, crs=crs)
    gdf["valley_area_km2"] = gdf["valley_area_m2"] / 1e6
    if mended:
        note(f"  {mended} valley boundary(ies) repaired where cells meet at "
             f"a corner")

    if attributes is not None:
        ap = str(guard.read_path(attributes))
        att = (pd.read_csv(ap) if ap.lower().endswith(".csv")
               else gpd.read_file(ap).drop(columns="geometry", errors="ignore"))
        # The classified layer calls it `seg_id` and the sections call it
        # `segment_id`. Assuming one name is how this join silently matched
        # nothing once already, taking the valley-type columns with it.
        key = next((c for c in (id_field, "segment_id", "seg_id")
                    if c in att.columns), None)
        if key is None:
            note(f"  attributes have no id column matching {id_field!r}, so "
                 f"the polygons carry no valley type")
        else:
            att = att.drop_duplicates(subset=[key])
            if key != id_field:
                att = att.rename(columns={key: id_field})
            before = len(gdf)
            gdf = gdf.merge(att, on=id_field, how="left", suffixes=("", "_att"))
            assert len(gdf) == before, "the attribute join duplicated polygons"
            if "valley_type" in gdf.columns:
                n = int(gdf["valley_type"].notna().sum())
                note(f"  {n}/{len(gdf)} valleys carry a valley type")

    path = out_dir / "valley_extent.gpkg"
    gdf.to_file(str(path), driver="GPKG")
    note(f"valley extent: {len(gdf)} valleys covering {frac:.0%} of the DEM"
         + ("" if frac > 0.95 else
            f" — the rest drains off the edge or into a flat"))
    return str(path)


def _crs_m2(crs) -> float:
    """Square metres per square CRS unit. A US survey foot is not a foot."""
    try:
        return float(crs.linear_units_factor[1]) ** 2
    except Exception:                                        # noqa: BLE001
        return 1.0


def _assign_to_reach(fdir: "np.ndarray", label: "np.ndarray") -> "np.ndarray":
    """Label every cell with the reach its flow path reaches first.

    The same walk HAND does, carrying an id instead of an elevation, and with
    the same two hard-won details: the path buffer is capped so it does not
    scale with the grid (a 1.94-billion-cell DEM would otherwise ask for
    15.5 GB before anything else), and the cycle guard counts steps
    separately from the buffer index, or capping the buffer silently removes
    the only protection against a loop in the flow directions.
    """
    import numpy as np
    from numba import njit

    from .flow import _CODES, _DR, _DC

    @njit(cache=True)
    def _walk(fdir, label, codes, dr, dc, cap):
        H, W = label.shape
        out = label.copy()
        path = np.empty(cap, dtype=np.int64)
        for r0 in range(H):
            for c0 in range(W):
                if out[r0, c0] != 0:
                    continue
                r, c, n, steps = r0, c0, 0, 0
                limit = H * W
                found = 0
                while True:
                    if out[r, c] != 0:
                        found = out[r, c]
                        break
                    if n < cap:
                        path[n] = r * W + c
                        n += 1
                    steps += 1
                    f = fdir[r, c]
                    if f == 0 or steps >= limit:
                        break
                    moved = False
                    for k in range(8):
                        if f == codes[k]:
                            rr = r + dr[k]
                            cc = c + dc[k]
                            if 0 <= rr < H and 0 <= cc < W:
                                r, c = rr, cc
                                moved = True
                            break
                    if not moved:
                        break
                if found != 0:
                    for i in range(n):
                        idx = path[i]
                        out[idx // W, idx % W] = found
        return out

    cap = max(1, min(label.size, 1_000_000))
    return _walk(np.ascontiguousarray(fdir, dtype=np.int32),
                 np.ascontiguousarray(label, dtype=np.int32),
                 _CODES, _DR, _DC, cap)


def valley_form(dem: str | Path,
                centerline: str | Path,
                segments: str | Path | None = None,
                id_field: str | None = None,
                spacing_m: float = 250.0,
                max_search_m: float = DEFAULT_MAX_SEARCH_M,
                floor_tolerance_m: float = FLOOR_TOLERANCE_M,
                min_rise_m: float = 5.0,
                thalweg_search_m: float = 200.0,
                point_spacing_m: float | None = None,
                line_layer: str | None = None,
                out_prefix: str = "valley_form",
                guard: Guard | None = None) -> ValleyFormResult:
    """Measure valley cross-section form from the floor to the divides.

    Parameters
    ----------
    centerline
        The valley axis -- the stream network or segments layer. One section is
        cut every ``spacing_m`` along it.

        Sections are cut along **each feature separately**, keeping that
        feature's id, so a branching network is measured as a network. Pass
        ``segments`` when the centreline and the segments are different
        layers.
    segments, id_field
        The classified segments to attribute each section to, and the column
        holding their id (auto-detected from ``seg_id`` / ``segment_id`` when
        not given).

        Without this every section came back with ``segment_id = 0`` and the
        join in :func:`~geolens.landscape.rosgen.rosgen_valley_types` matched
        nothing -- the valley-form evidence was computed correctly and then
        silently discarded. Each section is assigned to the segment nearest its
        own centre point, so a section is attributed to the reach it actually
        crosses rather than to whichever feature happened to come first in the
        merge.
    max_search_m
        How far to look for the divide on each side. The section is cut
        ``2 * max_search_m`` wide. Too small and broad valleys come back
        incomplete (which is reported, not hidden); too large and every section
        costs more and may cross into the next drainage before finding a crest.
    min_rise_m
        How far above the floor a crest has to be before it counts as a divide.
        Below this, low terrace risers and bar tops read as valley walls.
    thalweg_search_m
        How far either side of the centreline to look for the valley floor.
        The section is long enough to reach the divides, so it usually crosses
        into the next drainage; searching the whole transect for the low point
        finds that neighbour's floor instead of this valley's. Raise it only if
        the centreline is badly offset from the channel.

    The per-section table is the useful output. The segment aggregation uses
    **medians**, because a single section that crosses a tributary junction
    produces a wild Vf and a mean carries it into the segment.
    """
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import LineString

    t0 = time.monotonic()
    guard = guard or Guard.from_env()
    warnings: list[str] = []

    import rasterio

    dem_path = str(guard.read_path(dem))
    cl = gpd.read_file(str(guard.read_path(centerline)), layer=line_layer)
    if cl.empty:
        raise GeolensError(f"{centerline} has no features.")

    with rasterio.open(dem_path) as _src:
        dem_crs = _src.crs
        cell = abs(_src.res[0])                 # CRS units
        _cell_y = abs(_src.res[1])

    # Every parameter here is documented in metres, and the section geometry is
    # built directly in the DEM's CRS. Without converting, a US survey-foot grid
    # -- the normal case for US lidar -- silently reinterprets all of them:
    # max_search_m=3000 reaches 914 m per side, so divides genuinely in range
    # are never found and the run then blames the terrain; spacing_m=250
    # becomes 76 m, quadrupling the section count; and valley_floor_width_m,
    # valley_top_width_m and valley_relief_m are written in FEET under `_m`
    # names. (vf, shape_n and side_slope are ratios of like units and survive.)
    from ..units import unit_info

    _u = unit_info(dem_crs, cell, _cell_y)
    mpu = _u.metres_per_unit
    if not _u.is_metric:
        warnings.extend(_u.warnings)
    cell_m = _u.cell_size_m
    if cl.crs is not None and dem_crs is not None and cl.crs != dem_crs:
        cl = cl.to_crs(dem_crs)
    line_crs = dem_crs if dem_crs is not None else cl.crs

    # Each feature keeps its own id, so sections can be joined back.
    id_col = id_field
    if id_col is None:
        for cand in ("seg_id", "segment_id", "SEG_ID", "id"):
            if cand in cl.columns:
                id_col = cand
                break
    ids = (cl[id_col].tolist() if id_col in cl.columns
           else list(range(len(cl))))

    # Convert the metre parameters into CRS units for the geometry work.
    step_m = float(point_spacing_m or max(cell_m, 1.0))
    note(f"cutting valley sections every {spacing_m:g} m along {len(cl)} "
         f"feature(s), {2 * max_search_m:g} m wide, {step_m:g} m sampling"
         + ("" if _u.is_metric else f"  [grid is {_u.horizontal_unit}]"))
    raw = _cut_sections(dem_path, cl.geometry.tolist(), ids,
                        spacing_m=spacing_m / mpu,
                        half_width_m=float(max_search_m) / mpu,
                        point_spacing_m=step_m / mpu)
    if not raw:
        raise GeolensError(
            "No cross-sections could be cut. Check the centerline overlaps the "
            "DEM and is in the same CRS."
        )

    rows: list[dict[str, Any]] = []
    geoms: list[Any] = []
    floors: list[Any] = []
    for s in progress(raw, label="measuring valley form"):
        # Offsets are in CRS units and z is in the DEM's vertical unit; both
        # become metres here so every measurement below is metric.
        offsets = s["offsets"] * mpu
        z = s["z"] * _u.vertical_metres_per_unit
        if z.size < 7 or not np.isfinite(z).any():
            continue
        thalweg_i = _thalweg_index(offsets, z, thalweg_search_m)
        if thalweg_i is None:
            continue

        m = _measure_section(offsets, z, thalweg_i, floor_tolerance_m, min_rise_m)
        if m is not None:
            # The section spans +/- this, in metres. Needed to turn a divide
            # offset back into a position along the section geometry.
            m["section_half_width_m"] = float(max_search_m)
        if m is None:
            continue
        m["xs_id"] = s["xs_id"]
        # Stations are cut in CRS units; on a foot grid "station_m" was feet.
        m["station_m"] = float(s["station"]) * mpu
        m["segment_id"] = s["segment_id"]
        m["_cx"] = s["center_x"]
        m["_cy"] = s["center_y"]
        rows.append(m)

        # The line on the map is the part that was MEASURED: divide to
        # divide. It used to be the whole cut, 2 x max_search_m -- 12 km on
        # every Ruidoso section, crossing the neighbouring valleys, which is
        # what made the layer unreadable. A side whose divide was not found
        # runs to the end of the search (divide_found_left/right = False).
        xy = s["coords"]
        geoms.append(_measured_line(xy, s["center_x"], s["center_y"], m, mpu))
        floors.append(_offset_line(xy, s["center_x"], s["center_y"],
                                   m.get("floor_offset_left_m"),
                                   m.get("floor_offset_right_m"), mpu))

    if not rows:
        raise GeolensError(
            "No section yielded a valley form measurement. The usual cause is "
            "min_rise_m set above the actual valley relief."
        )

    df = pd.DataFrame(rows)

    # --- attribute each section to the segment it crosses --------------------
    if segments is not None and str(segments) != str(centerline):
        seg = gpd.read_file(str(guard.read_path(segments)))
        field = id_field
        if field is None:
            for cand in ("seg_id", "segment_id", "SEG_ID", "id"):
                if cand in seg.columns:
                    field = cand
                    break
        if field is None:
            warnings.append(
                f"No id column found on {Path(str(segments)).name} (looked for "
                f"seg_id, segment_id, id). Sections keep their merge order id, "
                f"so a join to the segments will not match — pass id_field."
            )
        elif df["_cx"].isna().all():
            warnings.append(
                "Sections carry no centre coordinates, so they could not be "
                "attributed to segments."
            )
        else:
            pts = gpd.GeoDataFrame(
                df[["xs_id"]].copy(),
                geometry=gpd.points_from_xy(df["_cx"], df["_cy"]),
                crs=line_crs,
            )
            if seg.crs is not None and pts.crs is not None and seg.crs != pts.crs:
                seg = seg.to_crs(pts.crs)
            joined = gpd.sjoin_nearest(pts, seg[[field, "geometry"]],
                                       how="left", distance_col="_dist")
            joined = joined.drop_duplicates(subset="xs_id", keep="first")
            # Rename before merging. `df` already has a `segment_id` column, so
            # when the segments layer also names its id `segment_id` -- which
            # `id_field` explicitly invites, and which auto-detect picks when
            # there is no `seg_id` -- pandas disambiguates to `segment_id_x`
            # and `segment_id_y` and the next line raises KeyError, AFTER every
            # section has been cut and measured.
            attr = joined[["xs_id", field, "_dist"]].rename(
                columns={field: "_attr_segment_id"})
            df = df.merge(attr, on="xs_id", how="left")
            df["segment_id"] = df["_attr_segment_id"]
            n_attr = int(df["segment_id"].notna().sum())
            note(f"attributed {n_attr} of {len(df)} sections to segments "
                 f"by nearest centre")
            if n_attr < len(df):
                warnings.append(
                    f"{len(df) - n_attr} sections could not be attributed to a "
                    f"segment. They are kept with a null segment_id and will "
                    f"not join."
                )
    df = df.drop(columns=[c for c in ("_cx", "_cy", "_dist", "_attr_segment_id")
                          if c in df.columns])

    n_complete = int(df["complete"].sum())
    n_unb = int(df["unbounded"].sum())
    if n_complete < 0.5 * len(df):
        warnings.append(
            f"{n_complete} of {len(df)} sections reached a divide on both sides "
            f"within max_search_m={max_search_m:g} m; {n_unb} ran out of search "
            f"without climbing at all, which is itself evidence of a broad "
            f"valley and is reported as vf_class='very_broad'. The remainder "
            f"are LOWER BOUNDS — their Vf can only be larger and their side "
            f"slopes gentler than shown. Read the `quality` column: 'measured' "
            f"means both divides were found."
        )

    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)

    table = out_dir / "valley_form_sections.csv"
    df.to_csv(table, index=False)

    sec_path = out_dir / "valley_form_sections.gpkg"
    # Re-read the (possibly re-ordered) frame so the written attributes carry
    # the attributed segment_id rather than the pre-join one.
    by_xs = {int(r["xs_id"]): r for r in df.to_dict("records")}
    valid = [(g, by_xs.get(int(r["xs_id"]), r))
             for g, r in zip(geoms, rows) if g is not None]
    if valid:
        gpd.GeoDataFrame(
            pd.DataFrame([r for _, r in valid]),
            geometry=[g for g, _ in valid],
            crs=line_crs,
        ).to_file(str(sec_path), driver="GPKG")
    fl = [(g, by_xs.get(int(r["xs_id"]), r))
          for g, r in zip(floors, rows) if g is not None]
    if fl:
        keep = ("xs_id", "segment_id", "station_m", "valley_floor_width_m",
                "floor_z", "incision_m")
        gpd.GeoDataFrame(
            pd.DataFrame([{k: r.get(k) for k in keep} for _, r in fl]),
            geometry=[g for g, _ in fl], crs=line_crs,
        ).to_file(str(out_dir / "valley_form_floors.gpkg"), driver="GPKG")

    # -- aggregate to segments, on medians ----------------------------------
    #
    # Over ALL sections, not only complete ones. Filtering to complete sections
    # dropped segment coverage from 222 to 5 on the fan fixture -- a handful of
    # sections happening to find both crests silently deleted every other
    # segment from the output. Completeness is reported per segment instead, so
    # the caller can filter knowing what it costs.
    keep = df
    agg = (keep.groupby("segment_id")
           .agg(n_sections=("xs_id", "size"),
                n_complete=("complete", "sum"),
                n_unbounded=("unbounded", "sum"),
                vf=("vf", "median"),
                side_slope=("side_slope", "median"),
                side_slope_left=("side_slope_left", "median"),
                side_slope_right=("side_slope_right", "median"),
                shape_n=("shape_n", "median"),
                valley_relief_m=("valley_relief_m", "median"),
                valley_floor_width_m=("valley_floor_width_m", "median"),
                valley_top_width_m=("valley_top_width_m", "median"),
                asymmetry=("asymmetry", "median"))
           .reset_index())
    agg["side_slope_class"] = [side_slope_class(v) for v in agg["side_slope"]]
    agg["vf_class"] = [vf_class(v) for v in agg["vf"]]
    # A segment whose sections mostly ran out of search without climbing is a
    # broad valley, and saying "unknown" about it would be wrong.
    broad = (agg["n_unbounded"] > 0.5 * agg["n_sections"])
    agg.loc[broad, "vf_class"] = "very_broad"
    agg["quality"] = np.where(
        agg["n_complete"] > 0, "measured",
        np.where(broad, "unbounded_broad", "lower_bound"))
    # A valley that is one-sided in the SAME direction section after section is
    # the structural-control signature. A sign that flips is just meander.
    agg["persistent_asymmetry"] = agg["asymmetry"].abs() > 0.35

    seg_table = out_dir / "valley_form_segments.csv"
    agg.to_csv(seg_table, index=False)

    elapsed = time.monotonic() - t0
    note(f"valley form: {len(df)} sections, {n_complete} complete, "
         f"in {elapsed:.1f}s")
    return ValleyFormResult(
        sections=str(sec_path), table=str(table), segment_table=str(seg_table),
        n_sections=len(df), n_measured=n_complete,
        median_vf=float(np.nanmedian(df["vf"])) if len(df) else float("nan"),
        median_side_slope=float(np.nanmedian(df["side_slope"])) if len(df) else float("nan"),
        median_shape_n=(float(np.nanmedian(df["shape_n"]))
                        if len(df) and np.isfinite(df["shape_n"]).any()
                        else float("nan")),
        seconds=elapsed, warnings=warnings,
    )


def valley_polygons(alloc_raster: str | Path,
                    attributes: str | Path | None = None,
                    out_prefix: str = "valley_extent",
                    guard: Guard | None = None) -> str:
    """The valley of a reach as a polygon: its share of the valley bottom.

    :func:`valley_catchments` draws every cell that drains to a reach, which
    is a watershed, not a valley -- the Basin tool's answer, and on Kingman
    (Oct 2026) 560 MB of hillslope. This draws the valley bottom the
    evidence extraction found (HAND and slope), split between the reaches
    by the nearest-reach allocation ``classify_segments`` already made
    (``valley_alloc.tif``: seg_id + 1 per valley-bottom cell), and carries
    each reach's valley types on its polygon. Floor to the valley margin,
    not to the ridgetop; the sections in :func:`valley_form` measure the
    walls.

    Writes ``<out_prefix>_dir/valley_extent.gpkg``.
    """
    import geopandas as gpd
    import pandas as pd
    import rasterio
    from rasterio import features
    from shapely.geometry import shape
    from shapely.ops import unary_union

    guard = guard or Guard.from_env()
    t0 = time.monotonic()
    with rasterio.open(str(guard.read_path(alloc_raster))) as src:
        alloc = src.read(1)
        transform, crs = src.transform, src.crs
    parts: dict[int, list] = {}
    for geom, value in features.shapes(alloc, mask=alloc > 0,
                                       transform=transform):
        parts.setdefault(int(value) - 1, []).append(shape(geom))
    del alloc
    ids = sorted(parts)
    geoms = [unary_union(parts[i]) if len(parts[i]) > 1 else parts[i][0]
             for i in ids]
    gdf = gpd.GeoDataFrame({"seg_id": ids}, geometry=geoms, crs=crs)
    # true metres, whatever the CRS's unit
    mpu = 1.0
    try:
        from ..units import unit_info

        mpu = float(unit_info(crs, abs(transform.a), abs(transform.e))
                    .metres_per_unit or 1.0) if crs is not None else 1.0
    except Exception:                                        # noqa: BLE001
        mpu = 1.0
    gdf["valley_area_m2"] = gdf.geometry.area * mpu * mpu
    gdf["valley_area_km2"] = gdf["valley_area_m2"] / 1e6
    if attributes is not None:
        attr = gpd.read_file(str(guard.read_path(attributes)))
        if "seg_id" in attr.columns:
            cols = [c for c in attr.columns
                    if c not in ("geometry", "valley_area_m2",
                                 "valley_area_km2", "seg_id")]
            gdf = gdf.merge(pd.DataFrame(attr[["seg_id"] + cols]), on="seg_id",
                            how="left")
    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "valley_extent.gpkg"
    gdf.to_file(str(path), driver="GPKG", layer="valley_extent")
    note(f"  valley polygons: {len(gdf):,} reaches, "
         f"{gdf['valley_area_m2'].sum() / 1e6:,.1f} km2 of valley bottom, "
         f"{time.monotonic() - t0:.1f}s")
    return str(path)
