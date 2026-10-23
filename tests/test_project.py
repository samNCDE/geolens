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
from geolens.safety import GeolensError, Guard  # noqa: E402

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
        calls.setdefault("res", []).append(resolution_m)
        return {"dem": str(dst), "coverage": 1.0, "lidar_fraction": 1.0,
                "cell_size_m": 30.0, "crs": UTM, "warnings": []}

    def data_step(step, root, inside, st, timeout):
        # The watershed data package, without the internet: one column each.
        import pandas as pd

        calls.setdefault("data", []).append(step)
        ddir = root / "data"
        ddir.mkdir(exist_ok=True)
        csv = ddir / f"{step}_huc12.csv"
        pd.DataFrame({"huc12": list(inside["huc12"]),
                      f"{step}_value": [1.0] * len(inside)}).to_csv(csv, index=False)
        return {"table": str(csv), "columns": [f"{step}_value"],
                "perimeters": 2, "map_units": 3, "with_site": 2,
                "first_year": 1986, "last_year": 2025, "missing_years": [],
                "failed": 0}

    monkeypatch.setattr(project, "query_layer", query_layer)
    monkeypatch.setattr("geolens.dem_fetch.search_1m", search_1m)
    monkeypatch.setattr(api, "fetch_dem", fetch_dem)
    monkeypatch.setattr(project, "_data_step", data_step)
    monkeypatch.setattr("geolens.wsdata.nhd_flowlines",
                        lambda *a, **k: {"path": None, "flowlines": 0,
                                         "named": 0})
    monkeypatch.setenv("GEOLENS_PERSIST", "0")
    # the container's disk is small; the lidar pass's disk guard is tested
    # on its own
    monkeypatch.setattr(project, "_free_gb", lambda p: 1000.0)
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
    assert "| over 20% | 2.66 | 6.64 |" in report               # trust in 10 m
    # The pilot stands for the whole boundary, the HUC12 without lidar too.
    assert status["pilot"]["by"] == "representative"
    assert "## Pilot HUC12s" in report and "| 150100000101 |" in report
    group = gpd.read_file(res["huc12_status"]).set_index("huc12")["pilot_group"]
    assert group["150100000101"] == group["150100000102"] == "150100000101"
    assert group.isna()["150100000103"]                        # upstream only

    # The watershed data package ran once per step, for the HUC12s inside
    # the boundary, and everything met in the HUC12 summary.
    assert stubs["data"] == ["fire", "ecosites", "prism", "atlas14", "rap"]
    assert {"data_fire", "data_rap"} <= set(status["steps"])
    summary = gpd.read_file(res["huc12_summary"]).set_index("huc12")
    assert set(summary.index) == {"150100000101", "150100000102"}
    assert summary.loc["150100000101", "pilot_group"] == "150100000101"
    assert summary["stream_km"].gt(0).all()
    assert {"fire_value", "rap_value", "prism_value", "slope_mean_pct",
            "valley_types_2022", "valley_types_1996"} <= set(summary.columns)
    assert any(summary.loc["150100000101", f"form_{k}_km"] > 0
               for k in ("stable", "unstable", "check", "unknown")
               if f"form_{k}_km" in summary.columns)
    assert any(c.startswith("form1996_") for c in summary.columns)
    assert (out / "merged" / "huc12_summary.csv").exists()
    assert "## Watershed data by HUC12" in report
    assert "| fire history | NIFC InterAgency Fire Perimeter History | 2 fire perimeters |" in report
    assert "| vegetation cover | Rangeland Analysis Platform cover v3, 30 m | 1986 to 2025 |" in report

    # The last step: stream power and a stream id on every reach, the major
    # streams, and the sensitivity table -- which two HUC12s cannot have,
    # and the report says so instead of failing.
    assert res.get("major_streams") and Path(res["major_streams"]).exists()
    assert "## Stream power, major streams and the sensitivity table" in report
    assert "fewer than 3" in report
    vt_again = gpd.read_file(res["valley_types"])
    assert {"stream_id", "dist_to_mouth_km", "q_bankfull_m3s",
            "unit_stream_power_w_m2"} <= set(vt_again.columns)
    assert vt_again["stream_id"].ge(1).all()
    assert status["synthesis"]["tracing"]["outlets"] >= 1

    # Again: nothing is fetched or recomputed; the merged layers are rebuilt.
    again = project.run_project(str(bnd), level_b="pilot", pilot_count=1,
                                stream_threshold_km2=3.0, geology=None,
                                section_plots=False, soils=False,
                              guard=Guard(out_dir=out))
    assert stubs["fetch"] == ["1/3", "1m"]
    assert len(stubs["data"]) == 5                             # not again
    assert again["level_b"] == {"150100000101": "done"}
    assert again["huc12_summary"] == res["huc12_summary"]


