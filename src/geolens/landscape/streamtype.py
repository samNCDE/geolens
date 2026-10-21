"""Rosgen **stream** types (A-G), which are not Rosgen valley types.

Rosgen's system is two classifications and geolens only had one. Valley types
I-XI describe the landform the channel sits in and are discriminated by
hillslope properties -- divide spacing, wall slope, cross-section shape. Stream
types A-G describe the *channel* and are discriminated by four measurements of
the channel itself:

======================  ==========================================
entrenchment ratio      flood-prone width / bankfull width, where
                        flood-prone width is measured at twice the
                        maximum bankfull depth (Rosgen 1994)
width/depth ratio       bankfull width / mean bankfull depth
sinuosity               channel length / valley length
slope                   water-surface slope, taken here as the
                        channel gradient over the reach
======================  ==========================================

Putting valley-type candidates on the stream network, which is what this
package did, is a category error: it labels a line with a property of the
ground either side of it. The valley type belongs on the valley polygon; the
stream type belongs on the line.

**The number is not the letter.** A full Rosgen call is a letter and a
substrate number -- C4 is a gravel-bed C. The number is a bed-material
observation and a DEM cannot see the bed, so it is left off unless a geology
layer supports a call, and then it is reported as what it is: a prior from a
map, not a pebble count.

**Resolution decides whether this is measurable at all.** Bankfull width and
depth need a DEM that resolves the channel. At 10 m a 6 m channel is one cell
and the measurement is meaningless; at 1 m it is real. So the discriminators
that cannot be measured are reported as unknown and the key returns every type
still consistent with the ones that could -- the same shape as the geology flag
on valley types. Pass ``dem_fine`` to measure the channel on the native grid
while everything upstream stays on a coarse one.
"""
from __future__ import annotations

import os

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..safety import Guard, GeolensError, note

# -- the key ------------------------------------------------------------------
#
# Rosgen (1994, 1996) Level II, letter only. Ranges are (low, high] pairs;
# None means the criterion does not discriminate that type.
#
# Sources of disagreement between published versions of this table are real and
# are resolved here toward the 1996 book: sinuosity for B is ">1.2", for C
# ">1.4", for E ">1.5", and G is "2-3.9% slope, entrenched, low W/D". Where a
# published boundary is a soft "typically", the range here is widened rather
# than narrowed -- this returns CANDIDATES, and excluding a real type is worse
# than carrying one extra.

#
# SLOPE IS NOT A CUT. Rosgen's key delineates on entrenchment and width/depth
# (and sinuosity), and gives each letter a TYPICAL slope; a reach outside it
# keeps its letter and gets a slope suffix -- Bc is a B at under 2%, Cb a C at
# 2-4%, Fb, Gc, and Aa+ is an A over 10%. The first version made slope a hard
# criterion, and on Eagle Creek -- the first run at a resolution that resolves
# the channel -- 6 of 13 reaches came back "no type fits", including one at
# ER 1.51, W/D 29, 1.5% slope: a textbook Bc. So Aa+ is no longer its own
# entry: it is A with the a+ slope class, which is how Rosgen writes it.

_KEY: list[tuple[str, dict[str, tuple[float | None, float | None]]]] = [
    ("A",   {"er": (None, 1.4), "wd": (None, 12.0), "sin": (1.0, 1.2),
             "slope": (0.04, None)}),
    ("B",   {"er": (1.4, 2.2),  "wd": (12.0, None), "sin": (1.2, None),
             "slope": (0.02, 0.04)}),
    ("C",   {"er": (2.2, None), "wd": (12.0, None), "sin": (1.4, None),
             "slope": (None, 0.02)}),
    ("D",   {"er": (None, None), "wd": (40.0, None), "sin": (None, 1.4),
             "slope": (None, 0.04)}),
    ("DA",  {"er": (2.2, None), "wd": (None, 40.0), "sin": (None, None),
             "slope": (None, 0.005)}),
    ("E",   {"er": (2.2, None), "wd": (None, 12.0), "sin": (1.5, None),
             "slope": (None, 0.02)}),
    ("F",   {"er": (None, 1.4), "wd": (12.0, None), "sin": (1.4, None),
             "slope": (None, 0.02)}),
    ("G",   {"er": (None, 1.4), "wd": (None, 12.0), "sin": (1.2, None),
             "slope": (0.02, 0.04)}),
]

STREAM_TYPE_DESC = {
    "Aa+": "very steep, deeply entrenched, debris-transport torrent",
    "A": "steep, entrenched, cascading step/pool",
    "B": "moderately entrenched, moderate gradient, riffle-dominated",
    "C": "low-gradient, meandering, point-bar, broad floodplain",
    "D": "braided, multiple channels, aggrading",
    "DA": "anastomosed, multiple narrow channels, broad wetland floodplain",
    "E": "low-gradient, highly sinuous, low width/depth, stable banks",
    "F": "entrenched meandering riffle/pool in a low-gradient valley",
    "G": "entrenched gully, step/pool, moderate gradient, high erosion",
}

# Rosgen's substrate number, as far as a surficial geology map can support it.
# This is a PRIOR, never a measurement: the map says what the valley floor is
# made of, not what is on the bed at a riffle. Classes that do not imply a
# dominant calibre map to None rather than being forced.
# `None` is a real entry, not a gap: some map units do not imply a dominant
# bed calibre at all, and inventing one to fill the table is exactly the kind
# of guess the number is supposed to be protected from. Every class the
# geology module can produce must appear here, with a number or with None.
_SUBSTRATE_FROM_MATERIAL: dict[str, tuple[int | None, str]] = {
    "bedrock_igneous_metamorphic": (1, "bedrock-floored valley"),
    "residual": (1, "residual/pre-Quaternary rock"),
    "mass_movement": (2, "debris-flow and colluvial supply"),
    "ice_marginal": (3, "ice-contact and outwash gravel"),
    "glacial_till": (3, "till, cobble-dominated"),
    "alluvial": (4, "alluvium, gravel-dominated"),
    "eolian": (5, "eolian sand"),
    "coastal": (5, "coastal sand"),
    "lacustrine": (6, "lacustrine fines"),
    "playa": (6, "playa fines"),
    "organic": (6, "peat and muck"),
    "bedrock_sedimentary": (None,
                            "sedimentary bedrock — calibre depends on the "
                            "unit, from shale to conglomerate"),
    "marine": (None, "marine sediment — calibre not implied, beach sand to "
                     "offshore clay"),
}

SUBSTRATE_DESC = {1: "bedrock", 2: "boulder", 3: "cobble",
                  4: "gravel", 5: "sand", 6: "silt/clay"}

# The widest a measured bankfull may be, as a multiple of the width the
# drainage area supports, before it is treated as the delineator having found
# something else. Regional curves in the Southwest scatter by roughly +/-50%;
# a factor of three is well outside that, so this excludes misdetections and
# not genuinely wide channels. It is a bound on a generic curve, so it is kept
# loose on purpose -- tighten it only with a LOCAL regional curve.
MAX_BANKFULL_RATIO = 3.0


def _in(v: float | None, rng: tuple[float | None, float | None]) -> bool | None:
    """Is a measurement inside a range? ``None`` means "not measured"."""
    if v is None or not np.isfinite(v):
        return None
    lo, hi = rng
    if lo is None and hi is None:
        return None                      # criterion does not discriminate
    if lo is not None and v < lo:
        return False
    if hi is not None and v > hi:
        return False
    return True


def slope_class(slope: float | None) -> str | None:
    """Rosgen's slope class: a+, a, b, c, c-.

    Written after the letter (and after the substrate number when there is
    one): A2a+, B4c, C4b. Only appended when the slope is outside the
    letter's typical range -- a B at 3% is just B.
    """
    if slope is None or not np.isfinite(slope):
        return None
    if slope > 0.10:
        return "a+"
    if slope >= 0.04:
        return "a"
    if slope >= 0.02:
        return "b"
    if slope >= 0.001:
        return "c"
    return "c-"


# Rosgen's continuum: a measured entrenchment ratio may sit 0.2 either side of
# a boundary and a width/depth ratio 2.0 (Rosgen 1994, 1996). Misfit on the
# delineative criteria is counted in those units.
_TOL = {"er": 0.2, "wd": 2.0}
# Rosgen's chart, letter by letter: the slope classes each letter exists in,
# and how each is written (Rosgen 1994, 1996). A class missing from a letter's
# row means that combination is not a Rosgen type, and the letter is excluded
# at that slope whatever the entrenchment says -- a 13% reach is not a C.
#   A  4-10%, Aa+ >10%            B  Ba 4-10%, B 2-4%, Bc <2%
#   C  Cb 2-4%, C 0.1-2%, Cc- <0.1%   D  Db 2-4%, D <2%
#   DA <0.5%                       E  Eb 2-4%, E <2%
#   F  Fb 2-4%, F <2%              G  G 2-4%, Gc <2%
# An entrenched, low width/depth channel is an A above 4% and a G below it --
# slope is the whole difference -- so there is no Ab, and no Ga.
# One table sets both the exclusion and the suffix, so the two cannot
# disagree at a class boundary (v24 excluded C "above 4%" but classed 4.0%
# as "a", and wrote Ca at exactly 0.04).
_SLOPE_SUFFIX: dict[str, dict[str, str]] = {
    "A": {"a+": "a+", "a": ""},
    "B": {"a": "a", "b": "", "c": "c", "c-": "c"},
    "C": {"b": "b", "c": "", "c-": "c-"},
    "D": {"b": "b", "c": "", "c-": ""},
    "DA": {"c": "", "c-": ""},
    "E": {"b": "b", "c": "", "c-": ""},
    "F": {"b": "b", "c": "", "c-": ""},
    "G": {"b": "", "c": "c", "c-": "c"},
}
_DA_MAX_SLOPE = 0.005


