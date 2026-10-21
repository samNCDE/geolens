"""Rosgen's fluvial landscape types (2022): the valley named by what built it.

Rosgen's newer valley classification replaces valley types I to XI with
codes that say three things: whether the valley confines its river (U
unconfined, C confined), the material that built it (AL alluvial, BR
bedrock, EO eolian, RE residual, CO colluvial, GL glacial, LA lacustrine,
MA marine, PE periglacial), and the landform (AD alluvial deposition, AF
active fans, IF inactive fans, ...). ``U-AL-AD`` is an unconfined alluvial
valley with terraces.

Each type comes with a slope range and the stream types that form in it:
the natural ones (bold on the card) and the ones seen under disequilibrium
(bracketed). That second list is what the stable / unstable judgement
needs, so each reach gets ``landscape_form`` beside the older ``form_status``:

``stable``     its stream type is one of the type's natural stream types;
``unstable``   it is one the card lists only under disequilibrium;
``check``      the card does not list it for this type, or lists it both
               ways (a D in an alluvial valley can be either);
``unknown``    no landscape type or no stream type.

Matching is on the stream type with its slope class, as the card writes it:
``Bc`` is not ``B``. Source: Rosgen (2022), "Fluvial landscape types",
Wildland Hydrology field card, transcribed in ``FLUVIAL_LANDSCAPES``.

What a DEM and a soils or geology map can tell apart is less than the card
does: active from inactive fans, sand dunes from loess, an inner gorge from
a narrow alluvial valley. Where the evidence does not decide, the candidates
are kept in ``landscape_alternatives`` and the form is judged under each.
The slope used is the reach's channel gradient, a stand-in for the valley
slope the card means.
"""
from __future__ import annotations

from typing import Any

SOURCE = ("Rosgen (2022) fluvial landscape types, Wildland Hydrology field "
          "card")

# code -> (name, slope rule, natural stream types, disequilibrium types).
# A slope rule is (">", 2.0) for "slope > 2%", ("<", 3.0) for "< 3%", or None
# for "slope varies".
FLUVIAL_LANDSCAPES: dict[str, tuple[str, tuple | None, tuple, tuple]] = {
    # unconfined
    "U-AL-AD": ("alluvial deposition, terraces", ("<", 3.0),
                ("Bc", "C", "D", "E"), ("A", "D", "F", "Gc")),
    "U-AL-AF": ("alluvial: active fans", (">", 2.0),
                ("D",), ("A", "Fb", "G")),
    "U-AL-IF": ("alluvial: inactive fans", (">", 2.0),
                ("Ba", "B"), ("A", "D", "Fb", "G")),
    "U-AL-RD": ("alluvial: deltas, estuaries, coastal plains", ("<", 2.0),
                ("C", "D", "DA", "E"), ("F", "Gc")),
    "U-BR-BC": ("bedrock-controlled", ("<", 2.0), ("C",), ()),
    "U-EO-SD": ("eolian: sand dunes", ("<", 2.0),
                ("Bc", "C", "D"), ("F", "Gc")),
    "U-EO-LP": ("eolian: loess plains and hills", ("<", 2.0),
                ("Bc", "C", "E"), ("D", "F", "Gc")),
    "U-GL-GO": ("glacial outwash plains", ("<", 4.0),
                ("Bc", "C", "D"), ("F", "Gc")),
    "U-GL-TP": ("glacial till plains", ("<", 4.0),
                ("B", "C", "E"), ("D", "F", "G")),
    "U-PE-CS": ("periglacial: cryoplanated surfaces", ("<", 2.0),
                ("Bc", "C", "E"), ("F", "Gc")),
    "U-LA-LD": ("lacustrine deposition", ("<", 2.0),
                ("C", "DA", "E"), ("D", "F", "Gc")),
    # confined
    "C-AL-AD": ("alluvial deposition, narrow", ("<", 5.0),
                ("B", "C", "D", "E", "F", "G"), ("A", "D", "F", "G")),
    "C-AL-IG": ("alluvial: inner gorges", ("<", 2.0),
                ("Bc", "C", "F"), ("D", "Gc")),
    "C-BR-BC": ("bedrock-controlled", None,
                ("Aa+", "A", "Ba", "B", "F", "G"), ()),
    "C-EO-LH": ("eolian: loess hills, plains and plateaus", (">", 2.0),
                ("Aa+", "A", "B", "Cb", "Eb"), ("Fb", "G")),
    "C-EO-SH": ("eolian: sand hills", (">", 2.0),
                ("Aa+", "A", "B", "Cb", "Eb", "Fb", "G"), ()),
    "C-EO-FD": ("eolian: fluvial-dissected", (">", 2.0),
                ("Aa+", "A", "B", "Fb", "G"), ()),
    "C-RE-FD": ("residual: fluvial-dissected", (">", 2.0),
                ("Aa+", "A", "B", "Fb", "G"), ()),
    "C-CO-FD": ("colluvial: fluvial-dissected", (">", 2.0),
                ("Aa+", "A", "B", "Fb", "G"), ()),
    "C-CO-VS": ("colluvial: V-shaped, steep", (">", 6.0),
                ("Aa+", "A", "Ba"), ()),
    "C-CO-US": ("colluvial: U-shaped, moderately steep", ("<", 6.0),
                ("Ba", "B"), ("Fb", "G")),
    "C-GL-GT": ("glacial trough", ("<", 5.0),
                ("B", "C", "D"), ("F", "G")),
    "C-GL-TP": ("glacial till plains", ("<", 5.0),
                ("B", "C", "E"), ("D", "F", "G")),
    "C-LA-AB": ("lacustrine: abandoned beaches", (">", 4.0),
                ("Aa+", "A", "B"), ("D", "Fb", "G")),
    "C-MA-AB": ("marine: abandoned beaches", (">", 2.0),
                ("Aa+", "A", "B", "Cb", "Eb"), ("D", "Fb", "G")),
}

