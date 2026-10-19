# -*- coding: utf-8 -*-
"""geolens tools for ArcGIS Pro.

Every tool runs geolens in its own Python environment (the one
install_windows.bat builds), streams its progress into the geoprocessing
pane, can be cancelled, and adds its layers to the map with colours and, for
anything with a plot, a pop-up showing the plot.

Run the whole routine in one tool, or one step at a time: step 1 does the
routing and valley bottom (the slow part, once); every later step reads it
from the run folder, so a step can be redone without redoing step 1.
"""
import importlib
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import arcpy  # noqa: E402

import geolens_runner  # noqa: E402
import geolens_arcgis as ga  # noqa: E402

# ArcGIS Pro keeps modules loaded between runs; refreshing the toolbox after
# an update should pick up the new code without restarting Pro.
importlib.reload(geolens_runner)
importlib.reload(ga)

P = ga.param
D = ga.derived
OPT = "Options"
LAYERS = [("Valley bottom", "out_valley_bottom"),
          ("Reaches (confinement)", "out_reaches"),
          ("Valley extent", "out_valley_extent"),
          ("Valley types", "out_valley_types"),
          ("Alluvial fans", "out_fans"),
          ("Fan candidates", "out_fan_candidates"),
          ("Stream types", "out_stream_types"),
          ("Stream-type sections", "out_sections"),
          ("Stable / unstable form", "out_stability"),
          ("Gullies", "out_gullies")]


def _set_pass_outputs(v, res):
    ga.set_output(v, "out_valley_bottom", ga.dig(res, "valley", "valley_polygons"))
    ga.set_output(v, "out_reaches", ga.dig(res, "classified", "segments"))
    ga.set_output(v, "out_valley_extent", ga.dig(res, "valley_extent"))
    ga.set_output(v, "out_valley_types", ga.dig(res, "valley_types", "segments"))
    ga.set_output(v, "out_fans", ga.dig(res, "fans", "fans"))
    ga.set_output(v, "out_fan_candidates", ga.dig(res, "fans", "candidates"))
    ga.set_output(v, "out_stream_types", ga.dig(res, "stream_types", "segments"))
    ga.set_output(v, "out_sections", ga.dig(res, "stream_types", "sections"))
    ga.set_output(v, "out_stability", ga.dig(res, "stability", "stability"))


WS = "Clip to one watershed first (optional)"


def _gdb_param():
    return P("Store layers in a file geodatabase, plots as attachments "
             "(pop-ups show them with no setup)", "to_gdb", "GPBoolean",
             default=True, category=OPT)


def _watershed_params():
    pour = P("Pour point (point layer; the first point is used)", "pour_point",
             "GPFeatureLayer", required=False, category=WS)
    pour.filter.list = ["Point"]
    bnd = P("Watershed boundary polygon", "boundary", "GPFeatureLayer",
            required=False, category=WS)
    bnd.filter.list = ["Polygon"]
    return [pour, bnd,
            P("HUC12 code", "huc", "GPString", required=False, category=WS),
            P("Buffer around the watershed, m", "ws_buffer", "GPDouble",
              default=0.0, category=WS)]


def _watershed_messages(v):
    given = [n for n in ("pour_point", "boundary", "huc")
             if n in v and v[n].value is not None]
    if len(given) > 1:
        v[given[-1]].setErrorMessage(
            "Give one of pour point, boundary or HUC12: they are three ways of "
            "naming the same watershed.")


def _clip(v, dem, out):
    """The DEM clipped to one complete watershed, or the DEM as it was."""
    pour = ga.vector_path(v.get("pour_point"), out, "pour_point")
    bnd = ga.vector_path(v.get("boundary"), out, "boundary")
    huc = ga.text(v.get("huc"))
    if not (pour or bnd or huc):
        return dem
    ws = ga.run("watershed", {
        "dem": dem, "pour_point": pour, "boundary": bnd, "huc": huc,
        "buffer_m": (v["ws_buffer"].value if "ws_buffer" in v else 0.0) or 0.0,
    }, out, "clipping to the watershed")
    arcpy.AddMessage("watershed: %.1f km2 (%s)" % (ws.get("area_km2") or 0,
                                                  ws.get("boundary_source")))
    ga.set_output(v, "out_watershed", ws.get("boundary"))
    return ws["dem"]


def _geology(v, out):
    g = ga.text(v["geology"]) or ga.GEOLOGY[0]
    if g == ga.GEOLOGY[0]:
        return "auto"
    if g == ga.GEOLOGY[1]:
        return None
    if g == ga.GEOLOGY[3]:
        return "ssurgo"
    path = ga.vector_path(v["geology_layer"], out, "geology")
    if not path:
        ga.fail("Choose a geology layer, or pick download or none.")
    return path


class Toolbox(object):
    def __init__(self):
        self.label = "geolens"
        self.alias = "geolens"
        self.tools = [FetchDEM, NaipImagery, Sentinel1Radar, SsurgoSoils,
                      FindEnvironment, CheckServices, RunProject,
                      SaveEarthdataLogin, CheckMemory, CoarsenDEM, WholeRoutine,
                      Step1Terrain, Step1bCalibrate, Step2ValleyTypes, Step3StreamTypes,
                      Step4Stability, Step5Gullies, Step6Riparian,
                      AlluvialFans, WatershedClip, Headcuts, SectionPlots,
                      CrossSections]


class _Tool(object):
    label = ""
    description = ""
    category = ""

    def __init__(self):
        self.canRunInBackground = False

    def isLicensed(self):
        return True

    def getParameterInfo(self):
        ps = self.parameters()
        if any(p.parameterType == "Derived" and p.datatype == "DEFeatureClass"
               for p in ps):
            ps.append(_gdb_param())
        return ps

    def parameters(self):
        return []

    def updateParameters(self, parameters):
        return

    def updateMessages(self, parameters):
        v = ga.by_name(parameters)
        for name in ("out_folder",):
            if name in v:
                ga.new_folder_ok(v[name])
        if "run_folder" in v:
            ga.check_run_folder(v["run_folder"])
        _watershed_messages(v)

    def postExecute(self, parameters):
        ga.style_outputs(parameters)


# -- setup ---------------------------------------------------------------------


class FindEnvironment(_Tool):
    label = "Find the geolens environment"
    category = "0 Setup"
    description = (
        "Checks that the tools can find the Python environment "
        "install_windows.bat built, and says which one they will use. If it "
        "cannot be found, give it the environment's python.exe (usually "
        "...\\miniforge3\\envs\\geolens\\python.exe) and it is remembered.")

    def parameters(self):
        return [P("geolens python.exe (optional)", "python", "DEFile",
                  required=False),
                D("geolens version", "version", "GPString")]

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        given = ga.text(v["python"])
        if given:
            info = geolens_runner.probe(given)
            if not info.get("version") or info.get("missing"):
                ga.fail("That Python cannot run geolens: "
                        + (info.get("error") or "missing "
                           + ", ".join(info.get("missing") or [])))
            ga.remember_python(given)
        py, tried = geolens_runner.find_python(None, fresh=True)
        if not py:
            ga.find_python()
        ver = tried[py].get("version")
        arcpy.AddMessage("geolens %s found:\n  %s" % (ver, py))
        if tried[py].get("code"):
            arcpy.AddMessage("  code: %s" % tried[py]["code"])
        warn = geolens_runner.numba_warning(tried[py])
        if warn:
            arcpy.AddWarning(warn)
        v["version"].value = ver