def _exists(letter: str, slope: float | None) -> bool:
    """Is ``letter`` at this slope a type in Rosgen's chart?"""
    if slope is None or not np.isfinite(slope):
        return True
    if letter == "DA" and slope >= _DA_MAX_SLOPE:
        return False
    return slope_class(slope) in _SLOPE_SUFFIX[letter]


# Weights of the ranking criteria against one tolerance unit of the
# delineative ones. TUNING: slope counts 0.5 per doubling outside the typical
# range, sinuosity 0.25 per 0.1 -- sinuosity off a DEM-traced line over a
# 500 m reach is the noisiest of the four, so it ranks and rarely decides.
_W_SLOPE, _W_SIN = 0.5, 0.25
# Where only an unmeasurable property separates two types, the commoner one
# takes the letter and the other stays in the alternatives. E differs from C
# only in width/depth, which a regional basis never has.
_PRIOR = {"E": 0.1}
# Letters that are never the answer, only an alternative. DA (anastomosed) is
# multi-thread, which a single DEM-traced centreline cannot show; with no
# sinuosity range of its own it outranked C on every flat, straight reach --
# the parity fixture typed every reach under 0.5% as DA.
_NEVER_LETTER = {"DA"}
# How much better the best type must be than the next before it gets the
# letter. With entrenchment unmeasured a small margin is noise in slope and
# sinuosity, so the bar is higher.
_MARGIN_MEASURED, _MARGIN_UNMEASURED, _MARGIN_HIGH = 0.05, 0.25, 0.2


def _dist(v: float, rng: tuple[float | None, float | None], kind: str
          ) -> float:
    """How far outside a range a measurement is, in that criterion's unit."""
    lo, hi = rng
    if kind == "slope":
        v = max(v, 1e-4)
        if lo is not None and v < lo:
            return math.log2(lo / v)
        if hi is not None and v > hi:
            return math.log2(v / hi)
        return 0.0
    unit = {"er": _TOL["er"], "wd": _TOL["wd"], "sin": 0.1}[kind]
    if lo is not None and v < lo:
        return (lo - v) / unit
    if hi is not None and v > hi:
        return (v - hi) / unit
    return 0.0


def classify_stream_type(entrenchment: float | None, width_depth: float | None,
                         sinuosity: float | None, slope: float | None,
                         threads: float | None = None) -> dict[str, Any]:
    """Apply the Level II key to one reach: the nearest Rosgen type.

    **Entrenchment and width/depth delineate. Slope and sinuosity rank.**
    That is how Rosgen's key is built: the letter comes from how entrenched
    the channel is and its width/depth; each letter then has a TYPICAL slope
    and sinuosity, and a reach outside them keeps its letter with a slope
    suffix (Bc, Cb, Fb, Aa+).

    Every type gets a misfit: how far each measured criterion falls outside
    its range, the delineative ones in Rosgen's own tolerance units (0.2 of
    entrenchment, 2.0 of width/depth), slope and sinuosity at a lower weight.
    The letter is the type with the least misfit, when it beats the next one
    clearly. Unmeasured criteria add nothing to anyone, so they cannot pick a
    type -- a reach measured only on slope and sinuosity gets a letter only
    when those separate the types on their own (a 20% reach is an A with or
    without a bankfull), and otherwise returns the tied candidates.

    v23 counted criteria in range instead of measuring how far out they
    were, so every near-tie was a tie: on Eagle Creek 27 of 55 reaches had
    no letter. Measuring the misfit breaks the ties that data can break.
    """
    known = {k: float(v) for k, v in (("er", entrenchment),
                                      ("wd", width_depth),
                                      ("sin", sinuosity), ("slope", slope))
             if v is not None and np.isfinite(v)}
    key = dict(_KEY)
    hard = ("er", "wd")

    # D only when width/depth was MEASURED above 40. Without that it
    # survives on everything else and sweeps up every straight low-gradient
    # reach: 1,376 reaches in a mountain catchment called "possibly braided".
    # And (v0.38) only when the lidar sections hold more than one channel
    # at bankfull: a wide shallow bed with one thread is C or F, however
    # high its width/depth. Kingman's pilots had 48% of their length typed
    # D on width/depth alone, at a regional depth of 0.2 m on a 16 m bed.
    braided_possible = "wd" in known and known["wd"] > 40.0
    single_thread = (threads is not None and np.isfinite(threads)
                     and threads < 1.5)
    if braided_possible and single_thread:
        braided_possible = False

    mis: dict[str, float] = {}
    hard_out: dict[str, float] = {}
    for name, rng in _KEY:
        if name == "D" and not braided_possible:
            continue
        if not _exists(name, known.get("slope")):
            continue
        h = [_dist(known[k], rng[k], k) for k in hard if k in known]
        hard_out[name] = max(h, default=0.0)
        m = sum(h)
        if "slope" in known:
            m += _W_SLOPE * _dist(known["slope"], rng["slope"], "slope")
        if "sin" in known:
            m += _W_SIN * _dist(known["sin"], rng["sin"], "sin")
        mis[name] = m + _PRIOR.get(name, 0.0)

    order = sorted(mis, key=lambda n: mis[n])
    ranked = [n for n in order if n not in _NEVER_LETTER]
    best = ranked[0]
    runner = mis[ranked[1]] if len(ranked) > 1 else float("inf")
    margin = runner - mis[best]
    need = _MARGIN_MEASURED if "er" in known else _MARGIN_UNMEASURED
    letter = best if margin >= need else None
    near = [n for n in ranked if mis[n] - mis[best] < need]
    # The types Rosgen's continuum allows: every measured delineative
    # criterion within tolerance of the range.
    feasible = [n for n in order if hard_out[n] <= 1.0]

    suffix = None
    if letter and "slope" in known:
        suffix = _SLOPE_SUFFIX[letter][slope_class(known["slope"])] or None
    label = (letter + (suffix or "")) if letter else None

    sin_consistent = (None if "sin" not in known or not letter
                      else _in(known["sin"], key[letter]["sin"])
                      is not False)
    missing = [k for k in ("er", "wd", "sin", "slope") if k not in known]
    # Outside every box by more than Rosgen's continuum allows: the letter is
    # still the nearest type, but the measurements sit in a gap of the table
    # (or are wrong), and that is worth knowing.
    no_fit = bool(letter) and hard_out[letter] > 1.0
    if not letter:
        conf = "low"
    elif (all(k in known for k in hard) and hard_out[letter] == 0.0
          and "slope" in known and sin_consistent and margin >= _MARGIN_HIGH):
        # Slope is deliberately not part of this. A slope outside the
        # letter's typical range is what the suffix is FOR -- an A at 14% is
        # a confident Aa+, not a doubtful A.
        conf = "high"
    elif all(k in known for k in hard) and not no_fit:
        conf = "medium"
    else:
        conf = "low"
    return {
        "stream_type": label,
        "stream_letter": letter,
        "slope_class": suffix,
        "candidates": [letter] if letter else near,
        # The best type and every one close behind it, letter or not -- what
        # a field visit would be deciding between.
        "alternatives": [n for n in order
                         if mis[n] - mis[best] < _MARGIN_UNMEASURED],
        "all_candidates": feasible,
        "n_candidates": 1 if letter else len(near),
        "misfit": round(mis[best], 3),
        "margin": round(margin, 3) if np.isfinite(margin) else None,
        "measured": sorted(known),
        "missing": missing,
        "braided_possible": braided_possible,
        "single_thread": single_thread,
        "sinuosity_consistent": sin_consistent,
        "no_type_fits": no_fit,
        "confidence": conf,
    }


def _floodprone_width(points: list[tuple[float, float]], thalweg_z: float,
                      max_depth: float,
                      thalweg_offset: float | None = None) -> float | None:
    """Rosgen's flood-prone width: the section's width at 2x max bankfull depth.

    Not the same as the floodplain breakpoint geolens already finds. The
    floodplain edge is a break in slope; flood-prone width is a contour, taken
    at a stage twice the deepest point of the bankfull channel. Rosgen's
    entrenchment ratio is defined on the contour, so it is measured here rather
    than substituted.

    Measured from the thalweg outward on both sides, stopping at the first
    point above that stage -- so a section that clears the stage on one side
    and never on the other returns None rather than a width that ran to the
    end of the section.
    """
    e = _floodprone_edges(points, thalweg_z, max_depth, thalweg_offset)
    return None if e is None else abs(e[1] - e[0])


def _floodprone_edges(points: list[tuple[float, float]], thalweg_z: float,
                      max_depth: float,
                      thalweg_offset: float | None = None
                      ) -> tuple[float, float] | None:
    """Left and right offsets where the section crosses 2x max bankfull depth.

    Walks out from the THALWEG -- the given offset when there is one. It used
    to start from the lowest point anywhere on the section, and a section is
    cut twenty channel widths wide, so on a V-valley flank or near a
    confluence that could be somebody else's channel.
    """
    if not points or max_depth is None or not np.isfinite(max_depth) \
            or max_depth <= 0:
        return None
    stage = thalweg_z + 2.0 * max_depth
    pts = sorted(points)
    offs = [o for o, _ in pts]
    zs = [z for _, z in pts]
    if thalweg_offset is not None:
        ti = min(range(len(offs)), key=lambda i: abs(offs[i] - thalweg_offset))
    else:
        ti = min(range(len(zs)), key=lambda i: zs[i])

    def edge(step: int) -> float | None:
        i = ti
        while 0 <= i + step < len(zs):
            if zs[i + step] > stage:
                # Linear interpolation onto the stage, so the width does not
                # quantise to the sample spacing.
                z0, z1 = zs[i], zs[i + step]
                o0, o1 = offs[i], offs[i + step]
                if z1 == z0:
                    return o1
                f = (stage - z0) / (z1 - z0)
                return o0 + f * (o1 - o0)
            i += step
        return None

    left, right = edge(-1), edge(+1)
    if left is None or right is None:
        return None
    return (left, right)