def test_a_project_typed_under_an_older_revision_is_retyped(stubs, tmp_path,
                                                             monkeypatch):
    """Kingman was typed under revision 1 (1,500 km of glacial trough on
    alluvium). Running the project again under a newer TYPING redoes the
    valley types from the saved valley form and fans, nothing is routed
    again, and the level B form of the HUC12s already done is judged again
    against the new types."""
    bnd = tmp_path / "boundary.gpkg"
    gpd.GeoDataFrame({"name": ["field office"]},
                     geometry=[box(405000, 4503000, 425000, 4517000)],
                     crs=UTM).to_file(bnd, driver="GPKG")
    out = tmp_path / "proj"
    kw = dict(level_b="pilot", pilot_count=1, stream_threshold_km2=3.0,
              geology=None, section_plots=False, soils=False, data="none")
    res = project.run_project(str(bnd), guard=Guard(out_dir=out), **kw)
    status = json.loads((out / "project.json").read_text())
    assert status["steps"]["pass_a"]["typing"] == project.TYPING
    first_stab = gpd.read_file(res["stability"])
    first_vt = Path(status["steps"]["pass_a"]["valley_types"])
    stamp = first_vt.stat().st_mtime_ns

    # As if typed by an older release.
    status["steps"]["pass_a"]["typing"] = 1
    (out / "project.json").write_text(json.dumps(status))
    calls = []
    real_pass = api.landscape_pass
    monkeypatch.setattr(api, "landscape_pass",
                        lambda *a, **k: calls.append("pass") or real_pass(*a, **k))
    judged = []
    from geolens.landscape import stability as stab_mod
    real_judge = stab_mod.stream_stability

    def judge(*a, **k):
        judged.append(a[1])
        return real_judge(*a, **k)

    monkeypatch.setattr(stab_mod, "stream_stability", judge)
    res2 = project.run_project(str(bnd), guard=Guard(out_dir=out), **kw)
    status = json.loads((out / "project.json").read_text())
    assert calls == [] and stubs["fetch"] == ["1/3", "1m"]       # no rerun
    assert status["steps"]["pass_a"]["typing"] == project.TYPING
    assert Path(status["steps"]["pass_a"]["valley_types"]).stat().st_mtime_ns > stamp
    assert judged == [status["huc12"]["150100000101"]["stream_types"]]
    assert res2["level_b"] == {"150100000101": "done"}
    again = gpd.read_file(res2["stability"])
    assert len(again) == len(first_stab)
    assert set(again["form_status"]) <= {"stable", "unstable", "check", "unknown"}
    assert not any("could not be redone" in w for w in res2["warnings"])


def test_a_failed_data_step_is_a_warning_and_is_retried(stubs, tmp_path,
                                                         monkeypatch):
    """A watershed data source that does not answer is a warning, not the
    end of the run; the next run tries it again, and the warning goes once
    it works. "data" picks the steps, and a typo fails before any work."""
    real = project._data_step
    broken = {"prism"}

    def data_step(step, *a, **k):
        if step in broken:
            raise RuntimeError("PRISM did not answer")
        return real(step, *a, **k)

    monkeypatch.setattr(project, "_data_step", data_step)
    bnd = tmp_path / "boundary.gpkg"
    gpd.GeoDataFrame({"name": ["ranch"]},
                     geometry=[box(405000, 4503000, 425000, 4517000)],
                     crs=UTM).to_file(bnd, driver="GPKG")
    out = tmp_path / "proj"
    with pytest.raises(Exception, match="Unknown watershed data"):
        project.run_project(str(bnd), level_b="none", geology=None,
                            soils=False, data="fire,prsm",
                            guard=Guard(out_dir=out))
    assert not (out / "project.json").exists()
    res = project.run_project(str(bnd), level_b="none", geology=None,
                              stream_threshold_km2=3.0, soils=False,
                              section_plots=False, data="fire, prism",
                              guard=Guard(out_dir=out))
    assert stubs["data"] == ["fire"]
    assert any("climate normals: RuntimeError: PRISM" in w for w in res["warnings"])
    summary = gpd.read_file(res["huc12_summary"])
    assert "fire_value" in summary.columns and "prism_value" not in summary.columns
    report = (out / "RESULTS.md").read_text()
    assert "| climate normals | PRISM 1991-2020 normals, 800 m | not fetched" in report
    assert "| fire history |" in report and "| vegetation cover |" not in report

    broken.clear()
    res = project.run_project(str(bnd), level_b="none", geology=None,
                              stream_threshold_km2=3.0, soils=False,
                              section_plots=False, data=["prism", "fire"],
                              guard=Guard(out_dir=out))
    assert stubs["data"] == ["fire", "prism"]                  # fire kept
    assert not any("PRISM" in w for w in res["warnings"])
    assert "prism_value" in gpd.read_file(res["huc12_summary"]).columns

    assert project.data_steps("none") == [] and project.data_steps(None) == []
    assert project.data_steps("all") == list(project.data_steps(True))
    assert project.data_steps("rap, fire") == ["fire", "rap"]


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

    def get(url, params, timeout, tries=3):
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


def _points(n):
    return [{"type": "Feature", "id": i, "properties": {"a": i},
             "geometry": {"type": "Point", "coordinates": [0, 0]}}
            for i in range(n)]


