"""geolens.pyt, run against a stand-in arcpy and real geolens.

Checks that the toolbox loads, that every tool's parameters are sound, and
that each tool calls the right geolens function, turns the result into
output layers, progress and messages, and styles the layers it adds. It
cannot check ArcGIS Pro itself: the stand-in records calls, it draws nothing.
"""
from __future__ import annotations

import filecmp
import importlib.machinery
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
    calls = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "ga"
                and node.args and isinstance(node.args[0], ast.Constant)):
            continue
        fn = getattr(api, node.args[0].value, None)
        assert callable(fn), node.args[0].value
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