# geolens's material classes (geology.py, soils.py) -> the card's origins.
_ORIGIN = {
    "alluvial": "AL",
    "bedrock_igneous_metamorphic": "BR", "bedrock_sedimentary": "BR",
    "eolian": "EO", "residual": "RE", "mass_movement": "CO",
    "glacial_till": "GL", "ice_marginal": "GL",
    "lacustrine": "LA", "playa": "LA",
    "marine": "MA", "coastal": "MA",
}


def card_wording(code: str) -> str:
    """A type's stream types as the card prints them: ``Bc, C, D, E [A, D,
    F, Gc]``."""
    _, _, ok, bad = FLUVIAL_LANDSCAPES[code]
    return ", ".join(ok) + (f" [{', '.join(bad)}]" if bad else "")


def _slope_ok(code: str, pct: float | None) -> bool | None:
    rule = FLUVIAL_LANDSCAPES[code][1]
    if rule is None or pct is None:
        return None
    op, lim = rule
    return pct < lim if op == "<" else pct > lim


def _candidates(conf: str, origin: str | None, material: str | None,
                pct: float | None, on_fan: bool, shape_n: float | None,
                fan_activity: str | None = None) -> list[str]:
    """The card's types this evidence allows, most likely first."""
    if origin is None:
        return []
    if conf == "U":
        if origin == "AL":
            if on_fan:
                return (["U-AL-IF", "U-AL-AF"] if fan_activity == "inactive"
                        else ["U-AL-AF", "U-AL-IF"])
            return ["U-AL-AD"] + (["U-AL-IF"] if pct is not None and pct > 3
                                  else [])
        if origin == "MA":
            return ["U-AL-RD"]
        if origin == "GL":
            return ["U-GL-GO"] if material == "ice_marginal" else ["U-GL-TP"]
        return {"BR": ["U-BR-BC"], "EO": ["U-EO-SD", "U-EO-LP"],
                "LA": ["U-LA-LD"]}.get(origin, [])
    # confined
    if origin == "AL":
        return ["C-AL-AD", "C-AL-IG"] if pct is not None and pct < 2 \
            else ["C-AL-AD"]
    if origin == "CO":
        if shape_n is not None:
            first = "C-CO-VS" if shape_n < 1.5 else "C-CO-US"
        else:
            first = "C-CO-VS" if pct is not None and pct > 6 else "C-CO-US"
        second = "C-CO-US" if first == "C-CO-VS" else "C-CO-VS"
        return [first, second, "C-CO-FD"]
    if origin == "GL":
        return ["C-GL-GT", "C-GL-TP"]
    return {"BR": ["C-BR-BC"], "EO": ["C-EO-LH", "C-EO-SH", "C-EO-FD"],
            "RE": ["C-RE-FD"], "LA": ["C-LA-AB"], "MA": ["C-MA-AB"]
            }.get(origin, [])


