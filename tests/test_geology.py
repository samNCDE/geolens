"""Geology fetcher tests.

The network call itself cannot be tested here — the environment this was
written in blocks outbound HTTPS to the USGS services. So everything BELOW the
network boundary is tested against recorded-shape responses, and the live call
is covered by ``geolens.geology.geology_selftest()``, which has to be run
somewhere with internet access. Be honest about which is which: these tests
prove the parsing, joining, clipping and summarising are right. They prove
nothing about whether the service still exists.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "geo_out"


# -- recorded response shapes -------------------------------------------------
#
# Field names and GeoMaterial strings are taken verbatim from the live service
# schema (CNGM National_Earth_Surface_v2, layer 6 and table 8). If the service
# changes its schema these fixtures go stale silently -- that is what the
# selftest is for.

def _poly(mapunit, ring):
    return {
        "type": "Feature",
        "properties": {"MapUnit": mapunit, "Source_MapUnit": f"23|{mapunit}",
                       "IdentityConfidence": "certain", "Label": mapunit},
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


# Two units side by side: alluvium in the west half, granite in the east.
FAKE_POLYGONS = {
    "type": "FeatureCollection",
    "features": [
        _poly("Qa", [[-105.30, 40.30], [-105.20, 40.30],
                     [-105.20, 40.50], [-105.30, 40.50], [-105.30, 40.30]]),
        _poly("Xi", [[-105.20, 40.30], [-105.10, 40.30],
                     [-105.10, 40.50], [-105.20, 40.50], [-105.20, 40.30]]),
    ],
}

FAKE_UNITS = {
    "features": [
        {"attributes": {"MapUnit": "Qa", "Name": "Alluvial sediment", "Age": "Quaternary",
                        "Description": "Sand, gravel and silt of stream channels",
                        "GeoMaterial": "Alluvial sediment",
                        "GeoMaterialConfidence": "high"}},
        {"attributes": {"MapUnit": "Xi", "Name": "Intrusive rock", "Age": "Paleoproterozoic",
                        "Description": "Coarse-grained granitic rock",
                        "GeoMaterial": "Coarse-grained, felsic-composition intrusive igneous rock",
                        "GeoMaterialConfidence": "high"}},
    ]
}


@pytest.fixture(scope="module", autouse=True)
def _out():
    # Wipe first. The cache test asserts on how many times the unit table was
    # fetched, and a units_cache.json left by a previous run makes the first
    # fetch a cache hit -- so the test would pass or fail depending on whether
    # anyone had run it before, which is no test at all.
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"


@pytest.fixture()
def aoi(tmp_path):
    """A small AOI covering the western half only, so clipping is observable."""
    import geopandas as gpd
    from shapely.geometry import box

    p = tmp_path / "aoi.shp"
    gpd.GeoDataFrame(
        {"name": ["test"]},
        geometry=[box(-105.29, 40.35, -105.25, 40.45)],
        crs="EPSG:4326",
    ).to_file(str(p))
    return p


@pytest.fixture()
def no_network(monkeypatch):
    """Replace the HTTP layer with the recorded shapes."""
    from geolens import geology

    calls = []

    def fake(url, params, timeout=60, retries=3):
        calls.append((url, params))
        if "/6/query" in url or "/5/query" in url or "/3/query" in url:
            if params.get("resultOffset", 0):
                return {"type": "FeatureCollection", "features": []}
            return FAKE_POLYGONS
        return FAKE_UNITS

    monkeypatch.setattr(geology, "_get_json", fake)
    return calls


# -- material classification --------------------------------------------------

@pytest.mark.parametrize("geomaterial,expected", [
    ("Alluvial sediment", "alluvial"),
    ("Glacial till", "glacial_till"),
    ("Ice-contact and ice-marginal sediment", "ice_marginal"),
    ("Lacustrine sediment", "lacustrine"),
    ("Debris flows, landslides, and other localized mass-movement sediment",
     "mass_movement"),
    ("Eolian sediment", "eolian"),
    ("Loess", "eolian"),
    ("Dune sand", "eolian"),
    ("Playa", "playa"),
    ("Peat and muck", "organic"),
    ("Residual materials and pre-Quaternary rocks", "residual"),
    ("Pre-Quaternary igneous and metamorphic rock", "bedrock_igneous_metamorphic"),
    ("Coarse-grained, felsic-composition intrusive igneous rock",
     "bedrock_igneous_metamorphic"),
    ("Sandstone and mudstone", "bedrock_sedimentary"),
    ("Mostly carbonate rock", "bedrock_sedimentary"),
    ("Quartzite", "bedrock_igneous_metamorphic"),
])
def test_classify_material(geomaterial, expected):
    from geolens.geology import classify_material
    assert classify_material(geomaterial) == expected


def test_classify_material_unknown_is_none():
    """Unmapped material must return None, never a guessed default.

    A silently wrong class propagates into a valley type; an explicit unknown
    tells the caller to fall back to DEM evidence.
    """
    from geolens.geology import classify_material
    assert classify_material(None) is None
    assert classify_material("") is None
    assert classify_material("some unit nobody has vocabulary for") is None


# -- fetch --------------------------------------------------------------------

def test_fetch_joins_unit_semantics(aoi, no_network):
    import geopandas as gpd
    from geolens.geology import fetch_geology

    r = fetch_geology(str(aoi), buffer_m=0, out_prefix="g1")
    gdf = gpd.read_file(r.polygons)

    assert r.n_polygons > 0
    assert r.n_units == 2
    # The join happened: semantics are on the polygons, not just in the table.
    assert "GeoMaterial" in gdf.columns
    assert "material_class" in gdf.columns
    assert set(gdf["MapUnit"]) <= {"Qa", "Xi"}
    qa = gdf[gdf["MapUnit"] == "Qa"].iloc[0]
    assert qa["GeoMaterial"] == "Alluvial sediment"
    assert qa["material_class"] == "alluvial"


def test_fetch_clips_to_aoi_not_envelope(aoi, no_network):
    """The AOI is entirely inside the western (Qa) polygon.

    A fetch that returned the envelope rather than clipping would include the
    eastern granite too. This is the check that the clip is real.
    """
    import geopandas as gpd
    from geolens.geology import fetch_geology

    r = fetch_geology(str(aoi), buffer_m=0, out_prefix="g2")
    gdf = gpd.read_file(r.polygons)
    assert set(gdf["MapUnit"]) == {"Qa"}, f"clip failed, got {set(gdf['MapUnit'])}"
    assert r.dominant_material == "alluvial"


def test_buffer_pulls_in_the_neighbour(aoi, no_network):
    """A buffer wide enough to cross the contact must pull in the granite.

    The AOI's east edge is -105.25 and the Qa/Xi contact is at -105.20, about
    4.3 km at this latitude -- so 8 km crosses it and 3 km does not. Worth
    stating: an under-sized buffer here would pass the "only Qa" test above for
    the wrong reason.
    """
    import geopandas as gpd
    from geolens.geology import fetch_geology

    r = fetch_geology(str(aoi), buffer_m=8000, out_prefix="g3")
    gdf = gpd.read_file(r.polygons)
    assert set(gdf["MapUnit"]) == {"Qa", "Xi"}
    assert "alluvial" in r.material_area_km2
    assert "bedrock_igneous_metamorphic" in r.material_area_km2


def test_unit_area_ratio_flags_unmapped_valleys(aoi, no_network):
    """One polygon covering the whole AOI must raise the low-confidence warning.

    This is the guard against the real failure mode: a 1:500k source map that
    never resolved the valley floor, returning the hillslope unit and looking
    like a confident answer.
    """
    from geolens.geology import fetch_geology

    r = fetch_geology(str(aoi), buffer_m=0, out_prefix="g4")
    assert r.mapped_fraction > 0.9
    assert any("low confidence" in w for w in r.warnings), r.warnings


def test_units_cache_avoids_a_second_fetch(aoi, no_network):
    from geolens.geology import fetch_geology

    fetch_geology(str(aoi), buffer_m=0, out_prefix="g5")
    n_first = sum(1 for url, _ in no_network if "/8/query" in url)
    fetch_geology(str(aoi), buffer_m=0, out_prefix="g5")
    n_second = sum(1 for url, _ in no_network if "/8/query" in url)
    assert n_first == 1
    assert n_second == 1, "unit table was refetched despite the cache"


def test_outputs_are_written_to_disk(aoi, no_network):
    """Sam's requirement: expensive results land on disk, not just in memory."""
    from geolens.geology import fetch_geology

    r = fetch_geology(str(aoi), buffer_m=0, out_prefix="g6")
    assert Path(r.polygons).exists(), "geology.gpkg not written"
    assert Path(r.units_csv).exists(), "units.csv not written"
    assert (Path(r.polygons).parent / "units_cache_surface.json").exists()