def _sinuosity(geom) -> float | None:
    """Channel length over straight-line distance between the reach ends.

    Rosgen's denominator is *valley* length, not the chord. On a reach short
    enough that the valley runs straight through it these agree; on a long
    reach that curves with its valley the chord is shorter than the valley and
    sinuosity comes out too high. Reaches here are segment-length (500 m by
    default), which is the scale where they agree -- but the field is named
    for what is actually computed.
    """
    try:
        if geom is None or geom.is_empty or geom.length <= 0:
            return None
        cs = list(geom.coords) if geom.geom_type == "LineString" else None
        if cs is None:
            parts = list(getattr(geom, "geoms", []))
            if not parts:
                return None
            cs = list(parts[0].coords) + list(parts[-1].coords)
        (x0, y0), (x1, y1) = cs[0][:2], cs[-1][:2]
        chord = math.hypot(x1 - x0, y1 - y0)
        if chord <= 0:
            return None
        return float(geom.length / chord)
    except Exception:                                        # noqa: BLE001
        return None


# -- measurement --------------------------------------------------------------


_trapz = getattr(np, "trapezoid", None) or getattr(np, "trapz")

# Regional hydraulic geometry: bankfull width W = a A^b and MEAN depth
# D = c A^d, A in km2, W and D in metres. Used for exactly three things --
# sizing the bank search, bounding what is plausible, and, only where the DEM
# cannot show the banks, the channel against which the flood-prone width is
# read. Never reported as a measurement.
REGIONAL_CURVES: dict[str, dict[str, Any]] = {
    "bieger_us": {
        "width": (2.70, 0.352), "depth": (0.30, 0.213),
        "source": "Bieger et al. (2015), all-US relation -- generic",
    },
    # Moody, Wirtanen & Yard (2003), "Regional Relationships for Bankfull
    # Stage in Natural Channels of the Arid Southwest", Natural Channel
    # Design, Flagstaff. Published in ft and mi2 and converted here:
    # Eastern AZ / NM province  W = 9.9059 DA^0.2811 ft, D = 0.4663 DA^0.237 ft
    # Central & southern AZ     W = 15.763 DA^0.3169 ft, D = 0.7812 DA^0.219 ft
    # Their field sites on Eagle Creek near Alto, NM sit on the NM curve:
    # below South Fork, 8.1 mi2, 15.8 ft x 0.7 ft measured against 17.8 x
    # 0.77 predicted. (Moody's Eagle Creek is not the "Eagle Creek" DEM
    # geolens is tested on elsewhere in these comments -- different places.)
    # Also from the report (Figures 7, 15): bankfull cross-sectional area,
    # A = 11.962 DA^0.5396 ft2 (AZ), 4.7799 DA^0.512 ft2 (NM), and bankfull
    # discharge, Q = 88.73 DA^0.4711 cfs (AZ), 15.31 DA^0.6119 cfs (NM),
    # converted the same way (m2, m3/s; A in km2). The division between the
    # provinces is the report's Figure 2: a dashed line near 109.6 W, west of
    # the New Mexico line; its study region runs about 114.0 to 104.0 W and
    # 31.6 to 38.1 N (see geolens.project.choose_curve).
    "arid_sw_nm": {
        "width": (2.31063, 0.2811), "depth": (0.11343, 0.237),
        "area": (0.2728, 0.512), "discharge": (0.24217, 0.6119),
        "source": "Moody et al. (2003), Eastern Arizona / New Mexico province",
    },
    "arid_sw_az": {
        "width": (3.55369, 0.3169), "depth": (0.19331, 0.219),
        "area": (0.66499, 0.5396), "discharge": (1.60476, 0.4711),
        "source": "Moody et al. (2003), central & southern Arizona province",
    },
}
# Without a named curve the width is the segments layer's own
# (classify.py, 2.7 A^0.35) and the depth Bieger's matching relation.
_DEPTH_COEF, _DEPTH_EXP = REGIONAL_CURVES["bieger_us"]["depth"]
# Maximum over mean bankfull depth in riffles: mean = 0.58 x max, R2 0.90,
# across the Arizona, New Mexico and Navajo Nation sites of Moody et al.
# (2003); 0.4 for triangular sections to 0.8 for rectangular ones.
_MAX_OVER_MEAN = 1.0 / 0.58


def _resolve_curve(curve: str | dict | None) -> dict | None:
    """A named regional curve, a custom one, or None for the default."""
    if curve is None:
        return None
    if isinstance(curve, str):
        if curve not in REGIONAL_CURVES:
            raise GeolensError(
                f"Unknown regional_curve {curve!r}. Use one of "
                f"{sorted(REGIONAL_CURVES)} or a dict with 'width' and "
                f"'depth' as (coefficient, exponent) pairs, A in km2, "
                f"metres out.")
        return REGIONAL_CURVES[curve]
    if not (isinstance(curve, dict) and "width" in curve and "depth" in curve):
        raise GeolensError("regional_curve dict needs 'width' and 'depth' as "
                           "(coefficient, exponent), A in km2, metres out.")
    return {"source": "user-supplied", **curve}


def _side(o: np.ndarray, z: np.ndarray, ti: int, sign: int,
          limit: float | None = None):
    """Offsets, elevations and running maximum walking out from ``ti``."""
    oo, zz = (o[ti::-1], z[ti::-1]) if sign < 0 else (o[ti:], z[ti:])
    if limit is not None:
        keep = np.abs(oo - o[ti]) <= limit + 1e-9
        n = keep.size if keep.all() else int(np.argmin(keep))
        oo, zz = oo[:n], zz[:n]
    return oo, zz, np.maximum.accumulate(zz)


def _cross_many(oo, zz, cm, levels):
    """Where the ground first reaches each level walking out along one side.

    The running maximum is monotone, so the first sample at or above a level
    is a searchsorted on it -- every stage at once, no loop. Returns
    (offsets, reached); an unreached level returns the end of the walk.
    """
    j = np.searchsorted(cm, levels, side="left")
    reached = j < cm.size
    jj = np.clip(j, 1, cm.size - 1)
    z0, z1 = zz[jj - 1], zz[jj]
    f = np.clip((levels - z0) / np.maximum(z1 - z0, 1e-12), 0.0, 1.0)
    off = oo[jj - 1] + f * (oo[jj] - oo[jj - 1])
    off = np.where(reached, off, oo[-1])
    off = np.where(j == 0, oo[0], off)
    return off, reached


def _thalweg(o: np.ndarray, z: np.ndarray, near: float,
             t_hint: float = 0.0) -> int | None:
    """Index of the low point within ``near`` of the mapped channel."""
    idx = np.flatnonzero(np.abs(o - t_hint) <= near)
    return None if idx.size == 0 else int(idx[np.argmin(z[idx])])


def _floodprone_at(o: np.ndarray, z: np.ndarray, ti: int, height: float):
    """(left, right, lower_bound) where the section crosses thalweg+height.

    Walks the WHOLE section from the thalweg. lower_bound is True when a side
    ran off the end of the section without reaching the stage, so the width
    is at least this and possibly more.
    """
    lvl = np.array([z[ti] + height])
    a, b = _side(o, z, ti, -1), _side(o, z, ti, +1)
    if a[0].size < 2 or b[0].size < 2:
        return None
    left, lr = _cross_many(*a, lvl)
    right, rr = _cross_many(*b, lvl)
    return float(left[0]), float(right[0]), not (bool(lr[0]) and bool(rr[0]))


def _wetted_area(o: np.ndarray, z: np.ndarray, stage: float,
                 left: float, right: float) -> float:
    """Area below ``stage`` between two crossings, trapezoid rule."""
    m = (o > left) & (o < right)
    oo = np.concatenate([[left], o[m], [right]])
    dd = np.concatenate([[0.0], np.maximum(stage - z[m], 0.0), [0.0]])
    trap = getattr(np, "trapezoid", None) or np.trapz
    return float(trap(dd, oo))


def _threads(o: np.ndarray, z: np.ndarray, stage: float, left: float,
             right: float, min_width: float, min_bar: float) -> int:
    """How many separate channels the section holds below ``stage`` between
    the banks: runs of wetted points at least ``min_width`` wide, parted by
    ground at least ``min_bar`` above the stage. A braided (D) channel has
    two or more; a single wide shallow bed has one. Measured on the lidar
    section at the bankfull stage, which is where Rosgen's D is defined."""
    m = (o >= left) & (o <= right)
    oo, zz = o[m], z[m]
    if oo.size < 3:
        return 1 if oo.size else 0
    wet = zz < stage
    threads, i, n = [], 0, oo.size
    while i < n:
        if wet[i]:
            j = i
            while j + 1 < n and wet[j + 1]:
                j += 1
            threads.append((oo[i], oo[j]))
            i = j + 1
        else:
            i += 1
    if not threads:
        return 0
    # bars: the dry ground between two runs must stand above the stage
    kept = [threads[0]]
    for a, b in threads[1:]:
        pa, pb = kept[-1]
        between = (oo > pb) & (oo < a)
        bar = float(zz[between].max() - stage) if between.any() else 0.0
        if bar >= min_bar:
            kept.append((a, b))
        else:
            kept[-1] = (pa, b)
    dx = float(np.median(np.diff(oo))) if oo.size > 1 else 0.0
    return sum(1 for a, b in kept if (b - a + dx) >= min_width)