class CheckMemory(_Tool):
    label = "Check memory before a run"
    category = "0 Setup"
    description = (
        "Reads only the DEM's header and estimates the peak memory of the "
        "whole routine on it, and of the gully step, against this machine's "
        "RAM. Run it first on an unfamiliar DEM: an out-of-memory run gives "
        "no message.")

    def parameters(self):
        return [P("DEM", "dem", "GPRasterLayer"),
                D("Verdict", "verdict", "GPString")]

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        dem = ga.raster_path(v["dem"], arcpy.env.scratchFolder, "dem")
        r = ga.run("plan_landscape_run", {"dem": dem}, arcpy.env.scratchFolder,
                   "checking memory")
        arcpy.AddMessage("%s cells at %.2f m, peak ~%.1f GB of %s GB available"
                         % ("{:,}".format(r["cells"]), r["cell_size"],
                            r["peak_gb"], r.get("available_gb")))
        for stage, gb in (r.get("per_stage_gb") or {}).items():
            arcpy.AddMessage("  %-44s %6.1f GB" % (stage, gb))
        arcpy.AddMessage("VERDICT: %s" % r["verdict"])
        for a in r.get("advice", []):
            arcpy.AddMessage("  - %s" % a)
        v["verdict"].value = r["verdict"]


# -- the whole routine -----------------------------------------------------------


class WholeRoutine(_Tool):
    label = "Run the whole routine"
    category = "1 Whole routine"
    description = (
        "Every step on one DEM: flow routing and HAND, valley bottom, reaches "
        "and confinement, valley form, alluvial fans and Rosgen valley types, "
        "Rosgen stream types with a plot of every channel section, and "
        "whether each reach's stream type is a stable form for its valley. "
        "Optionally gullies after that. Checks memory first. The same steps "
        "are also separate tools, for redoing one without the others.")

    def parameters(self):
        ps = [
            P("DEM (routing and valleys run on this)", "dem", "GPRasterLayer"),
            P("Output folder", "out_folder", "DEFolder"),
            P("Fine DEM for channel measurements", "dem_fine", "GPRasterLayer",
              required=False),
            P("Geology", "geology", "GPString", default=ga.GEOLOGY[0],
              choices=ga.GEOLOGY),
            P("Geology layer (with material_class)", "geology_layer",
              "GPFeatureLayer", required=False),
            P("Regional bankfull curve", "curve", "GPString",
              default=ga.CURVES[0][0], choices=[c[0] for c in ga.CURVES]),
            P("NHD flowlines to join", "nhd", "GPFeatureLayer",
              required=False, category=OPT),
            P("Stream threshold, km2 (0 = fit to the DEM)", "threshold",
              "GPDouble", default=0.0, category=OPT),
            P("Reach length, m", "segment", "GPDouble", default=500.0,
              category=OPT),
            P("Bankfull method", "method", "GPString",
              default=ga.METHODS[0][0], choices=[m[0] for m in ga.METHODS],
              category=OPT),
            P("Search for alluvial fans", "fans", "GPBoolean", default=True,
              category=OPT),
            P("Draw a plot of every stream-type section", "plots",
              "GPBoolean", default=True, category=OPT),
            P("Also find gullies (experimental)", "gullies", "GPBoolean",
              default=False, category=OPT),
            P("Gully DEM (lidar; default: the fine DEM)", "gully_dem",
              "GPRasterLayer", required=False, category=OPT),
            P("Run anyway if the memory check says it will not fit", "force",
              "GPBoolean", default=False, category=OPT),
        ] + _watershed_params()
        return ps + [D(lab, name) for lab, name in LAYERS] + [
            D("Watershed boundary", "out_watershed")]

    def updateParameters(self, parameters):
        v = ga.by_name(parameters)
        v["geology_layer"].enabled = ga.text(v["geology"]) == ga.GEOLOGY[2]
        v["gully_dem"].enabled = bool(v["gullies"].value)

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        out = ga.text(v["out_folder"])
        dem = _clip(v, ga.raster_path(v["dem"], out, "dem"), out)
        fine = ga.raster_path(v["dem_fine"], out, "dem_fine")
        force = bool(v["force"].value)
        ga.memory_check(dem, out, force)
        thr = v["threshold"].value or 0.0
        res = ga.run("landscape_pass", {
            "dem": dem, "dem_fine": fine,
            "nhd": ga.vector_path(v["nhd"], out, "nhd"),
            "geology": _geology(v, out),
            "regional_curve": ga.choice(v["curve"], ga.CURVES),
            "stream_threshold_km2": thr if thr > 0 else None,
            "segment_length_m": v["segment"].value or 500.0,
            "bankfull_method": ga.choice(v["method"], ga.METHODS),
            "fans": bool(v["fans"].value),
            "section_plots": bool(v["plots"].value),
        }, out, "whole routine")
        _set_pass_outputs(v, res)
        if v["gullies"].value:
            gdem = ga.raster_path(v["gully_dem"], out, "gully_dem") or fine or dem
            arcpy.AddMessage("gullies on %s" % gdem)
            ga.memory_check(gdem, out, force, stage="gullies")
            source = res.get("terrain") if gdem == dem else gdem
            g = ga.run("detect_gullies", {
                "terrain": source, "max_drainage_km2": 0.5,
                "stream_threshold_km2": 0.5, "exclude_streams": True,
            }, out, "gullies")
            ga.set_output(v, "out_gullies", g.get("gully_lines"))


# -- the steps -------------------------------------------------------------------


class Step1Terrain(_Tool):
    label = "1. Terrain, valley bottom and confinement"
    category = "2 Steps"
    description = (
        "The preprocessing, run once: pit filling and flow routing, streams, "
        "HAND, the valley bottom, reaches and their confinement. Every later "
        "step reads it from this output folder. The routing is also cached "
        "next to the DEM, so a later run on the same DEM skips it. Give the "
        "fine DEM here too if you have one: step 3 uses it.")

    def parameters(self):
        return [
            P("DEM (routing and valleys run on this)", "dem", "GPRasterLayer"),
            P("Output folder (the run folder)", "out_folder", "DEFolder"),
            P("Fine DEM for channel measurements (used by step 3)",
              "dem_fine", "GPRasterLayer", required=False),
            P("NHD flowlines to join", "nhd", "GPFeatureLayer",
              required=False, category=OPT),
            P("Stream threshold, km2 (0 = fit to the DEM)", "threshold",
              "GPDouble", default=0.0, category=OPT),
            P("Reach length, m", "segment", "GPDouble", default=500.0,
              category=OPT),
            P("Run anyway if the memory check says it will not fit", "force",
              "GPBoolean", default=False, category=OPT),
        ] + _watershed_params() + [
            D("Valley bottom", "out_valley_bottom"),
            D("Reaches (confinement)", "out_reaches"),
            D("Watershed boundary", "out_watershed"),
        ]

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        out = ga.text(v["out_folder"])
        dem = _clip(v, ga.raster_path(v["dem"], out, "dem"), out)
        ga.memory_check(dem, out, bool(v["force"].value))
        thr = v["threshold"].value or 0.0
        res = ga.run("landscape_pass", {
            "dem": dem, "dem_fine": ga.raster_path(v["dem_fine"], out, "dem_fine"),
            "nhd": ga.vector_path(v["nhd"], out, "nhd"),
            "stream_threshold_km2": thr if thr > 0 else None,
            "segment_length_m": v["segment"].value or 500.0,
            "valley_types": False, "stream_types": False,
        }, out, "terrain, valley bottom, confinement")
        _set_pass_outputs(v, res)