def test_missing_crs_is_a_clear_error(tmp_path, no_network):
    import geopandas as gpd
    from shapely.geometry import box
    from geolens.geology import fetch_geology
    from geolens.safety import GeolensError

    p = tmp_path / "nocrs.shp"
    gpd.GeoDataFrame(geometry=[box(-105.3, 40.3, -105.2, 40.4)]).to_file(str(p))
    prj = p.with_suffix(".prj")
    if prj.exists():
        prj.unlink()   # a shapefile that lost its .prj: the usual way this happens
    with pytest.raises(GeolensError, match="no CRS"):
        fetch_geology(str(p), out_prefix="g7")


def test_unknown_service_is_a_clear_error(aoi, no_network):
    from geolens.geology import fetch_geology
    from geolens.safety import GeolensError

    with pytest.raises(GeolensError, match="Unknown service"):
        fetch_geology(str(aoi), service="not_a_service", out_prefix="g8")


def test_envelope_params_match_the_services_quirks(aoi, no_network):
    """Two ArcGIS quirks that produce wrong-but-successful responses if missed.

    The envelope must go as a bare comma string, and `where` must NOT be sent
    alongside `geometry`.
    """
    from geolens.geology import fetch_geology

    fetch_geology(str(aoi), buffer_m=0, out_prefix="g9")
    poly_calls = [p for url, p in no_network if "/6/query" in url]
    assert poly_calls, "no polygon query was made"
    p = poly_calls[0]
    assert isinstance(p["geometry"], str) and p["geometry"].count(",") == 3
    assert p["geometryType"] == "esriGeometryEnvelope"
    assert "where" not in p, "where= alongside geometry= is rejected by the service"


def test_the_unit_cache_is_per_service(aoi, no_network, monkeypatch):
    """surface and quaternary are different FeatureServers.

    They have different DescriptionOfMapUnits tables and different MapUnit code
    spaces. One shared cache file meant the second call joined against the
    first's lookup table: where a code existed in both, the join succeeded with
    the WRONG name, age and material, and said nothing.
    """
    from pathlib import Path
    from geolens.geology import fetch_geology

    r1 = fetch_geology(aoi, service="surface", out_prefix="cachechk")
    r2 = fetch_geology(aoi, service="quaternary", out_prefix="cachechk")

    d = Path(r1.polygons).parent
    names = sorted(p.name for p in d.glob("units_cache*.json"))
    assert names == ["units_cache_quaternary.json", "units_cache_surface.json"], (
        f"the two services shared a unit cache: {names}"
    )
    assert Path(r2.polygons).exists()