def regional_stage_width(offsets, elevations, max_depth: float, near: float,
                         t_hint: float = 0.0, max_width: float | None = None,
                         min_depth: float = 0.1) -> dict[str, Any] | None:
    """Bankfull at the regional curve's depth, with the DEM giving the shape.

    Offsets and elevations in METRES. The stage is the thalweg plus
    ``max_depth`` -- the regional curve's maximum bankfull depth for this
    drainage area (Moody et al. 2003: mean depth / 0.58) -- and the width,
    area and mean depth are what the section holds at that stage. The
    flood-prone width is read the same way at twice the depth.

    Why this and not the bank picker (``bankfull_stage_width``): on Ruidoso
    (v0.26, 1,875 usable picks) the picker's depths ran a median 2.1-2.6x
    Moody's New Mexico field depths in drainages under 20 km2, only about
    one pick in four or five within a factor of 1.5, because at 0.5-1 m
    lidar the first break it can see is usually the top of a small incised
    channel -- or, in a wide sandy wash, a low-flow thread. A stage set by
    field data is consistent from section to section; the DEM still decides
    the width, the width/depth ratio and the entrenchment. The picker's
    answer is kept beside this one as a check.

    ``one_sided`` is True when the water ran off an end of the section at
    the bankfull stage: the width is then only a lower bound.

    ``max_width`` (the caller passes 3x the regional width) handles the
    opposite failure: a small channel on a flat floor. At the full regional
    depth the water leaves the channel and covers the floor, and the "width"
    is the valley's -- replayed on Ruidoso, 852 of 4,280 sections came out
    over 3x the regional width that way and flat-floored C/E reaches read as
    entrenched F and D. So the stage is lowered until the width is
    plausible: bankfull is the highest stage no deeper than the regional
    depth whose width is at most ``max_width``, and no shallower than
    ``min_depth`` (0.1 m, about the vertical noise of 1 m lidar). No such
    stage -- nothing channel-shaped at this scale -- returns None.
    ``stage_capped`` says the stage was lowered.
    """
    o = np.asarray(offsets, dtype=float)
    z = np.asarray(elevations, dtype=float)
    ok = np.isfinite(o) & np.isfinite(z)
    o, z = o[ok], z[ok]
    if o.size < 5 or not (max_depth and max_depth > 0):
        return None
    order = np.argsort(o)
    o, z = o[order], z[order]
    ti = _thalweg(o, z, near, t_hint)
    if ti is None:
        return None
    depth, capped = float(max_depth), False
    if max_width is not None and max_width > 0:
        # Widths at every stage from min_depth to the regional depth at once.
        lo = min(float(max_depth), max(min_depth, 0.0))
        hs = np.linspace(lo, float(max_depth), 64)
        a, b = _side(o, z, ti, -1), _side(o, z, ti, +1)
        if a[0].size < 2 or b[0].size < 2:
            return None
        lvl = z[ti] + hs
        lft, _ = _cross_many(*a, lvl)
        rgt, _ = _cross_many(*b, lvl)
        ok = (rgt - lft) <= max_width
        if not ok.any():
            return None
        k = int(np.flatnonzero(ok)[-1])
        capped = k < hs.size - 1
        depth = float(hs[k])
    bank = _floodprone_at(o, z, ti, depth)
    if bank is None:
        return None
    left, right, lower_bound = bank
    width = right - left
    if not width > 0:
        return None
    stage = float(z[ti]) + depth
    area = _wetted_area(o, z, stage, left, right)
    fp = _floodprone_at(o, z, ti, 2.0 * depth)
    return {
        "stage_capped": capped,
        "width": width, "max_depth": depth,
        "mean_depth": area / width if width > 0 else float("nan"),
        "area": area, "left": left, "right": right,
        "one_sided": bool(lower_bound),
        "thalweg_offset": float(o[ti]), "thalweg_z": float(z[ti]),
        "n_candidates": None, "picked_by": "regional depth",
        "candidate_stages": [],
        "floodprone_left": None if fp is None else fp[0],
        "floodprone_right": None if fp is None else fp[1],
        "floodprone_width": None if fp is None else fp[1] - fp[0],
        "floodprone_lower_bound": None if fp is None else bool(fp[2]),
    }


def bankfull_stage_width(offsets, elevations, expected_width: float,
                         min_bank_height: float = 0.3, t_hint: float = 0.0,
                         window_factor: float = 2.0, jump: float = 2.0,
                         dh: float = 0.02,
                         expected_area: float | None = None
                         ) -> dict[str, Any] | None:
    """Bankfull from the stage-width curve (Riley's bench index).

    Offsets and elevations in METRES. Raise the water from the thalweg in
    ``dh`` steps and measure the connected width at each stage. In the
    channel, width grows slowly -- the banks are steep. At bankfull it starts
    growing much faster, because the water has left the channel: onto a
    floodplain, a bench, or just a gentler valley wall. Bankfull is the
    LOWEST stage, at least ``min_bank_height`` up, where the rate dW/dh stays
    ``jump`` times above its in-channel median for three steps.

    Replaces the slope-break walker, which needs a flat to stop at: a
    confined channel has none, so it climbed to a bench, a terrace or the
    valley wall -- on Eagle Creek median bankfull was 3.7x what the drainage
    area supports, and 23 of 34 sections were thrown out as implausible. On
    the known-answer profiles (``tests/bankfull_profiles.py``) this recovers
    14.0 m where the walker read 22.5-24 m off a trench terrace.

    Every such stage is a candidate -- the banks, an inset floodplain, a
    terrace. The lowest is bankfull unless ``expected_area`` (the regional
    curve's cross-section, m2) is given, in which case the candidate whose
    area is nearest it, within 3x, is -- the field test Moody et al. (2003)
    use in the Southwest, where bankfull is rarely the top of bank.
    EXPERIMENTAL and off in the pipeline: with the expected area wrong by
    x1.6 it picks a trench's terrace over its bench, and on Eagle Creek it
    never changed a pick. Leave it None unless the curve is local and good.

    Returns None when there is no such stage -- a pure V has none, and
    "indeterminate" is the true answer; so is a bank face narrower than the
    sample spacing, which no DEM can show.

    TUNING: ``jump`` 2.0 separates a 1:1 bank from a 1:1.2 valley wall only
    when the section resolves the bank face; lowering it below ~1.6 starts
    picking noise on the bed. ``window_factor`` bounds the search to 2x the
    expected width either side of the thalweg -- wide enough for a channel
    three times the regional curve, narrow enough not to reach most valley
    walls.
    """
    o = np.asarray(offsets, float)
    z = np.asarray(elevations, float)
    ok = np.isfinite(o) & np.isfinite(z)
    if ok.sum() < 7:
        return None
    o, z = o[ok], z[ok]
    srt = np.argsort(o)
    o, z = o[srt], z[srt]
    dx = float(np.median(np.diff(o)))
    if dx <= 0:
        return None
    w_exp = max(float(expected_width), 4 * dx)
    ti = _thalweg(o, z, max(w_exp, 3 * dx), t_hint)
    if ti is None:
        return None
    zt = z[ti]
    half = max(window_factor * w_exp, 6 * dx)
    lo_s = _side(o, z, ti, -1, half)
    ro_s = _side(o, z, ti, +1, half)
    if lo_s[0].size < 2 or ro_s[0].size < 2:
        return None
    # A few steps past the higher side's crest, so a jump AT the crest --
    # water spilling onto a dead-flat floodplain -- is on the curve. Capped:
    # no bankfull is deeper than about 1.5x the channel is wide, and one
    # undeclared nodata value in the window (-9999 read as ground) would
    # otherwise ask for 600,000 stages. The step grows before the count does.
    top = max(lo_s[2][-1], ro_s[2][-1]) - zt + 3 * dh
    top = min(top, max(3.0, 1.5 * w_exp))
    dh = max(dh, top / 2000.0)
    hs = np.arange(dh, max(top, 0.0) + dh, dh)
    if hs.size < 5:
        return None
    lv = zt + hs
    lo, lr = _cross_many(*lo_s, lv)
    ro, rr = _cross_many(*ro_s, lv)
    ws = ro - lo
    both_open = ~lr & ~rr
    k = 3
    rate = np.convolve(np.gradient(ws, hs), np.ones(k) / k, mode="same")

    # Every stage where width starts growing `jump` times faster than it did
    # since the last bench filled -- the channel's banks, then an inset
    # floodplain, then a terrace. Each run of fast growth is one candidate.
    cands: list[int] = []
    seg0, i, n = 0, 0, hs.size
    while i < n:
        if hs[i] < min_bank_height or both_open[i] or i - seg0 < 3:
            i += 1
            continue
        base = float(np.median(rate[seg0:i]))
        ahead = rate[i:i + k]
        if base > 0 and ahead.size and float(np.min(ahead)) >= jump * base:
            cands.append(i)
            j = i + 1
            while j < n and rate[j] >= jump * base:
                j += 1
            seg0 = i = j
            continue
        i += 1
    if not cands:
        return None

    def _at(ci: int) -> dict[str, float] | None:
        lc, rc = float(lo[ci]), float(ro[ci])
        w = rc - lc
        if w <= 0:
            return None
        lvl = zt + float(hs[ci])
        xs = np.linspace(lc, rc, max(40, int(w / dx) * 4))
        a = float(_trapz(np.clip(lvl - np.interp(xs, o, z), 0, None), xs))
        return {"i": ci, "stage_h": float(hs[ci]), "left": lc, "right": rc,
                "width": w, "area": a}

    options = [g for g in (_at(c) for c in cands) if g and g["area"] > 0]
    if not options:
        return None
    pick, picked_by = options[0], "lowest"
    # Only candidates the width bound would accept compete on area.
    options_ok = [g for g in options if g["width"] <= 3.0 * w_exp]
    if expected_area and expected_area > 0 and len(options_ok) > 1:
        options = options_ok
        # Moody et al. (2003): in the Southwest bankfull is rarely the top of
        # the bank, and the field test among competing indicators is the
        # regional cross-sectional area. Same here: of the stages the DEM
        # shows, the one whose area is nearest the curve's -- if any is
        # within 3x of it -- otherwise the lowest.
        near = [(abs(math.log(g["area"] / expected_area)), g) for g in options]
        near = [t for t in near if t[0] <= math.log(3.0)]
        if near:
            best = min(near, key=lambda t: t[0])[1]
            if best is not options[0]:
                pick, picked_by = best, "regional area"
    hit = pick["i"]
    stage_h = pick["stage_h"]
    lc, rc, width, area = pick["left"], pick["right"], pick["width"], \
        pick["area"]
    fp = _floodprone_at(o, z, ti, 2.0 * stage_h)
    return {
        "width": width, "max_depth": stage_h,
        "mean_depth": area / width, "area": area,
        "left": lc, "right": rc,
        "one_sided": not (bool(lr[hit]) and bool(rr[hit])),
        "thalweg_offset": float(o[ti]), "thalweg_z": float(zt),
        "n_candidates": len(options), "picked_by": picked_by,
        "candidate_stages": [round(g["stage_h"], 2) for g in options],
        "floodprone_left": None if fp is None else fp[0],
        "floodprone_right": None if fp is None else fp[1],
        "floodprone_width": None if fp is None else fp[1] - fp[0],
        "floodprone_lower_bound": True if fp is None else fp[2],
    }



