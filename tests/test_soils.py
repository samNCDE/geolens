"""SSURGO soils from Soil Data Access, offline.

The service is stood in for by a function answering the same SQL with
canned rows: a valley floor of alluvium between residuum on one side and
slope alluvium on the other. Checked: parent material to material class,
tiling and the split of a tile the service cannot answer, duplicate
polygons across tiles, the attribute joins, clipping, and that the layer
works as valley typing's geology.
"""
from __future__ import annotations

import json
import re

import pytest

gpd = pytest.importorskip("geopandas")

from pyproj import Transformer  # noqa: E402
from shapely.geometry import LineString, box  # noqa: E402
from shapely.ops import transform  # noqa: E402

from geolens import soils  # noqa: E402
from geolens.safety import Guard, GeolensError  # noqa: E402

CRS = "EPSG:32613"
AOI = (430000.0, 3690000.0, 433000.0, 3693000.0)
_TO_WGS = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform


def _wkt(*b):
    return transform(_TO_WGS, box(*b)).wkt


# key, mukey, polygon (UTM, wider than the area)
POLYS = [(1, 100, (431300, 3689500, 431700, 3693500)),      # valley floor
         (2, 200, (429500, 3689500, 431300, 3693500)),      # west slopes
         (3, 300, (431700, 3689500, 433500, 3693500))]      # east slopes
UNITS = {100: ("Gd", "Glendale loam, 0 to 2 percent slopes", "Occasional"),
         200: ("RoA", "Rock outcrop-Lithic complex", "None"),
         300: ("Mf", "Manzano-Lithic complex", "None")}
COMPONENTS = [(100, 1001, "Glendale", "85"), (100, 1002, "Minor", "15"),
              (200, 2001, "Rock outcrop", "60"), (300, 3001, "Manzano", "70")]
PM = [(1001, "alluvium", "1", "Yes"), (2001, "residuum", "1", "Yes"),
      (3001, "slope alluvium", "1", "Yes"), (3001, "residuum", "2", "Yes")]
LANDFORM = [(1001, "flood plains"), (2001, "hills"), (3001, "hillslopes")]


def _table(cols, rows):
    return [dict(zip(cols, r)) for r in rows]


def fake_sda(log):
    def ask(q):
        log.append(q)
        keys = {int(k) for k in re.findall(r"\d+", q.split(" IN (")[-1])} \
            if " IN (" in q else set()
        if "FROM mupolygon" in q:
            return _table(["mupolygonkey", "mukey", "wkt"],
                          [(str(k), str(m), _wkt(*b)) for k, m, b in POLYS])
        if "FROM mapunit" in q:
            return _table(["mukey", "musym", "muname", "flodfreqdcd",
                           "drclassdcd", "hydgrpdcd", "brockdepmin"],
                          [(str(m), *UNITS[m], "Well drained", "B", "152")
                           for m in UNITS if m in keys])
        if "FROM component" in q:
            return _table(["mukey", "cokey", "compname", "comppct_r"],
                          [(str(m), str(c), n, p) for m, c, n, p in COMPONENTS
                           if m in keys])
        if "FROM copmgrp" in q:
            return _table(["cokey", "pmgroupname", "rvindicator", "pmkind",
                           "pmorder"],
                          [(str(c), k, rv, k, o) for c, k, o, rv in PM
                           if c in keys])
        if "FROM cogeomordesc" in q:
            return _table(["cokey", "geomfname", "rvindicator"],
                          [(str(c), f, "Yes") for c, f in LANDFORM
                           if c in keys])
        raise AssertionError(q)
    return ask


def _aoi(tmp_path):
    p = tmp_path / "aoi.gpkg"
    gpd.GeoDataFrame(geometry=[box(*AOI)], crs=CRS).to_file(p, driver="GPKG")
    return str(p)


@pytest.mark.parametrize("pm,cls", [
    ("alluvium", "alluvial"), ("slope alluvium", "mass_movement"),
    ("colluvium", "mass_movement"), ("residuum", "residual"),
    ("eolian sands", "eolian"), ("loess", "eolian"),
    ("lacustrine deposits", "lacustrine"), ("volcanic ash", None),
    (None, None)])
def test_parent_material_to_material_class(pm, cls):
    assert soils.soil_material(pm) == cls


def test_the_service_answer_is_read():
    assert soils._rows('{"Table": [["a", "b"], ["1", null]]}') == \
        [{"a": "1", "b": None}]
    assert soils._rows("{}") == [] and soils._rows("") == []


def test_soils_are_fetched_clipped_and_classed(tmp_path, monkeypatch):
    log = []
    monkeypatch.setattr(soils, "TILE_DEG", 0.015)         # several tiles
    res = soils.fetch_soils(_aoi(tmp_path), ask=fake_sda(log),
                            guard=Guard(out_dir=tmp_path / "out"))
    g = gpd.read_file(res["soils"], layer="soils")
    assert g.crs.to_epsg() == 32613
    assert sorted(g["mupolygonkey"]) == [1, 2, 3]          # once each
    by_key = g.set_index("mukey")
    assert by_key.loc[100, "material_class"] == "alluvial"
    assert by_key.loc[100, "compname"] == "Glendale"        # the dominant one
    assert by_key.loc[100, "landform"] == "flood plains"
    assert by_key.loc[100, "flodfreqdcd"] == "Occasional"
    assert by_key.loc[200, "material_class"] == "residual"
    assert by_key.loc[300, "material_class"] == "mass_movement"
    assert by_key.loc[300, "pmkind"] == "slope alluvium"     # pmorder 1
    assert by_key.loc[100, "brockdepmin"] == 152
    assert g.unary_union.within(box(*AOI).buffer(1))        # clipped
    assert sum(g["area_km2"]) == pytest.approx(9.0, rel=0.01)
    rec = json.loads((tmp_path / "out" / "soils_dir" /
                      "soils_sources.json").read_text())
    assert rec["polygons"] == 3 and rec["tiles"] > 1
    assert rec["km2_by_material"]["alluvial"] == pytest.approx(1.2, rel=0.02)


def test_a_tile_the_service_cannot_answer_is_split(tmp_path):
    log, calls = [], []
    inner = fake_sda(log)

    def ask(q):
        calls.append(q)
        if "FROM mupolygon" in q and len(calls) == 1:
            raise GeolensError("HTTP 500: response too large")
        return inner(q)

    rows, asked = soils._tile_polygons((-105.8, 33.3, -105.7, 33.4), ask)
    assert asked == 4 and len(rows) == 12


def test_no_network_is_not_split_into_more_requests():
    calls = []

    def ask(q):
        calls.append(q)
        raise soils.SDAUnreachable("Tunnel connection failed: 403")

    with pytest.raises(soils.SDAUnreachable):
        soils._tile_polygons((-105.8, 33.3, -105.7, 33.4), ask)
    assert len(calls) == 1


def test_the_soils_layer_works_as_geology(tmp_path, monkeypatch):
    from geolens.landscape.rosgen import attach_material

    res = soils.fetch_soils(_aoi(tmp_path), ask=fake_sda([]),
                            guard=Guard(out_dir=tmp_path / "out"))
    seg = gpd.GeoDataFrame(geometry=[LineString([(431500, 3690500),
                                                 (431500, 3692500)])],
                           crs=CRS)
    materials, covers = attach_material(seg, gpd.read_file(res["soils"]),
                                        buffer_m=150.0)
    assert materials == ["alluvial"] and covers[0] > 0.5