URL = "https://example.org/arcgis/rest/services/wbd/MapServer/6"


def test_a_slow_server_is_asked_for_smaller_pages(monkeypatch):
    """The USGS hydrography services time out on big pages."""
    feats, calls = _points(300), []

    def get(url, params, timeout, tries=3):
        n, off = params["resultRecordCount"], params["resultOffset"]
        calls.append(n)
        if n > 125:
            raise project.GeolensError("timed out")
        return {"features": feats[off:off + n]}

    monkeypatch.setattr(project, "_get", get)
    assert len(project.query_layer(URL)) == 300
    assert calls == [500, 250, 125, 125, 125]


def test_an_error_reply_and_a_server_page_limit(monkeypatch):
    """ArcGIS reports a query it gave up on as {"error": ...} with HTTP 200,
    and caps pages at its own limit, saying there is more."""
    feats, calls = _points(250), []

    def get(url, params, timeout, tries=3):
        n, off = params["resultRecordCount"], params["resultOffset"]
        calls.append((n, off))
        if n > 250:
            return {"error": {"code": 500,
                              "message": "Error performing query operation"}}
        part = feats[off:off + min(n, 100)]          # the server's own cap
        return {"features": part,
                "exceededTransferLimit": off + len(part) < len(feats)}

    monkeypatch.setattr(project, "_get", get)
    assert len(project.query_layer(URL)) == 250
    assert calls == [(500, 0), (250, 0), (250, 100), (250, 200)]


def test_a_server_that_never_answers_is_an_error(monkeypatch):
    calls = []

    def get(url, params, timeout, tries=3):
        calls.append((params["resultRecordCount"], tries))
        raise project.GeolensError("down")

    monkeypatch.setattr(project, "_get", get)
    with pytest.raises(project.GeolensError, match="down"):
        project.query_layer(URL)
    assert calls == [(500, 1), (250, 1), (125, 1), (62, 1), (50, 3)]


def test_ssurgo_types_the_valleys(stubs, tmp_path, monkeypatch):
    """geology="ssurgo" (the default): the boundary's soils are fetched
    first and the level A valleys are typed by them, Rosgen's 2022 landscape
    types included."""
    asked = []

    def fetch_soils(aoi, guard=None, max_area_km2=None, **k):
        asked.append(aoi)
        out = Path(guard.out_dir) / "soils_dir"
        out.mkdir(parents=True, exist_ok=True)
        p = out / "soils.gpkg"
        gpd.GeoDataFrame(
            {"mukey": [1], "material_class": ["alluvial"],
             "pmkind": ["alluvium"], "hydgrpdcd": ["B"], "kwfact": [0.2]},
            geometry=[box(380000, 4480000, 450000, 4540000)],
            crs=UTM).to_file(p, driver="GPKG", layer="soils")
        return {"soils": str(p), "polygons": 1,
                "km2_by_material": {"alluvial": 4200.0}}

    monkeypatch.setattr("geolens.soils.fetch_soils", fetch_soils)
    bnd = tmp_path / "boundary.gpkg"
    gpd.GeoDataFrame({"name": ["ranch"]},
                     geometry=[box(405000, 4503000, 425000, 4517000)],
                     crs=UTM).to_file(bnd, driver="GPKG")
    out = tmp_path / "proj"
    res = project.run_project(str(bnd), level_b="none", soils=False,
                              stream_threshold_km2=3.0,
                              section_plots=False, guard=Guard(out_dir=out))
    status = json.loads((out / "project.json").read_text())
    assert len(asked) == 1                          # needed for the valleys
    assert status["steps"]["pass_a"]["geology"] == status["steps"]["soils"]["path"]
    vt = gpd.read_file(res["valley_types"])
    assert (vt["material_class"] == "alluvial").any()
    assert vt["landscape_type"].str.startswith(("U-AL-", "C-AL-")).any()
    assert {"landscape_alternatives", "landscape_basis"} <= set(vt.columns)