def classify_landscape_type(confinement: Any, gradient: Any,
                            material: Any = None, on_fan: bool = False,
                            form: dict | None = None,
                            fan_activity: str | None = None,
                            landform: Any = None) -> dict[str, Any]:
    """A reach's fluvial landscape type from what geolens measures.

    ``confinement`` is geolens's class (unconfined, partly confined,
    confined), ``gradient`` the channel slope as a fraction, ``material``
    a geolens material class, ``on_fan`` whether the reach is on an
    alluvial fan (detected on the DEM, or named by the soils map),
    ``form`` the valley form (``shape_n``: 1 a V, 2 a U). ``fan_activity``
    is ``"active"`` or ``"inactive"`` when the soils map's ``landform``
    says which (v0.39): that settles the card's AF / IF, which the DEM
    cannot.

    Returns ``landscape_type`` (None when the evidence leaves more than one
    type), ``alternatives``, ``confidence`` and ``basis``.
    """
    import math

    c = str(confinement or "").strip().lower()
    try:
        g = float(gradient)
        pct = abs(g) * 100.0 if math.isfinite(g) else None
    except (TypeError, ValueError):
        pct = None
    mat = str(material).strip() if material not in (None, "") else None
    mat = None if mat in ("nan", "None") else mat
    origin = _ORIGIN.get(mat) if mat else None
    shape_n = None
    try:
        v = float((form or {}).get("shape_n"))
        shape_n = v if math.isfinite(v) else None
    except (TypeError, ValueError):
        pass

    def out(t, alts, conf, basis):
        return {"landscape_type": t, "alternatives": list(alts),
                "confidence": conf, "basis": basis}

    if c not in ("unconfined", "partly confined", "confined"):
        return out(None, [], "low", "confinement unknown")
    slope = "slope unknown" if pct is None else f"slope {pct:.1f}%"

    # An unconfined valley floor is stream-built. The soils or geology map
    # may still say colluvium, residuum or nothing over it: at map scale a
    # floor narrower than the map unit takes the hillslope's unit, and the
    # soils are fetched over the boundary, not the whole area. Kingman
    # (Oct 2026) left 22% of its stream length without a landscape type that
    # way. So the floor's material is taken as alluvial, at low confidence,
    # with the map's word in the basis; a confined valley keeps the map's
    # material, which there names the walls as the card means it to.
    assumed = ""
    map_origin, map_mat = origin, mat
    if c in ("unconfined", "partly confined") and origin in (None, "CO", "RE"):
        assumed = ("no material mapped" if origin is None
                   else f"the soils map {mat}, the hillslope's unit")
        assumed += "; an unconfined floor is stream-built, so alluvial (AL)"
        origin, mat = "AL", "alluvial"
    if origin is None:
        return out(None, [], "low",
                   "no material: the card's types are named by what built "
                   "the valley" if not mat else
                   f"the card has no type for {mat} material")
    if c == "partly confined":
        # The unconfined candidate is the floor's (alluvial when assumed);
        # the confined one keeps the map's material, which names the walls.
        cands = (_candidates("U", origin, mat, pct, on_fan, shape_n,
                             fan_activity)[:1]
                 + (_candidates("C", map_origin, map_mat, pct, on_fan, shape_n)
                    if map_origin else [])[:1])
        fit = [k for k in cands if _slope_ok(k, pct) is not False]
        if len(fit) == 1:
            return out(fit[0], [k for k in cands if k != fit[0]], "low",
                       f"partly confined; only {fit[0]} fits the slope"
                       + (f" ({assumed})" if assumed else ""))
        if len(fit) == 2:
            # A floor wider than the channel needs, if not by much: the
            # channel can still shift and deposit, which is what the card's
            # unconfined types describe. The confined one stays listed.
            return out(fit[0], fit[1:], "low",
                       f"partly confined, {slope}: the floor is wider than "
                       f"the channel, so the unconfined type, with the "
                       f"confined one as the alternative"
                       + (f" ({assumed})" if assumed else ""))
        return out(None, cands, "low",
                   "partly confined: between the card's unconfined and "
                   "confined types")
    cands = _candidates("U" if c == "unconfined" else "C", origin, mat, pct,
                        on_fan, shape_n, fan_activity)
    if not cands:
        return out(None, [], "low",
                   f"the card has no {c} type built of {mat} material")
    fits = [k for k in cands if _slope_ok(k, pct) is not False]
    # A detected fan: active or inactive is a field call the DEM cannot
    # make, so the active type is given at low confidence with the inactive
    # one open (a fan whose wash still crosses its surface is being built
    # along that wash), rather than no type at all on the map.
    if on_fan and c == "unconfined" and origin == "AL":
        if fan_activity in ("active", "inactive"):
            return out(cands[0], cands[1:], "medium",
                       f"on a fan surface the soils map calls "
                       f"'{landform}' ({fan_activity}), {slope}")
        return out(cands[0], cands[1:], "low",
                   f"on a detected alluvial fan, {slope}; active (AF) or "
                   f"inactive (IF) is a field call")
    if origin == "EO" and len(fits) > 1:
        return out(None, fits, "low",
                   f"eolian, {slope}; the soils map does not say sand or "
                   f"loess")
    best = fits[0] if fits else cands[0]
    conf = "low" if assumed else "medium" if fits else "low"
    note = "" if fits else (f"; {slope} is outside its "
                            f"{FLUVIAL_LANDSCAPES[best][1][0]} "
                            f"{FLUVIAL_LANDSCAPES[best][1][1]:g}%")
    return out(best, [k for k in cands if k != best], conf,
               f"{c}, {assumed if assumed else mat + ' material'}, "
               f"{slope}{note}")


