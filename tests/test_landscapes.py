"""Rosgen's 2022 fluvial landscape types: the transcription, the crosswalk
from what geolens measures, and the stable / unstable call against the
card's natural and disequilibrium stream types."""
from __future__ import annotations

import re

import pytest

from geolens.landscape import landscapes as L

STREAM = re.compile(r"^(Aa\+|A|Ba|B|Bc|Cb|C|Cc-|DA|D|Eb|E|Fb|F|Gc|G)$")


def test_the_card_is_transcribed_whole():
    codes = set(L.FLUVIAL_LANDSCAPES)
    assert len(codes) == 25                  # 11 unconfined, 14 confined
    assert sum(c.startswith("U-") for c in codes) == 11
    for code, (name, rule, ok, bad) in L.FLUVIAL_LANDSCAPES.items():
        assert re.match(r"^[UC]-(AL|BR|EO|RE|CO|GL|LA|MA|PE)-[A-Z]{2}$", code)
        assert name and ok, code
        assert rule is None or (rule[0] in "<>" and 0 < rule[1] < 10)
        for s in ok + bad:
            assert STREAM.match(s), (code, s)
    assert L.card_wording("U-AL-AD") == "Bc, C, D, E [A, D, F, Gc]"
    assert L.card_wording("C-BR-BC") == "Aa+, A, Ba, B, F, G"
    assert L.FLUVIAL_LANDSCAPES["C-CO-VS"][1] == (">", 6.0)


@pytest.mark.parametrize("conf,grad,mat,fan,shape,want,alts", [
    ("unconfined", 0.01, "alluvial", False, None, "U-AL-AD", []),
    ("unconfined", 0.04, "alluvial", True, None, "U-AL-AF",
     ["U-AL-IF"]),
    ("unconfined", 0.01, "bedrock_sedimentary", False, None, "U-BR-BC", []),
    ("unconfined", 0.01, "playa", False, None, "U-LA-LD", []),
    ("confined", 0.03, "alluvial", False, None, "C-AL-AD", []),
    ("confined", 0.01, "alluvial", False, None, "C-AL-AD", ["C-AL-IG"]),
    ("confined", 0.08, "mass_movement", False, None, "C-CO-VS",
     ["C-CO-US", "C-CO-FD"]),
    ("confined", 0.04, "mass_movement", False, None, "C-CO-US",
     ["C-CO-VS", "C-CO-FD"]),
    ("confined", 0.04, "mass_movement", False, {"shape_n": 1.1}, "C-CO-US",
     ["C-CO-VS", "C-CO-FD"]),
    ("confined", 0.12, "bedrock_igneous_metamorphic", False, None,
     "C-BR-BC", []),
    ("confined", 0.05, "residual", False, None, "C-RE-FD", []),
    ("unconfined", 0.01, "eolian", False, None, None,
     ["U-EO-SD", "U-EO-LP"]),
])
def test_the_crosswalk(conf, grad, mat, fan, shape, want, alts):
    r = L.classify_landscape_type(conf, grad, mat, on_fan=fan, form=shape)
    assert r["landscape_type"] == want, r
    assert r["alternatives"] == alts, r


def test_a_v_shaped_section_overrides_the_slope_for_colluvial_valleys():
    # A V-shaped section ranks C-CO-VS first, but the card's slope rule
    # (more than 6%) still decides between V and U.
    r = L.classify_landscape_type("confined", 0.04, "mass_movement",
                                  form={"shape_n": 1.0})
    assert r["landscape_type"] == "C-CO-US"           # the slope rule fits
    r = L.classify_landscape_type("confined", 0.09, "mass_movement",
                                  form={"shape_n": 2.0})
    assert r["landscape_type"] == "C-CO-VS"           # only VS fits > 6%


def test_what_the_evidence_cannot_say():
    assert L.classify_landscape_type("unknown", 0.01, "alluvial"
                                     )["landscape_type"] is None
    r = L.classify_landscape_type("confined", 0.01, None)
    assert r["landscape_type"] is None and "material" in r["basis"]
    r = L.classify_landscape_type("confined", 0.01, "mass_movement",
                                  form={"shape_n": 2.0})
    assert r["landscape_type"] == "C-CO-US"           # the walls' material
    r = L.classify_landscape_type("partly confined", 0.04, "alluvial")
    assert r["landscape_type"] == "C-AL-AD"          # U-AL-AD wants < 3%
    r = L.classify_landscape_type("unconfined", 0.05, "alluvial")
    assert r["landscape_type"] == "U-AL-IF" and r["confidence"] == "medium"


