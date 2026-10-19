"""A project run, offline: the synthetic landscape as both the 10 m and the
"lidar" DEM, three stand-in HUC12s, and a stand-in lidar index.

Checked: the HUC12s the boundary touches plus the one draining into them;
lidar coverage from tile footprints; level A over the widened area; a level B
pilot only where there is lidar; merged layers carrying the cell size they
were measured on; RESULTS.md; and that a second run carries on instead of
starting again.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

gpd = pytest.importorskip("geopandas")

from shapely.geometry import box  # noqa: E402
from shapely.ops import transform  # noqa: E402
from pyproj import Transformer  # noqa: E402

from geolens import api, project  # noqa: E402
from geolens.safety import Guard  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEM = ROOT / "tests" / "landscape" / "landscape_dem.tif"
UTM = "EPSG:32612"
_W = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True).transform


def _wgs(*b):
    return transform(_W, box(*b))


HUCS = [  # code, name, polygon (UTM), drains to
    ("150100000101", "West Wash", (400000, 4500000, 415000, 4520010), "150100000102"),
    ("150100000102", "East Wash", (415000, 4500000, 430000, 4520010), "150100000199"),
    ("150100000103", "Upper West", (395000, 4500000, 400000, 4520010), "150100000101"),
]


def _features(rows):
    return gpd.GeoDataFrame(
        {"huc12": [r[0] for r in rows], "name": [r[1] for r in rows],
         "areasqkm": [300.0] * len(rows), "tohuc": [r[3] for r in rows],
         "hutype": ["S"] * len(rows), "states": ["AZ"] * len(rows)},
        geometry=[_wgs(*r[2]) for r in rows], crs="EPSG:4326")


@pytest.fixture
def stubs(monkeypatch, tmp_path):
    calls = {"fetch": [], "query": []}

    def query_layer(url, where="1=1", fields="*", bbox=None, **k):
        calls["query"].append(where)
        if bbox is not None:                 # the boundary's envelope
            return _features(HUCS[:2])
        return _features(HUCS)                # whole HUC8s

    def search_1m(bbox, timeout=60):         # lidar over West Wash only
        b = _wgs(400000, 4500000, 415000, 4520010).bounds
        return [{"boundingBox": {"minX": b[0], "minY": b[1],
                                 "maxX": b[2], "maxY": b[3]}}]

    def fetch_dem(aoi, source="best", resolution_m=None, crs=None,
                  buffer_m=0.0):
        out = Path(Guard.from_env().out_dir)
        out.mkdir(parents=True, exist_ok=True)
        dst = out / "dem_mosaic.tif"
        shutil.copyfile(DEM, dst)
        (out / "dem_tiles").mkdir(exist_ok=True)
        calls["fetch"].append(source)
        return {"dem": str(dst), "coverage": 1.0, "lidar_fraction": 1.0,
                "cell_size_m": 30.0, "crs": UTM, "warnings": []}

    monkeypatch.setattr(project, "query_layer", query_layer)
    monkeypatch.setattr("geolens.dem_fetch.search_1m", search_1m)
    monkeypatch.setattr(api, "fetch_dem", fetch_dem)
    monkeypatch.setenv("GEOLENS_PERSIST", "0")
    return calls


def test_a_project_runs_in_two_levels_and_resumes(stubs, tmp_path):
    bnd = tmp_path / "boundary.gpkg"
    gpd.GeoDataFrame({"name": ["field office"]},
                     geometry=[box(405000, 4503000, 425000, 4517000)],
                     crs=UTM).to_file(bnd, driver="GPKG")
    out = tmp_path / "proj"
    res = project.run_project(str(bnd), level_b="pilot", pilot_count=1,
                              stream_threshold_km2=3.0, geology=None,
                              section_plots=False, soils=False,
                              guard=Guard(out_dir=out))

    hucs = gpd.read_file(out / "huc12.gpkg").set_index("huc12")
    assert hucs.loc["150100000103", "role"] == "upstream"     # drains in
    assert hucs.loc["150100000101", "lidar_frac"] == pytest.approx(1.0, abs=0.01)
    assert hucs.loc["150100000102", "lidar_frac"] < 0.05

    status = json.loads((out / "project.json").read_text())
    assert {"boundary", "huc12", "dem_a", "plan_a", "pass_a"} <= set(status["steps"])
    assert res["level_b"] == {"150100000101": "done"}
    assert stubs["fetch"] == ["1/3", "1m"]                     # one level B
    assert not (out / "B_huc12" / "150100000101" / "dem" / "dem_tiles").exists()

    vt = gpd.read_file(res["valley_types"])
    assert len(vt) and (vt["dem_cell_m"] == 30.0).all()
    assert "valley_resolved" in vt.columns and vt["huc12"].notna().any()
    st = gpd.read_file(res["stream_types"])
    assert (st["huc12"] == "150100000101").all()
    assert (st["fine_dem_cell_m"] == 1.0).all()
    lb = gpd.read_file(res["huc12_status"]).set_index("huc12")["level_b"]
    assert lb.to_dict() == {"150100000101": "done", "150100000102": "no lidar",
                            "150100000103": "upstream only"}
    report = (out / "RESULTS.md").read_text()
    assert "arid_sw_az" in report or "bieger_us" in report
    assert "30 m" in report

    # Again: nothing is fetched or recomputed; the merged layers are rebuilt.
    again = project.run_project(str(bnd), level_b="pilot", pilot_count=1,
                                stream_threshold_km2=3.0, geology=None,
                                section_plots=False, soils=False,
                              guard=Guard(out_dir=out))
    assert stubs["fetch"] == ["1/3", "1m"]
    assert again["level_b"] == {"150100000101": "done"}


def test_regional_curve_by_location():
    assert project.choose_curve(-114.0, 35.2)[0] == "arid_sw_az"   # Kingman
    assert project.choose_curve(-105.7, 33.3)[0] == "arid_sw_nm"   # Ruidoso
    assert project.choose_curve(-122.0, 45.0)[0] == "bieger_us"    # Oregon
    assert project.choose_curve(-122.0, 45.0, "arid_sw_nm")[0] == "arid_sw_nm"


def test_paging_stops_when_a_server_ignores_it(monkeypatch):
    page = [{"type": "Feature", "id": i, "properties": {"a": i},
             "geometry": {"type": "Point", "coordinates": [0, 0]}}
            for i in range(3)]
    calls = []

    def get(url, params, timeout):
        calls.append(params["resultOffset"])
        return {"features": page}             # the same page, every time

    monkeypatch.setattr(project, "_get", get)
    g = project.query_layer("https://example.org/MapServer/3", page=3)
    assert len(g) == 3 and calls == [0, 3]
