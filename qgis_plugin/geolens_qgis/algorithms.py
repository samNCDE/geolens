"""geolens tools for the QGIS Processing Toolbox.

Each tool collects its inputs in the standard Processing dialog, runs the
matching ``geolens.api`` call in the geolens environment (``runner.py``),
streams its progress into the dialog, and adds its layers to the project
with geolens's own styles (section plots on hover, types coloured).
"""
from __future__ import annotations

from pathlib import Path

from qgis.core import (QgsProcessing, QgsProcessingAlgorithm,
                       QgsProcessingContext, QgsProcessingException,
                       QgsProcessingLayerPostProcessorInterface,
                       QgsProcessingOutputFolder, QgsProcessingOutputString,
                       QgsProcessingParameterBoolean,
                       QgsProcessingParameterEnum, QgsProcessingParameterFile,
                       QgsProcessingParameterFolderDestination,
                       QgsProcessingParameterNumber,
                       QgsProcessingParameterRasterLayer,
                       QgsProcessingParameterVectorLayer,
                       QgsProcessingUtils, QgsSettings)
from qgis.PyQt.QtCore import QCoreApplication

from .runner import SETTINGS_KEY, Cancelled, find_python, run_api

CURVES = ["arid_sw_nm", "arid_sw_az", "bieger_us", None]
CURVE_LABELS = ["Moody et al. 2003, eastern AZ / NM (arid_sw_nm)",
                "Moody et al. 2003, central & southern AZ (arid_sw_az)",
                "Bieger et al. 2015, all US (bieger_us)",
                "Generic (none)"]
METHODS = ["regional_stage", "hybrid", "picker"]


def _tr(s: str) -> str:
    return QCoreApplication.translate("geolens", s)


def _file(layer) -> str | None:
    """A layer's file path, without the |layername= suffix."""
    if layer is None:
        return None
    return layer.source().split("|")[0]


class _Styler(QgsProcessingLayerPostProcessorInterface):
    """Applies the .qml geolens writes beside each layer.

    Processing loads outputs without their default style, so the map tips
    and colours would be missing. Kept alive in _keep: QGIS holds only a
    pointer."""
    _keep: list = []

    def __init__(self, path: str):
        super().__init__()
        self.path = path

    def postProcessLayer(self, layer, context, feedback):   # noqa: N802
        qml = Path(self.path).with_suffix(".qml")
        if qml.exists():
            layer.loadNamedStyle(str(qml))
            layer.triggerRepaint()

    @classmethod
    def make(cls, path: str) -> "_Styler":
        obj = cls(path)
        cls._keep.append(obj)
        return obj


class _Geolens(QgsProcessingAlgorithm):
    NAME = ""
    TITLE = ""
    GROUP = "geolens"
    HELP = ""

    def name(self):
        return self.NAME

    def displayName(self):
        return _tr(self.TITLE)

    def group(self):
        return _tr(self.GROUP)

    def groupId(self):
        return self.GROUP.lower().replace(" ", "_")

    def shortHelpString(self):
        return _tr(self.HELP)

    def createInstance(self):
        return type(self)()

    # -- running -------------------------------------------------------

    def _python(self, feedback) -> str:
        s = QgsSettings()
        setting = s.value(SETTINGS_KEY, "", type=str) or None
        py, tried = find_python(setting)
        if not py:
            lines = [f"  {p}: {i.get('error') or 'missing ' + ', '.join(i.get('missing') or []) or 'no geolens'}"
                     for p, i in tried.items()] or ["  (no candidates found)"]
            raise QgsProcessingException(
                "The geolens Python environment was not found.\n"
                "Run install_windows.bat from the geolens folder, or set the "
                "environment's python.exe with Plugins > geolens > Set up.\n"
                "Tried:\n" + "\n".join(lines))
        if py != setting:
            s.setValue(SETTINGS_KEY, py)
        return py

    def _run(self, function, kwargs, out_dir, feedback) -> dict:
        py = self._python(feedback)
        feedback.pushInfo(f"geolens environment: {py}")
        feedback.pushInfo(f"output folder: {out_dir}")
        try:
            return run_api(
                py, function, kwargs, str(out_dir),
                on_progress=lambda p, lab: (feedback.setProgress(p),
                                            feedback.setProgressText(lab)),
                on_info=feedback.pushInfo,
                on_debug=feedback.pushDebugInfo,
                is_cancelled=feedback.isCanceled)
        except Cancelled:
            raise QgsProcessingException("Cancelled.")
        except RuntimeError as exc:
            raise QgsProcessingException(str(exc))

    @staticmethod
    def _load(context, path, name):
        if path and Path(str(path)).exists():
            d = QgsProcessingContext.LayerDetails(name, context.project(), name)
            d.setPostProcessor(_Styler.make(str(path)))
            context.addLayerToLoadOnCompletion(str(path), d)

    @staticmethod
    def _warn(res, feedback):
        for w in (res or {}).get("warnings", []) or []:
            feedback.pushWarning(str(w))

    @staticmethod
    def _temporary(out, feedback) -> bool:
        """Warn when the output folder was left as 'Save to temporary folder':
        QGIS deletes that folder when it closes."""
        try:
            tmp = Path(QgsProcessingUtils.tempFolder()).resolve()
            is_tmp = Path(out).resolve().is_relative_to(tmp)
        except Exception:                                    # noqa: BLE001
            return False
        if is_tmp:
            feedback.pushWarning(
                "The output folder is QGIS's temporary folder, which QGIS "
                f"deletes when it closes:\n  {out}\nCopy it somewhere "
                "permanent before closing QGIS, or choose an output folder.")
        return is_tmp


