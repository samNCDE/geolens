"""geolens.pyt, run against a stand-in arcpy and real geolens.

Checks that the toolbox loads, that every tool's parameters are sound, and
that each tool calls the right geolens function, turns the result into
output layers, progress and messages, and styles the layers it adds. It
cannot check ArcGIS Pro itself: the stand-in records calls, it draws nothing.
"""
from __future__ import annotations

import filecmp
import importlib.machinery
import json
import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TBX = ROOT / "arcgis_toolbox"
FAKE = ROOT / "tests" / "arcgis" / "fake_arcpy"
DEM = ROOT / "tests" / "landscape" / "landscape_dem.tif"
FAN_DEM = ROOT / "tests" / "fans" / "fan_dem.tif"
COMPOUND = ROOT / "tests" / "compound"


@pytest.fixture(scope="module")
def tbx():
    sys.path.insert(0, str(FAKE))
    old = os.environ.get("GEOLENS_PYTHON")
    os.environ["GEOLENS_PYTHON"] = sys.executable
    # The step tools read the run record; other test files turn records off.
    persist = os.environ.pop("GEOLENS_PERSIST", None)
    loader = importlib.machinery.SourceFileLoader("geolens_pyt",
                                                  str(TBX / "geolens.pyt"))
    spec = importlib.util.spec_from_loader("geolens_pyt", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    import arcpy
    yield mod, arcpy
    sys.path.remove(str(FAKE))
    if persist is not None:
        os.environ["GEOLENS_PERSIST"] = persist
    if old is None:
        os.environ.pop("GEOLENS_PYTHON", None)
    else:
        os.environ["GEOLENS_PYTHON"] = old


def _tool(tbx, cls, **values):
    mod, arcpy = tbx
    arcpy.reset()
    tool = getattr(mod, cls)()
    params = tool.getParameterInfo()
    v = {p.name: p for p in params}
    for k, val in values.items():
        v[k].value = val
    return tool, params, v


def _execute(tbx, tool, params):
    _, arcpy = tbx
    tool.updateParameters(params)
    tool.updateMessages(params)
    errors = [(p.name, p.message) for p in params if p.hasError()]
    assert not errors, errors
    tool.execute(params, None)
    # The .pyt reloads geolens_arcgis whenever Pro imports it, which can
    # happen between execute and postExecute: postExecute must not depend on
    # anything execute kept in memory.
    importlib.reload(sys.modules["geolens_arcgis"])
    # What Pro does next: add each derived output to the map, then call
    # postExecute.
    for p in params:
        if p.direction == "Output" and p.value is not None \
                and p.datatype == "DEFeatureClass":
            arcpy.mp.MAP.layers.append(arcpy.Layer(p.valueAsText))
    tool.postExecute(params)
    return {lyr.name: lyr for lyr in arcpy.mp.MAP.layers}


def _layer(layers, base):
    hit = [lyr for name, lyr in layers.items() if name.startswith(base)]
    assert hit, (base, sorted(layers))
    return hit[0]


def _log(tbx, kind):
    return [e for e in tbx[1].LOG if e[0] == kind]


# -- the file ------------------------------------------------------------------

def test_the_toolbox_is_plain_ascii():
    """Pro reads .pyt files; keep them free of encoding surprises."""
    (TBX / "geolens.pyt").read_bytes().decode("ascii")
    (TBX / "geolens_arcgis.py").read_bytes().decode("ascii")


def test_the_runner_is_the_same_file_as_the_qgis_plugins():
    assert filecmp.cmp(TBX / "geolens_runner.py",
                       ROOT / "qgis_plugin" / "geolens_qgis" / "runner.py",
                       shallow=False)


def test_every_tool_has_sound_parameters(tbx):
    mod, _ = tbx
    tools = mod.Toolbox().tools
    assert len(tools) >= 18
    labels = set()
    for cls in tools:
        t = cls()
        assert t.label and t.description and t.category
        labels.add(t.label)
        params = t.getParameterInfo()
        names = [p.name for p in params]
        assert len(names) == len(set(names)), (t.label, names)
        for p in params:
            assert p.direction in ("Input", "Output")
            if p.parameterType == "Derived":
                assert p.direction == "Output"
    assert "Run the whole routine" in labels
    assert sum(1 for t in tools if t().category == "2 Steps") == 7


# -- tools, end to end ---------------------------------------------------------

def test_check_memory(tbx):
    tool, params, v = _tool(tbx, "CheckMemory", dem=str(DEM))
    _execute(tbx, tool, params)
    assert v["verdict"].value
    assert _log(tbx, "progressor")
    assert any("VERDICT" in m for _, m in _log(tbx, "message"))


def test_the_steps_on_one_run_folder(tbx, tmp_path):
    run = tmp_path / "run"
    tool, params, v = _tool(tbx, "Step1Terrain", dem=str(DEM),
                            out_folder=str(run), threshold=3.0, segment=800.0)
    layers = _execute(tbx, tool, params)
    assert "Valley bottom" in layers and "Reaches (confinement)" in layers
    assert not (run / "valley_types_dir").exists()
    reaches = layers["Reaches (confinement)"]
    assert reaches.applied_symbology.renderer.fields == ["confinement"]

    tool, params, v = _tool(tbx, "Step2ValleyTypes", run_folder=str(run),
                            geology="None", fans=True)
    layers = _execute(tbx, tool, params)
    assert "Valley types" in layers and "Valley extent" in layers
    assert layers["Valley types"].applied_symbology.renderer.fields == \
        ["valley_type"]
    assert v["out_fan_candidates"].value

    tool, params, v = _tool(tbx, "Step3StreamTypes", run_folder=str(run),
                            plots=True, name="stream_types_v29")
    layers = _execute(tbx, tool, params)
    sec = _layer(layers, "Stream-type sections")
    assert sec.name == "Stream-type sections (stream_types_v29)"
    assert ".gdb" in sec.dataSource
    ren = sec.applied_symbology.renderer
    assert ren.type == "UniqueValueRenderer" and ren.fields == ["status"]
    coloured = [i for g in ren.groups for i in g.items if i.symbol.color]
    assert coloured, "section colours were not taken from the QML"
    # Plots are attachments, so the layer keeps ArcGIS's default pop-up,
    # which shows them.
    attached = {e[1]: e[3] for e in _log(tbx, "AddAttachments")}
    assert any("stream_type_sections" in k and n > 0 for k, n in attached.items())
    assert any(k.endswith("stream_types_v29__stream_types") and n > 0
               for k, n in attached.items())
    assert sec.getDefinition("V3").popupInfo is None
    stab = _layer(layers, "Stable / unstable form")
    assert stab.applied_symbology.renderer.type == "UniqueValueRenderer"
    assert "stream_types_v29" in v["out_stream_types"].value

    tool, params, v = _tool(
        tbx, "Step4Stability",
        valley=str(run / "valley_types_dir" / "valley_types.gpkg"),
        stream=str(run / "stream_types_v29_dir" / "stream_types.gpkg"),
        out_folder=str(tmp_path / "stab"))
    layers = _execute(tbx, tool, params)
    assert "Stable / unstable form" in layers

    tool, params, v = _tool(tbx, "SectionPlots",
                            folder=str(run / "stream_types_v29_dir"),
                            to_gdb=False)
    layers = _execute(tbx, tool, params)
    assert "Stream-type sections" in layers and "Stream types" in layers
    # Without the geodatabase, the plot comes from an image pop-up instead.
    img = layers["Stream-type sections"].getDefinition("V3").popupInfo.mediaInfos[0]
    assert img._class == "CIMImageMediaInfo"
    assert img.sourceURL == "{plot_path}" and img.linkURL == "{plot_path}"


def test_the_whole_routine_with_gullies(tbx, tmp_path):
    out = tmp_path / "whole"
    tool, params, v = _tool(tbx, "WholeRoutine", dem=str(DEM),
                            out_folder=str(out), geology="None",
                            threshold=3.0, segment=800.0, plots=False,
                            gullies=True)
    layers = _execute(tbx, tool, params)
    for name in ("Valley bottom", "Reaches (confinement)", "Valley types",
                 "Stream types", "Stream-type sections",
                 "Stable / unstable form", "Gullies"):
        assert name in layers, (name, sorted(layers))
    labels = [e[1] for e in _log(tbx, "label")]
    assert labels, "no step labels reached the progressor"
    assert _log(tbx, "position"), "no progress reached the progressor"
    assert _log(tbx, "reset")


def test_alluvial_fans(tbx, tmp_path):
    tool, params, v = _tool(tbx, "AlluvialFans", dem=str(FAN_DEM),
                            out_folder=str(tmp_path / "fans"))
    layers = _execute(tbx, tool, params)
    assert "Alluvial fans" in layers and "Fan candidates (for review)" in layers
    fans = layers["Alluvial fans"]
    assert fans.applied_symbology.renderer.type == "SimpleRenderer"
    assert any("3 fans" in m for _, m in _log(tbx, "message"))


def test_cross_sections(tbx, tmp_path):
    tool, params, v = _tool(tbx, "CrossSections",
                            dem=str(COMPOUND / "compound_dem.tif"),
                            centerline=str(COMPOUND / "centerline.shp"),
                            out_folder=str(tmp_path / "xs"), spacing=25.0,
                            width=160.0, max_plots=5)
    layers = _execute(tbx, tool, params)
    lyr = layers["Centerline sections"]
    assert ".gdb" in lyr.dataSource
    assert any(n > 0 for _, _, _, n in _log(tbx, "AddAttachments"))


# -- inputs, folders, cancelling ----------------------------------------------

def test_inputs_geolens_cannot_open_are_copied(tbx, tmp_path):
    _, arcpy = tbx
    import geolens_arcgis as ga
    import geopandas as gpd
    from shapely.geometry import LineString

    arcpy.reset()
    two = tmp_path / "two.gpkg"
    g = gpd.GeoDataFrame({"material_class": ["alluvial"]},
                         geometry=[LineString([(0, 0), (1, 1)])],
                         crs="EPSG:32613")
    g.to_file(two, layer="a", driver="GPKG")
    g.to_file(two, layer="b", driver="GPKG")
    one = tmp_path / "one.gpkg"
    g.to_file(one, layer="only", driver="GPKG")

    p = arcpy.Parameter(name="x")
    p.value = os.path.join(str(one), "main.only")
    assert ga.vector_path(p, str(tmp_path), "x") == str(one)       # as is
    p.value = os.path.join(str(two), "main.b")
    got = ga.vector_path(p, str(tmp_path), "geology")              # copied
    assert got.endswith(os.path.join("_inputs", "geology.gpkg"))
    assert list(gpd.read_file(got).columns) == ["material_class", "geometry"]
    p.value = arcpy.SelectedLayer(str(one))                        # selection
    got = ga.vector_path(p, str(tmp_path), "sel")
    assert got.endswith("sel.gpkg")
    assert [e[0] for e in arcpy.LOG].count("CopyFeatures") == 2


def test_a_new_output_folder_is_a_warning_and_a_non_run_an_error(tbx,
                                                                 tmp_path):
    tool, params, v = _tool(tbx, "Step1Terrain", dem=str(DEM),
                            out_folder=str(tmp_path / "not_yet"))
    tool.updateMessages(params)
    assert v["out_folder"].messageType == "warning"
    tool, params, v = _tool(tbx, "Step3StreamTypes",
                            run_folder=str(tmp_path))
    tool.updateMessages(params)
    assert v["run_folder"].hasError()


def test_cancel_stops_the_run(tbx, tmp_path):
    _, arcpy = tbx
    tool, params, v = _tool(tbx, "Step1Terrain", dem=str(DEM),
                            out_folder=str(tmp_path / "c"), force=True)
    arcpy.env.isCancelled = True
    with pytest.raises(arcpy.ExecuteError, match="Cancelled"):
        tool.execute(params, None)
    assert arcpy.env.autoCancelling is True, "autoCancelling not restored"


# -- v0.29.1: watershed, coarsen, headcuts, riparian ---------------------------

def _outlet_point(tmp_path):
    """A point layer at the fixture's outlet (its largest drainage)."""
    import geopandas as gpd
    import numpy as np
    import rasterio
    from shapely.geometry import Point

    from geolens.landscape import flow_routing

    os.environ["GEOLENS_OUT"] = str(tmp_path / "flow")
    fl = flow_routing(str(DEM), out_prefix="outlet_flow")
    with rasterio.open(fl.accumulation) as src:
        acc = src.read(1)
        r, c = np.unravel_index(np.nanargmax(acc), acc.shape)
        x, y = src.xy(r, c)
        crs = src.crs
    path = tmp_path / "pour.gpkg"
    gpd.GeoDataFrame(geometry=[Point(x, y)], crs=crs).to_file(path,
                                                              driver="GPKG")
    return path


def test_watershed_clip_tool(tbx, tmp_path):
    pour = _outlet_point(tmp_path)
    tool, params, v = _tool(tbx, "WatershedClip", dem=str(DEM),
                            out_folder=str(tmp_path / "ws"),
                            pour_point=os.path.join(str(pour), "main.pour"))
    layers = _execute(tbx, tool, params)
    assert "Watershed boundary" in layers
    assert v["out_dem"].value and os.path.isfile(v["out_dem"].value)


def test_step1_can_clip_to_a_watershed_first(tbx, tmp_path):
    pour = _outlet_point(tmp_path)
    run = tmp_path / "run"
    tool, params, v = _tool(tbx, "Step1Terrain", dem=str(DEM),
                            out_folder=str(run), threshold=3.0, segment=800.0,
                            pour_point=str(pour))
    layers = _execute(tbx, tool, params)
    assert "Watershed boundary" in layers and "Reaches (confinement)" in layers
    import json
    rec = sorted((run / "runs").glob("*_landscape_pass.json"))[-1]
    assert "watershed_dir" in json.loads(rec.read_text())["arguments"]["dem"]


def test_one_watershed_source_only(tbx, tmp_path):
    tool, params, v = _tool(tbx, "Step1Terrain", dem=str(DEM),
                            out_folder=str(tmp_path), huc="130600010101",
                            pour_point=str(tmp_path / "x.gpkg"))
    tool.updateMessages(params)
    assert any(p.hasError() for p in params)


def test_coarsen_tool_works_in_metres(tbx, tmp_path):
    import rasterio

    from geolens.units import unit_info

    with rasterio.open(DEM) as s:
        cell = unit_info(s.crs, abs(s.res[0]), abs(s.res[1])).cell_size_m
    tool, params, v = _tool(tbx, "CoarsenDEM", dem=str(DEM),
                            out_folder=str(tmp_path / "c"), cell=cell * 2)
    _execute(tbx, tool, params)
    with rasterio.open(v["out_dem"].value) as s:
        got = unit_info(s.crs, abs(s.res[0]), abs(s.res[1])).cell_size_m
    assert abs(got - 2 * cell) < 0.1 * cell


def test_headcuts_tool(tbx, tmp_path):
    tool, params, v = _tool(tbx, "Headcuts",
                            dem=str(ROOT / "tests" / "gullies" / "gully_dem.tif"),
                            out_folder=str(tmp_path / "hc"))
    layers = _execute(tbx, tool, params)
    assert "Headcuts" in layers
    assert layers["Headcuts"].applied_symbology.renderer.type == "SimpleRenderer"


def test_riparian_step_parameters(tbx):
    """The run itself needs NAIP over the internet; api.riparian_for_run is
    tested in test_run_steps with the imagery stubbed."""
    tool, params, v = _tool(tbx, "Step6Riparian")
    assert {"run_folder", "start", "end", "min_excess", "max_hand",
            "out_riparian", "out_ndvi", "out_rgb"} <= set(v)
    assert v["out_ndvi"].datatype == "DERasterDataset"


def test_fetch_tools_parameters(tbx):
    """Both fetch from the internet, so their runs are tested at the API
    level (test_dem_fetch, test_naip_tiles) with the services stubbed."""
    tool, params, v = _tool(tbx, "FetchDEM")
    assert {"aoi", "out_folder", "source", "buffer", "cell", "crs",
            "out_dem"} <= set(v)
    assert v["out_dem"].datatype == "DERasterDataset"
    assert "to_gdb" not in v                    # no vector outputs
    tool, params, v = _tool(tbx, "NaipImagery", aoi_raster=str(DEM),
                            aoi_polygon=str(DEM))
    tool.updateMessages(params)
    assert v["aoi_polygon"].hasError()


def test_spatial_reference_to_pyproj(tbx):
    import types

    import geolens_arcgis as ga
    p = types.SimpleNamespace(value=types.SimpleNamespace(factoryCode=6529),
                              valueAsText="x")
    assert ga.spatial_reference(p) == "EPSG:6529"
    p = types.SimpleNamespace(value=types.SimpleNamespace(
        factoryCode=0, exportToString=lambda: "PROJCS[...];-5120900 10000"),
        valueAsText="x")
    assert ga.spatial_reference(p) == "PROJCS[...]"


def test_a_geopackage_layer_source_as_arcgis_pro_reports_it(tbx):
    """Sam's ArcGIS Pro reports a GeoPackage layer's dataSource as
    ``Instance=...\\a.gpkg,Dataset=main.%a``. No path test matched it, so
    the toolbox never named or coloured a GeoPackage output (Kingman v42
    layers came in grey as "main.major_streams")."""
    import geolens_arcgis as ga

    src = r"Instance=S:\k\v42\merged\major_streams.gpkg,Dataset=main.%major_streams"
    assert ga._dataset_key(src)[0] == "major_streams"
    assert ga._style_key(ga._dataset_key(src)[0])[0] == "major_streams"
    assert str(ga.qml_for(src)).endswith("major_streams.qml")
    assert "Instance=" not in str(ga.qml_for(src))
    plain = ga.plain_source(src)
    assert plain.lower().replace("\\", "/").endswith("major_streams.gpkg/main.major_streams")
    # the output parameter's path and the layer's source compare equal
    assert ga._norm(src) == ga._norm(plain)
    # anything else is left alone
    assert ga.plain_source(r"S:\a\geolens.gdb\valley_types") == r"S:\a\geolens.gdb\valley_types"


def test_calibrate_confinement_tool(tbx, tmp_path):
    run = tmp_path / "run"
    tool, params, v = _tool(tbx, "Step1Terrain", dem=str(DEM),
                            out_folder=str(run), threshold=3.0, segment=800.0)
    _execute(tbx, tool, params)
    # Breaks blank: a report only.
    tool, params, v = _tool(tbx, "Step1bCalibrate", run_folder=str(run))
    _execute(tbx, tool, params)
    msgs = [m for _, m in _log(tbx, "message")]
    assert any("how the reaches divide" in m for m in msgs)
    assert v["out_reaches"].value is None
    # One break only is refused before running.
    tool, params, v = _tool(tbx, "Step1bCalibrate", run_folder=str(run),
                            confined_max=3.0)
    tool.updateMessages(params)
    assert v["partly_max"].hasError()
    # Both: calibrated reaches.
    tool, params, v = _tool(tbx, "Step1bCalibrate", run_folder=str(run),
                            confined_max=3.0, partly_max=8.0)
    layers = _execute(tbx, tool, params)
    assert "Reaches (confinement)" in layers
    assert (run / "reclassified_dir" / "segments_classified.gpkg").exists()


def test_every_geolens_call_matches_the_api():
    """Each ga.run("<function>", {...}) names an api function, with keyword
    arguments it takes. The tools run geolens in another process, so a typo
    here would only show up as a failed run in ArcGIS Pro."""
    import ast
    import inspect

    from geolens import api

    tree = ast.parse((TBX / "geolens.pyt").read_text())
    # A call's arguments built first (kw = {...}) are looked up by name, so
    # the project tool's call, run in Pro or in its own window, is checked.
    named = {t.id: n.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
             and isinstance(n.value, ast.Dict)
             for t in n.targets if isinstance(t, ast.Name)}
    calls = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("run", "launch")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "ga"
                and node.args and isinstance(node.args[0], ast.Constant)):
            continue
        fn = getattr(api, node.args[0].value, None)
        assert callable(fn), node.args[0].value
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Name):
            node.args[1] = named.get(node.args[1].id, node.args[1])
        if len(node.args) < 2 or not isinstance(node.args[1], ast.Dict):
            continue
        sig = inspect.signature(inspect.unwrap(fn))
        if any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values()):
            continue
        keys = {k.value for k in node.args[1].keys
                if isinstance(k, ast.Constant)}
        unknown = keys - set(sig.parameters)
        assert not unknown, (node.args[0].value, sorted(unknown))
        calls += 1
    assert calls >= 15