@dataclass
class StreamTypeResult:
    segments: str                       # GeoPackage, one row per reach
    sections: str | None                # the measured channel sections
    n_segments: int
    n_measured: int
    type_counts: dict
    confidence_counts: dict
    unresolved: int
    substrate_used: bool
    cell_size_m: float
    resolvable: bool
    seconds: float
    warnings: list = field(default_factory=list)
    basis_counts: dict = field(default_factory=dict)
    plots: dict = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in
                ("segments", "sections", "n_segments", "n_measured",
                 "type_counts", "confidence_counts", "unresolved",
                 "substrate_used", "cell_size_m", "resolvable", "seconds",
                 "warnings", "basis_counts", "plots")}


def _write_channel_sections(rows: list[dict], id_field: str, crs,
                            mpu: float, vpu: float, out_dir: Path) -> str:
    """The channel cross-sections behind the stream types, on disk.

    Three files, because checking a stream type is three questions:

    ``stream_type_sections.gpkg``
        One line per section, with its bankfull width, depths, width/depth,
        flood-prone width, entrenchment ratio, the ratio to the expected
        width, whether it was used, and why not if it was not.
    ``stream_type_banks.gpkg``
        A point where each bank top and each flood-prone edge was picked.
        This is the layer that answers "is it measuring the channel?" -- on a
        hillshade, a bank point sitting on a terrace or halfway up the valley
        wall is obvious at a glance, and that is exactly the failure that
        makes a confined mountain stream read as braided.
    ``stream_type_profiles.csv``
        Offset and elevation for every point on every section, in metres, to
        plot any of them.
    """
    import math

    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import LineString, Point

    lines, attrs, banks, profile = [], [], [], []
    for r in rows:
        sec = r["_sec"]
        br = math.radians(float(sec["bearing_deg"]))
        tx, ty = math.sin(br), math.cos(br)
        nx, ny = -ty, tx                      # left-hand normal, as cut
        cx, cy = float(sec["center_x"]), float(sec["center_y"])
        offs = [float(p["offset"]) for p in sec["points"]]
        if not offs:
            continue
        lines.append(LineString([(cx + nx * min(offs), cy + ny * min(offs)),
                                 (cx + nx * max(offs), cy + ny * max(offs))]))
        attrs.append({k: v for k, v in r.items() if k != "_sec"})

        def _pt(off_m, kind, side):
            if off_m is None:
                return
            o = off_m / mpu
            banks.append({id_field: r[id_field], "xs_id": r["xs_id"],
                          "kind": kind, "side": side,
                          "offset_m": off_m, "used": r["used"],
                          "basis": r.get("basis"),
                          "geometry": Point(cx + nx * o, cy + ny * o)})

        _pt(r.get("bank_left_offset_m"), "bank_top", "left")
        _pt(r.get("bank_right_offset_m"), "bank_top", "right")
        # The picker's own banks, where it was only a check: the two sets
        # side by side on a hillshade are the quickest way to see who is
        # right on a given reach.
        _pt(r.get("picker_left_offset_m"), "picker_bank_top", "left")
        _pt(r.get("picker_right_offset_m"), "picker_bank_top", "right")
        _pt(r.get("floodprone_left_offset_m"), "floodprone_edge", "left")
        _pt(r.get("floodprone_right_offset_m"), "floodprone_edge", "right")

        for p in sec["points"]:
            profile.append({
                id_field: r[id_field], "xs_id": r["xs_id"],
                "station_m": r["station_m"],
                "offset_m": float(p["offset"]) * mpu,
                "elevation_m": (float(p["elevation"]) * vpu
                                if p["elevation"] is not None else None),
            })

    sec_path = out_dir / "stream_type_sections.gpkg"
    if lines:
        from .section_plots import section_plot_file
        df = pd.DataFrame(attrs)
        # Where each section's picture goes (section_plots.py renders it):
        # relative, so the folder can move, and absolute for ArcGIS.
        df["plot_file"] = [section_plot_file(x) for x in df["xs_id"]]
        df["plot_path"] = [str((out_dir / f).resolve())
                           for f in df["plot_file"]]
        # The same columns every run, whether or not any bank was found --
        # a layer whose schema depends on the data breaks a QGIS style.
        for col in ("status", "basis", "bank_left_offset_m",
                    "bank_right_offset_m", "bankfull_width_m",
                    "bankfull_mean_depth_m", "bankfull_max_depth_m",
                    "width_depth_ratio", "one_sided", "n_stage_candidates",
                    "picked_by", "floodprone_left_offset_m",
                    "floodprone_right_offset_m", "floodprone_width_m",
                    "entrenchment_ratio", "floodprone_lower_bound",
                    "bankfull_ratio", "excluded", "thalweg_offset_m",
                    "thalweg_z_m", "bankfull_stage_m", "candidate_stages_m",
                    "floodprone_stage_m", "regional_max_depth_m",
                    "picker_width_m", "picker_max_depth_m", "picker_stage_m",
                    "picker_left_offset_m", "picker_right_offset_m",
                    "picker_depth_ratio", "stage_capped",
                    "plot_file", "plot_path"):
            if col not in df.columns:
                df[col] = None
        gpd.GeoDataFrame(df, geometry=lines, crs=crs).to_file(
            str(sec_path), driver="GPKG", layer="stream_type_sections")
    if banks:
        gpd.GeoDataFrame(banks, geometry="geometry", crs=crs).to_file(
            str(out_dir / "stream_type_banks.gpkg"), driver="GPKG")
    if profile:
        pd.DataFrame(profile).to_csv(out_dir / "stream_type_profiles.csv",
                                     index=False)
    note(f"  {len(lines)} channel sections written to {sec_path.name}, "
         f"bank picks to stream_type_banks.gpkg")
    return str(sec_path) if lines else None


def _measure_channel(dem_path: Path, lines, ids, widths_crs, n_per_reach: int,
                     point_spacing_crs: float) -> dict:
    """Cut channel-scale sections and sample them. One DEM handle, whole run.

    ``extract_cross_sections`` does this for one feature at a time and reopens
    the DEM and the centreline each call; at 2,838 reaches that is 5,676 file
    opens to read a few hundred points each. The geometry convention is copied
    from it exactly -- origin on the line, left-hand normal from the local
    tangent -- so the two remain interchangeable.
    """
    import rasterio
    from shapely.geometry import LineString, MultiLineString
    from shapely.ops import linemerge

    out: list[dict[str, Any]] = []
    with rasterio.open(str(dem_path)) as src:
        nodata = src.nodata
        for geom, sid, half in zip(lines, ids, widths_crs):
            if geom is None or geom.is_empty:
                continue
            part = geom
            if isinstance(geom, MultiLineString):
                merged = linemerge(geom)
                parts = list(getattr(merged, "geoms", [merged]))
                part = max(parts, key=lambda g: g.length)
            if not isinstance(part, LineString) or part.length <= 0:
                continue
            n_pts = max(9, int(round(2.0 * half / point_spacing_crs)) + 1)
            offsets = np.linspace(-half, half, n_pts)
            stations = np.linspace(0.0, part.length, n_per_reach + 2)[1:-1]
            delta = max(point_spacing_crs, part.length / 100.0)
            for st in stations:
                origin = part.interpolate(float(st))
                a = part.interpolate(max(0.0, st - delta))
                b = part.interpolate(min(part.length, st + delta))
                tx, ty = b.x - a.x, b.y - a.y
                n = math.hypot(tx, ty)
                if n == 0:
                    continue
                tx, ty = tx / n, ty / n
                nx, ny = -ty, tx
                coords = [(origin.x + nx * o, origin.y + ny * o)
                          for o in offsets]
                zs = []
                for v in src.sample(coords, indexes=1):
                    raw = float(v[0])
                    bad = math.isnan(raw) or (nodata is not None
                                              and raw == nodata)
                    zs.append(None if bad else raw)
                pts = [{"offset": float(o), "elevation": z}
                       for o, z in zip(offsets, zs)]
                good = [(float(o), z) for o, z in zip(offsets, zs)
                        if z is not None]
                if len(good) < 5:
                    continue
                # The thalweg is the low point NEAR THE MAPPED CHANNEL, not
                # anywhere on the section. A section is cut twenty channel
                # widths wide so it can reach the flood-prone stage, and on a
                # valley flank or near a confluence the lowest point on it
                # can be the neighbouring channel -- whose banks would then be
                # measured and attributed to this reach.
                near = [(o, z) for o, z in good if abs(o) <= half / 4.0]
                t_off, t_z = min(near or good, key=lambda p: p[1])
                out.append({
                    "station": float(st), "points": pts,
                    "thalweg_offset": t_off, "thalweg_elevation": t_z,
                    "segment_id": sid, "center_x": origin.x,
                    "center_y": origin.y,
                    "bearing_deg": round(
                        math.degrees(math.atan2(tx, ty)) % 360, 2),
                })
    return {"sections": out, "point_spacing": float(point_spacing_crs)}