# ---------------------------------------------------------------------------


class PlanRun(_Geolens):
    NAME = "plan_run"
    TITLE = "1. Check memory before a run"
    HELP = ("Reads only the DEM's header and estimates the peak memory of a "
            "landscape pass on it, against the RAM this machine has. Run it "
            "first on an unfamiliar DEM: an out-of-memory kill gives no "
            "message. If it will not fit, it says what resolution would.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer("DEM", _tr("DEM")))
        self.addOutput(QgsProcessingOutputString("VERDICT", _tr("Verdict")))

    def processAlgorithm(self, parameters, context, feedback):
        dem = _file(self.parameterAsRasterLayer(parameters, "DEM", context))
        out = QgsProcessingUtils.tempFolder()
        r = self._run("plan_landscape_run", {"dem": dem}, out, feedback)
        feedback.pushInfo("")
        feedback.pushInfo(f"{r['cells']:,} cells at {r['cell_size']:g} m, "
                          f"peak ~{r['peak_gb']:.1f} GB"
                          + (f" of {r['available_gb']:.1f} GB available"
                             if r.get('available_gb') else ""))
        feedback.pushInfo(f"VERDICT: {r['verdict']}")
        for a in r.get("advice", []):
            feedback.pushInfo(f"  - {a}")
        return {"VERDICT": r["verdict"]}


class LandscapePass(_Geolens):
    NAME = "landscape_pass"
    TITLE = "2. Landscape pass (valleys, confinement, valley and stream types)"
    HELP = (
        "The whole pipeline on one DEM: flow routing, HAND, valley bottom, "
        "reaches and confinement, valley form and Rosgen valley types, Rosgen "
        "stream types with a plot of every channel section, and whether each "
        "reach's stream type is a stable form for its valley. Checks memory "
        "first and stops if the DEM will not fit (tick 'Run anyway' to "
        "override). Results load into the project styled; hover over a "
        "stream-type section with Map Tips on to see its plot.\n\n"
        "Lidar at 0.5-2 m is what the stream types need; routing a very "
        "large DEM at 2-3 m and giving the full-resolution DEM as 'Fine DEM' "
        "keeps memory down without losing the channel measurements.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer("DEM", _tr("DEM")))
        self.addParameter(QgsProcessingParameterRasterLayer(
            "DEM_FINE", _tr("Fine DEM for channel measurements (optional)"),
            optional=True))
        self.addParameter(QgsProcessingParameterVectorLayer(
            "NHD", _tr("NHD flowlines to join (optional)"),
            [QgsProcessing.TypeVectorLine], optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            "GEOLOGY", _tr("Geology"),
            options=[_tr("Download USGS surficial geology (auto)"),
                     _tr("None (valley types stay unresolved where it "
                         "matters)"),
                     _tr("Use the layer below")], defaultValue=0))
        self.addParameter(QgsProcessingParameterVectorLayer(
            "GEOLOGY_LAYER", _tr("Geology polygons with material_class "
                                 "(optional)"),
            [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            "CURVE", _tr("Regional bankfull curve"), options=CURVE_LABELS,
            defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            "THRESHOLD", _tr("Stream threshold, km² (0 = fit to the DEM)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.0,
            minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            "SEGMENT", _tr("Reach length, m"),
            QgsProcessingParameterNumber.Double, defaultValue=500.0,
            minValue=50.0))
        self.addParameter(QgsProcessingParameterEnum(
            "METHOD", _tr("Bankfull method"),
            options=[_tr("Regional depth, DEM shape (default)"),
                     _tr("Hybrid: bank picker where it agrees"),
                     _tr("Bank picker (v0.27 and earlier)")], defaultValue=0))
        self.addParameter(QgsProcessingParameterBoolean(
            "PLOTS", _tr("Draw a plot of every stream-type section"),
            defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(
            "FORCE", _tr("Run anyway if the memory check says it will not "
                         "fit"), defaultValue=False))
        self.addParameter(QgsProcessingParameterFolderDestination(
            "OUTPUT", _tr("Output folder")))
        self.addOutput(QgsProcessingOutputFolder("FOLDER", _tr("Folder")))

    def processAlgorithm(self, parameters, context, feedback):
        dem = _file(self.parameterAsRasterLayer(parameters, "DEM", context))
        fine = _file(self.parameterAsRasterLayer(parameters, "DEM_FINE",
                                                 context))
        nhd = _file(self.parameterAsVectorLayer(parameters, "NHD", context))
        out = self.parameterAsString(parameters, "OUTPUT", context)
        g = self.parameterAsEnum(parameters, "GEOLOGY", context)
        geology = ("auto" if g == 0 else None if g == 1 else
                   _file(self.parameterAsVectorLayer(parameters,
                                                     "GEOLOGY_LAYER", context)))
        if g == 2 and not geology:
            raise QgsProcessingException("Choose a geology layer, or pick "
                                         "'auto' or 'None'.")
        thr = self.parameterAsDouble(parameters, "THRESHOLD", context)
        temporary = self._temporary(out, feedback)

        feedback.pushInfo("Checking memory ...")
        plan = self._run("plan_landscape_run", {"dem": dem}, out, feedback)
        feedback.pushInfo(f"  {plan['verdict']}")
        if plan.get("fits") is False and not self.parameterAsBool(
                parameters, "FORCE", context):
            raise QgsProcessingException(
                "This DEM will not fit in memory:\n  "
                + "\n  ".join([plan["verdict"]] + list(plan.get("advice", [])))
                + "\nTick 'Run anyway' to try regardless.")
        if feedback.isCanceled():
            return {}

        kwargs = {
            "dem": dem, "nhd": nhd, "dem_fine": fine, "geology": geology,
            "regional_curve": CURVES[self.parameterAsEnum(parameters,
                                                          "CURVE", context)],
            "stream_threshold_km2": thr if thr > 0 else None,
            "segment_length_m": self.parameterAsDouble(parameters, "SEGMENT",
                                                       context),
            "bankfull_method": METHODS[self.parameterAsEnum(parameters,
                                                            "METHOD",
                                                            context)],
            "section_plots": self.parameterAsBool(parameters, "PLOTS",
                                                  context),
        }
        res = self._run("landscape_pass", kwargs, out, feedback)
        self._warn(res, feedback)

        def g_(*keys):
            d = res
            for k in keys:
                d = d.get(k) if isinstance(d, dict) else None
                if d is None:
                    return None
            return d

        for path, name in (
                (g_("valley", "valley_polygons"), "Valley bottom"),
                (g_("classified", "segments"), "Reaches (confinement)"),
                (g_("valley_extent"), "Valley extent"),
                (g_("valley_types", "segments"), "Valley types"),
                (g_("stream_types", "segments"), "Stream types"),
                (g_("stream_types", "sections"), "Stream-type sections"),
                (g_("stability", "stability"), "Stable / unstable form")):
            self._load(context, path, name)
        if temporary:
            self._temporary(out, feedback)
        return {"FOLDER": out}


class SectionPlots(_Geolens):
    NAME = "section_plots"
    TITLE = "3. Plot the stream-type sections of an earlier run"
    HELP = ("Point at a run's stream_types_dir folder. Draws every section "
            "and a sheet per reach, and links them to the map (hover with "
            "Map Tips on, or use Run Feature Action). No DEM needed.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile(
            "FOLDER", _tr("stream_types_dir folder"),
            behavior=QgsProcessingParameterFile.Folder))
        self.addOutput(QgsProcessingOutputFolder("OUT", _tr("Folder")))

    def processAlgorithm(self, parameters, context, feedback):
        folder = self.parameterAsFile(parameters, "FOLDER", context)
        if not (Path(folder) / "stream_type_profiles.csv").exists():
            raise QgsProcessingException(
                "That folder has no stream_type_profiles.csv: pick the "
                "stream_types_dir of a run.")
        res = self._run("plot_stream_sections",
                        {"stream_types_dir": folder}, folder, feedback)
        self._warn(res, feedback)
        feedback.pushInfo(f"{res.get('section_plots')} section plots, "
                          f"{res.get('reach_sheets')} reach sheets")
        for f, name in (("stream_type_sections.gpkg", "Stream-type sections"),
                        ("stream_types.gpkg", "Stream types")):
            self._load(context, str(Path(folder) / f), name)
        return {"OUT": folder}


class StreamStability(_Geolens):
    NAME = "stream_stability"
    TITLE = "4. Stable or unstable form (stream type vs valley type)"
    HELP = ("Joins valley_types.gpkg and stream_types.gpkg of one run and "
            "judges each reach: stable (a stream type Rosgen associates with "
            "the valley type), unstable (one he lists for it only out of "
            "balance), check (not associated at all: a departure, or a wrong "
            "type) or unknown. Source: NRCS NEH 654 TS3E Table TS3E-3, after "
            "Rosgen (1996).")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(
            "VALLEY", _tr("Valley types (valley_types.gpkg)"),
            [QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterVectorLayer(
            "STREAM", _tr("Stream types (stream_types.gpkg)"),
            [QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterFolderDestination(
            "OUTPUT", _tr("Output folder")))
        self.addOutput(QgsProcessingOutputFolder("FOLDER", _tr("Folder")))

    def processAlgorithm(self, parameters, context, feedback):
        v = _file(self.parameterAsVectorLayer(parameters, "VALLEY", context))
        s = _file(self.parameterAsVectorLayer(parameters, "STREAM", context))
        out = self.parameterAsString(parameters, "OUTPUT", context)
        self._temporary(out, feedback)
        res = self._run("stream_stability", {"valley_types": v,
                                             "stream_types": s}, out, feedback)
        feedback.pushInfo(f"reaches by form: {res.get('counts')}")
        self._load(context, res.get("stability"), "Stable / unstable form")
        return {"FOLDER": out}


class CrossSections(_Geolens):
    NAME = "cross_sections"
    TITLE = "5. Cross-sections along a centerline"
    HELP = ("Cuts sections across a centerline, delineates channel, banks "
            "and floodplain on each, and writes them as lines with a plot "
            "per section (up to 'Most plots'), linked to the map. Spacing and "
            "width are in the DEM's units (feet on a foot grid).")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer("DEM", _tr("DEM")))
        self.addParameter(QgsProcessingParameterVectorLayer(
            "CENTERLINE", _tr("Centerline"), [QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterNumber(
            "SPACING", _tr("Spacing (DEM units)"),
            QgsProcessingParameterNumber.Double, defaultValue=25.0,
            minValue=0.1))
        self.addParameter(QgsProcessingParameterNumber(
            "WIDTH", _tr("Section width (DEM units)"),
            QgsProcessingParameterNumber.Double, defaultValue=200.0,
            minValue=1.0))
        self.addParameter(QgsProcessingParameterNumber(
            "MAX_PLOTS", _tr("Most plots to draw"),
            QgsProcessingParameterNumber.Integer, defaultValue=200,
            minValue=0))
        self.addParameter(QgsProcessingParameterFolderDestination(
            "OUTPUT", _tr("Output folder")))
        self.addOutput(QgsProcessingOutputFolder("FOLDER", _tr("Folder")))

    def processAlgorithm(self, parameters, context, feedback):
        dem = _file(self.parameterAsRasterLayer(parameters, "DEM", context))
        cl = _file(self.parameterAsVectorLayer(parameters, "CENTERLINE",
                                               context))
        out = self.parameterAsString(parameters, "OUTPUT", context)
        self._temporary(out, feedback)
        res = self._run("export_cross_sections", {
            "dem": dem, "centerline": cl,
            "spacing": self.parameterAsDouble(parameters, "SPACING", context),
            "width": self.parameterAsDouble(parameters, "WIDTH", context),
            "plot": True,
            "max_plots": self.parameterAsInt(parameters, "MAX_PLOTS", context),
            "name": "centerline_sections"}, out, feedback)
        self._warn(res, feedback)
        lines = res.get("lines")
        gpkg = str(Path(lines).with_suffix(".gpkg")) if lines else None
        self._load(context, gpkg, "Centerline sections")
        return {"FOLDER": out}


ALGORITHMS = [PlanRun, LandscapePass, SectionPlots, StreamStability,
              CrossSections]