def stream_code(stream_type: Any, letter: Any = None,
                slope_class: Any = None) -> str | None:
    """A reach's stream type as the card writes it: letter plus slope class
    (``Bc``, ``Aa+``, ``DA``), without the bed-material number."""
    st = str(stream_type or "").strip()
    if st and st not in ("nan", "None"):
        return st.replace(" ", "")
    lt = str(letter or "").strip()
    if not lt or lt in ("nan", "None"):
        return None
    sc = str(slope_class or "").strip()
    return lt + ("" if sc in ("", "nan", "None") else sc)


def landscape_status(code: Any, stream: Any) -> tuple[str, str]:
    """(status, note) for one landscape type and one stream type."""
    k = str(code or "").strip()
    s = str(stream or "").strip()
    if not k or k in ("nan", "None"):
        return "unknown", "no landscape type"
    if not s or s in ("nan", "None"):
        return "unknown", "no stream type"
    if k not in FLUVIAL_LANDSCAPES:
        return "unknown", f"{k} is not one of the card's types"
    name, _, ok, bad = FLUVIAL_LANDSCAPES[k]
    words = f"{k} {name}: {card_wording(k)}"
    if s in ok and s in bad:
        return "check", (f"the card lists {s} both as natural and under "
                         f"disequilibrium in {words}")
    if s in ok:
        return "stable", f"{s} is natural in {words}"
    if s in bad:
        return "unstable", f"{s} is seen under disequilibrium in {words}"
    return "check", (f"{s} is not listed for {words}: a departure, or one "
                     f"of the two classifications is wrong")


def landscape_form(code: Any, alternatives: Any, stream: Any
                   ) -> tuple[str, str]:
    """``landscape_status``, judged under every candidate when the landscape
    type is unresolved; agreement gives the answer, disagreement "check"."""
    k = str(code or "").strip()
    if k and k not in ("nan", "None"):
        return landscape_status(k, stream)
    cands = [c.strip() for c in str(alternatives or "").replace(",", "|")
             .split("|") if c.strip() in FLUVIAL_LANDSCAPES]
    if not cands:
        return landscape_status(None, stream)
    res = {c: landscape_status(c, stream)[0] for c in cands}
    if len(set(res.values())) == 1:
        st = next(iter(res.values()))
        return st, (f"landscape type unresolved ({'/'.join(cands)}); {st} "
                    f"under each")
    return "check", ("depends on the landscape type, which is unresolved: "
                     + ", ".join(f"{c} -> {v}" for c, v in res.items()))