def _type_reaches(seg, sections: list[dict], id_field: str, cell_m: float,
                  mpu: float, vpu: float, warnings: list[str],
                  curve: dict | None = None, select_by_area: bool = False,
                  method: str = "regional_stage"
                  ) -> tuple[list[dict], list[dict]]:
    """Measure every section and type every reach.

    ``sections`` are as ``_measure_channel`` cuts them: offsets in CRS units,
    elevations in the DEM's z unit, converted here with ``mpu`` and ``vpu``.
    Split out so the same code can be replayed on a saved
    ``stream_type_profiles.csv`` (metres, so mpu = vpu = 1) without the DEM.
    Returns (one row per reach, one row per section).
    """
    import pandas as pd

    # Regional hydraulic geometry per reach, in metres: width from the
    # segments layer (the classifier's drainage-area relation), mean depth
    # from the matching Bieger relation. Sizes the bank search, bounds what
    # is plausible, and -- only where the DEM cannot show the banks --
    # supplies the stage at which the flood-prone width is read.
    expected: dict[Any, float] = {}
    dmax_reg: dict[Any, float] = {}
    dmean_reg: dict[Any, float] = {}
    area_col = "drainage_km2" if "drainage_km2" in seg.columns else None
    for i, sid in enumerate(seg[id_field]):
        w = (pd.to_numeric(seg["channel_width_m"].iloc[i], errors="coerce")
             if "channel_width_m" in seg.columns else float("nan"))
        a = (pd.to_numeric(seg[area_col].iloc[i], errors="coerce")
             if area_col else float("nan"))
        if curve is not None and np.isfinite(a) and a > 0:
            w = curve["width"][0] * float(a) ** curve["width"][1]
        if np.isfinite(w) and w > 0:
            expected[sid] = float(w)
        if curve is not None and np.isfinite(a) and a > 0:
            dmean = curve["depth"][0] * float(a) ** curve["depth"][1]
        elif np.isfinite(a) and a > 0:
            dmean = _DEPTH_COEF * float(a) ** _DEPTH_EXP
        elif sid in expected:
            dmean = expected[sid] / 12.0
        else:
            continue
        dmax_reg[sid] = _MAX_OVER_MEAN * dmean
        dmean_reg[sid] = dmean

    per: dict[Any, dict[str, list]] = {}
    implausible = subres = 0
    # Every section, whatever happened to it, for the sections layer.
    sec_rows: list[dict[str, Any]] = []
    # Three SAMPLES, not three cells: sections are sampled no finer than
    # 0.5 m (_measure_channel), whatever the grid.
    min_w = 3.0 * max(cell_m, 0.5)
    for xs_i, sec in enumerate(sections):
        sid = sec["segment_id"]
        d = per.setdefault(sid, {"w": [], "md": [], "wd": [], "er": [],
                                 "er_reg": [], "bad": [], "ratio": [],
                                 "picker_ratio": []})
        # Everything below is in METRES. Offsets come off the CRS and
        # elevations in the DEM's z unit; converting once here is the whole
        # of the unit handling, so width/depth needs no correction factor.
        good = [(float(p["offset"]) * mpu, float(p["elevation"]) * vpu)
                for p in sec["points"] if p["elevation"] is not None]
        o_m = np.array([g[0] for g in good], dtype=float)
        z_m = np.array([g[1] for g in good], dtype=float)
        fin = np.isfinite(o_m) & np.isfinite(z_m)
        o_m, z_m = o_m[fin], z_m[fin]
        exp_w = expected.get(sid)
        dreg = dmax_reg.get(sid)
        # Banks lower than about half the regional depth are noise on a
        # small channel and the old fixed 0.3 m misses whole tributaries.
        mbh = min(0.3, max(0.15, 0.5 * dreg)) if dreg else 0.3
        # A channel the regional curve puts under three cells wide cannot
        # show its banks on this grid, and what the detector finds instead
        # is the valley floor or a bench: on Eagle Creek at 1.2 m that was
        # every tributary. Those go straight to the regional basis.
        can_see = exp_w is None or exp_w >= min_w
        exp_a = (exp_w * dmean_reg[sid]
                 if exp_w and sid in dmean_reg and select_by_area else None)
        err = None
        try:
            # The bank picker. Under method="regional_stage" it is only a
            # check, kept in the picker_* columns.
            pick = (bankfull_stage_width(o_m, z_m, exp_w or 3.0,
                                         min_bank_height=mbh,
                                         expected_area=exp_a)
                    if o_m.size >= 7 and can_see else None)
        except Exception as exc:                             # noqa: BLE001
            # One bad section must not cost the whole run its stream types.
            pick, err = None, f"{type(exc).__name__}: {exc}"
        bf = pick
        # "hybrid": trust the picker where its depth agrees with the regional
        # depth within a factor of two -- the DEM saw a bank where the field
        # data put one -- and fall back to the regional stage elsewhere.
        agree = (pick is not None and dreg is not None and dreg > 0
                 and 0.5 <= pick["max_depth"] / dreg <= 2.0
                 and not pick["one_sided"])
        if (method in ("regional_stage", "hybrid") and not
                (method == "hybrid" and agree)
                and o_m.size >= 7 and can_see and exp_w and dreg):
            try:
                _dx = float(np.median(np.diff(np.sort(o_m))))
                bf = regional_stage_width(
                    o_m, z_m, dreg, near=max(exp_w, 3 * _dx),
                    max_width=MAX_BANKFULL_RATIO * exp_w)
                err = None
            except Exception as exc:                         # noqa: BLE001
                bf, err = None, f"{type(exc).__name__}: {exc}"
        row: dict[str, Any] = {
            id_field: sid, "xs_id": xs_i,
            "station_m": float(sec["station"]) * mpu,
            "expected_width_m": exp_w, "used": False, "basis": "none",
            "regional_max_depth_m": dreg,
            "_sec": sec,
        }
        if pick is not None and bf is not pick:
            row.update({
                "picker_width_m": pick["width"],
                "picker_max_depth_m": pick["max_depth"],
                "picker_stage_m": pick["thalweg_z"] + pick["max_depth"],
                "picker_left_offset_m": pick["left"],
                "picker_right_offset_m": pick["right"],
                "picker_depth_ratio": (pick["max_depth"] / dreg
                                       if dreg else None),
            })
            d["picker_ratio"].append(pick["max_depth"] / dreg
                                     if dreg else float("nan"))
        sec_rows.append(row)
        status = ("error: " + err if err else
                  "no bank found" if can_see else "below resolution")
        if bf is not None:
            row.update({
                "bank_left_offset_m": bf["left"],
                "bank_right_offset_m": bf["right"],
                "bankfull_width_m": bf["width"],
                "bankfull_mean_depth_m": bf["mean_depth"],
                "bankfull_max_depth_m": bf["max_depth"],
                "width_depth_ratio": bf["width"] / bf["mean_depth"]
                if bf["mean_depth"] > 0 else None,
                "one_sided": bf["one_sided"],
                "n_stage_candidates": bf["n_candidates"],
                "picked_by": bf["picked_by"],
                # Absolute elevations, so a plot of the section can draw
                # the pick without re-running the detector.
                "thalweg_offset_m": bf["thalweg_offset"],
                "thalweg_z_m": bf["thalweg_z"],
                "bankfull_stage_m": bf["thalweg_z"] + bf["max_depth"],
                "candidate_stages_m": ", ".join(
                    f"{h:.2f}" for h in bf.get("candidate_stages") or []),
            })
            r = bf["width"] / exp_w if exp_w else None
            if r is not None:
                d["ratio"].append(r)
                row["bankfull_ratio"] = r
            if bf["width"] < min_w:
                # Found, but narrower than three cells: the width is the
                # cell size, not a measurement.
                status = "sub-resolution"
                subres += 1
            elif bf["one_sided"]:
                # The water left the search window on one side before the
                # stage was reached: the width is the window, not the
                # channel. A 20 m channel against an 8 m expectation read
                # 17 m this way and was used.
                status = "one-sided"
            elif (r is not None and r > MAX_BANKFULL_RATIO
                  and bf.get("picked_by") != "regional depth"):
                status = "implausible"
                implausible += 1
                d["bad"].append(r)
                row["excluded"] = (f"bankfull {r:.1f}x the expected width — "
                                   f"likely a bench, terrace or valley wall")
            else:
                # At the regional depth a wide width is what the ground is:
                # a flat-floored wash really does spread that far.
                if bf.get("stage_capped"):
                    # Shallower than the regional depth, because at that
                    # depth the water covered a flat floor.
                    row["stage_capped"] = True
                status = "bankfull"
        if status == "bankfull":
            row["used"] = True
            row["basis"] = "measured"
            if bf.get("picked_by") == "regional depth":
                d["n_regional_depth"] = d.get("n_regional_depth", 0) + 1
            d["w"].append(bf["width"])
            d["md"].append(bf["mean_depth"])
            if row.get("width_depth_ratio"):
                d["wd"].append(row["width_depth_ratio"])
            try:
                # Threads at the bankfull stage across the flood-prone
                # width: the bankfull width itself is the first wetted run
                # out from the thalweg, so within it there is always one.
                # A braided belt's other channels lie beyond its bars.
                _dx = float(np.median(np.diff(np.sort(o_m)))) if o_m.size > 1 \
                    else 1.0
                fl = bf.get("floodprone_left")
                fr = bf.get("floodprone_right")
                if fl is None or fr is None or not np.isfinite([fl, fr]).all():
                    fl, fr = bf["left"], bf["right"]
                nt = _threads(o_m, z_m, bf["thalweg_z"] + bf["max_depth"],
                              min(fl, bf["left"]), max(fr, bf["right"]),
                              min_width=max(2.0 * _dx, 0.5),
                              min_bar=max(0.1, 0.25 * bf["max_depth"]))
                row["threads"] = nt
                d.setdefault("threads", []).append(nt)
            except Exception:                                # noqa: BLE001
                pass
            if bf["floodprone_width"] is not None:
                er = bf["floodprone_width"] / bf["width"]
                row.update({
                    "floodprone_left_offset_m": bf["floodprone_left"],
                    "floodprone_right_offset_m": bf["floodprone_right"],
                    "floodprone_width_m": bf["floodprone_width"],
                    "floodprone_lower_bound": bf["floodprone_lower_bound"],
                    "floodprone_stage_m": bf["thalweg_z"]
                    + 2.0 * bf["max_depth"],
                    "entrenchment_ratio": er,
                })
                d["er"].append(er)
        elif exp_w and dreg and o_m.size >= 7:
            # No bank the DEM can show. The flood-prone width is still there
            # to read -- it is several channel widths across -- at twice the
            # regional depth, against the regional width. Entrenchment only;
            # width/depth stays unknown.
            dx = float(np.median(np.diff(np.sort(o_m))))
            ti = _thalweg(o_m, z_m, max(exp_w, 3 * dx))
            fp = (_floodprone_at(o_m, z_m, ti, 2.0 * dreg)
                  if ti is not None else None)
            if fp is not None:
                er = (fp[1] - fp[0]) / exp_w
                row.update({
                    "basis": "regional",
                    "floodprone_left_offset_m": fp[0],
                    "floodprone_right_offset_m": fp[1],
                    "floodprone_width_m": fp[1] - fp[0],
                    "floodprone_lower_bound": fp[2],
                    "floodprone_stage_m": float(z_m[ti]) + 2.0 * dreg,
                    "regional_max_depth_m": dreg,
                    "entrenchment_ratio": er,
                })
                if row.get("thalweg_z_m") is None:
                    row["thalweg_offset_m"] = float(o_m[ti])
                    row["thalweg_z_m"] = float(z_m[ti])
                d["er_reg"].append(er)
        row["status"] = status

    n_bf = sum(len(v["ratio"]) for v in per.values())
    if method in ("regional_stage", "hybrid"):
        pr = np.array([x for v in per.values() for x in v["picker_ratio"]
                       if np.isfinite(x)])
        if pr.size:
            within = float(((pr > 1 / 1.5) & (pr < 1.5)).mean())
            warnings.append(
                f"Bankfull is set at the regional curve's depth above the "
                f"thalweg; the DEM gives the width and shape there. The bank "
                f"picker, kept as a check in the picker_* columns, found a "
                f"bank on {pr.size} sections at a median "
                f"{float(np.median(pr)):.2f}x that depth ({within:.0%} within "
                f"a factor of 1.5). Where they disagree, the section plot "
                f"shows both."
            )
    if implausible:
        warnings.append(
            f"{implausible} of {n_bf} bankfull picks were more than "
            f"{MAX_BANKFULL_RATIO:g}x the width this drainage area supports "
            f"and were not used; those sections fall back to the regional "
            f"entrenchment. Check them in stream_type_banks.gpkg."
        )
    if subres:
        warnings.append(
            f"{subres} bankfull picks were narrower than three cells "
            f"({min_w:.1f} m) and were not used as widths. A finer DEM "
            f"(dem_fine=) would measure them."
        )

    def _med(v):
        return float(np.median(v)) if v else float("nan")

    rows = []
    for _, r in seg.iterrows():
        sid = r[id_field]
        d = per.get(sid, {"w": [], "md": [], "wd": [], "er": [],
                          "er_reg": [], "bad": [], "ratio": []})
        slope = r.get("gradient")
        slope = float(slope) if slope is not None and np.isfinite(
            pd.to_numeric(slope, errors="coerce")) else None
        sin = _sinuosity(r.geometry)
        # What the letter rests on, strongest first. A reach with measured
        # banks uses only those; one without falls back to the flood-prone
        # width against the regional channel; one with neither has slope
        # and sinuosity, which name a type only when they separate it alone.
        #
        # Entrenchment is the median over every section that has one,
        # measured or regional: they estimate the same ratio, and a reach
        # where two sections found a bench and three found a V is a V.
        # Width/depth needs two measured sections -- one is a station, not a
        # reach.
        n_m, n_r = len(d["wd"]), len(d["er_reg"])
        if n_m >= 2:
            # Banks in two or more sections: entrenchment and width/depth
            # both from THOSE sections, so the basis says what the letter
            # actually rests on.
            basis = "measured"
            er, wd = _med(d["er"]), _med(d["wd"])
        elif d["er"] or d["er_reg"]:
            # One measured section is a station, not a reach: its ER joins
            # the regional ones and width/depth is left out.
            basis = "partial" if n_m == 1 else "regional"
            er, wd = _med(d["er"] + d["er_reg"]), float("nan")
        else:
            basis = "slope_sinuosity"
            er = wd = float("nan")
        th = d.get("threads") or []
        n_threads = float(np.median(th)) if th else None
        cls = classify_stream_type(
            er if np.isfinite(er) else None,
            wd if np.isfinite(wd) else None,
            sin, slope,
            threads=n_threads if basis == "measured" else None)
        conf = cls["confidence"]
        if basis != "measured" and conf != "low":
            conf = "low"        # a regional width is not a measurement
        n_rd = d.get("n_regional_depth", 0)
        src = ("none" if not d["w"] else
               "regional depth" if n_rd == len(d["w"]) else
               "picker" if n_rd == 0 else "mixed")
        if src in ("regional depth", "mixed") and conf == "high":
            # The depth came from the regional curve, not the ground, so
            # width/depth is not an independent measurement: at most medium.
            conf = "medium"
        rows.append({
            id_field: sid,
            "regional_width_m": expected.get(sid, float("nan")),
            "bankfull_width_m": _med(d["w"]),
            "bankfull_mean_depth_m": _med(d["md"]),
            "width_depth_ratio": wd,
            "entrenchment_ratio": er,
            "sinuosity": sin,
            "slope": slope,
            "stream_basis": basis,
            "bankfull_source": src,
            "n_sections_measured": len(d["wd"]),
            "n_sections_regional": len(d["er_reg"]),
            # Measured bankfull over the regional estimate, across ALL
            # picks including the unused ones -- so a reach where the
            # detector kept climbing is visible, not just silently thinner.
            "bankfull_ratio_median": (float(np.median(d["ratio"]))
                                      if d["ratio"] else float("nan")),
            "n_sections_implausible": len(d["bad"]),
            "stream_type": cls["stream_type"],          # letter + slope class
            "stream_letter": cls["stream_letter"],
            "slope_class": cls["slope_class"],
            "stream_candidates": "|".join(cls["alternatives"]) or "none",
            "stream_misfit": cls["misfit"],
            # Only meaningful on measured banks: a regional entrenchment
            # rests on a generic width and depth and is easily 2x out, so
            # "outside every box" would be a statement about the curve.
            "no_type_fits": bool(cls["no_type_fits"]) and basis == "measured",
            "sinuosity_consistent": cls["sinuosity_consistent"],
            "stream_missing": "|".join(cls["missing"]),
            "braided_possible": cls["braided_possible"],
            "threads_at_bankfull": n_threads,
            "single_thread": cls.get("single_thread", False),
            "stream_confidence": conf,
            "stream_type_desc": STREAM_TYPE_DESC.get(cls["stream_letter"]
                                                     or "", ""),
        })

    return rows, sec_rows