class Step1bCalibrate(_Tool):
    label = "1b. Calibrate confinement breaks"
    category = "2 Steps"
    description = (
        "The confinement classes hinge on two breaks in the confinement "
        "ratio, and the textbook ones (2 and 5 channel widths) rarely fit a "
        "landscape. Run this on a run folder with the breaks blank to see "
        "this landscape's percentiles and how its reaches divide under five "
        "candidate pairs; then run it again with the pair you choose. That "
        "writes the calibrated reaches and re-types the valleys from the "
        "run's existing measurements (nothing is re-measured); later steps "
        "use the calibrated reaches.")

    def parameters(self):
        return [
            P("Run folder", "run_folder", "DEFolder"),
            P("Confined at or below (blank: just show the candidates)",
              "confined_max", "GPDouble", required=False),
            P("Partly confined at or below", "partly_max", "GPDouble",
              required=False),
            P("Re-type valleys with the new breaks", "retype", "GPBoolean",
              default=True),
            D("Reaches (calibrated confinement)", "out_reaches"),
            D("Valley types", "out_valley_types"),
            D("Stable / unstable form", "out_stability"),
        ]

    def updateMessages(self, parameters):
        _Tool.updateMessages(self, parameters)
        v = ga.by_name(parameters)
        c, pm = v["confined_max"].value, v["partly_max"].value
        if (c is None) != (pm is None):
            v["partly_max"].setErrorMessage("Give both breaks, or neither to "
                                            "see the candidates.")
        elif c is not None and c >= pm:
            v["partly_max"].setErrorMessage("Must be above the confined break.")

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        run = ga.text(v["run_folder"])
        res = ga.run("calibrate_confinement_for_run", {
            "run_dir": run, "confined_max": v["confined_max"].value,
            "partly_confined_max": v["partly_max"].value,
            "retype_valleys": bool(v["retype"].value)}, run, "confinement")
        sw = res.get("sweep") or {}
        arcpy.AddMessage("confinement ratio percentiles in this landscape:")
        for k, val in (sw.get("confinement_ratio_percentiles") or {}).items():
            arcpy.AddMessage("  p%-4s %8.2f" % (k, val))
        arcpy.AddMessage("how the reaches divide (share of length):")
        for c in sw.get("candidates") or []:
            f = c.get("fraction_km") or {}
            arcpy.AddMessage(
                "  confined <= %6.2f  partly <= %6.2f :  confined %3.0f%%  "
                "partly %3.0f%%  unconfined %3.0f%%"
                % (c["confined_max"], c["partly_confined_max"],
                   100 * f.get("confined", 0), 100 * f.get("partly confined", 0),
                   100 * f.get("unconfined", 0)))
        if res.get("reclassified"):
            ga.set_output(v, "out_reaches", ga.dig(res, "reclassified", "segments"))
            ga.set_output(v, "out_valley_types", ga.dig(res, "valley_types", "segments"))
            ga.set_output(v, "out_stability", ga.dig(res, "stability", "stability"))
        else:
            arcpy.AddMessage("Pick a pair and run again with both breaks.")


class Step2ValleyTypes(_Tool):
    label = "2. Valley form, alluvial fans and valley types"
    category = "2 Steps"
    description = (
        "For a run folder from step 1 (or the whole routine): cross-sections "
        "floor to divide, the alluvial fan search, Rosgen valley types and "
        "the valley as an area. Replaces the run's valley folders, and "
        "re-judges stable/unstable form if the run has stream types. Nothing "
        "is routed again.")

    def parameters(self):
        return [
            P("Run folder", "run_folder", "DEFolder"),
            P("Geology", "geology", "GPString", default=ga.GEOLOGY[0],
              choices=ga.GEOLOGY),
            P("Geology layer (with material_class)", "geology_layer",
              "GPFeatureLayer", required=False),
            P("Search for alluvial fans", "fans", "GPBoolean", default=True),
            D("Valley types", "out_valley_types"),
            D("Valley extent", "out_valley_extent"),
            D("Alluvial fans", "out_fans"),
            D("Fan candidates", "out_fan_candidates"),
            D("Stable / unstable form", "out_stability"),
        ]

    def updateParameters(self, parameters):
        v = ga.by_name(parameters)
        v["geology_layer"].enabled = ga.text(v["geology"]) == ga.GEOLOGY[2]

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        run = ga.text(v["run_folder"])
        res = ga.run("valley_types_for_run", {
            "run_dir": run, "geology": _geology(v, run),
            "fans": bool(v["fans"].value)}, run, "valley types")
        _set_pass_outputs(v, res)


class Step3StreamTypes(_Tool):
    label = "3. Stream types"
    category = "2 Steps"
    description = (
        "Rosgen stream types for a run folder: five channel sections a reach "
        "on the fine DEM, bankfull at the regional curve's depth, width/depth, "
        "entrenchment, slope and sinuosity, a plot of every section, then "
        "stable/unstable form against the run's valley types. Nothing is "
        "routed again. The DEM and fine DEM default to the ones step 1 was "
        "given. Keep the default name to replace the run's stream types, or "
        "give another (stream_types_v2) to keep them.")

    def parameters(self):
        return [
            P("Run folder", "run_folder", "DEFolder"),
            P("Fine DEM (default: the run's)", "dem_fine", "GPRasterLayer",
              required=False),
            P("Regional bankfull curve", "curve", "GPString",
              default=ga.CURVES[0][0], choices=[c[0] for c in ga.CURVES]),
            P("Bankfull method", "method", "GPString",
              default=ga.METHODS[0][0], choices=[m[0] for m in ga.METHODS]),
            P("Draw a plot of every section", "plots", "GPBoolean",
              default=True),
            P("Output name", "name", "GPString", default="stream_types",
              category=OPT),
            D("Stream types", "out_stream_types"),
            D("Stream-type sections", "out_sections"),
            D("Stable / unstable form", "out_stability"),
        ]

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        run = ga.text(v["run_folder"])
        res = ga.run("stream_types_for_run", {
            "run_dir": run,
            "dem_fine": ga.raster_path(v["dem_fine"], run, "dem_fine"),
            "regional_curve": ga.choice(v["curve"], ga.CURVES),
            "bankfull_method": ga.choice(v["method"], ga.METHODS),
            "section_plots": bool(v["plots"].value),
            "out_prefix": ga.text(v["name"]) or "stream_types",
        }, run, "stream types")
        _set_pass_outputs(v, res)


