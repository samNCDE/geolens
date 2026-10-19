"""A project run, offline: the synthetic landscape as both the 10 m and the
"lidar" DEM, three stand-in HUC12s, and a stand-in lidar index.

Checked: the HUC12s the boundary touches plus the one draining into them;
lidar coverage from tile footprints; level A over the widened area; a level B
pilot only where there is lidar; merged layers carrying the cell size they
were measured on; RESULTS.md; and that a second run carries on instead of
starting again. Then the pilot itself: HUC12s grouped by valley types and
soils, each group's most typical HUC12 with lidar standing for it.
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
    # The pilot stands for the whole boundary, the HUC12 without lidar too.
    assert status["pilot"]["by"] == "representative"
    assert "## Pilot HUC12s" in report and "| 150100000101 |" in report
    group = gpd.read_file(res["huc12_status"]).set_index("huc12")["pilot_group"]
    assert group["150100000101"] == group["150100000102"] == "150100000101"
    assert group.isna()["150100000103"]                        # upstream only

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


def test_a_bad_pilot_choice_stops_before_any_work(tmp_path):
    with pytest.raises(project.GeolensError, match="pilot_by"):
        project.run_project("unused.gpkg", pilot_by="widest",
                            guard=Guard(out_dir=tmp_path / "p"))
    assert not (tmp_path / "p" / "project.json").exists()


def test_medoids_are_the_middle_of_each_group():
    import numpy as np

    pts = np.array([[0, 0], [0.1, 0], [0.2, 0],         # a group, middle 1
                    [5, 5], [5.1, 5], [5.2, 5],          # another, middle 4
                    [20, 20]])                            # a sliver far off
    w = np.array([1, 1, 1, 1, 1, 1, 0.01])
    D = np.sqrt(((pts[:, None] - pts[None]) ** 2).sum(-1))
    med, lab = project._medoids(D, w, 2)
    assert sorted(med) == [1, 4]
    assert len(set(lab[:3])) == len(set(lab[3:6])) == 1 and lab[0] != lab[3]
    allowed = np.array([1, 0, 1, 1, 1, 1, 1], bool)     # 1 has no lidar
    med, _ = project._medoids(D, w, 2, allowed)
    assert 1 not in med and len(med) == 2 and 4 in med
    assert project._medoids(D, w, 9, allowed)[0].__len__() == 6


# Six HUC12s in a row, 10 km squares: three canyons (valley types I and II on
# hydrologic group D soils, K 0.40) and three basins (valley type VIII, some
# IX, on group B soils, K 0.15). In each, the middle one is the typical one.
# A seventh, a sliver of a HUC12 mostly outside the boundary, looks like a
# basin.
_SHAPES = [  # code, valley-type shares, soil group, K, share inside
    ("150100000201", {"I": 0.8, "II": 0.2}, "D", 0.40, 1.0),
    ("150100000202", {"I": 0.6, "II": 0.4}, "D", 0.40, 1.0),
    ("150100000203", {"I": 0.4, "II": 0.6}, "D", 0.40, 1.0),
    ("150100000204", {"VIII": 1.0}, "B", 0.15, 1.0),
    ("150100000205", {"VIII": 0.9, "IX": 0.1}, "B", 0.15, 1.0),
    ("150100000206", {"VIII": 0.8, "IX": 0.2}, "B", 0.15, 1.0),
    ("150100000207", {"VIII": 0.9, "IX": 0.1}, "B", 0.15, 0.1),
]


def _shapes():
    from shapely.geometry import LineString

    hucs, lines, soil = [], [], []
    for i, (code, shares, hsg, k, inside) in enumerate(_SHAPES):
        x0, y0 = 400000 + 10000 * i, 4500000
        sq = box(x0, y0, x0 + 10000, y0 + 10000)
        hucs.append({"huc12": code, "name": f"HUC {code[-2:]}",
                     "areasqkm": 100.0, "in_boundary": inside,
                     "geometry": transform(_W, sq)})
        y = y0 + 1000
        for vt, share in shares.items():          # 5 km of channel, split
            lines.append({"valley_type": vt, "geometry": LineString(
                [(x0 + 2000, y), (x0 + 2000 + 5000 * share, y)])})
            y += 1000
        soil.append({"hydgrpdcd": hsg, "kwfact": k, "geometry": sq})
    lines.append({"valley_type": None, "geometry": LineString(   # unclassed
        [(400500, 4509000), (400600, 4509000)])})
    return (gpd.GeoDataFrame(hucs, crs="EPSG:4326"),
            gpd.GeoDataFrame(lines, crs=UTM), gpd.GeoDataFrame(soil, crs=UTM))


def test_a_representative_pilot_stands_for_the_whole_area():
    hucs, vt, soils = _shapes()
    order, plan = project._representative_pilot(hucs, 2, valley_types=vt,
                                                soils=soils)
    # One typical canyon, one typical basin; the basins (with the sliver)
    # are the larger group, so theirs runs first.
    assert order[:2] == ["150100000205", "150100000202"]
    assert len(order) == 6                       # the sliver never runs
    assert plan["by"] == "representative"
    assert plan["basis"] == ["stream length by valley type", "area by soil "
                             "hydrologic group and soil erodibility (K)"]
    g = {r["huc12"]: r for r in plan["groups"]}
    assert sorted(g["150100000202"]["members"]) == [c for c, *_ in _SHAPES[:3]]
    assert plan["group_of"]["150100000207"] == "150100000205"
    assert g["150100000205"]["km2"] == pytest.approx(310.0)
    assert sum(r["share"] for r in plan["groups"]) == pytest.approx(1.0, abs=0.01)
    lines = project._pilot_lines(plan)
    assert "2 groups" in lines[2]
    assert lines[6].startswith("| 150100000205 | HUC 05 | 4 HUC12s | 51% (310 km")


def test_without_lidar_the_next_most_typical_stands_in():
    hucs, vt, soils = _shapes()
    runnable = [c for c, *_ in _SHAPES if c != "150100000202"]
    order, plan = project._representative_pilot(hucs, 2, can_run=runnable,
                                                valley_types=vt, soils=soils)
    assert "150100000202" not in order
    assert order[1] in ("150100000201", "150100000203")
    assert plan["group_of"]["150100000202"] == order[1]  # still in its group


def test_the_pilot_falls_back_to_confinement_then_area():
    hucs, vt, _ = _shapes()
    reaches = vt.rename(columns={"valley_type": "confinement"})
    reaches["confinement"] = ["confined"] * 6 + ["unconfined"] * 7 + [None]
    order, plan = project._representative_pilot(hucs, 2, reaches=reaches)
    assert plan["basis"] == ["stream length by confinement"]
    canyons = {c for c, *_ in _SHAPES[:3]}
    assert len(set(order[:2]) & canyons) == 1     # one canyon, one basin
    order, plan = project._representative_pilot(hucs, 1)
    assert plan["basis"] == ["area only"] and len(order) == 6


def test_soil_shares_by_hydrologic_group():
    hucs, _, soils = _shapes()
    soils.loc[0, "hydgrpdcd"] = "C/D"            # dual group: undrained, D
    soils.loc[1, "hydgrpdcd"] = None
    t = project._soil_shares(soils, hucs)
    assert t.loc["150100000201", "soil_hsg_D"] == pytest.approx(1.0)
    assert t.loc["150100000202", "soil_hsg_unknown"] == pytest.approx(1.0)
    assert t.loc["150100000204", "soil_hsg_B"] == pytest.approx(1.0)
    assert t.loc["150100000204", "soil_k"] == pytest.approx(0.30)   # 0.15 / 0.5
