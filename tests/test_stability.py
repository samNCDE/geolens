"""Stable or unstable form: stream type against valley type.

The table is NRCS NEH 654 TS3E Table TS3E-3 (after Rosgen 1996); these pin
the transcription and the three-way judgement.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from geolens.landscape.stability import (VALLEY_STREAM_TYPES, _combined,
                                         form_status)


@pytest.mark.parametrize("valley,letter,status", [
    ("I", "A", "stable"), ("II", "B", "stable"), ("IV", "F", "stable"),
    ("VIII", "C", "stable"), ("VIII", "E", "stable"),
    ("VIII", "G", "unstable"), ("VIII", "F", "unstable"),
    ("VIII", "D", "unstable"), ("X", "G", "unstable"), ("VI", "G", "unstable"),
    ("VII", "G", "stable"),       # badlands: gullies ARE the form
    ("III", "D", "stable"),       # fans braid
    ("I", "B", "check"), ("II", "D", "check"), ("VIII", "A", "check"),
    (None, "B", "unknown"), ("II", None, "unknown"), ("XIV", "B", "unknown"),
])
def test_the_table(valley, letter, status):
    assert form_status(valley, letter)[0] == status


def test_every_valley_type_is_in_the_table():
    assert set(VALLEY_STREAM_TYPES) == {"I", "II", "III", "IV", "V", "VI",
                                        "VII", "VIII", "IX", "X", "XI"}


def test_an_unresolved_valley_is_judged_under_every_candidate():
    # stable under both V and IX
    assert _combined(None, "V|IX", "C")[0] == "stable"
    # stable in VIII, unstable in X -> depends: flagged, not guessed
    st, why = _combined(None, "VIII|X", "F")
    assert st == "unstable"        # unstable under both
    st, why = _combined(None, "II|VIII", "B")
    assert st == "check" and "depends on the valley type" in why


def test_the_layer_is_written_with_its_style(tmp_path):
    import geopandas as gpd
    from shapely.geometry import LineString

    from geolens.landscape.stability import stream_stability

    os.environ["GEOLENS_OUT"] = str(tmp_path / "out")
    os.environ["GEOLENS_PERSIST"] = "0"
    line = [LineString([(0, 0), (100, 0)]), LineString([(100, 0), (200, 0)]),
            LineString([(200, 0), (300, 0)])]
    vt = gpd.GeoDataFrame({"seg_id": [0, 1, 2], "valley_type": ["VIII", "VIII", None],
                           "alternatives": ["", "", "II|VI"],
                           "confidence": ["high", "medium", "low"]},
                          geometry=line, crs="EPSG:32613")
    st = gpd.GeoDataFrame({"seg_id": [0, 1, 2], "stream_letter": ["C", "G", "B"],
                           "stream_type": ["C", "G", "Ba"],
                           "stream_confidence": ["medium", "high", "low"]},
                          geometry=line, crs="EPSG:32613")
    (tmp_path / "vt").mkdir()
    (tmp_path / "st").mkdir()
    vt.to_file(tmp_path / "vt" / "valley_types.gpkg", driver="GPKG")
    st.to_file(tmp_path / "st" / "stream_types.gpkg", driver="GPKG",
               layer="stream_types")
    r = stream_stability(tmp_path / "vt" / "valley_types.gpkg",
                         tmp_path / "st" / "stream_types.gpkg")
    g = gpd.read_file(r["stability"])
    assert list(g["form_status"]) == ["stable", "unstable", "stable"]
    assert list(g["form_confidence"]) == ["medium", "medium", "low"]
    assert Path(r["stability"]).with_suffix(".qml").exists()