def test_an_unconfined_floor_is_stream_built():
    """Kingman: 22% of stream length had no landscape type because the soils
    map said colluvium or residuum, or nothing, over an unconfined floor.
    That is the hillslope's unit; the floor is alluvial, at low confidence."""
    r = L.classify_landscape_type("unconfined", 0.01, "residual")
    assert r["landscape_type"] == "U-AL-AD" and r["confidence"] == "low"
    assert "hillslope" in r["basis"] and "residual" in r["basis"]
    r = L.classify_landscape_type("unconfined", 0.04, "mass_movement")
    assert r["landscape_type"] == "U-AL-IF" and r["alternatives"] == ["U-AL-AD"]
    r = L.classify_landscape_type("unconfined", 0.01, None)
    assert r["landscape_type"] == "U-AL-AD" and "no material mapped" in r["basis"]
    # Confined valleys keep the map's material: there it names the walls.
    r = L.classify_landscape_type("confined", 0.08, "mass_movement")
    assert r["landscape_type"] == "C-CO-VS"
    assert L.classify_landscape_type("confined", 0.01, None)["landscape_type"] is None
    # Bedrock and the other origins are not touched.
    r = L.classify_landscape_type("unconfined", 0.01, "bedrock_sedimentary")
    assert r["landscape_type"] == "U-BR-BC" and r["confidence"] == "medium"


def test_partly_confined_leans_unconfined():
    """When both the card's unconfined and confined types fit the slope,
    the floor being wider than the channel picks the unconfined one, with
    the confined one kept as the alternative."""
    r = L.classify_landscape_type("partly confined", 0.01, "alluvial")
    assert r["landscape_type"] == "U-AL-AD" and r["alternatives"] == ["C-AL-AD"]
    assert r["confidence"] == "low" and "wider than the channel" in r["basis"]
    # With colluvium mapped, the unconfined candidate is the floor's (AL) and
    # the confined one the walls' (CO), and the slope decides between them.
    r = L.classify_landscape_type("partly confined", 0.01, "mass_movement")
    assert r["landscape_type"] == "U-AL-AD" and "hillslope" in r["basis"]
    assert r["alternatives"] == ["C-CO-US"]
    r = L.classify_landscape_type("partly confined", 0.09, "mass_movement")
    assert r["landscape_type"] == "C-CO-VS"           # only it fits > 6%
    r = L.classify_landscape_type("partly confined", 0.01, None)
    assert r["landscape_type"] == "U-AL-AD" and r["alternatives"] == []


def test_stream_codes_as_the_card_writes_them():
    assert L.stream_code("Bc") == "Bc"
    assert L.stream_code(None, "A", "a+") == "Aa+"
    assert L.stream_code(None, "C", None) == "C"
    assert L.stream_code(float("nan"), None, None) is None


@pytest.mark.parametrize("code,stream,want", [
    ("U-AL-AD", "C", "stable"),
    ("U-AL-AD", "Gc", "unstable"),
    ("U-AL-AD", "D", "check"),          # listed both ways
    ("U-AL-AD", "B", "check"),          # the card lists Bc, not B
    ("C-CO-US", "Fb", "unstable"),
    ("C-BR-BC", "Aa+", "stable"),
    ("C-BR-BC", "C", "check"),
    (None, "C", "unknown"),
    ("U-AL-AD", None, "unknown"),
])
def test_the_form_call(code, stream, want):
    assert L.landscape_status(code, stream)[0] == want


def test_an_unresolved_type_is_judged_under_each_candidate():
    # A D on a fan: natural on an active fan, disequilibrium on an inactive.
    st, note = L.landscape_form(None, "U-AL-AF|U-AL-IF", "D")
    assert st == "check" and "U-AL-AF -> stable" in note
    st, note = L.landscape_form(None, "U-AL-AF|U-AL-IF", "A")
    assert st == "unstable" and "under each" in note