def test_project_tool_passes_the_pilot_choice(tbx, tmp_path, monkeypatch):
    """Landscape projects (the default) and stream projects choose their
    level B pilot differently; the choice reaches run_project."""
    mod, _ = tbx
    seen = []
    monkeypatch.setattr(mod.ga, "run",
                        lambda fn, kw, *a, **k: seen.append((fn, kw)) or {})
    labels = [c[0] for c in mod.RunProject.LEVEL_B]
    assert labels[0].startswith("Pilot: HUC12s that together represent")
    for label in labels[:3]:
        tool, params, v = _tool(
            tbx, "RunProject", out_folder=str(tmp_path), level_b=label,
            url="https://example.org/arcgis/rest/services/x/MapServer/3")
        tool.execute(params, None)
    assert [(fn, kw["level_b"], kw["pilot_by"]) for fn, kw in seen] == [
        ("run_project", "pilot", "representative"),
        ("run_project", "pilot", "unconfined"),
        ("run_project", "all", "representative")]
    # Valleys are typed by SSURGO unless asked otherwise (USGS geology calls
    # most valleys bedrock), and the whole watershed data package is fetched.
    assert {kw["geology"] for _, kw in seen} == {"ssurgo"}
    assert {kw["data"] for _, kw in seen} == {"all"}
    tool, params, v = _tool(
        tbx, "RunProject", out_folder=str(tmp_path),
        data=mod.RunProject.DATA[4][0],
        url="https://example.org/arcgis/rest/services/x/MapServer/3")
    tool.execute(params, None)
    assert seen[-1][1]["data"] == "atlas14"
    names = [p.name for p in tool.getParameterInfo()]
    assert {"out_summary", "out_ecosites", "out_fire", "out_major",
            "out_sensitivity"} <= set(names)
    # The last step's knobs (v0.40) and the reconnaissance grid (v0.41):
    # 10 m is the product's own and is passed as "not asked".
    kw = seen[-1][1]
    assert kw["major_streams_km2"] == 25.0 and kw["sensitivity_weights"] is None
    assert kw["level_a_cell_m"] is None
    tool, params, v = _tool(
        tbx, "RunProject", out_folder=str(tmp_path), cell_a=30.0,
        major_km2=100.0,
        url="https://example.org/arcgis/rest/services/x/MapServer/3")
    tool.execute(params, None)
    assert seen[-1][1]["level_a_cell_m"] == 30.0
    assert seen[-1][1]["major_streams_km2"] == 100.0