class Step4Stability(_Tool):
    label = "4. Stable or unstable form"
    category = "2 Steps"
    description = (
        "Joins a run's valley types and stream types and judges each reach: "
        "stable (a stream type Rosgen associates with the valley type), "
        "unstable (one he lists for it only out of balance), check (not "
        "associated at all: a departure, or a wrong type) or unknown. Source: "
        "NRCS NEH 654 TS3E Table TS3E-3, after Rosgen (1996). To judge with "
        "your own table, edit a copy of docs\\valley_stream_associations.csv "
        "and give it as the association table: the run keeps it, so later "
        "steps use it too.")

    def parameters(self):
        return [
            P("Valley types (valley_types.gpkg)", "valley", "GPFeatureLayer"),
            P("Stream types (stream_types.gpkg)", "stream", "GPFeatureLayer"),
            P("Output folder", "out_folder", "DEFolder"),
            P("Association table (CSV, optional)", "table", "DEFile",
              required=False),
            P("Go back to the built-in table", "builtin", "GPBoolean",
              required=False, default=False),
            D("Stable / unstable form", "out_stability"),
        ]

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        out = ga.text(v["out_folder"])
        res = ga.run("stream_stability", {
            "valley_types": ga.vector_path(v["valley"], out, "valley_types"),
            "stream_types": ga.vector_path(v["stream"], out, "stream_types"),
            "association_table": ("builtin" if v["builtin"].value
                                  else ga.text(v["table"]) or None),
        }, out, "stable or unstable form")
        arcpy.AddMessage("judged with: %s" % res.get("source"))
        arcpy.AddMessage("reaches by form: %s" % res.get("counts"))
        ga.set_output(v, "out_stability", res.get("stability"))


class Step5Gullies(_Tool):
    label = "5. Gullies (experimental)"
    category = "2 Steps"
    description = (
        "Gully centrelines from lidar: roughness, topographic position and "
        "slope, below a drainage-area ceiling so channels are left out. Needs "
        "a lidar DEM (2 m or finer); on a 10 m DEM the results are slope "
        "noise. Widths, depths and volumes are unreliable in steep mountain "
        "terrain. Checks memory first: about 40 bytes a cell.")

    def parameters(self):
        return [
            P("Lidar DEM", "dem", "GPRasterLayer"),
            P("Output folder", "out_folder", "DEFolder"),
            P("Largest drainage a gully can have, km2", "max_km2", "GPDouble",
              default=0.5),
            P("Also build footprint polygons", "polygons", "GPBoolean",
              default=False, category=OPT),
            P("Run anyway if the memory check says it will not fit", "force",
              "GPBoolean", default=False, category=OPT),
            D("Gullies", "out_gullies"),
            D("Gully footprints", "out_gully_polygons"),
        ]

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        out = ga.text(v["out_folder"])
        dem = ga.raster_path(v["dem"], out, "dem")
        ga.memory_check(dem, out, bool(v["force"].value), stage="gullies")
        km2 = v["max_km2"].value or 0.5
        res = ga.run("detect_gullies", {
            "terrain": dem, "max_drainage_km2": km2,
            "stream_threshold_km2": km2, "exclude_streams": True,
            "polygons": bool(v["polygons"].value)}, out, "gullies")
        arcpy.AddMessage("%s gullies, %.1f km" % (res.get("gully_count"),
                                                  res.get("total_length_km") or 0))
        ga.set_output(v, "out_gullies", res.get("gully_lines"))
        ga.set_output(v, "out_gully_polygons", res.get("gully_polygons"))


class Step6Riparian(_Tool):
    label = "6. Riparian band from NAIP imagery"
    category = "2 Steps"
    description = (
        "For a run folder: NAIP imagery over the run's DEM, scene by scene, "
        "saved as NDVI and imagery tiles with an NDVI and a true-colour "
        "mosaic in the NAIP folder you choose (default: the run's naip "
        "folder; a folder already holding the tiles is reused). Then the "
        "riparian band: NDVI in excess of the local upland background, "
        "limited to ground near a channel by the run's HAND. Needs internet "
        "for new imagery. Patches that look like irrigated fields are "
        "flagged, not removed: nothing in NDVI separates alfalfa from "
        "cottonwood.")

    def parameters(self):
        return [
            P("Run folder", "run_folder", "DEFolder"),
            P("NAIP folder (tiles and mosaics; default: run\\naip)",
              "naip_folder", "DEFolder", required=False),
            P("Imagery from (YYYY-MM-DD; blank = newest)", "start", "GPString",
              required=False, category=OPT),
            P("Imagery to (YYYY-MM-DD)", "end", "GPString", required=False,
              category=OPT),
            P("NDVI cell size, m", "resolution", "GPDouble", default=1.0,
              category=OPT),
            P("NDVI above the background to count as riparian", "min_excess",
              "GPDouble", default=0.12, category=OPT),
            P("Highest above the nearest channel (HAND), m", "max_hand",
              "GPDouble", default=15.0, category=OPT),
            D("Riparian band", "out_riparian"),
            D("NDVI", "out_ndvi", "DERasterDataset"),
            D("True colour", "out_rgb", "DERasterDataset"),
        ]

    def updateMessages(self, parameters):
        _Tool.updateMessages(self, parameters)
        ga.new_folder_ok(ga.by_name(parameters)["naip_folder"])

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        run = ga.text(v["run_folder"])
        naip = ga.text(v["naip_folder"]) or os.path.join(run, "naip")
        img = ga.run("naip_tiles", {
            "aoi": _run_dem(run), "start": ga.text(v["start"]),
            "end": ga.text(v["end"]),
            "resolution_m": v["resolution"].value or 1.0}, naip,
            "NAIP scenes")
        if not img.get("ndvi"):
            ga.fail("No NAIP imagery covered the run; see the warnings.")
        res = ga.run("riparian_for_run", {
            "run_dir": run, "ndvi": img["ndvi"],
            "min_excess": v["min_excess"].value or 0.12,
            "max_hand_m": v["max_hand"].value,
        }, run, "riparian band")
        rip = res.get("riparian") or {}
        arcpy.AddMessage("%s riparian patches" % rip.get("n_patches"))
        ga.set_output(v, "out_riparian", rip.get("band_polygons"))
        for name, key in (("out_ndvi", "ndvi"), ("out_rgb", "rgb")):
            if img.get(key) and os.path.isfile(img[key]):
                v[name].value = img[key]


