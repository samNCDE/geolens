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


# -- an editable association table ---------------------------------------------

def _layers(tmp_path):
    import geopandas as gpd
    from shapely.geometry import LineString

    line = [LineString([(0, 0), (100, 0)]), LineString([(100, 0), (200, 0)]),
            LineString([(200, 0), (300, 0)])]
    vt = gpd.GeoDataFrame({"seg_id": [0, 1, 2],
                           "valley_type": ["VIII", "VIII", None],
                           "alternatives": ["", "", "II|VI"],
                           "confidence": ["high", "medium", "low"]},
                          geometry=line, crs="EPSG:32613")
    st = gpd.GeoDataFrame({"seg_id": [0, 1, 2],
                           "stream_letter": ["C", "G", "B"],
                           "stream_type": ["C", "G", "Ba"],
                           "stream_confidence": ["medium", "high", "low"]},
                          geometry=line, crs="EPSG:32613")
    (tmp_path / "vt").mkdir()
    (tmp_path / "st").mkdir()
    vt.to_file(tmp_path / "vt" / "valley_types.gpkg", driver="GPKG")
    st.to_file(tmp_path / "st" / "stream_types.gpkg", driver="GPKG",
               layer="stream_types")
    return (tmp_path / "vt" / "valley_types.gpkg",
            tmp_path / "st" / "stream_types.gpkg")


def test_the_shipped_csv_is_the_built_in_table():
    from geolens.landscape.stability import read_association_table

    csv = (Path(__file__).resolve().parents[1] / "docs"
           / "valley_stream_associations.csv")
    assert read_association_table(csv) == VALLEY_STREAM_TYPES


@pytest.mark.parametrize("body,msg", [
    ("valley_type,stable,unstable,wording\nII,B Q,,\n", "'Q' is not a Rosgen"),
    ("valley_type,stable,unstable,wording\nII,B,,\nII,C,,\n", "listed twice"),
    ("valley_type,stable,unstable,wording\nVIII,C G,G,\n",
     "both stable and unstable"),
    ("name,letters\nII,B\n", "not an association table"),
])
def test_a_bad_table_says_what_is_wrong(tmp_path, body, msg):
    from geolens.landscape.stability import read_association_table
    from geolens.safety import GeolensError

    p = tmp_path / "t.csv"
    p.write_text(body)
    with pytest.raises(GeolensError, match=msg):
        read_association_table(p)


def test_a_custom_table_is_used_and_kept_with_the_run(tmp_path, monkeypatch):
    import geopandas as gpd

    from geolens.landscape.stability import stream_stability

    monkeypatch.setenv("GEOLENS_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("GEOLENS_PERSIST", "0")
    vt, st = _layers(tmp_path)
    mine = tmp_path / "mine.csv"           # saved from Excel: BOM, extra column
    mine.write_text("valley_type,stable,unstable,wording,notes\n"
                    "VIII,C E G,D F,local reading,arroyos\nII,B,,,\n"
                    "VI,B C F,G,,\n", encoding="utf-8-sig")

    def statuses(r):
        return list(gpd.read_file(r["stability"])["form_status"])

    r = stream_stability(vt, st, association_table=mine)
    assert statuses(r) == ["stable", "stable", "stable"]
    assert "mine.csv" in r["source"]
    kept = tmp_path / "out" / "association_table.csv"
    assert kept.exists() and r["association_table"] == str(kept)
    # a later step passes no table: the run's copy is used
    assert statuses(stream_stability(vt, st)) == ["stable", "stable", "stable"]
    # "builtin" goes back, and forgets the run's copy
    r = stream_stability(vt, st, association_table="builtin")
    assert statuses(r) == ["stable", "unstable", "stable"]
    assert not kept.exists() and r["association_table"] == ""


def test_api_writes_and_checks_a_table(tmp_path):
    from geolens import api

    r = api.association_table(str(tmp_path / "t.csv"), write=True)
    assert r["valley_types"] == len(VALLEY_STREAM_TYPES)
    assert r["rows"]["VIII"]["unstable"] == ["D", "F", "G"]