def test_save_earthdata_login_tool(tbx, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    tool, params, v = _tool(tbx, "SaveEarthdataLogin", username="sam_ncde",
                            password="Xy7-secret")
    assert v["password"].datatype == "GPEncryptedString"
    _execute(tbx, tool, params)
    netrc = Path(v["saved"].value)
    assert netrc.parent == tmp_path
    assert "machine urs.earthdata.nasa.gov login sam_ncde password Xy7-secret" \
        in netrc.read_text()
    # The password is never echoed into the geoprocessing messages.
    assert not any("Xy7-secret" in str(m) for m in _log(tbx, "message"))
    assert not any("Xy7-secret" in str(e) for e in tbx[1].LOG)


def test_sentinel1_tool_parameters(tbx):
    """It fetches from NASA with a login, so its runs are tested at the API
    level (test_sar) with the services stood in for."""
    tool, params, v = _tool(tbx, "Sentinel1Radar")
    assert {"aoi_raster", "aoi_polygon", "out_folder", "start", "end",
            "polygons", "min_passes", "out_stats", "out_zonal"} <= set(v)
    assert v["out_stats"].datatype == "DERasterDataset"
    assert v["min_passes"].value == 8
    tool, params, v = _tool(tbx, "Sentinel1Radar", aoi_raster=str(DEM),
                            aoi_polygon=str(DEM))
    tool.updateMessages(params)
    assert v["aoi_polygon"].hasError()


def test_ssurgo_tool_and_geology_choice(tbx):
    """Soil Data Access is stood in for in test_soils; here, the wiring."""
    mod, _ = tbx
    tool, params, v = _tool(tbx, "SsurgoSoils")
    assert {"aoi_raster", "aoi_polygon", "out_folder", "buffer",
            "out_soils"} <= set(v)
    assert "to_gdb" in v                       # soils are a vector output
    tool, params, v = _tool(tbx, "Step2ValleyTypes",
                            geology="Download USDA SSURGO soils")
    assert mod._geology(v, "unused") == "ssurgo"
    assert "Download USDA SSURGO soils" in v["geology"].filter.list


def test_project_and_services_tools(tbx):
    """Both reach the internet; their runs are tested at the API level
    (test_project, test_services)."""
    tool, params, v = _tool(tbx, "RunProject")
    assert {"boundary", "url", "where", "out_folder", "level_b", "pilot",
            "huc12s", "geology", "curve", "threshold", "hours", "plots",
            "out_valley_types", "out_stream_types", "out_sections",
            "out_stability", "out_huc12", "to_gdb"} <= set(v)
    assert v["curve"].value.startswith("By location")
    tool.updateMessages(params)
    assert v["url"].message                     # no boundary yet: a warning
    assert {"own_window", "fine", "fine_cell", "fine_workers", "copy_from",
            "keep_dem", "redo_holes"} <= set(v)
    assert v["own_window"].category is None     # not hidden under Options
    tool, params, v = _tool(tbx, "CheckServices")
    assert "all_ok" in v


# -- long runs (v0.45) ---------------------------------------------------------

def test_a_long_run_starts_in_its_own_window(tbx, tmp_path, monkeypatch):
    """Ticked, the project tool writes the call and a launcher into the
    project folder and starts the launcher, instead of running in Pro; a
    run over every HUC12 left unticked gets a warning; the lidar valley
    settings reach run_project."""
    mod, arcpy = tbx
    started, ran = [], []
    real = mod.ga.runner.launch_detached

    def launch(*a, **k):
        started.append((a, k))
        return real(*a, **dict(k, start=False))

    monkeypatch.setattr(mod.ga.runner, "launch_detached", launch)
    monkeypatch.setattr(mod.ga, "run", lambda *a, **k: ran.append(a) or {})
    out = tmp_path / "v43"
    fine_all = mod.RunProject.FINE[2][0]
    tool, params, v = _tool(
        tbx, "RunProject", out_folder=str(out), fine=fine_all,
        url="https://example.org/arcgis/rest/services/x/MapServer/3")
    tool.updateMessages(params)
    assert v["own_window"].messageType == "warning"   # a day or more: tick it
    tool, params, v = _tool(
        tbx, "RunProject", out_folder=str(out), fine=fine_all, own_window=True,
        fine_cell=2.0, fine_workers=4, keep_dem=True,
        url="https://example.org/arcgis/rest/services/x/MapServer/3")
    tool.updateMessages(params)
    assert not v["own_window"].message
    tool.execute(params, None)
    assert ran == [] and len(started) == 1
    settings = json.loads((out / "geolens_run.json").read_text())
    assert settings["function"] == "run_project"
    kw = settings["kwargs"]
    assert (kw["fine_valleys"], kw["fine_cell_m"], kw["fine_workers"],
            kw["keep_lidar_dem"]) == ("all", 2.0, 4, True)
    launcher = out / ("run_geolens.bat" if os.name == "nt" else "run_geolens.sh")
    assert launcher.exists()
    text = launcher.read_text()
    assert "geolens.job" in text and str(out / "geolens_run.json") in text
    msgs = " ".join(m for _, m in _log(tbx, "message"))
    assert "its own window" in msgs and "Check on a project run" in msgs
    tool, params, v = _tool(tbx, "RunProject", out_folder=str(out),
                            fine_cell=0.5,
                            url="https://example.org/arcgis/rest/services/x/MapServer/3")
    tool.updateMessages(params)
    assert v["fine_cell"].hasError()


def test_the_windows_launcher(tbx, tmp_path):
    """What run_geolens.bat does: the environment's DLLs first and the GIS's
    settings removed, this release's geolens, up to five tries a minute
    apart, no retry when another run has the folder, CRLF line ends, and
    special characters in a folder name kept literal."""
    mod, _ = tbx
    r = mod.ga.runner
    out = tmp_path / "proj 100% & more"
    settings, bat = r.write_launcher(r"C:\envs\geolens\python.exe", "run_project",
                                     {"boundary": "b.gpkg"}, str(out),
                                     title="project v43", by="test",
                                     windows=True)
    raw = bat.read_bytes()
    assert b"\r\n" in raw and raw.count(b"\n") == raw.count(b"\r\n")
    text = raw.decode("utf-8")
    assert text.startswith("@echo off\r\nchcp 65001")
    assert 'set "PY=C:\\envs\\geolens\\python.exe"' in text
    assert "%ENVDIR%\\Library\\bin" in text and "PROJ_LIB" in text
    src = r.release_src()
    assert f'set "PYTHONPATH={src}"' in text
    assert '-u -m geolens.job "' in text
    assert 'if "%RC%"=="3" goto busy' in text and "GEQ 5 goto failed" in text
    assert "proj 100%% ^& more" in text          # echo / title lines
    assert json.loads(settings.read_text())["kwargs"] == {"boundary": "b.gpkg"}


def test_a_folder_with_a_live_run_is_refused(tbx, tmp_path, monkeypatch):
    mod, _ = tbx
    r = mod.ga.runner
    out = tmp_path / "proj"
    out.mkdir()
    (out / "run.lock").write_text(f"{os.getppid()} 2026-10-23T00:00:00Z\n")
    with pytest.raises(RuntimeError, match="Another geolens run"):
        r.launch_detached(sys.executable, "run_project", {}, str(out),
                          start=False)
    (out / "run.lock").write_text("0 x\n")
    assert r.launch_detached(sys.executable, "run_project", {}, str(out),
                             start=False)["settings"]


def test_the_launcher_runs_the_call_and_tries_again(tbx, tmp_path):
    """The .sh launcher, for real: a call that fails once is tried again
    (here without the minute's wait) and then finishes."""
    if os.name == "nt":
        pytest.skip("the .bat is checked by its text")
    mod, _ = tbx
    r = mod.ga.runner
    out = tmp_path / "proj"
    settings, sh = r.write_launcher(sys.executable, "project_status",
                                    {"project_folder": str(tmp_path / "none")},
                                    str(out), windows=False)
    text = sh.read_text().replace("sleep 60", "sleep 0")
    sh.write_text(text)
    import subprocess

    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    p = subprocess.run(["sh", str(sh)], capture_output=True, text=True,
                       env=env, timeout=600)
    # no project.json there: an error each time, five tries, then it stops
    assert p.returncode == 1, p.stdout + p.stderr
    assert p.stdout.count("attempt") == 5
    (tmp_path / "none").mkdir()
    (tmp_path / "none" / "project.json").write_text("{}")
    p = subprocess.run(["sh", str(sh)], capture_output=True, text=True,
                       env=env, timeout=600)
    assert p.returncode == 0, p.stdout + p.stderr
    res = json.loads((out / "geolens_run_result.json").read_text())
    assert res["result"]["running"] is False


def _project_folder(tmp_path):
    """A finished project's layers, as merged writes them."""
    import geopandas as gpd
    from shapely.geometry import LineString, box

    root = tmp_path / "geolens_v43"
    merged = root / "merged"
    extent = root / "A_landscape" / "run" / "valley_extent_dir"
    for d in (merged, extent):
        d.mkdir(parents=True)
    line = [LineString([(400000, 4500000), (401000, 4501000)])]
    poly = [box(400000, 4500000, 401000, 4501000)]
    crs = "EPSG:32612"
    for name, geom, folder in (("valley_types", line, merged),
                               ("valley_types_3m", line, merged),
                               ("valley_extent_3m", poly, merged),
                               ("valley_extent", poly, extent)):
        gpd.GeoDataFrame({"valley_type": ["U-AL-AD"]}, geometry=geom,
                         crs=crs).to_file(folder / f"{name}.gpkg", layer=name,
                                          driver="GPKG")
    gpd.GeoDataFrame({"name": ["Big Sandy"]}, geometry=line, crs=crs).to_file(
        merged / "major_streams.gpkg", layer="major_streams", driver="GPKG")
    (root / "project.json").write_text(json.dumps({"steps": {}, "huc12": {}}))
    return root


def test_a_projects_results_on_the_map(tbx, tmp_path):
    """Named for the folder and coloured, each set of valley polygons just
    under the valley-type lines it matches (Pro's AFTER: below)."""
    mod, arcpy = tbx
    root = _project_folder(tmp_path)
    tool, params, v = _tool(tbx, "AddProjectResults", project=str(root))
    tool.updateMessages(params)
    assert not v["project"].hasError()
    tool.execute(params, None)
    t = " - geolens_v43"
    # the stand-in's list is bottom to top
    assert [lyr.name for lyr in arcpy.mp.MAP.layers] == [
        "Valley extent (by valley type)" + t, "Valley types" + t,
        "Valley extent on the lidar at 3 m" + t,
        "Valley types on the lidar at 3 m" + t, "Major streams" + t]
    fill = arcpy.mp.MAP.layers[2]
    item = fill.symbology.renderer.groups[0].items[0]
    assert item.symbol.color["RGB"] == [27, 158, 119, 35]     # U-AL-AD, see-through
    assert item.symbol.outlineColor == {"RGB": [27, 158, 119, 100]}
    assert "5 layers" in " ".join(m for _, m in _log(tbx, "message"))
    tool, params, v = _tool(tbx, "AddProjectResults", project=str(tmp_path))
    tool.updateMessages(params)
    assert v["project"].hasError()


def test_adding_again_recolours_and_keeps_on_off(tbx, tmp_path):
    mod, arcpy = tbx
    root = _project_folder(tmp_path)
    arcpy.reset()
    mod.ga.add_project_layers(str(root), "v43")
    first = list(arcpy.mp.MAP.layers)
    first[0].visible = False
    names = mod.ga.add_project_layers(str(root), "v43 3 m")
    assert arcpy.mp.MAP.layers == first                   # nothing added twice
    assert all(n.endswith(" - v43 3 m") for n in names)
    assert first[0].visible is False
    msgs = [m for _, m in _log(tbx, "message")]
    assert sum("already there" in m for m in msgs) == len(first)
    (root / "merged").rename(root / "elsewhere")
    (root / "A_landscape").rename(root / "elsewhere2")
    with pytest.raises(arcpy.ExecuteError, match="No results"):
        mod.ga.add_project_layers(str(root), "x")


def test_checking_on_a_project_run(tbx, tmp_path):
    """The check tool runs geolens.project_status in the geolens environment
    and puts its lines in the messages."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "project.json").write_text(json.dumps({
        "steps": {"boundary": {"status": "done"}},
        "huc12": {"150100000101": {"status": "done"}},
        "fine_valleys": {"mode": "pilot", "cell_m": 3.0, "workers": 1,
                         "chosen": ["150100000101"],
                         "huc12": {"150100000101": {"status": "done",
                                                    "seconds": 600.0}}}}))
    tool, params, v = _tool(tbx, "CheckProjectRun", project=str(root))
    _execute(tbx, tool, params)
    msgs = "\n".join(m for _, m in _log(tbx, "message"))
    assert "No run is working in it now" in msgs
    assert "1 of 1 HUC12s done" in msgs
    assert v["running"].value is False
    tool, params, v = _tool(tbx, "CheckProjectRun", project=str(tmp_path))
    tool.updateMessages(params)
    assert v["project"].hasError()