def _run_dem(run):
    """The DEM an earlier run was made on, from its run record."""
    import glob
    import json

    recs = sorted(glob.glob(os.path.join(run, "runs", "*_landscape_pass.json"))
                  + glob.glob(os.path.join(run, "runs", "*_watershed_pass.json")),
                  key=os.path.basename, reverse=True)
    for rec in recs:
        try:
            with open(rec, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:                                    # noqa: BLE001
            continue
        dem = (d.get("arguments") or {}).get("dem")
        if dem:
            return dem
    ga.fail("The run folder has no landscape pass record to find its DEM in.")


class FetchDEM(_Tool):
    label = "Fetch a DEM (USGS 3DEP)"
    category = "0 Get data"
    description = (
        "Downloads USGS 3DEP elevation for an area into the folder you "
        "choose: 1 m lidar where a lidar project covers it, the 1/3 "
        "arc-second (about 10 m) DEM where none does (the tool says how "
        "much), or either one alone. Only the part of each tile over the "
        "area is fetched. Each tile is saved, clipped, in dem_tiles, and "
        "mosaicked into dem_mosaic.tif with the newest lidar on top. The "
        "output is in the lidar's own UTM zone (metres) unless you choose a "
        "coordinate system; on a foot coordinate system the elevations are "
        "converted to feet too.")

    def parameters(self):
        return [
            P("Area (polygon layer)", "aoi", "GPFeatureLayer"),
            P("Output folder", "out_folder", "DEFolder"),
            P("Source", "source", "GPString", default="Best: 1 m lidar, 1/3 "
              "arc-second where there is none",
              choices=["Best: 1 m lidar, 1/3 arc-second where there is none",
                       "1 m lidar only", "1/3 arc-second only"]),
            P("Buffer around the area, m", "buffer", "GPDouble",
              default=0.0),
            P("Cell size, m (blank: 1 m, or 10 m for 1/3 arc-second)",
              "cell", "GPDouble", required=False, category=OPT),
            P("Output coordinate system (blank: the lidar's UTM zone)", "crs",
              "GPSpatialReference", required=False, category=OPT),
            D("DEM", "out_dem", "DERasterDataset"),
        ]

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        out = ga.text(v["out_folder"])
        src = ga.text(v["source"]) or ""
        source = ("1m" if src.startswith("1 m") else
                  "1/3" if src.startswith("1/3") else "best")
        res = ga.run("fetch_dem", {
            "aoi": ga.vector_path(v["aoi"], out, "dem_area"),
            "source": source, "resolution_m": v["cell"].value,
            "crs": ga.spatial_reference(v["crs"]),
            "buffer_m": v["buffer"].value or 0.0}, out, "fetching the DEM")
        arcpy.AddMessage("%s tile(s), %.0f%% lidar, %s m cells, %s"
                         % (res.get("n_tiles"),
                            100 * (res.get("lidar_fraction") or 0),
                            res.get("cell_size_m"), res.get("crs")))
        v["out_dem"].value = res.get("dem")


class NaipImagery(_Tool):
    label = "NAIP imagery and NDVI"
    category = "0 Get data"
    description = (
        "NAIP imagery over an area (a DEM's extent or a polygon layer), "
        "scene by scene, into the folder you choose: an NDVI tile and an "
        "imagery tile (red, green, blue, near-infrared) per scene, and an "
        "NDVI mosaic and a true-colour mosaic of all of them, newest imagery "
        "on top. Older scenes are skipped where newer imagery covers the "
        "area. A folder that already holds the tiles is reused.")

    def parameters(self):
        return [
            P("Area: a DEM", "aoi_raster", "GPRasterLayer", required=False),
            P("Area: or a polygon layer", "aoi_polygon", "GPFeatureLayer",
              required=False),
            P("Output folder", "out_folder", "DEFolder"),
            P("Imagery from (YYYY-MM-DD; blank = newest)", "start", "GPString",
              required=False, category=OPT),
            P("Imagery to (YYYY-MM-DD)", "end", "GPString", required=False,
              category=OPT),
            P("Cell size, m", "resolution", "GPDouble", default=1.0,
              category=OPT),
            D("NDVI mosaic", "out_ndvi", "DERasterDataset"),
            D("True colour mosaic", "out_rgb", "DERasterDataset"),
        ]

    def updateMessages(self, parameters):
        _Tool.updateMessages(self, parameters)
        v = ga.by_name(parameters)
        if v["aoi_raster"].value is not None and v["aoi_polygon"].value is not None:
            v["aoi_polygon"].setErrorMessage("Give a DEM or a polygon layer, "
                                             "not both.")

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        out = ga.text(v["out_folder"])
        aoi = (ga.raster_path(v["aoi_raster"], out, "naip_area")
               or ga.vector_path(v["aoi_polygon"], out, "naip_area"))
        if not aoi:
            ga.fail("Give a DEM or a polygon layer for the area.")
        res = ga.run("naip_tiles", {
            "aoi": aoi, "start": ga.text(v["start"]), "end": ga.text(v["end"]),
            "resolution_m": v["resolution"].value or 1.0}, out, "NAIP scenes")
        arcpy.AddMessage("%s scene(s), years %s, %.0f%% of the area"
                         % (res.get("n_used"), res.get("years"),
                            100 * (res.get("coverage") or 0)))
        for name, key in (("out_ndvi", "ndvi"), ("out_rgb", "rgb")):
            if res.get(key) and os.path.isfile(res[key]):
                v[name].value = res[key]


class Sentinel1Radar(_Tool):
    label = "Sentinel-1 radar statistics (OPERA RTC)"
    category = "0 Get data"
    description = (
        "Sentinel-1 radar backscatter over an area, from NASA's OPERA RTC-S1 "
        "(30 m, terrain-corrected, from 2016), for telling riparian woodland "
        "from irrigated fields, which NDVI cannot. Saves a VV/VH tile per "
        "satellite pass in sar_tiles (reused on a rerun) and writes "
        "sar_stats.tif: median VH and VV (dB), VH/VV (dB), how much VH "
        "varies through the period (coefficient of variation) and the "
        "number of passes. Give polygons (the riparian band, fields you "
        "digitised) to get the same numbers per polygon. Needs your NASA "
        "Earthdata login: run 0 Setup > Save Earthdata login once.")

    def parameters(self):
        return [
            P("Area: a DEM", "aoi_raster", "GPRasterLayer", required=False),
            P("Area: or a polygon layer", "aoi_polygon", "GPFeatureLayer",
              required=False),
            P("Output folder", "out_folder", "DEFolder"),
            P("From (YYYY-MM-DD; blank = a year before the end)", "start",
              "GPString", required=False),
            P("To (YYYY-MM-DD; blank = today)", "end", "GPString",
              required=False),
            P("Polygons to summarise (optional)", "polygons",
              "GPFeatureLayer", required=False),
            P("Fewest passes for a value", "min_passes", "GPLong",
              default=8, category=OPT),
            D("Radar statistics", "out_stats", "DERasterDataset"),
            D("Radar statistics per polygon", "out_zonal"),
        ]

    def updateMessages(self, parameters):
        _Tool.updateMessages(self, parameters)
        v = ga.by_name(parameters)
        if v["aoi_raster"].value is not None and v["aoi_polygon"].value is not None:
            v["aoi_polygon"].setErrorMessage("Give a DEM or a polygon layer, "
                                             "not both.")

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        out = ga.text(v["out_folder"])
        aoi = (ga.raster_path(v["aoi_raster"], out, "sar_area")
               or ga.vector_path(v["aoi_polygon"], out, "sar_area"))
        if not aoi:
            ga.fail("Give a DEM or a polygon layer for the area.")
        res = ga.run("sentinel1_stats", {
            "aoi": aoi, "start": ga.text(v["start"]) or None,
            "end": ga.text(v["end"]) or None,
            "polygons": ga.vector_path(v["polygons"], out, "sar_polygons"),
            "min_passes": int(v["min_passes"].value or 8)},
            out, "Sentinel-1 passes")
        arcpy.AddMessage("%s to %s; passes per track: %s (%s reused)"
                         % (res.get("start"), res.get("end"),
                            res.get("passes"), res.get("reused")))
        if res.get("stats") and os.path.isfile(res["stats"]):
            v["out_stats"].value = res["stats"]
        ga.set_output(v, "out_zonal", res.get("zonal"))


class SsurgoSoils(_Tool):
    label = "SSURGO soils"
    category = "0 Get data"
    description = (
        "USDA SSURGO soil map units over an area (a DEM's extent or a "
        "polygon layer), from Soil Data Access, clipped to it. Each polygon "
        "has material_class (alluvial, mass movement, residual, eolian...) "
        "from its main soil's parent material, so the layer can be given to "
        "step 2 as the geology layer; or pick 'Download USDA SSURGO soils' "
        "there. SSURGO is mapped at about 1:24,000, so a narrow valley's own "
        "alluvium usually shows, where the national geologic map often "
        "gives the hillslope bedrock. Also: soil name, landform, flooding "
        "frequency, drainage class, hydrologic group, depth to bedrock.")

    def parameters(self):
        return [
            P("Area: a DEM", "aoi_raster", "GPRasterLayer", required=False),
            P("Area: or a polygon layer", "aoi_polygon", "GPFeatureLayer",
              required=False),
            P("Output folder", "out_folder", "DEFolder"),
            P("Buffer around the area, m", "buffer", "GPDouble", default=0.0,
              category=OPT),
            D("Soils (SSURGO)", "out_soils"),
        ]

    def updateMessages(self, parameters):
        _Tool.updateMessages(self, parameters)
        v = ga.by_name(parameters)
        if v["aoi_raster"].value is not None and v["aoi_polygon"].value is not None:
            v["aoi_polygon"].setErrorMessage("Give a DEM or a polygon layer, "
                                             "not both.")

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        out = ga.text(v["out_folder"])
        aoi = (ga.raster_path(v["aoi_raster"], out, "soils_area")
               or ga.vector_path(v["aoi_polygon"], out, "soils_area"))
        if not aoi:
            ga.fail("Give a DEM or a polygon layer for the area.")
        res = ga.run("fetch_soils", {"aoi": aoi,
                                     "buffer_m": v["buffer"].value or 0.0},
                     out, "SSURGO soils")
        arcpy.AddMessage("%s polygons, %s map units; km2 by material: %s"
                         % (res.get("polygons"), res.get("map_units"),
                            res.get("km2_by_material")))
        ga.set_output(v, "out_soils", res.get("soils"))


class CheckServices(_Tool):
    label = "Check internet services"
    category = "0 Setup"
    description = (
        "Tries each service geolens downloads from once: USGS 3DEP and the "
        "Watershed Boundary Dataset, the national geologic map, USDA soils, "
        "NAIP on the Planetary Computer, NASA's Sentinel-1 catalog, and "
        "whether an Earthdata login is saved. A few seconds. Run it before a "
        "long run, or when a download fails, to see which service this "
        "computer cannot reach.")

    def parameters(self):
        return [D("All reachable", "all_ok", "GPBoolean")]

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        res = ga.run("check_services", {}, arcpy.env.scratchFolder,
                     "checking services")
        for r in res.get("services", []):
            line = "%-44s %-4s %5.1fs  %s" % (
                r["service"], "ok" if r["ok"] else "FAIL", r["seconds"],
                r["detail"])
            if r["ok"]:
                arcpy.AddMessage(line)
            else:
                arcpy.AddWarning(line + "   (used by: %s)" % r["used_by"])
        v["all_ok"].value = bool(res.get("ok"))


class RunProject(_Tool):
    label = "Run a project area (by HUC12)"
    category = "1 Whole routine"
    description = (
        "Any area, any size: a BLM field office, a ranch, a district. Level "
        "A runs the whole area at 10 m (USGS 3DEP), widened to every HUC12 "
        "watershed it touches and those draining into them, so no stream is "
        "cut off at the boundary: valley bottoms, confinement and valley "
        "types everywhere. Level B then runs one HUC12 at a time on 1 m "
        "lidar where 3DEP has it: stream types, section plots and stable / "
        "unstable form. A pilot either represents the whole area (HUC12s "
        "grouped by valley types and soils; each group's most typical one "
        "runs) or goes where the valleys are most unconfined. A HUC12 "
        "without lidar is reported as valleys only. "
        "Merged layers, a HUC12 status layer and RESULTS.md (what ran at "
        "what resolution, and every warning) go in the project folder. "
        "Run it again on the same folder to carry on where it stopped.")

    LEVEL_B = [("Pilot: HUC12s that together represent the area "
                "(landscape projects)", "pilot:representative"),
               ("Pilot: the HUC12s with the most unconfined valley "
                "(stream projects)", "pilot:unconfined"),
               ("Every HUC12 with lidar", "all"),
               ("Only the HUC12s listed below", "listed"),
               ("None: valleys only", "none")]
    CURVES = ga.CURVES

    def parameters(self):
        return [
            P("Boundary (polygon layer)", "boundary", "GPFeatureLayer",
              required=False),
            P("or a map service layer URL", "url", "GPString",
              required=False),
            P("Which features of it (where clause)", "where", "GPString",
              required=False),
            P("Project folder", "out_folder", "DEFolder"),
            P("Level B (stream types on lidar)", "level_b", "GPString",
              default=self.LEVEL_B[0][0], choices=[c[0] for c in self.LEVEL_B]),
            P("Pilot HUC12s", "pilot", "GPLong", default=3),
            P("HUC12 codes, comma-separated (for 'listed')", "huc12s",
              "GPString", required=False),
            P("Geology", "geology", "GPString", default=ga.GEOLOGY[0],
              choices=[ga.GEOLOGY[0], ga.GEOLOGY[3], ga.GEOLOGY[1]]),
            P("Regional bankfull curve", "curve", "GPString",
              default=self.CURVES[0][0], choices=[c[0] for c in self.CURVES]),
            P("Stream threshold, km2", "threshold", "GPDouble", default=1.0,
              category=OPT),
            P("Stop level B after this many hours (0 = no limit)", "hours",
              "GPDouble", default=0.0, category=OPT),
            P("Section plots", "plots", "GPBoolean", default=True,
              category=OPT),
            P("SSURGO soils over the boundary", "soils", "GPBoolean",
              default=True, category=OPT),
            D("Valley types", "out_valley_types"),
            D("Stream types", "out_stream_types"),
            D("Stream-type sections", "out_sections"),
            D("Stable / unstable form", "out_stability"),
            D("HUC12 status", "out_huc12"),
            D("Soils (SSURGO)", "out_soils"),
        ]

    def updateMessages(self, parameters):
        _Tool.updateMessages(self, parameters)
        v = ga.by_name(parameters)
        if v["boundary"].value is None and not ga.text(v["url"]):
            v["url"].setWarningMessage("Give a boundary layer or a map "
                                       "service layer URL.")

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        out = ga.text(v["out_folder"])
        boundary = (ga.vector_path(v["boundary"], out, "boundary")
                    or ga.text(v["url"]))
        if not boundary:
            ga.fail("Give a boundary layer or a map service layer URL.")
        geo = ga.text(v["geology"])
        geology = ("ssurgo" if geo == ga.GEOLOGY[3]
                   else None if geo == ga.GEOLOGY[1] else "auto")
        level_b, _, pilot_by = ga.choice(v["level_b"],
                                         self.LEVEL_B).partition(":")
        res = ga.run("run_project", {
            "boundary": boundary, "where": ga.text(v["where"]) or None,
            "level_b": level_b,
            "pilot_count": int(v["pilot"].value or 3),
            "pilot_by": pilot_by or "representative",
            "huc12s": ga.text(v["huc12s"]) or None,
            "stream_threshold_km2": v["threshold"].value or 1.0,
            "geology": geology,
            "regional_curve": ga.choice(v["curve"], self.CURVES),
            "section_plots": bool(v["plots"].value),
            "level_b_hours": v["hours"].value or None,
            "soils": bool(v["soils"].value),
        }, out, "project")
        arcpy.AddMessage("level B: %s" % res.get("level_b"))
        arcpy.AddMessage("results: %s" % os.path.join(out, "RESULTS.md"))
        for name, key in (("out_valley_types", "valley_types"),
                          ("out_stream_types", "stream_types"),
                          ("out_sections", "sections"),
                          ("out_stability", "stability"),
                          ("out_huc12", "huc12_status"),
                          ("out_soils", "soils")):
            ga.set_output(v, name, res.get(key))


class SaveEarthdataLogin(_Tool):
    label = "Save Earthdata login"
    category = "0 Setup"
    description = (
        "Saves your NASA Earthdata login (free, from "
        "https://urs.earthdata.nasa.gov) for the Sentinel-1 tool. It goes in "
        "your own Windows profile (the _netrc file NASA's tools read), not "
        "in geolens, so it is never shared with geolens. Each person saves "
        "their own. Run it again to change it.")

    def parameters(self):
        return [P("Earthdata username", "username", "GPString"),
                P("Earthdata password", "password", "GPEncryptedString"),
                D("Saved in", "saved", "GPString")]

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        user = ga.text(v["username"])
        try:
            path = geolens_runner.save_earthdata_login(user,
                                                       ga.text(v["password"]))
        except ValueError as exc:
            ga.fail(str(exc))
        arcpy.AddMessage("Saved the Earthdata login for %s in %s" % (user, path))
        v["saved"].value = path


# -- more ------------------------------------------------------------------------


class CoarsenDEM(_Tool):
    label = "Coarsen a DEM"
    category = "0 Setup"
    description = (
        "An averaged copy of a DEM at a coarser cell size, in metres whatever "
        "the DEM's units. The answer to a DEM too big for memory: route and "
        "map valleys on the coarse copy, and give the lidar as the fine DEM "
        "so channels are still measured at full resolution. Leave the cell "
        "size at 0 to use the size the memory check suggests.")

    def parameters(self):
        return [
            P("DEM", "dem", "GPRasterLayer"),
            P("Output folder", "out_folder", "DEFolder"),
            P("Cell size, m (0 = what the memory check suggests)", "cell",
              "GPDouble", default=0.0),
            D("Coarse DEM", "out_dem", "DERasterDataset"),
        ]

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        out = ga.text(v["out_folder"])
        dem = ga.raster_path(v["dem"], out, "dem")
        cell = v["cell"].value or 0.0
        if cell <= 0:
            plan = ga.run("plan_landscape_run", {"dem": dem}, out,
                          "checking memory")
            cell = (plan.get("suggested") or {}).get("resample_to_cell_m")
            if not cell:
                arcpy.AddMessage("This DEM fits in memory as it is: nothing to "
                                 "do. Give a cell size to coarsen anyway.")
                return
            arcpy.AddMessage("the memory check suggests %.1f m cells" % cell)
        res = ga.run("resample_dem", {"dem": dem, "target_cell_m": cell}, out,
                     "coarsening")
        arcpy.AddMessage("%s x %s cells at %s m" % (res.get("width"),
                                                   res.get("height"), cell))
        v["out_dem"].value = res.get("path")


class WatershedClip(_Tool):
    label = "Clip a DEM to one watershed"
    category = "3 More tools"
    description = (
        "Clips a DEM to one complete watershed, from a pour point (snapped to "
        "the largest drainage within the snap distance), a boundary polygon, "
        "or a HUC12 code, so nothing downstream carries a truncated inflow. "
        "The whole routine and step 1 can do this themselves.")

    def parameters(self):
        ps = [P("DEM", "dem", "GPRasterLayer"),
              P("Output folder", "out_folder", "DEFolder")]
        for p in _watershed_params():
            p.category = None
            ps.append(p)
        ps.append(P("Snap the pour point within, m", "snap", "GPDouble",
                    default=150.0))
        return ps + [D("Watershed boundary", "out_watershed"),
                     D("Watershed DEM", "out_dem", "DERasterDataset")]

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        out = ga.text(v["out_folder"])
        dem = ga.raster_path(v["dem"], out, "dem")
        args = {"dem": dem,
                "pour_point": ga.vector_path(v["pour_point"], out, "pour_point"),
                "boundary": ga.vector_path(v["boundary"], out, "boundary"),
                "huc": ga.text(v["huc"]),
                "buffer_m": v["ws_buffer"].value or 0.0,
                "snap_radius_m": v["snap"].value or 150.0}
        if not (args["pour_point"] or args["boundary"] or args["huc"]):
            ga.fail("Give a pour point, a boundary polygon or a HUC12 code.")
        ws = ga.run("watershed", args, out, "clipping to the watershed")
        arcpy.AddMessage("watershed: %.1f km2" % (ws.get("area_km2") or 0))
        ga.set_output(v, "out_watershed", ws.get("boundary"))
        v["out_dem"].value = ws.get("dem")


class Headcuts(_Tool):
    label = "Headcuts (knickpoints)"
    category = "3 More tools"
    description = (
        "Finds headcuts: convex breaks in a channel's long profile where the "
        "bed drops more than the reach around it explains. Give the gully "
        "lines from the gully step to look only along gullies; without them "
        "you get every convexity in the network. Needs lidar and the "
        "topotoolbox package (the installer adds it).")

    def parameters(self):
        return [
            P("Lidar DEM", "dem", "GPRasterLayer"),
            P("Output folder", "out_folder", "DEFolder"),
            P("Gully lines (from the gully step)", "lines", "GPFeatureLayer",
              required=False),
            P("Smallest drainage searched, km2", "threshold", "GPDouble",
              default=0.005, category=OPT),
            P("Smallest drop beyond the local gradient, m", "min_drop",
              "GPDouble", default=0.8, category=OPT),
            D("Headcuts", "out_headcuts"),
        ]

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        out = ga.text(v["out_folder"])
        res = ga.run("detect_headcuts", {
            "dem": ga.raster_path(v["dem"], out, "dem"),
            "lines": ga.vector_path(v["lines"], out, "gully_lines"),
            "stream_threshold_km2": v["threshold"].value or 0.005,
            "min_excess_drop_m": v["min_drop"].value or 0.8,
        }, out, "headcuts")
        arcpy.AddMessage("%s headcuts" % res.get("headcut_count"))
        ga.set_output(v, "out_headcuts", res.get("headcuts"))


class AlluvialFans(_Tool):
    label = "Alluvial fans"
    category = "3 More tools"
    description = (
        "Searches any DEM for alluvial fans: an apex where a channel's "
        "gradient drops as it leaves its canyon, a cone grown down from it "
        "within a fan slope band, kept only if it is cone-shaped. Every "
        "candidate, kept or not, is in the candidates layer with the reason. "
        "Give a run folder to re-type its valleys with these fans (written "
        "beside the run's own valley types). DEMs over 60 million cells are "
        "averaged to a coarser copy first.")

    def parameters(self):
        return [
            P("DEM", "dem", "GPRasterLayer"),
            P("Output folder", "out_folder", "DEFolder"),
            P("Run folder to re-type with these fans", "run_folder",
              "DEFolder", required=False),
            P("Smallest drainage that builds a fan, km2", "min_km2",
              "GPDouble", default=0.5, category=OPT),
            P("Fan slope band, lower", "slope_min", "GPDouble", default=0.02,
              category=OPT),
            P("Fan slope band, upper", "slope_max", "GPDouble", default=0.15,
              category=OPT),
            P("Smallest cone angle at the apex, degrees", "sector",
              "GPDouble", default=60.0, category=OPT),
            D("Alluvial fans", "out_fans"),
            D("Fan candidates", "out_fan_candidates"),
            D("Valley types (with fans)", "out_valley_types"),
            D("Stable / unstable form (with fans)", "out_stability"),
        ]

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        out = ga.text(v["out_folder"])
        res = ga.run("detect_alluvial_fans", {
            "dem": ga.raster_path(v["dem"], out, "dem"),
            "min_drainage_km2": v["min_km2"].value or 0.5,
            "fan_slope_min": v["slope_min"].value or 0.02,
            "fan_slope_max": v["slope_max"].value or 0.15,
            "min_sector_deg": v["sector"].value or 60.0,
        }, out, "alluvial fans")
        arcpy.AddMessage("%s fans (%.2f km2); %s candidate(s) failed the cone "
                         "test" % (res.get("fan_count"),
                                   res.get("total_area_km2") or 0,
                                   res.get("rejected_count")))
        ga.set_output(v, "out_fans", res.get("fans"))
        ga.set_output(v, "out_fan_candidates", res.get("candidates"))
        run = ga.text(v["run_folder"])
        if run:
            if not res.get("fan_count"):
                arcpy.AddWarning("No fans passed the cone test, so the run's "
                                 "valley types are unchanged.")
                return
            r2 = ga.run("retype_valleys_with_fans", {
                "run_dir": run, "fans": res.get("fans")}, run,
                "valley types with fans")
            ga.set_output(v, "out_valley_types",
                          ga.dig(r2, "valley_types", "segments"))
            ga.set_output(v, "out_stability", ga.dig(r2, "stability", "stability"))


class SectionPlots(_Tool):
    label = "Section plots for an earlier run"
    category = "3 More tools"
    description = (
        "Point at a run's stream types folder (stream_types_dir). Draws every "
        "section and a sheet per reach and links them to the map: click a "
        "feature with the Explore tool to see its plot. No DEM needed.")

    def parameters(self):
        return [
            P("Stream types folder (stream_types_dir)", "folder", "DEFolder"),
            D("Stream-type sections", "out_sections"),
            D("Stream types", "out_stream_types"),
        ]

    def updateMessages(self, parameters):
        v = ga.by_name(parameters)
        f = v["folder"]
        if f.value is not None and not os.path.exists(
                os.path.join(f.valueAsText, "stream_type_profiles.csv")):
            f.setErrorMessage("No stream_type_profiles.csv here: pick the "
                              "stream_types_dir of a run.")

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        folder = ga.text(v["folder"])
        res = ga.run("plot_stream_sections", {"stream_types_dir": folder},
                     folder, "section plots")
        arcpy.AddMessage("%s section plots, %s reach sheets"
                         % (res.get("section_plots"), res.get("reach_sheets")))
        ga.set_output(v, "out_sections",
                      os.path.join(folder, "stream_type_sections.gpkg"))
        ga.set_output(v, "out_stream_types",
                      os.path.join(folder, "stream_types.gpkg"))


class CrossSections(_Tool):
    label = "Cross-sections along a centerline"
    category = "3 More tools"
    description = (
        "Cuts sections across a centerline, finds channel, banks and "
        "floodplain on each, and writes them as lines with a plot per section "
        "(up to 'Most plots'), linked to the map. Spacing and width are in "
        "the DEM's units (feet on a foot grid).")

    def parameters(self):
        return [
            P("DEM", "dem", "GPRasterLayer"),
            P("Centerline", "centerline", "GPFeatureLayer"),
            P("Output folder", "out_folder", "DEFolder"),
            P("Spacing (DEM units)", "spacing", "GPDouble", default=25.0),
            P("Section width (DEM units)", "width", "GPDouble", default=200.0),
            P("Most plots to draw", "max_plots", "GPLong", default=200,
              category=OPT),
            D("Centerline sections", "out_sections"),
        ]

    def execute(self, parameters, messages):
        v = ga.by_name(parameters)
        out = ga.text(v["out_folder"])
        res = ga.run("export_cross_sections", {
            "dem": ga.raster_path(v["dem"], out, "dem"),
            "centerline": ga.vector_path(v["centerline"], out, "centerline"),
            "spacing": v["spacing"].value, "width": v["width"].value,
            "plot": True, "max_plots": v["max_plots"].value,
            "name": "centerline_sections"}, out, "cross-sections")
        lines = res.get("lines")
        if lines:
            ga.set_output(v, "out_sections",
                          os.path.splitext(lines)[0] + ".gpkg")