def test_a_failed_pilot_choice_does_not_stop_the_run(stubs, tmp_path,
                                                     monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("no shapely.contains_xy here")

    monkeypatch.setattr(project, "_representative_pilot", broken)
    bnd = tmp_path / "boundary.gpkg"
    gpd.GeoDataFrame({"name": ["field office"]},
                     geometry=[box(405000, 4503000, 425000, 4517000)],
                     crs=UTM).to_file(bnd, driver="GPKG")
    res = project.run_project(str(bnd), level_b="pilot", pilot_count=1,
                              stream_threshold_km2=3.0, geology=None,
                              section_plots=False, soils=False,
                              guard=Guard(out_dir=tmp_path / "p"))
    assert res["level_b"] == {"150100000101": "done"}
    assert any("could not be chosen" in w for w in res["warnings"])


def test_the_project_command(tmp_path, monkeypatch):
    """`geolens project boundary.gpkg`: the folder beside the boundary, and
    the landscape defaults (SSURGO material, a representative pilot)."""
    import os

    from geolens import cli

    seen = {}

    def run_project(boundary, **kw):
        seen.update(kw, boundary=boundary, out=os.environ["GEOLENS_OUT"])
        return {"project": seen["out"], "level_b": {}, "warnings": [],
                "seconds": 1.0}

    monkeypatch.setattr(api, "run_project", run_project)
    monkeypatch.delenv("GEOLENS_OUT", raising=False)
    bnd = tmp_path / "ranch.gpkg"
    bnd.write_text("")
    assert cli.main(["project", str(bnd)]) == 0
    assert Path(seen["out"]) == tmp_path / "ranch_geolens"
    assert (seen["geology"], seen["level_b"], seen["pilot_by"],
            seen["pilot_count"], seen["soils"]) == (
        "ssurgo", "pilot", "representative", 3, True)
    url = "https://gis.blm.gov/arcgis/rest/services/x/MapServer/3"
    assert cli.main(["project", url, "--where", "ADM_UNIT_CD='AZC01000'",
                     "--out", str(tmp_path / "kingman"), "--geology", "none",
                     "--pilot-by", "unconfined"]) == 0
    assert seen["boundary"] == url and seen["where"] == "ADM_UNIT_CD='AZC01000'"
    assert Path(seen["out"]) == tmp_path / "kingman"
    assert seen["geology"] is None and seen["pilot_by"] == "unconfined"


def test_a_line_boundary_is_buffered_into_a_polygon(tmp_path):
    from shapely.geometry import LineString

    src = tmp_path / "wash.gpkg"
    gpd.GeoDataFrame(geometry=[LineString([(410000, 4505000),
                                           (412000, 4506000)])],
                     crs=UTM).to_file(src, driver="GPKG")
    out = project.fetch_boundary(str(src), guard=Guard(out_dir=tmp_path / "o"))
    g = gpd.read_file(out)
    assert list(g.geom_type) == ["Polygon"]
    length = 2236.07                                   # metres of line
    assert g.to_crs(UTM).area.sum() == pytest.approx(
        2 * 250 * length + 3.14159 * 250 ** 2, rel=0.02)


def test_running_out_of_memory_coarsens_and_carries_on(stubs, tmp_path,
                                                       monkeypatch):
    """Left on its own overnight, a project that runs out of memory in level
    A runs it again at twice the cell size instead of stopping."""
    real = api.landscape_pass
    calls = []

    def landscape_pass(dem, **kw):
        calls.append(dem)
        if len(calls) == 1:
            raise MemoryError("Unable to allocate 61.2 GiB")
        return real(dem, **kw)

    monkeypatch.setattr(api, "landscape_pass", landscape_pass)
    bnd = tmp_path / "boundary.gpkg"
    gpd.GeoDataFrame({"name": ["field office"]},
                     geometry=[box(405000, 4503000, 425000, 4517000)],
                     crs=UTM).to_file(bnd, driver="GPKG")
    out = tmp_path / "proj"
    res = project.run_project(str(bnd), level_b="none", geology=None,
                              stream_threshold_km2=3.0, soils=False,
                              section_plots=False, guard=Guard(out_dir=out))
    status = json.loads((out / "project.json").read_text())
    assert len(calls) == 2 and calls[0] != calls[1]
    assert status["steps"]["plan_a"]["cell_m"] == 60.0       # 30 m doubled
    assert any("ran out of memory" in w for w in res["warnings"])
    vt = gpd.read_file(res["valley_types"])
    assert (vt["dem_cell_m"] == 60.0).all()


def test_confinement_breaks_are_fitted_to_the_lidar():
    """Three ranges of 10 m ratio that the lidar calls confined, partly and
    unconfined: the fitted breaks fall between them, and the published 2 / 5
    agree worse. Too few reaches, or an empty class, and the fit is skipped
    with the reason."""
    import numpy as np

    rng = np.random.default_rng(1)
    ratio = np.concatenate([rng.uniform(3, 8, 40), rng.uniform(9, 20, 60),
                            rng.uniform(25, 90, 80)])
    lidar = np.array([0] * 40 + [1] * 60 + [2] * 80)
    f = project.fit_confinement_breaks(ratio, lidar)
    b1, b2 = f["breaks"]
    assert 8 <= b1 <= 9 and 19 <= b2 <= 25
    assert f["agreement_after"] == 1.0 and f["agreement_before"] < 0.5
    assert f["table"]["confined"]["confined"] == 40 and f["n"] == 180
    assert f["lidar_classes"] == [40, 60, 80]
    # length-weighted: a long reach counts for more
    f2 = project.fit_confinement_breaks([1, 10, 30], [2, 2, 2], [1000, 1, 1],
                                        min_reaches=3, min_per_class=0)
    assert f2["agreement_before"] < 0.01                  # the long one is wrong
    s = project.fit_confinement_breaks(ratio[:20], lidar[:20])
    assert "skipped" in s and "at least 30" in s["skipped"]
    s = project.fit_confinement_breaks(ratio, np.where(lidar == 0, 1, lidar))
    assert "skipped" in s and "0 entrenched" in s["skipped"]


def test_fitted_breaks_are_applied_and_the_valleys_retyped(stubs, tmp_path,
                                                            monkeypatch):
    """A calibration that improves agreement is applied to every level A
    reach, the valleys are re-typed and the form judged again; one that does
    not is reported and left alone; a second run does not redo it."""
    bnd = tmp_path / "boundary.gpkg"
    gpd.GeoDataFrame({"name": ["field office"]},
                     geometry=[box(405000, 4503000, 425000, 4517000)],
                     crs=UTM).to_file(bnd, driver="GPKG")
    out = tmp_path / "proj"
    kw = dict(level_b="pilot", pilot_count=1, stream_threshold_km2=3.0,
              geology=None, section_plots=False, soils=False, data="none")
    fake = {"breaks": [1.5, 3.0], "n": 120, "agreement_before": 0.4,
            "agreement_after": 0.8, "huc12s": ["150100000101"], "basis": "test",
            "table": {k: {m: 1 for m in project._ER_TO_CONF}
                      for k in project._ER_TO_CONF}, "lidar_classes": [40, 40, 40]}
    monkeypatch.setattr(project, "_calibrate_confinement", lambda st, pa: dict(fake))
    retyped = []
    real = project._retype_valleys
    monkeypatch.setattr(project, "_retype_valleys",
                        lambda *a, **k: retyped.append(1) or real(*a, **k))
    res = project.run_project(str(bnd), guard=Guard(out_dir=out), **kw)
    status = json.loads((out / "project.json").read_text())
    assert status["confinement"]["applied"] is True
    assert status["steps"]["pass_a"]["confinement_breaks"] == [1.5, 3.0]
    assert "reclassified_dir" in status["steps"]["pass_a"]["segments"]
    assert status["steps"]["pass_a"]["typing"] == project.TYPING
    assert retyped == [1]
    vt = gpd.read_file(res["valley_types"])
    assert set(vt["confinement_basis"]) <= {"sections", "hand area"}
    assert {"valley_floor_width_m", "confinement_ratio_hand",
            "valley_type_1996"} <= set(vt.columns)
    report = (out / "RESULTS.md").read_text()
    assert "## Confinement at 10 m, against the lidar" in report
    assert "breaks of 1.5 and 3 agree on 80%" in report
    assert "**The fitted breaks were applied" in report
    assert "| confined | 1 | 1 | 1 |" in report
    # again: the same breaks, nothing redone
    project.run_project(str(bnd), guard=Guard(out_dir=out), **kw)
    assert retyped == [1]
    # a fit not worth applying is reported and left alone
    weak = dict(fake, breaks=[2.5, 6.0], agreement_after=0.42)
    monkeypatch.setattr(project, "_calibrate_confinement", lambda st, pa: dict(weak))
    project.run_project(str(bnd), guard=Guard(out_dir=out), **kw)
    status = json.loads((out / "project.json").read_text())
    assert status["confinement"]["applied"] is False
    assert "The gain is too small" in (out / "RESULTS.md").read_text()
    assert retyped == [1]
    # and no calibration at all when asked
    skip = dict(fake, skipped="4 reaches measured", breaks=None)
    monkeypatch.setattr(project, "_calibrate_confinement", lambda st, pa: dict(skip))
    project.run_project(str(bnd), guard=Guard(out_dir=out), **kw)
    assert "were kept: 4 reaches measured" in (out / "RESULTS.md").read_text()
    project.run_project(str(bnd), guard=Guard(out_dir=out),
                        calibrate_confinement=False, **kw)
    assert retyped == [1]


def test_the_lidar_entrenchment_is_read_from_the_level_b_layers(stubs, tmp_path):
    """The real pilot's stream types feed the calibration: measured reaches
    with an entrenchment ratio and at least three sections."""
    bnd = tmp_path / "boundary.gpkg"
    gpd.GeoDataFrame({"name": ["field office"]},
                     geometry=[box(405000, 4503000, 425000, 4517000)],
                     crs=UTM).to_file(bnd, driver="GPKG")
    out = tmp_path / "proj"
    project.run_project(str(bnd), level_b="pilot", pilot_count=1,
                        stream_threshold_km2=3.0, geology=None,
                        section_plots=False, soils=False, data="none",
                        guard=Guard(out_dir=out))
    st = project._Status(out)
    lid = project._lidar_entrenchment(st)
    status = json.loads((out / "project.json").read_text())
    assert status.get("confinement") is not None       # fitted or skipped, said
    if lid is not None:
        assert {"seg_id", "er", "huc12", "length_m"} <= set(lid.columns)
        assert (lid["huc12"] == "150100000101").all()
        cal = status["confinement"]
        assert cal.get("breaks") or cal.get("skipped")


def test_a_reconnaissance_run_asks_for_a_coarser_level_a(stubs, tmp_path):
    """A laptop or a cloud runner asks for level A at 30 m: the DEM is
    fetched straight onto that grid, and the report says what that costs.
    Finer than the 1/3 arc-second product is refused."""
    bnd = tmp_path / "boundary.gpkg"
    gpd.GeoDataFrame({"name": ["field office"]},
                     geometry=[box(405000, 4503000, 425000, 4517000)],
                     crs=UTM).to_file(bnd, driver="GPKG")
    with pytest.raises(GeolensError):
        project.run_project(str(bnd), level_b="none", geology=None,
                            soils=False, data="none", level_a_cell_m=5.0,
                            guard=Guard(out_dir=tmp_path / "fine"))
    out = tmp_path / "recon"
    project.run_project(str(bnd), level_b="none", stream_threshold_km2=3.0,
                        geology=None, section_plots=False, soils=False,
                        data="none", level_a_cell_m=30.0,
                        guard=Guard(out_dir=out))
    assert stubs["res"][-1] == 30.0
    report = (out / "RESULTS.md").read_text()
    assert "asked for at 30 m" in report and "narrower than 90 m" in report


def test_soils_that_arrive_after_the_pass_retype_the_valleys(stubs, tmp_path,
                                                             monkeypatch):
    """Soil Data Access was down the night the v0.42 Kingman run started:
    the pass typed its valleys without SSURGO. The next run, with the
    soils fetched, re-types them from the saved valley form instead of
    redoing the two-hour pass."""
    calls = {"soils": 0, "passes": 0}

    def fetch_soils(aoi, guard=None, max_area_km2=None, **k):
        calls["soils"] += 1
        if calls["soils"] == 1:
            raise GeolensError("Could not get an answer from USDA Soil Data "
                               "Access: JSONDecodeError")
        out = Path(guard.out_dir) / "soils_dir"
        out.mkdir(parents=True, exist_ok=True)
        p = out / "soils.gpkg"
        gpd.GeoDataFrame(
            {"mukey": [1], "material_class": ["alluvial"],
             "pmkind": ["alluvium"], "hydgrpdcd": ["B"], "kwfact": [0.2]},
            geometry=[box(380000, 4480000, 450000, 4540000)],
            crs=UTM).to_file(p, driver="GPKG", layer="soils")
        return {"soils": str(p), "polygons": 1,
                "km2_by_material": {"alluvial": 4200.0}}

    def no_geology(*a, **k):
        raise GeolensError("USGS geology service down too")

    monkeypatch.setattr("geolens.soils.fetch_soils", fetch_soils)
    monkeypatch.setattr("geolens.geology.fetch_geology", no_geology)
    real_pass = api.landscape_pass

    def counted_pass(*a, **k):
        calls["passes"] += 1
        return real_pass(*a, **k)

    monkeypatch.setattr(api, "landscape_pass", counted_pass)
    bnd = tmp_path / "boundary.gpkg"
    gpd.GeoDataFrame({"name": ["ranch"]},
                     geometry=[box(405000, 4503000, 425000, 4517000)],
                     crs=UTM).to_file(bnd, driver="GPKG")
    out = tmp_path / "proj"
    kw = dict(level_b="none", soils=False, stream_threshold_km2=3.0,
              section_plots=False, data="none")
    project.run_project(str(bnd), guard=Guard(out_dir=out), **kw)
    status = json.loads((out / "project.json").read_text())
    assert "soils" not in status["steps"] and calls["passes"] == 1
    assert status["steps"]["pass_a"]["geology"] is None
    assert any("USGS geology, not SSURGO" in w for w in status["warnings"])

    res = project.run_project(str(bnd), guard=Guard(out_dir=out), **kw)
    status = json.loads((out / "project.json").read_text())
    assert calls["soils"] == 2 and calls["passes"] == 1, "no second pass"
    assert status["steps"]["pass_a"]["geology"] == status["steps"]["soils"]["path"]
    assert status["steps"]["pass_a"]["typing"] == project.TYPING
    assert not any("USGS geology, not SSURGO" in w for w in status["warnings"])
    vt = gpd.read_file(res["valley_types"])
    assert (vt["material_class"] == "alluvial").any()


def test_valleys_again_on_the_lidar_at_3_m_for_a_pilot(stubs, tmp_path):
    """v0.43: the valley pass again on the lidar, HUC12 by HUC12, at 3 m,
    for a pilot, compared with the 10 m pass. Here the "lidar" is the same
    synthetic DEM, so the two must largely agree; what is checked is that
    the pilot is chosen, the tile fetched at the finer cell, the pass run,
    the comparison written, the layers merged, the report said, and that a
    second run carries on instead of starting again."""
    bnd = tmp_path / "boundary.gpkg"
    gpd.GeoDataFrame({"name": ["field office"]},
                     geometry=[box(405000, 4503000, 425000, 4517000)],
                     crs=UTM).to_file(bnd, driver="GPKG")
    out = tmp_path / "proj"
    kw = dict(level_b="pilot", pilot_count=1, stream_threshold_km2=3.0,
              geology=None, section_plots=False, soils=False, data="none",
              fine_valleys="pilot", fine_count=1)
    res = project.run_project(str(bnd), guard=Guard(out_dir=out), **kw)
    status = json.loads((out / "project.json").read_text())
    fine = status["fine_valleys"]
    # West Wash is the only HUC12 with lidar; Upper West drains into it, so
    # it is not a headwater one and the basis says so
    assert fine["chosen"] == ["150100000101"]
    assert "no headwater" in fine["why"]["basis"]
    assert "carries only the drainage inside it" in (out / "RESULTS.md").read_text()
    rec = fine["huc12"]["150100000101"]
    assert rec["status"] == "done", rec
    assert stubs["res"][-1] == 1.0                     # the tile's 1 m lidar
    assert rec["cell_m"] == 30.0          # the stand-in "lidar" is coarser than 3 m
    assert not list((out / "A_fine" / "150100000101" / "run").rglob("*.tif")), \
        "the working rasters are deleted"
    assert Path(rec["dem_1m"]).exists(), "the 1 m DEM is kept"
    assert "inflow" in rec
    c = rec["compare"]
    assert c["km_10m"] > 0 and c["km_fine"] > 0
    assert c["matched_share"] > 0.5
    assert 0.0 <= c["landscape_type_agreement"] <= 1.0
    assert Path(res["valley_types_3m"]).exists()
    assert Path(res["valley_extent_3m"]).exists()
    assert (out / "A_fine" / "comparison_3m.csv").exists()
    report = (out / "RESULTS.md").read_text()
    assert "## Valleys on the lidar at 3 m (pilot)" in report
    assert "| 150100000101 |" in report

    fetched = len(stubs["fetch"])
    again = project.run_project(str(bnd), guard=Guard(out_dir=out), **kw)
    assert len(stubs["fetch"]) == fetched, "nothing fetched again"
    assert Path(again["valley_types_3m"]).exists()


def test_a_new_project_starts_from_an_earlier_one_without_changing_it(stubs,
                                                                     tmp_path):
    """Sam, 23 Oct: "don't overwrite 42, call the new version 43". A new
    folder starts from the earlier project's measurements: copied, every
    path in project.json moved, nothing re-fetched or re-run that was done,
    the earlier folder untouched; this run's own steps (here the 3 m
    pilot) and the merged layers and report are made in the new folder."""
    bnd = tmp_path / "boundary.gpkg"
    gpd.GeoDataFrame({"name": ["field office"]},
                     geometry=[box(405000, 4503000, 425000, 4517000)],
                     crs=UTM).to_file(bnd, driver="GPKG")
    v42 = tmp_path / "geolens_v42"
    kw = dict(level_b="pilot", pilot_count=1, stream_threshold_km2=3.0,
              geology=None, section_plots=False, soils=False, data="none")
    project.run_project(str(bnd), guard=Guard(out_dir=v42), **kw)
    before = {p: p.stat().st_mtime_ns for p in v42.rglob("*") if p.is_file()}
    fetched = list(stubs["fetch"])

    v43 = tmp_path / "geolens_v43"
    res = project.run_project(str(bnd), guard=Guard(out_dir=v43),
                              copy_from=str(v42), fine_valleys="pilot",
                              fine_count=1, **kw)
    # the earlier project was only read
    after = {p: p.stat().st_mtime_ns for p in v42.rglob("*") if p.is_file()}
    assert after == before
    # nothing done there was fetched or run again: only the 3 m tile
    assert stubs["fetch"][:len(fetched)] == fetched
    assert stubs["fetch"][len(fetched):] == ["1m"]
    status = json.loads((v43 / "project.json").read_text())
    assert status["copied_from"]["project"] == str(v42)
    text = json.dumps({k: v for k, v in status.items() if k != "copied_from"})
    assert str(v42) not in text, "every path moved to the new folder"
    assert str(v43) in text
    assert status["huc12"]["150100000101"]["status"] == "done"
    assert Path(status["huc12"]["150100000101"]["stream_types"]).exists()
    assert Path(res["valley_types"]).parent == v43 / "merged"
    assert Path(res["valley_types_3m"]).exists()
    report = (v43 / "RESULTS.md").read_text()
    assert "copied from" in report and "was not changed" in report
    assert not (v43 / "merged" / "huc12_status.gpkg").samefile(
        v42 / "merged" / "huc12_status.gpkg")

    # a folder cannot start from itself, nor from a non-project
    with pytest.raises(GeolensError):
        project.copy_project(v42, v42)
    with pytest.raises(GeolensError):
        project.copy_project(tmp_path / "boundary_dir_missing", tmp_path / "x")



def test_a_main_stem_entering_a_tile_takes_the_10_m_drainage():
    """A HUC12 tile only routes the drainage inside it: a main stem coming
    in from upstream starts small. Along a 10 m reach that drains much
    more, running the same way, the tile reach takes the 10 m drainage; the
    channel widens as A**0.35 and the ratio narrows by as much. A tributary
    crossing near the main stem is left alone."""
    from shapely.geometry import LineString

    tile = gpd.GeoDataFrame(
        {"seg_id": [1, 2], "drainage_km2": [2.0, 1.5], "gradient": [0.01, 0.03],
         "valley_width_m": [300.0, 40.0], "channel_width_m": [3.0, 2.7],
         "confinement_ratio": [100.0, 14.8], "confinement": ["unconfined"] * 2},
        geometry=[LineString([(0, 0), (500, 0)]),             # along the river
                  LineString([(250, 10), (250, 500)])],       # a tributary
        crs=UTM)
    coarse = gpd.GeoDataFrame(
        {"drainage_km2": [500.0, 3.0]},
        geometry=[LineString([(0, 15), (600, 15)]),           # the river at 10 m
                  LineString([(260, 30), (260, 520)])],
        crs=UTM)
    fixed, n = project._inflow_correct(tile, coarse, breaks=(1.0, 16.0))
    assert n == 1
    river, trib = fixed.iloc[0], fixed.iloc[1]
    assert river["drainage_km2"] == 500.0 and river["drainage_from_10m"]
    f = (2.0 / 500.0) ** 0.35
    assert river["confinement_ratio"] == pytest.approx(100.0 * f, rel=1e-6)
    assert river["channel_width_m"] == pytest.approx(3.0 / f, rel=1e-6)
    assert river["confinement"] == "partly confined"         # 14.5 with breaks 1 and 16
    assert trib["drainage_km2"] == 1.5 and not trib["drainage_from_10m"]


def test_the_worker_count_follows_memory_and_cores(monkeypatch):
    import geolens.landscape.plan as plan

    monkeypatch.setattr(plan, "available_ram_gb", lambda: 64.0)
    monkeypatch.setattr(project.os, "cpu_count", lambda: 16)
    assert project.fine_workers(3.0) == 6                 # cores and cap allow 6
    monkeypatch.setattr(plan, "available_ram_gb", lambda: 8.0)
    assert project.fine_workers(3.0) == 1                 # 8 GB: one at a time
    monkeypatch.setattr(plan, "available_ram_gb", lambda: None)
    monkeypatch.setattr(project.os, "cpu_count", lambda: 4)
    assert project.fine_workers(3.0) == 1


def test_the_lidar_valley_pass_in_parallel_stops_when_the_disk_fills(
        stubs, tmp_path, monkeypatch):
    """Two HUC12s at once through the process pool; then, with the drive
    nearly full, the pass stops before a tile and says so, resumable."""
    bnd = tmp_path / "boundary.gpkg"
    gpd.GeoDataFrame({"name": ["field office"]},
                     geometry=[box(405000, 4503000, 425000, 4517000)],
                     crs=UTM).to_file(bnd, driver="GPKG")
    out = tmp_path / "proj"
    kw = dict(level_b="pilot", pilot_count=1, stream_threshold_km2=3.0,
              geology=None, section_plots=False, soils=False, data="none")
    project.run_project(str(bnd), guard=Guard(out_dir=out), **kw)
    # Workers are spawned (as on Windows), so the test's stand-in download
    # does not reach them: each tile's 1 m mosaic is already there, as one
    # kept from an interrupted run would be, and is used again.
    for code in ("150100000101", "150100000102"):
        d = out / "A_fine" / code / "dem"
        d.mkdir(parents=True)
        shutil.copyfile(DEM, d / "dem_mosaic.tif")
    fetched = len(stubs["fetch"])
    project.run_project(str(bnd), guard=Guard(out_dir=out), fine_valleys="listed",
                        fine_huc12s="150100000101,150100000102",
                        fine_workers=2, **kw)
    assert len(stubs["fetch"]) == fetched, "the kept mosaics were used"
    status = json.loads((out / "project.json").read_text())
    fine = status["fine_valleys"]
    assert fine["workers"] == 2
    assert {r["status"] for r in fine["huc12"].values()} == {"done"}, fine["huc12"]
    assert all(r.get("dem_reused") for r in fine["huc12"].values())
    assert fine["summary"]["done"] == 2

    out2 = tmp_path / "proj2"
    monkeypatch.setattr(project, "_free_gb", lambda p: 5.0)
    project.run_project(str(bnd), guard=Guard(out_dir=out2), fine_valleys="pilot",
                        fine_count=1, **kw)
    status = json.loads((out2 / "project.json").read_text())
    assert status["fine_valleys"]["huc12"] == {}
    assert "under 30 GB free" in status["fine_valleys"]["stopped"]
    assert any("valley pass stopped" in w for w in status["warnings"])
    assert "Stopped early" in (out2 / "RESULTS.md").read_text()


def test_one_run_at_a_time_per_project_folder(tmp_path, monkeypatch):
    """A second run started on a folder while the first still works is
    refused; a lock left by a run that died is taken over."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "run.lock").write_text("424242 2026-10-23T00:00:00Z\n")
    monkeypatch.setattr(project, "_pid_alive", lambda pid: True)
    with pytest.raises(GeolensError, match="Another geolens run"):
        project._take_lock(root)
    monkeypatch.setattr(project, "_pid_alive", lambda pid: False)
    project._take_lock(root)                       # the dead run's lock
    assert (root / "run.lock").read_text().split()[0] == str(__import__("os").getpid())
    project._take_lock(root)                       # this process again: fine
