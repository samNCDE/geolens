"""Rosgen valley-type crosswalk.

Two things are tested separately and should not be confused:

* **the crosswalk table** — given confinement, gradient and material, does it
  return the type Rosgen's descriptions imply? That is testable exactly.
* **the plumbing** — does material attach to segments correctly, do fans become
  Type III, does missing evidence produce an honest "unresolved" rather than a
  confident guess?

What is NOT tested here is whether the crosswalk agrees with a geomorphologist
in the field, because no field-typed valleys exist in this repository. That is
the calibration step, and until it happens these thresholds are an encoding of
descriptive classes, not validated breaks.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
FIX = ROOT / "fans"
OUT = ROOT / "rosgen_test_out"


@pytest.fixture(scope="module", autouse=True)
def _env():
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    if not (FIX / "fan_dem.tif").exists():
        subprocess.run([sys.executable, str(ROOT / "make_fans.py")], check=True)


# -- the crosswalk table ------------------------------------------------------

@pytest.mark.parametrize("confinement,gradient,material,expected", [
    # Confined
    ("confined", 0.15, None, "I"),                    # steep V-notch canyon
    ("confined", 0.03, "glacial_till", "V"),          # U-shaped glacial trough
    ("confined", 0.03, "mass_movement", "II"),        # colluvial
    ("confined", 0.01, "alluvial", "IV"),             # gentle canyon
    # Unconfined
    ("unconfined", 0.03, "ice_marginal", "IX"),       # outwash
    ("unconfined", 0.03, "eolian", "IX"),             # dune sand
    ("unconfined", 0.03, "glacial_till", "V"),        # trough floor
    ("unconfined", 0.01, "glacial_till", "IX"),       # ground moraine, not a trough
    ("unconfined", 0.01, "lacustrine", "X"),
    ("unconfined", 0.002, "coastal", "XI"),           # deltaic
    ("unconfined", 0.06, "alluvial", "VII"),          # dissected alluvial slope
    # Partly confined
    ("partly confined", 0.01, "alluvial", "VIII"),    # terraced floodplain
    ("partly confined", 0.03, "glacial_till", "V"),
])
def test_crosswalk_returns_the_documented_type(confinement, gradient,
                                               material, expected):
    from geolens.landscape.rosgen import classify_valley_type
    r = classify_valley_type(confinement, gradient, material)
    assert r["valley_type"] == expected, r["basis"]


def test_a_detected_fan_is_type_three_regardless(_env):
    """Type III is the one type that is measured rather than inferred."""
    from geolens.landscape.rosgen import classify_valley_type

    for conf in ("confined", "partly confined", "unconfined"):
        for grad in (0.002, 0.03, 0.2):
            r = classify_valley_type(conf, grad, material=None, on_fan=True)
            assert r["valley_type"] == "III"
            # medium since v0.29: the fan search is verified on synthetic
            # fans only.
            assert r["confidence"] == "medium"


def test_missing_material_is_unresolved_not_guessed():
    """The types geometry cannot separate must come back as alternatives.

    Returning a single confident type here would be the failure mode that
    matters: a valley typed from geometry it does not determine.
    """
    from geolens.landscape.rosgen import classify_valley_type

    r = classify_valley_type("confined", 0.03, material=None)
    assert r["valley_type"] is None
    assert set(r["alternatives"]) == {"II", "V", "VI"}
    assert r["confidence"] == "low"

    r = classify_valley_type("unconfined", 0.03, material=None)
    assert r["valley_type"] is None
    assert set(r["alternatives"]) == {"VIII", "IX"}


def test_a_u_shape_on_alluvium_is_not_a_glacial_trough():
    """Kingman (Oct 2026): 1,500 km of 'glacial trough' at 35 N, on alluvium
    and colluvium, because a wash between two concave bajada flanks fits a
    parabola as well as a trough does. A mapped material that is not till
    argues against a trough; with no material the shape still says V, at
    low confidence."""
    from geolens.landscape.rosgen import classify_from_form, classify_valley_type

    u = dict(side_slope=0.15, shape_n=2.0, vf_class_="narrow", relief_m=120.0,
             asymmetry=0.0)
    assert classify_from_form(material="glacial_till", **u)["valley_type"] == "V"
    r = classify_from_form(material=None, **u)
    assert r["valley_type"] == "V" and r["alternatives"] == ["II"]
    assert r["confidence"] == "low"
    assert classify_from_form(material="alluvial", **u) is None   # proxies decide
    r = classify_valley_type("unconfined", 0.03, "alluvial",
                             form=dict(side_slope=0.15, shape_n=2.0,
                                       vf_class="narrow", valley_relief_m=120.0,
                                       asymmetry=0.0))
    assert r["valley_type"] == "VIII"
    r = classify_from_form(material="mass_movement", **u)
    assert r["valley_type"] == "II" and "not a glacial trough" in r["basis"]
    # the same through the proxies, where a moderate U on alluvium used to
    # be V too
    r = classify_valley_type("partly confined", 0.03, "alluvial",
                             form={"shape_n": 1.7})
    assert r["valley_type"] != "V"
    r = classify_valley_type("partly confined", 0.03, None,
                             form={"shape_n": 1.7})
    assert r["valley_type"] == "V" and r["confidence"] == "low"


def test_a_broad_gentle_floor_is_alluvial_whatever_the_hillslope_map_says():
    """The soils are read into a floor narrower than the map unit, so a
    broad gentle valley 'built of colluvium' is the hillslope's unit over
    alluvial fill: VIII at low confidence, not nothing. Lake beds or sand
    would still make it X or IX."""
    from geolens.landscape.rosgen import classify_from_form, classify_valley_type

    b = dict(side_slope=0.03, shape_n=1.4, vf_class_="broad", relief_m=60.0,
             asymmetry=0.0)
    r = classify_from_form(material="mass_movement", **b)
    assert r["valley_type"] == "VIII" and r["confidence"] == "low"
    assert set(r["alternatives"]) == {"IX", "X"} and "hillslope" in r["basis"]
    assert classify_from_form(material="residual", **b)["valley_type"] == "VIII"
    assert classify_from_form(material="lacustrine", **b)["valley_type"] == "X"
    assert classify_from_form(material=None, **b)["valley_type"] is None
    # gentle side slopes that are not measurably broad: the confinement
    # tells a gentle canyon (IV) from an open valley (VIII)
    g = dict(side_slope=0.03, shape_n=1.4, vf_class_="narrow", relief_m=60.0,
             asymmetry=0.0)
    assert classify_from_form(material="residual", confinement="unconfined",
                              **g)["valley_type"] == "VIII"
    assert classify_from_form(material="residual", confinement="confined",
                              **g)["valley_type"] == "IV"
    assert classify_from_form(material="residual", **g)["valley_type"] is None
    # and through the proxies
    r = classify_valley_type("unconfined", 0.03, "mass_movement")
    assert r["valley_type"] == "VIII" and r["confidence"] == "low"
    r = classify_valley_type("unconfined", 0.003, "residual")
    assert r["valley_type"] == "VIII" and set(r["alternatives"]) == {"X", "XI"}
    assert classify_valley_type("unconfined", 0.03, None)["valley_type"] is None


def test_type_six_is_never_asserted():
    """VI is structural control, which no DEM and no geologic map can see.

    It may only ever appear as an alternative.
    """
    from geolens.landscape.rosgen import classify_valley_type

    materials = [None, "alluvial", "glacial_till", "mass_movement",
                 "bedrock_igneous_metamorphic", "bedrock_sedimentary",
                 "lacustrine", "ice_marginal", "residual", "coastal"]
    for conf in ("confined", "partly confined", "unconfined"):
        for grad in (0.001, 0.01, 0.03, 0.06, 0.2):
            for mat in materials:
                r = classify_valley_type(conf, grad, mat)
                assert r["valley_type"] != "VI", (
                    f"VI asserted for {conf}/{grad}/{mat} — it is not "
                    f"identifiable without structural mapping"
                )


def test_every_returned_type_is_a_real_rosgen_type():
    from geolens.landscape.rosgen import classify_valley_type, VALLEY_TYPES

    for conf in ("confined", "partly confined", "unconfined", "unknown"):
        for grad in (None, 0.0, 0.001, 0.01, 0.03, 0.06, 0.5):
            for mat in (None, "alluvial", "glacial_till", "lacustrine",
                        "ice_marginal", "mass_movement", "coastal", "nonsense"):
                r = classify_valley_type(conf, grad, mat)
                if r["valley_type"] is not None:
                    assert r["valley_type"] in VALLEY_TYPES
                for a in r["alternatives"]:
                    assert a in VALLEY_TYPES
                assert r["confidence"] in ("high", "medium", "low")
                assert r["basis"], "every answer must say what decided it"


def test_gradient_classes_are_ordered():
    from geolens.landscape.rosgen import gradient_class

    assert gradient_class(0.001) == "very_gentle"
    assert gradient_class(0.01) == "gentle"
    assert gradient_class(0.03) == "moderate"
    assert gradient_class(0.06) == "steep"
    assert gradient_class(0.2) == "very_steep"
    assert gradient_class(None) == "unknown"
    assert gradient_class(float("nan")) == "unknown"
    # Sign should not matter: a gradient is a magnitude here.
    assert gradient_class(-0.06) == "steep"


# -- material attachment ------------------------------------------------------

def _segments_and_geology(tmp_path):
    import geopandas as gpd
    from shapely.geometry import LineString, box

    seg = gpd.GeoDataFrame(
        {"seg_id": [0, 1],
         "confinement": ["unconfined", "unconfined"],
         "gradient": [0.01, 0.01],
         "valley_width_m": [500.0, 500.0]},
        geometry=[LineString([(0, 0), (1000, 0)]),
                  LineString([(0, 5000), (1000, 5000)])],
        crs="EPSG:32612",
    )
    geo = gpd.GeoDataFrame(
        {"material_class": ["lacustrine", "glacial_till"]},
        geometry=[box(-500, -500, 1500, 500), box(-500, 4500, 1500, 5500)],
        crs="EPSG:32612",
    )
    return seg, geo


def test_material_attaches_by_area(tmp_path):
    from geolens.landscape.rosgen import attach_material

    seg, geo = _segments_and_geology(tmp_path)
    mats, covers = attach_material(seg, geo, buffer_m=200.0)
    assert mats == ["lacustrine", "glacial_till"]
    assert all(c > 0.9 for c in covers)


def test_thin_cover_is_rejected_as_unmapped(tmp_path):
    """A sliver of material must not type a valley.

    The realistic case: a national map whose minimum mapping unit is larger
    than the valley, so the polygon that happens to clip the buffer is not
    describing this valley at all.
    """
    import geopandas as gpd
    from shapely.geometry import box
    from geolens.landscape.rosgen import attach_material

    seg, _ = _segments_and_geology(tmp_path)
    sliver = gpd.GeoDataFrame(
        {"material_class": ["lacustrine"]},
        geometry=[box(-500, 180, 1500, 260)],     # clips the top of the buffer
        crs="EPSG:32612",
    )
    mats, covers = attach_material(seg, sliver, buffer_m=200.0, min_cover=0.25)
    assert mats[0] is None, f"accepted {covers[0]:.0%} cover as a valley type"


def test_geology_without_material_class_is_a_clear_error(tmp_path):
    import geopandas as gpd
    from shapely.geometry import box
    from geolens.landscape.rosgen import attach_material
    from geolens.safety import GeolensError

    seg, _ = _segments_and_geology(tmp_path)
    bad = gpd.GeoDataFrame({"other": ["x"]}, geometry=[box(0, 0, 10, 10)],
                           crs="EPSG:32612")
    with pytest.raises(GeolensError, match="material_class"):
        attach_material(seg, bad)


# -- the whole call -----------------------------------------------------------

def test_run_without_geology_warns_and_stays_honest(tmp_path, _env):
    import geopandas as gpd
    from geolens.landscape.rosgen import rosgen_valley_types

    seg, _ = _segments_and_geology(tmp_path)
    p = tmp_path / "segs.gpkg"
    seg.to_file(str(p), driver="GPKG")

    r = rosgen_valley_types(str(p), geology=None, out_prefix="rt_nogeo").as_dict()
    assert r["geology_used"] is False
    assert any("No geology supplied" in w for w in r["warnings"])
    out = gpd.read_file(r["segments"])
    assert set(out["confidence"]) <= {"low", "medium", "high"}
    assert "basis" in out.columns


def test_run_with_geology_resolves_types(tmp_path, _env):
    import geopandas as gpd
    from geolens.landscape.rosgen import rosgen_valley_types

    seg, geo = _segments_and_geology(tmp_path)
    sp = tmp_path / "segs.gpkg"
    gp = tmp_path / "geo.gpkg"
    seg.to_file(str(sp), driver="GPKG")
    geo.to_file(str(gp), driver="GPKG")

    r = rosgen_valley_types(str(sp), geology=str(gp), out_prefix="rt_geo").as_dict()
    out = gpd.read_file(r["segments"]).sort_values("seg_id")
    assert list(out["valley_type_1996"]) == ["X", "IX"], list(out["valley_type_1996"])
    assert list(out["material_class"]) == ["lacustrine", "glacial_till"]
    # the valley type the layer carries is the 2022 landscape type
    assert list(out["valley_type"]) == ["U-LA-LD", "U-GL-TP"]
    assert r["unresolved"] == 0
    assert r["type_counts"] == {"U-LA-LD": 1, "U-GL-TP": 1}
    assert r["type_counts_1996"] == {"X": 1, "IX": 1}


def test_fan_words_from_the_soils_map():
    from geolens.landscape.rosgen import fan_from_landform

    assert fan_from_landform("fan terraces") == "inactive"
    assert fan_from_landform("fan remnants") == "inactive"
    assert fan_from_landform("fan piedmonts; hills") == "inactive"
    assert fan_from_landform("alluvial fans") == "active"
    assert fan_from_landform("fans") == "active"
    assert fan_from_landform("inset fans, fan aprons") == "active"
    assert fan_from_landform("fan terraces, alluvial fans") == "inactive"
    for not_a_fan in ("mountains", "stream terraces", "pediments",
                      "flood plains", "basin floors", "", None, float("nan")):
        assert fan_from_landform(not_a_fan) is None, not_a_fan


def test_a_reach_on_a_mapped_fan_surface_is_on_a_fan(tmp_path, _env):
    """Kingman: the cone test found 134 fans on a field office whose soils
    map puts a fifth of the ground on fan surfaces. A reach on a map unit
    NRCS calls a fan terrace is Type III and U-AL-IF (a relict fan the
    wash has cut into); on an alluvial fan, U-AL-AF; and fan ground is
    alluvium even where the material step came back empty."""
    import geopandas as gpd
    from shapely.geometry import LineString, box
    from geolens.landscape.rosgen import (attach_material_landform,
                                          rosgen_valley_types)

    seg = gpd.GeoDataFrame(
        {"seg_id": [0, 1, 2], "confinement": ["unconfined"] * 3,
         "gradient": [0.03, 0.03, 0.01], "valley_width_m": [120.0] * 3},
        geometry=[LineString([(0, 0), (1000, 0)]),
                  LineString([(0, 5000), (1000, 5000)]),
                  LineString([(0, 10000), (1000, 10000)])], crs="EPSG:32612")
    soils = gpd.GeoDataFrame(
        {"material_class": ["alluvial", None, "alluvial"],
         "landform": ["fan terraces", "alluvial fans", "flood plains"]},
        geometry=[box(-500, -500, 1500, 500), box(-500, 4500, 1500, 5500),
                  box(-500, 9500, 1500, 10500)], crs="EPSG:32612")
    mats, covers, lfs = attach_material_landform(seg, soils, buffer_m=200.0)
    assert lfs == ["fan terraces", "alluvial fans", "flood plains"]
    assert mats == ["alluvial", None, "alluvial"]

    sp, gp = tmp_path / "segs.gpkg", tmp_path / "soils.gpkg"
    seg.to_file(str(sp), driver="GPKG")
    soils.to_file(str(gp), driver="GPKG")
    r = rosgen_valley_types(str(sp), geology=str(gp), out_prefix="rt_fans")
    out = gpd.read_file(r.segments).sort_values("seg_id")
    assert list(out["valley_type"]) == ["U-AL-IF", "U-AL-AF", "U-AL-AD"]
    assert list(out["valley_type_1996"]) == ["III", "III", "VIII"]
    assert list(out["fan_activity"].fillna("-")) == ["inactive", "active", "-"]
    assert list(out["fan_source"].fillna("-")) == ["soils", "soils", "-"]
    assert list(out["material_class"]) == ["alluvial", "alluvial", "alluvial"]
    assert "fan terraces" in out.iloc[0]["basis"] and "inactive" in out.iloc[0]["basis"]
    assert out.iloc[0]["confidence"] == "medium"
    assert "fan terraces" in out.iloc[0]["basis_1996"]
    assert list(out["alternatives"])[:2] == ["U-AL-AF", "U-AL-IF"]
