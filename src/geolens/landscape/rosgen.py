"""Rosgen valley types, as a crosswalk from measured terrain plus geology.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
Rosgen's valley types (I-XI, Rosgen 1996, *Applied River Morphology*) are a
field classification. They describe landform *history* -- whether a valley was
cut by ice, filled by a lake, built by a fan, or dissected out of old alluvium
-- and history is not something a DEM contains.

So this is a **crosswalk, not a measurement**. It combines three things the
terrain and the geologic map genuinely do provide:

* **confinement** -- valley width relative to channel width, from
  :func:`classify_segments`
* **valley gradient** -- from the segment long profile
* **surficial material** -- from :mod:`geolens.geology`, area-weighted around
  each segment

and one thing that is directly detectable:

* **whether the segment sits on an alluvial fan** -- from
  :func:`detect_alluvial_fans`, which is Type III by definition

Where those resolve to one type, it says so. Where they do not -- and for
several types they genuinely cannot -- it returns the alternatives and says
which piece of evidence is missing, rather than picking one and sounding
certain. **Every output carries a confidence, and "low" means go and look.**

The types, and what actually separates them
-------------------------------------------
==== ======================================================= ==================
Type Rosgen's description                                    What decides it
==== ======================================================= ==================
I    Steep, confined, V-notched canyons; gorges              confined + steep
II   Moderately steep, gentle side slopes, colluvial         confined + moderate + colluvium
III  **Alluvial fans and debris cones**                      the fan detector
IV   Gentle-gradient canyons, gorges, confined alluvium      confined + gentle
V    Moderately steep U-shaped glacial troughs               moderate + glacial till
VI   Moderately steep, fault- or joint-controlled            structure — NOT resolvable here
VII  Steep, fluvial-dissected, high-relief alluvial slopes   steep + alluvium, unconfined
VIII Wide, gentle valley with terraces and floodplain        gentle + alluvium
IX   Broad, gentle; glacial outwash or eolian sand           gentle + outwash/eolian
X    Very broad, very gentle; glaciolacustrine/lacustrine    very gentle + lacustrine
XI   Large broad valleys, deltaic, highly developed          very gentle + deltaic/coastal
==== ======================================================= ==================

Three honest limitations, all of which change what you should do with the answer
--------------------------------------------------------------------------------
1. **Type VI cannot be identified from a DEM and a geologic map.** It is defined
   by structural control -- faults and joint sets steering the valley. Nothing
   here sees structure. VI is offered as an alternative wherever a confined
   bedrock valley could be either I/II or VI, and never asserted.

2. **Colluvial valleys (II) are systematically under-called.** The national
   geologic map explicitly ignores soil and minor colluvial deposits, and lumps
   thin colluvium into "residual materials". Only mappable mass-movement
   deposits appear. So II is inferred from geometry with bedrock/residual
   material, which is weaker evidence than it looks.

3. **Outwash (IX) is not a distinct map class.** It sits inside "ice-contact and
   ice-marginal sediment" or is absorbed into alluvium. Distinguishing IX from
   VIII in glaciated terrain often needs the source map's own wording, which
   ``fetch_geology`` keeps in ``Source_DescriptionOfMapUnits`` but this does not
   parse.

The numeric gradient breaks below are an ENCODING of Rosgen's descriptive
classes, not values he published. They are the first thing to calibrate against
valleys you have typed in the field.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..safety import Guard, GeolensError, note, progress
from .valleyform import side_slope_class, vf_class  # noqa: F401

# -- reference ----------------------------------------------------------------

VALLEY_TYPES: dict[str, str] = {
    "I": "Steep, confined, V-notched canyons and gorges; colluvial or bedrock",
    "II": "Moderately steep with gentler side slopes; colluvial valleys",
    "III": "Alluvial fans and debris cones",
    "IV": "Gentle-gradient canyons, gorges and confined alluvial slopes",
    "V": "Moderately steep, U-shaped glacial troughs",
    "VI": "Moderately steep, structurally (fault/joint) controlled",
    "VII": "Steep, fluvial-dissected, high-relief alluvial slopes",
    "VIII": "Wide, gentle valley slopes with terraces and a developed floodplain",
    "IX": "Broad, moderate-to-gentle slopes; glacial outwash or eolian sand",
    "X": "Very broad, very gentle; glaciolacustrine or lacustrine deposition",
    "XI": "Large broad valleys with highly developed floodplains; deltaic",
}

# Gradient breaks. An encoding of Rosgen's descriptive classes -- calibrate.
GRADIENT_BREAKS: dict[str, float] = {
    "very_gentle": 0.005,
    "gentle": 0.02,
    "moderate": 0.04,
    "steep": 0.10,
    # above `steep` is very_steep
}


def gradient_class(g: float | None) -> str:
    if g is None or not np.isfinite(g):
        return "unknown"
    g = abs(float(g))
    if g < GRADIENT_BREAKS["very_gentle"]:
        return "very_gentle"
    if g < GRADIENT_BREAKS["gentle"]:
        return "gentle"
    if g < GRADIENT_BREAKS["moderate"]:
        return "moderate"
    if g < GRADIENT_BREAKS["steep"]:
        return "steep"
    return "very_steep"


# Material groupings used by the crosswalk, in terms of the classes that
# geolens.geology.classify_material produces.
_GLACIAL = {"glacial_till"}
_OUTWASH = {"ice_marginal", "eolian"}
_LACUSTRINE = {"lacustrine", "playa"}
_DELTAIC = {"coastal", "marine", "organic"}
_ALLUVIAL = {"alluvial"}
_COLLUVIAL = {"mass_movement"}
_BEDROCK = {"bedrock_igneous_metamorphic", "bedrock_sedimentary", "residual"}


@dataclass
class ValleyTypeResult:
    segments: str
    n_segments: int
    type_counts: dict[str, int]
    confidence_counts: dict[str, int]
    unresolved: int
    geology_used: bool
    fans_used: bool
    seconds: float
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


# -- the crosswalk ------------------------------------------------------------


def classify_from_form(side_slope: float | None, shape_n: float | None,
                       vf_class_: str | None,
                       relief_m: float | None, asymmetry: float | None,
                       material: str | None = None,
                       confinement: str | None = None) -> dict[str, Any] | None:
    """Rosgen valley type from the valley's own cross-section form.

    This is the classification on its own terms. Every discriminator Rosgen
    names is here -- side-slope steepness, V-versus-U shape, relief from floor
    to divide, and how broad the floor is relative to that relief -- rather
    than the channel proxies (confinement ratio, channel gradient) the first
    version used.

    Returns ``None`` when the form is too thin to say anything, so the caller
    can fall back. It does NOT return a guess: on real terrain most sections
    never reach a crest, and a type asserted from a truncated measurement would
    be worse than an honest unresolved.

    ``confinement`` (geolens's class) is used only where the form itself
    cannot tell an open valley from a confined one of the same shape.
    """
    ss = side_slope if (side_slope is not None
                        and np.isfinite(side_slope)) else None
    n = shape_n if (shape_n is not None and np.isfinite(shape_n)) else None
    rel = relief_m if (relief_m is not None and np.isfinite(relief_m)) else None
    if ss is None and vf_class_ in (None, "unknown"):
        return None

    mat = material or "unknown"
    known_mat = mat not in ("unknown", "", None)
    sc = side_slope_class(ss) if ss is not None else "unknown"
    broad = vf_class_ in ("broad", "very_broad")
    # A genuine intermediate band. The U threshold was 1.6, which is not a
    # trough -- it is a shape between the two, and on the valleys fixture a
    # built n = 1.6 wide alluvial valley tripped it and came back as a glacial
    # trough. A parabola is n = 2; asking for 1.8 keeps the call meaningful and
    # leaves 1.3-1.8 as what it actually is, neither.
    u_shaped = n is not None and n >= 1.8
    v_shaped = n is not None and n < 1.3
    very_broad = vf_class_ == "very_broad"
    vi = asymmetry is not None and np.isfinite(asymmetry) and abs(asymmetry) > 0.35
    conf = str(confinement or "").strip().lower()

    def why(extra=""):
        bits = []
        if ss is not None:
            bits.append(f"side slopes {sc} ({ss:.0%})")
        if n is not None:
            bits.append(f"cross-section {'U' if u_shaped else 'V' if v_shaped else 'intermediate'} (n={n:.2f})")
        if rel is not None:
            bits.append(f"{rel:,.0f} m floor-to-divide")
        if vf_class_ and vf_class_ != "unknown":
            bits.append(f"Vf {vf_class_}")
        if u_shaped and known_mat and mat not in _GLACIAL:
            bits.append(f"U-shaped but {mat} material, not till, so not a "
                        f"glacial trough")
        return "; ".join(bits) + (f". {extra}" if extra else "")

    # -- a U-shaped trough with glacial till is a glacial trough -------------
    #
    # Any wall gradient, not just moderate ones. Real glacial troughs have wall
    # gradients of 0.6-1.0, which `side_slope_class` calls "very_steep" -- so
    # restricting this to moderate/steep excluded the common case, dropped it
    # out of this function entirely, and let the channel proxies type it as IX
    # ("ground moraine") without ever mentioning the measured parabola.
    # ... but not when the floor is enormous relative to the relief. Vf is the
    # floor width over the floor-to-divide height, so "very_broad" (> 4) means
    # a floor more than four times the valley's own depth. Yosemite, a textbook
    # trough, is about 1.7. A valley at 7.8 is an alluvial plain whatever its
    # flanks fit to, and the shape exponent alone must not override that.
    #
    # The shape alone is not enough either. "Nothing fluvial makes a U at
    # valley scale" is true of a trough between rock walls; it is not true at
    # 10 m over a desert piedmont, where a wash between two concave bajada
    # flanks fits a parabola as well as any trough does. Kingman (Oct 2026)
    # got 1,500 km of "glacial trough" on alluvium and colluvium at 35 N. A
    # mapped material that is not till is evidence against a trough, so with
    # one the U-shape is noted and the fluvial types below decide. Without
    # any material the shape still names V, at low confidence, with II open.
    if u_shaped and sc != "gentle" and not very_broad:
        if mat in _GLACIAL:
            return out_dict("V", (), "high", why("Glacial till confirms it."))
        if not known_mat:
            return out_dict("V", ("II",), "low",
                            why("No material mapped: a U-shape names a "
                                "trough, but at landscape scale a wash "
                                "between concave flanks fits one too, and "
                                "a broad colluvial valley (II) is not "
                                "excluded. Supply geology."))
        # a known, non-glacial material: carry on to the fluvial types below

    # -- broad and gentle: the depositional types ----------------------------
    #
    # "moderate" flanks are admitted when the floor is very_broad. A valley
    # whose floor is several times its depth is depositional regardless of
    # whether its flanks average 9% or 13%, and requiring "gentle" left the
    # wide alluvial valley in the fixture matching no rule at all.
    if (broad and sc in ("gentle", "unknown")) or (very_broad and sc in
                                                   ("gentle", "moderate",
                                                    "unknown")):
        if mat in _OUTWASH:
            return out_dict("IX", ("VIII",), "high",
                            why("Ice-marginal or eolian material."))
        if mat in _LACUSTRINE:
            return out_dict("X", ("XI",), "high", why("Lacustrine material."))
        if mat in _ALLUVIAL:
            return out_dict("VIII", ("IX",), "medium",
                            why("Alluvial fill; IX if this is outwash."))
        # A broad, gentle floor is depositional whatever the map says of the
        # hillslopes: colluvium or residuum here is the slope's map unit,
        # read into a floor narrower than the unit. Of the depositional
        # types, IX needs outwash or sand and X lacustrine fill, neither
        # mapped, so VIII at low confidence rather than nothing.
        if mat in _COLLUVIAL or mat in _BEDROCK:
            return out_dict("VIII", ("IX", "X"), "low",
                            why(f"A broad, gently sloping floor is alluvial "
                                f"fill; the {mat} mapped is the hillslope's "
                                f"unit. IX or X only if outwash, sand or "
                                f"lake beds are mapped."))
        return out_dict(None, ("VIII", "IX", "X"), "low",
                        why("A broad, gently sloping valley. Which of "
                            "VIII/IX/X depends on depositional history, which "
                            "geometry cannot see -- supply geology."))

    # -- steep and V-notched: the canyon types -------------------------------
    if sc == "very_steep":
        if vi:
            return out_dict("I", ("VI",), "low",
                            why(f"But the valley is markedly one-sided "
                                f"(asymmetry {asymmetry:+.2f}), the "
                                f"structural-control signature, so VI is a "
                                f"real candidate. Check for a mapped fault."))
        return out_dict("I", ("VI",), "medium" if mat != "unknown" else "low",
                        why("A V-notch canyon."))

    if sc == "steep":
        # VII is defined by RELIEF -- "steep, fluvial-dissected, high-relief
        # alluvial slopes" -- so the relief measurement decides it rather than
        # merely appearing in the explanation.
        if mat in _ALLUVIAL and not broad:
            high_relief = rel is not None and rel >= 100.0
            return out_dict("VII", ("III",),
                            "medium" if high_relief else "low",
                            why("Steep alluvial slope -- dissected alluvium; "
                                "III if a fan was simply not detected."
                                if high_relief else
                                "Steep and alluvial, but the relief is modest "
                                "for VII, which Rosgen defines as high-relief."))
        if v_shaped:
            return out_dict("I", ("II", "VI"), "low", why())
        return None

    # -- moderate side slopes: II unless the material says otherwise ---------
    if sc == "moderate":
        if mat in _COLLUVIAL or mat in _BEDROCK:
            return out_dict("II", ("VI",), "medium",
                            why("Colluvial or bedrock side slopes."))
        if mat in _GLACIAL:
            return out_dict("V", ("II",), "medium", why("Glacial till."))
        if v_shaped:
            return out_dict("II", ("V", "VI"), "low",
                            why("V-shaped, so not a glacial trough -- but "
                                "material is needed to separate II from VI."))
        return None

    # -- gentle side slopes but not measurably broad -------------------------
    if sc == "gentle":
        if mat in _ALLUVIAL:
            return out_dict("VIII", ("IV",), "medium",
                            why("Gentle alluvial valley."))
        # The form cannot tell a gentle canyon (IV) from an open valley
        # (VIII); the valley-bottom width against the channel can.
        if conf == "unconfined":
            return out_dict("VIII", ("IV",), "low",
                            why("Gentle side slopes and an unconfined "
                                "floor: an open valley rather than a gentle "
                                "canyon (IV)."))
        if conf == "confined":
            return out_dict("IV", ("VIII",), "low",
                            why("Gentle side slopes and a confined floor: a "
                                "gentle canyon rather than an open valley "
                                "(VIII)."))
        return out_dict(None, ("IV", "VIII"), "low",
                        why("Gentle side slopes; confined (IV) and open (VIII) "
                            "valleys share this form."))

    return None


def out_dict(t, alts=(), conf="medium", basis=""):
    return {"valley_type": t, "alternatives": list(alts),
            "confidence": conf, "basis": basis}


def classify_valley_type(
    confinement: str,
    gradient: float | None,
    material: str | None = None,
    valley_width_m: float | None = None,
    on_fan: bool = False,
    form: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Crosswalk one segment to a Rosgen valley type.

    Returns ``type``, ``alternatives``, ``confidence`` and ``basis`` — the last
    being a short statement of what actually decided it, so an answer can be
    argued with.

    Confidence means:

    ``high``    geometry and material agree on one type, or a fan was detected
    ``medium``  geometry narrows it and material picks between the candidates
    ``low``     geometry alone; material is missing, unmapped, or unhelpful

    ``form``
        Optional valley cross-section measurements from
        :func:`geolens.landscape.valleyform.valley_form`: ``shape_n``,
        ``side_slope``, ``vf``, ``asymmetry``, ``valley_relief_m``.

        **This is the evidence the classification is actually defined on**, and
        everything above it is a proxy. ``confinement`` is valley-bottom width
        over CHANNEL width and ``gradient`` is the channel's downstream slope;
        Rosgen's discriminators are the valley's side-slope steepness, its
        cross-section shape and its relief from floor to divide. Where ``form``
        is supplied it is preferred, and it resolves two cases the proxies
        cannot:

        * **V vs I without a geologic map.** A glacial trough is parabolic
          (n ~ 2) and a fluvial V-notch is linear (n ~ 1) at the same side-slope
          gradient. Until now V could only be asserted from glacial till.
        * **VI as a candidate rather than a permanent unknown.** A valley that
          is one-sided section after section is the classic signature of
          structural control. That is not proof — VI is defined by structure and
          a DEM does not show faults — but it is the first evidence for it here,
          so it is raised from "never assertable" to "flagged".
    """
    grad = gradient_class(gradient)
    mat = material or "unknown"
    f = form or {}

    def _num(key):
        v = f.get(key)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return v if np.isfinite(v) else None

    shape_n = _num("shape_n")
    side_slope = _num("side_slope")
    asym = _num("asymmetry")

    def out(t, alts=(), conf="medium", basis=""):
        return {"valley_type": t, "alternatives": list(alts),
                "confidence": conf, "basis": basis}

    # --- Type III is detected, not inferred ---------------------------------
    if on_fan:
        # medium, not high: the fan search is verified on synthetic fans
        # only, and the cone test is what keeps valley floors out.
        return out("III", (), "medium",
                   "at least half the segment lies on a detected alluvial fan")

    # --- measured form FIRST ------------------------------------------------
    #
    # Form is what the classification is defined on; confinement and channel
    # gradient are proxies for it. Consulting them first meant the form was
    # only reached in a couple of branches -- on a 222-segment fixture where
    # 216 segments are "unconfined", the measured side slopes and shapes were
    # never looked at, and 97% came back unresolved.
    if f:
        # on_fan is handled above and returns before this point, so it is not
        # passed: Type III is detected, never inferred from form.
        from_form = classify_from_form(
            side_slope=_num("side_slope"), shape_n=shape_n,
            vf_class_=f.get("vf_class"),
            relief_m=_num("valley_relief_m"), asymmetry=asym,
            material=mat, confinement=confinement,
        )
        if from_form is not None:
            return from_form

    # --- otherwise fall back to the channel proxies --------------------------
    #
    # A parabolic cross-section with glacial till is a glacial trough. The
    # shape alone is not (see classify_from_form): a mapped material that is
    # not till argues against a trough, and no material leaves V and II open.
    if shape_n is not None and shape_n >= 1.6 and confinement != "unconfined":
        if mat in _GLACIAL:
            return out("V", (), "high",
                       f"U-shaped cross-section (n={shape_n:.2f}) and glacial "
                       f"till — glacial trough, both lines of evidence agree")
        if mat == "unknown" or not mat:
            return out("V", ("II",), "low",
                       f"U-shaped cross-section (n={shape_n:.2f}) measured "
                       f"floor-to-divide and no material mapped: a glacial "
                       f"trough (V), unless a broad colluvial valley (II)")

    # Persistent one-sided relief is the structural-control signature.
    vi_flag = asym is not None and abs(asym) > 0.35

    if confinement == "unknown" or grad == "unknown":
        return out(None, (), "low",
                   "confinement or gradient unavailable for this segment")

    steep = grad in ("steep", "very_steep")
    moderate = grad == "moderate"
    gentle = grad == "gentle"
    very_gentle = grad == "very_gentle"
    known_mat = mat != "unknown" and mat is not None

    # --- confined ------------------------------------------------------------
    if confinement == "confined":
        if steep:
            # I is the default steep confined valley. VI is the same geometry
            # with structural control.
            if vi_flag:
                return out("I", ("VI",), "low",
                           f"confined and {grad}, but the valley is markedly "
                           f"one-sided (asymmetry {asym:+.2f}) section after "
                           f"section — that is the structural-control "
                           f"signature, so VI is a real candidate here rather "
                           f"than a formality. Check for a mapped fault.")
            if shape_n is not None and shape_n < 1.3 and side_slope is not None:
                return out("I", ("VI",), "medium" if known_mat else "low",
                           f"confined, {grad}, V-shaped (n={shape_n:.2f}) with "
                           f"{side_slope_class(side_slope)} side slopes "
                           f"({side_slope:.0%}) — a V-notch canyon")
            return out("I", ("VI",), "medium" if known_mat else "low",
                       f"confined and {grad}; VI cannot be excluded without "
                       f"structural mapping")
        if moderate:
            if mat in _GLACIAL:
                return out("V", (), "high",
                           "confined, moderate gradient, glacial till — U-shaped trough")
            if mat in _COLLUVIAL:
                return out("II", (), "high",
                           "confined, moderate gradient, mass-movement deposits")
            if mat in _BEDROCK:
                return out("II", ("VI",), "medium",
                           "confined, moderate gradient, bedrock/residual — "
                           "colluvial II unless structurally controlled")
            if shape_n is not None and shape_n < 1.3:
                return out("II", ("VI",), "medium",
                           f"confined and moderate with a V-shaped section "
                           f"(n={shape_n:.2f}) — not a glacial trough, so V is "
                           f"excluded on shape rather than on material")
            return out(None, ("II", "V", "VI"), "low",
                       "confined and moderate: II, V and VI share this geometry. "
                       "Material separates them, and so does cross-section "
                       "shape — run valley_form and pass form=")
        # gentle or very gentle
        return out("IV", (), "medium" if known_mat else "low",
                   f"confined but {grad} — gentle canyon or confined alluvium")

    # --- partly confined -----------------------------------------------------
    if confinement == "partly confined":
        if steep:
            if mat in _ALLUVIAL:
                return out("VII", ("III",), "medium",
                           "steep, partly confined, alluvial — dissected alluvial "
                           "slope; III if a fan was simply not detected")
            return out("I", ("II", "VII"), "low",
                       "steep and partly confined; material does not separate "
                       "the candidates")
        if moderate:
            if mat in _GLACIAL:
                return out("V", (), "high", "moderate gradient, glacial till")
            if mat in _COLLUVIAL or mat in _BEDROCK:
                return out("II", (), "medium",
                           "moderate gradient, colluvial or bedrock side slopes")
            return out(None, ("II", "V"), "low",
                       "moderate and partly confined: II and V need material")
        if mat in _OUTWASH:
            return out("IX", ("VIII",), "medium",
                       "gentle, ice-marginal or eolian material")
        if mat in _ALLUVIAL:
            return out("VIII", (), "high",
                       "gentle, partly confined, alluvial — terraced valley")
        return out("VIII", ("IV", "IX"), "low",
                   "gentle and partly confined; material unknown")

    # --- unconfined ----------------------------------------------------------
    if steep:
        # Steep AND unconfined is unusual and is nearly always a fan surface or
        # a dissected alluvial slope.
        if mat in _ALLUVIAL:
            return out("VII", ("III",), "medium",
                       "steep, unconfined, alluvial — dissected alluvial slope. "
                       "If the fan detector was not run, III is likely")
        return out(None, ("III", "VII"), "low",
                   "steep but unconfined — run detect_alluvial_fans; this is the "
                   "geometry of a fan surface")
    if moderate:
        if mat in _OUTWASH:
            return out("IX", (), "high", "moderate, outwash or eolian material")
        if mat in _GLACIAL:
            # Till at a moderate gradient is the floor of a glacial trough, and
            # "moderately steep" is exactly V's qualifier.
            return out("V", ("IX",), "medium",
                       "moderate, unconfined, glacial till — trough floor; IX if "
                       "this is ground moraine rather than a trough")
        if mat in _ALLUVIAL:
            return out("VIII", ("IX",), "medium",
                       "moderate, unconfined, alluvial")
        if mat in _COLLUVIAL or mat in _BEDROCK:
            return out("VIII", ("IX",), "low",
                       f"moderate and unconfined; the {mat} mapped is the "
                       f"hillslope's unit, and an unconfined floor is alluvial")
        return out(None, ("VIII", "IX"), "low",
                   "moderate and unconfined: VIII and IX need material")
    if gentle:
        if mat in _LACUSTRINE:
            return out("X", (), "high", "gentle, unconfined, lacustrine")
        if mat in _OUTWASH:
            return out("IX", (), "high", "gentle, unconfined, outwash or eolian")
        if mat in _GLACIAL:
            # Broad and gentle rules out V, whose defining qualifier is
            # "moderately steep". Till here is ground moraine, which sits with
            # the broad glacial-depositional type rather than the fluvial one --
            # but IX is written around outwash, so this is the weaker reading of
            # IX and is reported as medium, not high.
            return out("IX", ("V", "VIII"), "medium",
                       "gentle, unconfined, glacial till — ground moraine; note "
                       "IX is defined around outwash, and this is till")
        if mat in _ALLUVIAL:
            return out("VIII", ("XI",), "medium",
                       "gentle, unconfined, alluvial — terraced floodplain valley")
        return out("VIII", ("IX", "X"), "low",
                   "gentle and unconfined; material unknown")

    # very gentle
    if mat in _LACUSTRINE:
        return out("X", (), "high", "very gentle, lacustrine")
    if mat in _DELTAIC:
        return out("XI", (), "high", "very gentle, deltaic or coastal material")
    if valley_width_m is not None and valley_width_m > 2000:
        return out("XI", ("X",), "medium",
                   f"very gentle and {valley_width_m:.0f} m wide — broad "
                   f"floodplain valley")
    if mat in _ALLUVIAL:
        return out("VIII", ("X", "XI"), "medium",
                   "very gentle, alluvial, not especially broad")
    if mat in _COLLUVIAL or mat in _BEDROCK:
        return out("VIII", ("X", "XI"), "low",
                   f"very gentle and unconfined; the {mat} mapped is the "
                   f"hillslope's unit, and an unconfined floor is alluvial")
    return out(None, ("VIII", "X", "XI"), "low",
               "very gentle and unconfined: the broad lowland types are "
               "separated by depositional history, which needs material")


# -- attaching geology to segments -------------------------------------------


def attach_material(segments, geology, buffer_m: float = 200.0,
                    min_cover: float = 0.25):
    """Area-weighted dominant material class within a buffer of each segment.

    Buffered rather than sampled at a point because a segment is a line and the
    valley it describes has width; a point sample lands wherever the centreline
    happens to be and can pick the channel deposit rather than the valley fill.

    ``min_cover`` is the fraction of the buffer that must be covered by a single
    material before it is accepted. Below that the segment gets ``None``, which
    is a real answer: the map did not resolve this valley. Silently returning
    whichever sliver was largest is how a 1:500,000 map ends up looking like it
    typed a 60 m valley.
    """
    import geopandas as gpd
    import pandas as pd

    if "material_class" not in geology.columns:
        raise GeolensError(
            "Geology layer has no `material_class` column. Use the output of "
            "geolens.geology.fetch_geology, which adds it."
        )

    geo = geology.to_crs(segments.crs) if geology.crs != segments.crs else geology
    geo = geo[geo["material_class"].notna()]

    # buffer() works in the layer's own units. On EPSG:6529 a 200 m buffer was
    # 200 ft = 61 m, so the geology was sampled from a third of the ground it
    # was meant to cover -- and a reach whose valley floor is wider than that
    # picked up whatever unit the channel itself happens to sit on.
    try:
        _mpu = float(segments.crs.linear_units_factor[1])
    except Exception:                                        # noqa: BLE001
        _mpu = 1.0
    buffer_m = float(buffer_m) / max(_mpu, 1e-12)

    materials: list[str | None] = []
    covers: list[float] = []
    for geom in progress(list(segments.geometry), label="material per segment"):
        if geom is None or geom.is_empty:
            materials.append(None)
            covers.append(0.0)
            continue
        buf = geom.buffer(buffer_m)
        hit = geo[geo.intersects(buf)]
        if hit.empty:
            materials.append(None)
            covers.append(0.0)
            continue
        areas: dict[str, float] = {}
        for _, row in hit.iterrows():
            inter = row.geometry.intersection(buf)
            if inter.is_empty:
                continue
            areas[row["material_class"]] = areas.get(row["material_class"], 0.0) + inter.area
        if not areas:
            materials.append(None)
            covers.append(0.0)
            continue
        best = max(areas, key=areas.get)
        cover = areas[best] / buf.area if buf.area > 0 else 0.0
        materials.append(best if cover >= min_cover else None)
        covers.append(float(cover))
    return materials, covers


# -- public -------------------------------------------------------------------


def rosgen_valley_types(
    segments: str | Path,
    geology: str | Path | None = None,
    fans: str | Path | None = None,
    valley_form: str | Path | None = None,
    gradient_field: str = "gradient",
    confinement_field: str = "confinement",
    width_field: str = "valley_width_m",
    buffer_m: float = 200.0,
    min_cover: float = 0.25,
    guard: Guard | None = None,
    out_prefix: str = "valley_types",
) -> ValleyTypeResult:
    """Assign a Rosgen valley type to every segment.

    Parameters
    ----------
    segments
        Output of :func:`classify_segments` — needs confinement, gradient and
        valley width per segment.
    geology
        Output of :func:`geolens.geology.fetch_geology`. **Without it most
        segments come back unresolved or low confidence**, because the types
        that geometry alone cannot separate (II/V/VI, VIII/IX, X/XI) are most of
        the classification.
    fans
        Output of :func:`detect_alluvial_fans`. Any segment on a fan is Type III
        with high confidence; that is the one type here that is measured rather
        than inferred.
    valley_form
        ``valley_form_segments.csv`` from
        :func:`geolens.landscape.valleyform.valley_form` — cross-section shape,
        side slope, Vf and asymmetry measured floor-to-divide. **This is the
        evidence Rosgen's types are actually defined on**; confinement and
        channel gradient are proxies for it. With it, a glacial trough (V) can
        be told from a V-notch (I) without a geologic map, and structural
        control (VI) has evidence for the first time.

    Outputs ``<prefix>_dir/valley_types.gpkg`` with ``valley_type``,
    ``alternatives``, ``confidence``, ``basis``, ``material_class`` and
    ``material_cover`` per segment.
    """
    import geopandas as gpd
    import pandas as pd

    t0 = time.monotonic()
    guard = guard or Guard.from_env()
    warnings: list[str] = []

    seg = gpd.read_file(str(guard.read_path(segments)))
    if seg.empty:
        raise GeolensError(f"No segments in {segments}")

    for fld, label in ((confinement_field, "confinement"),
                       (gradient_field, "gradient")):
        if fld not in seg.columns:
            raise GeolensError(
                f"Segments have no '{fld}' column ({label}). Pass the output of "
                f"classify_segments, or name the column with {label}_field."
            )

    # --- material ------------------------------------------------------------
    materials: list[str | None] = [None] * len(seg)
    covers: list[float] = [0.0] * len(seg)
    if geology is not None:
        geo = gpd.read_file(str(guard.read_path(geology)))
        materials, covers = attach_material(seg, geo, buffer_m=buffer_m,
                                            min_cover=min_cover)
        resolved = sum(1 for m in materials if m)
        note(f"material resolved for {resolved}/{len(seg)} segments")
        if resolved < len(seg) * 0.5:
            warnings.append(
                f"Material resolved for only {resolved} of {len(seg)} segments. "
                f"At national map scale a valley narrower than the minimum "
                f"mapping unit returns its hillslope's bedrock or nothing at "
                f"all, so most types will fall back to geometry alone. Lower "
                f"min_cover to accept weaker evidence, or accept that these "
                f"valleys were not mapped."
            )
    else:
        warnings.append(
            "No geology supplied. Types II/V/VI, VIII/IX and X/XI are separated "
            "by depositional history and cannot be resolved from geometry — "
            "expect mostly low confidence and unresolved segments. Run "
            "geolens.geology.fetch_geology first."
        )

    # --- fans ----------------------------------------------------------------
    on_fan = np.zeros(len(seg), dtype=bool)
    if fans is not None:
        fan_gdf = gpd.read_file(str(guard.read_path(fans)))
        if not fan_gdf.empty:
            if fan_gdf.crs != seg.crs:
                fan_gdf = fan_gdf.to_crs(seg.crs)
            union = fan_gdf.geometry.union_all() if hasattr(fan_gdf.geometry, "union_all") \
                else fan_gdf.geometry.unary_union
            # Most of the reach, not a touch. A trunk stream running past a
            # fan toe crosses a sliver of the fan and is not on one.
            on_fan = np.array([
                g is not None and not g.is_empty and g.length > 0
                and g.intersection(union).length / g.length >= 0.5
                for g in seg.geometry])
            note(f"{int(on_fan.sum())} segments lie on a detected fan "
                 f"(at least half their length)")

    # --- valley form, joined by segment id -----------------------------------
    form_by_seg: dict[Any, dict[str, Any]] = {}
    seg_key: str | None = None
    if valley_form is not None:
        import pandas as pd

        vf_df = pd.read_csv(str(guard.read_path(valley_form)))
        key = "segment_id" if "segment_id" in vf_df.columns else vf_df.columns[0]

        # The segments layer names its id `seg_id`; valley_form keys on
        # `segment_id`. Looking only for `segment_id` on the segments meant the
        # join never matched, the evidence was silently dropped, and the
        # classification fell back to the channel proxies it was added to
        # replace — with a warning that blamed the segments for a naming
        # mismatch on our own side.
        seg_key = None
        for cand in ("seg_id", "segment_id", "SEG_ID", "id"):
            if cand in seg.columns:
                seg_key = cand
                break

        for _, row in vf_df.iterrows():
            form_by_seg[row[key]] = row.to_dict()
        note(f"valley form read for {len(form_by_seg)} segments "
             f"(joining on {seg_key!r})")
        if seg_key is None:
            warnings.append(
                f"The segments layer has no id column (looked for seg_id, "
                f"segment_id, id), so valley form could not be joined and was "
                f"ignored. Columns present: {sorted(seg.columns)[:8]}."
            )
            form_by_seg = {}

    # --- classify ------------------------------------------------------------
    from .landscapes import FLUVIAL_LANDSCAPES, classify_landscape_type

    rows = []
    lrows = []
    n_with_form = 0
    for i, (_, s) in enumerate(seg.iterrows()):
        f = form_by_seg.get(s.get(seg_key)) if form_by_seg else None
        if f:
            n_with_form += 1
        rows.append(classify_valley_type(
            confinement=str(s.get(confinement_field, "unknown")),
            gradient=s.get(gradient_field),
            material=materials[i],
            valley_width_m=s.get(width_field),
            on_fan=bool(on_fan[i]),
            form=f,
        ))
        # Rosgen's 2022 fluvial landscape types, from the same evidence.
        lrows.append(classify_landscape_type(
            confinement=s.get(confinement_field), gradient=s.get(gradient_field),
            material=materials[i], on_fan=bool(on_fan[i]), form=f))
    if form_by_seg and n_with_form < 0.5 * len(seg):
        warnings.append(
            f"valley form matched only {n_with_form} of {len(seg)} segments. "
            f"The rest fall back to confinement and channel gradient, which are "
            f"proxies — check that both were built from the same segmentation."
        )

    seg["valley_type"] = [r["valley_type"] for r in rows]
    seg["alternatives"] = ["|".join(r["alternatives"]) for r in rows]
    seg["confidence"] = [r["confidence"] for r in rows]
    seg["basis"] = [r["basis"] for r in rows]
    seg["material_class"] = materials
    seg["material_cover"] = covers
    seg["valley_type_desc"] = [
        VALLEY_TYPES.get(r["valley_type"], "") for r in rows
    ]
    seg["landscape_type"] = [r["landscape_type"] for r in lrows]
    seg["landscape_name"] = [
        FLUVIAL_LANDSCAPES[r["landscape_type"]][0] if r["landscape_type"]
        else None for r in lrows]
    seg["landscape_alternatives"] = ["|".join(r["alternatives"]) for r in lrows]
    seg["landscape_confidence"] = [r["confidence"] for r in lrows]
    seg["landscape_basis"] = [r["basis"] for r in lrows]

    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "valley_types.gpkg"
    seg.to_file(str(out_path), driver="GPKG")

    types = pd.Series([r["valley_type"] for r in rows])
    conf = pd.Series([r["confidence"] for r in rows])
    unresolved = int(types.isna().sum())
    if unresolved > len(seg) * 0.3:
        warnings.append(
            f"{unresolved} of {len(seg)} segments could not be resolved to a "
            f"single type. Read the `alternatives` and `basis` columns — the "
            f"classification is telling you which evidence is missing rather "
            f"than guessing."
        )

    return ValleyTypeResult(
        segments=str(out_path),
        n_segments=int(len(seg)),
        type_counts={str(k): int(v) for k, v in types.value_counts().items()},
        confidence_counts={str(k): int(v) for k, v in conf.value_counts().items()},
        unresolved=unresolved,
        geology_used=geology is not None,
        fans_used=fans is not None,
        seconds=time.monotonic() - t0,
        warnings=warnings,
    )