def rosgen_stream_types(dem: str | Path,
                        segments: str | Path,
                        dem_fine: str | Path | None = None,
                        id_field: str | None = None,
                        sections_per_reach: int = 5,
                        section_width_m: float | None = None,
                        geology: str | Path | None = None,
                        regional_curve: str | dict | None = None,
                        section_plots: bool | None = None,
                        bankfull_method: str = "regional_stage",
                        out_prefix: str = "stream_types",
                        guard: Guard | None = None) -> StreamTypeResult:
    """Classify each reach of the network as a Rosgen stream type.

    Parameters
    ----------
    dem
        The DEM the network was derived from. Used only if ``dem_fine`` is not
        given.
    dem_fine
        A finer DEM covering the same ground, used for the channel
        measurement. **This is the parameter that decides whether the answer
        means anything.** Bankfull width and depth need a grid that resolves
        the channel; routing does not. Route at 10 m, measure at 1 m.
    section_width_m
        Total cross-section width. Must comfortably exceed the flood-prone
        width or the entrenchment ratio is a limit of the section rather than
        a measurement. Defaults to 20x the estimated channel width per reach,
        floored at 60 m.
    geology
        Polygon layer with a ``material_class`` column (the output of
        ``geolens.geology.fetch_geology``). Supplies the substrate NUMBER as a
        prior. Without it the result is a letter only, which is an honest
        Level II call rather than half a Level III one.
    regional_curve
        The bankfull width and depth expected from drainage area:
        ``"arid_sw_nm"`` (Moody et al. 2003, Eastern Arizona / New Mexico),
        ``"arid_sw_az"`` (central & southern Arizona), ``"bieger_us"``, or a
        dict ``{"width": (a, b), "depth": (c, d)}`` with A in km2 and metres
        out. None uses the segments layer's generic width. It sizes the bank
        search and is the channel the regional basis measures against, so a
        local curve changes every low-confidence call.
    bankfull_method
        ``"regional_stage"`` (default since v0.28): bankfull is the regional
        curve's maximum depth above the thalweg, and the DEM gives the
        width, width/depth and entrenchment at that stage -- see
        :func:`regional_stage_width`. ``"picker"``: the v0.24-0.27 bank
        picker (:func:`bankfull_stage_width`), which on Ruidoso ran 2-2.6x
        too deep in small drainages. Under ``regional_stage`` the picker
        still runs, as a check, into the ``picker_*`` columns.
    section_plots
        Render a PNG per section and a sheet per reach, and link them to the
        two GeoPackages so hovering over a section in QGIS shows it (see
        ``section_plots.plot_stream_sections``). About 70 ms a section per
        process, several processes on a big network. None (default) means
        on unless the environment sets ``GEOLENS_SECTION_PLOTS=0`` (the test
        suite does). Off, ``api.plot_stream_sections`` renders them later.
    """
    import time

    import geopandas as gpd
    import pandas as pd
    import rasterio

    from ..units import unit_info

    t0 = time.monotonic()
    if bankfull_method not in ("regional_stage", "hybrid", "picker"):
        raise GeolensError(
            f"bankfull_method must be 'regional_stage', 'hybrid' or 'picker', "
            f"not {bankfull_method!r}.")
    guard = guard or Guard.from_env()
    warnings: list[str] = []
    seg = gpd.read_file(str(guard.read_path(segments)))
    if isinstance(regional_curve, str) and regional_curve == "auto":
        # The Moody et al. (2003) province from where the reaches are.
        from ..project import choose_curve

        c = (seg.to_crs(4326).geometry.unary_union.centroid
             if seg.crs is not None and len(seg) else None)
        regional_curve, why = (choose_curve(c.x, c.y) if c is not None
                               else ("bieger_us", "no location: generic"))
        note(f"regional curve by location: {regional_curve} ({why})")
        if "beyond" in why or regional_curve == "bieger_us":
            warnings.append(f"Regional curve {regional_curve}: {why}.")
    curve = _resolve_curve(regional_curve)

    if id_field is None:
        for cand in ("seg_id", "segment_id", "valley_id"):
            if cand in seg.columns:
                id_field = cand
                break
    if id_field is None:
        raise GeolensError(
            f"{segments} has none of seg_id / segment_id / valley_id.")

    dem_path = Path(str(guard.read_path(dem_fine if dem_fine is not None
                                        else dem)))
    with rasterio.open(str(dem_path)) as src:
        units = unit_info(src.crs, abs(src.transform.a), abs(src.transform.e))
        cell_m = units.cell_size_m
        dem_crs = src.crs
    if seg.crs is not None and dem_crs is not None and seg.crs != dem_crs:
        seg = seg.to_crs(dem_crs)

    # Channel width from the segments layer if the classifier estimated one,
    # otherwise a fixed section. Either way the SECTION has to be wide enough
    # to contain the flood-prone width, which for a C stream is several times
    # bankfull.
    if section_width_m is not None:
        half = np.full(len(seg), units.to_crs_units(section_width_m) / 2.0)
    elif "channel_width_m" in seg.columns:
        w = pd.to_numeric(seg["channel_width_m"], errors="coerce")
        w = np.where(np.isfinite(w), w, 3.0)
        half = np.array([units.to_crs_units(max(20.0 * float(x), 60.0)) / 2.0
                         for x in w])
    else:
        half = np.full(len(seg), units.to_crs_units(120.0) / 2.0)

    # Is the channel resolvable at all? A bankfull width smaller than about
    # three cells is not a measurement, it is the cell size, and W/D computed
    # from it is meaningless. Say so once, loudly, rather than emitting 2,838
    # confident wrong numbers.
    est_w = (float(pd.to_numeric(seg.get("channel_width_m"),
                                 errors="coerce").median())
             if "channel_width_m" in seg.columns else float("nan"))
    if curve is not None and "drainage_km2" in seg.columns:
        a_km2 = pd.to_numeric(seg["drainage_km2"], errors="coerce")
        est_w = float((curve["width"][0]
                       * a_km2.clip(lower=1e-6) ** curve["width"][1]).median())
        note(f"regional curve: {curve['source']}")
    resolvable = bool(np.isfinite(est_w) and est_w >= 3.0 * cell_m)
    if not resolvable:
        warnings.append(
            f"The DEM used for the channel measurement has {cell_m:.2f} m "
            f"cells and the median estimated bankfull width is {est_w:.1f} m "
            f"— about {est_w / cell_m:.1f} cells. Most banks cannot be seen "
            f"at that ratio, so most reaches are typed on the flood-prone "
            f"width against the regional channel (stream_basis = regional), "
            f"with width/depth unknown. dem_fine= pointing at the native "
            f"DEM measures more of them."
        )

    note(f"measuring {len(seg)} reaches x {sections_per_reach} sections on "
         f"{dem_path.name} ({cell_m:.2f} m cells)")
    cut = _measure_channel(dem_path, list(seg.geometry), list(seg[id_field]),
                           half, sections_per_reach,
                           units.to_crs_units(max(cell_m, 0.5)))
    if not cut["sections"]:
        raise GeolensError(
            "No channel sections could be sampled. The segments layer and the "
            "DEM may not overlap, or every section fell on nodata."
        )

    mpu = units.metres_per_unit
    vpu = units.vertical_metres_per_unit
    rows, sec_rows = _type_reaches(seg, cut["sections"], id_field, cell_m,
                                   mpu, vpu, warnings, curve=curve,
                                   method=bankfull_method)

    out = pd.DataFrame(rows)
    # The segments layer carries the VALLEY-type candidates from the
    # classifier as `rosgen_candidates`; beside a stream type that name reads
    # as the stream's. Kept, and named for what it is.
    seg = seg.rename(columns={"rosgen_candidates": "valley_candidates"})
    gdf = gpd.GeoDataFrame(seg.merge(out, on=id_field, how="left"),
                           geometry=seg.geometry.values, crs=seg.crs)

    substrate_used = False
    if geology is not None:
        from .rosgen import attach_material
        try:
            # Two bugs lived on this one line, and the first real run found
            # both. `geology` arrives as a PATH -- that is what landscape_pass
            # and geology="auto" hand over -- and attach_material wants a
            # loaded layer; and attach_material returns (materials, covers),
            # not a list. The valley-type path has always read the file first,
            # which is why a run showed geology_used=True on the valley types
            # and "could not be assigned" on the stream types, and looked as
            # if the download had failed when it had not.
            geo = (geology if hasattr(geology, "columns")
                   else gpd.read_file(str(guard.read_path(geology))))
            mats, _covers = attach_material(gdf, geo)
            gdf["material_class"] = mats
            sub = [_SUBSTRATE_FROM_MATERIAL.get(m or "", (None, ""))
                   for m in mats]
            gdf["substrate_class"] = [s[0] for s in sub]
            gdf["substrate_basis"] = [s[1] for s in sub]
            gdf["substrate_desc"] = [SUBSTRATE_DESC.get(s[0], "")
                                     for s in sub]
            # pd.notna, not truthiness: a missing letter comes back from the
            # frame as NaN, and NaN is truthy, so `if t and n` wrote "nan1"
            # into the full type for every unresolved reach.
            # Rosgen order: letter, substrate number, slope class -- B4c, A2a+.
            def _full(letter, n, sc):
                if not (pd.notna(letter) and letter and pd.notna(n)):
                    return None
                return f"{letter}{int(n)}{sc if pd.notna(sc) and sc else ''}"

            gdf["stream_type_full"] = [
                _full(t, n, s) for t, n, s in zip(
                    gdf["stream_letter"], gdf["substrate_class"],
                    gdf["slope_class"])]
            substrate_used = True
            warnings.append(
                "Substrate numbers come from a surficial geology map, not "
                "from the bed. They are a prior for the Rosgen number, good "
                "enough to separate a bedrock reach from an alluvial one and "
                "not good enough to distinguish gravel from cobble. Confirm "
                "with a pebble count before using the full type."
            )
        except Exception as exc:                             # noqa: BLE001
            warnings.append(
                f"Substrate could not be assigned from the geology layer "
                f"({type(exc).__name__}: {exc}). The letter types are "
                f"unaffected."
            )

    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "stream_types.gpkg"
    from .section_plots import reach_plot_file
    gdf["plot_file"] = [reach_plot_file(v) for v in gdf[id_field]]
    gdf["plot_path"] = [str((out_dir / f).resolve()) for f in gdf["plot_file"]]
    gdf.to_file(str(path), driver="GPKG", layer="stream_types")

    sections_path = _write_channel_sections(sec_rows, id_field, seg.crs,
                                            mpu, vpu, out_dir)
    plots: dict[str, Any] = {}
    if section_plots is None:
        section_plots = os.environ.get("GEOLENS_SECTION_PLOTS", "1") not in (
            "0", "false", "False")
    if section_plots and sections_path:
        try:
            from .section_plots import plot_stream_sections
            plots = plot_stream_sections(out_dir)
            warnings.extend(plots.get("warnings", []))
        except Exception as exc:                             # noqa: BLE001
            warnings.append(
                f"Section plots could not be rendered ({type(exc).__name__}: "
                f"{exc}). The stream types are unaffected; run "
                f"api.plot_stream_sections(r'{out_dir}') to retry.")

    basis_counts = gdf["stream_basis"].value_counts().to_dict()
    note(f"  typed on: {basis_counts}")
    counts = (gdf["stream_type"].value_counts(dropna=True).to_dict())
    conf = gdf["stream_confidence"].value_counts(dropna=True).to_dict()
    n_meas = int((gdf["n_sections_measured"] > 0).sum())
    unresolved = int(gdf["stream_type"].isna().sum())
    note(f"  stream types: {counts}"
         + (f", {unresolved} unresolved" if unresolved else ""))
    if n_meas == 0:
        warnings.append(
            "No reach yielded a measurable bankfull channel, so width/depth "
            "is unknown everywhere. Letters rest on the flood-prone width "
            "against the regional channel where that could be read, and on "
            "slope and sinuosity where not; confidence is low throughout."
        )
    return StreamTypeResult(
        segments=str(path), sections=sections_path, n_segments=len(gdf),
        n_measured=n_meas, type_counts=counts, confidence_counts=conf,
        unresolved=unresolved, substrate_used=substrate_used,
        cell_size_m=cell_m, resolvable=resolvable,
        seconds=time.monotonic() - t0, warnings=warnings,
        basis_counts={str(k): int(v) for k, v in basis_counts.items()},
        plots=plots)
