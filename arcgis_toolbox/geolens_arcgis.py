"""The arcpy side of the geolens toolbox: inputs, running, outputs, styling.

Kept out of geolens.pyt so the toolbox file stays a readable list of tools.
Only arcpy and the standard library here: geolens itself runs in its own
Python environment, through geolens_runner, so nothing needs installing into
ArcGIS Pro's Python and a DEM too big for memory cannot take ArcGIS down.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

import arcpy

import geolens_runner as runner

CURVES = [
    ("By location: Moody et al. 2003 province, or generic outside the arid SW",
     "auto"),
    ("Moody et al. 2003, eastern AZ / NM (arid_sw_nm)", "arid_sw_nm"),
    ("Moody et al. 2003, central and southern AZ (arid_sw_az)", "arid_sw_az"),
    ("Bieger et al. 2015, all US (bieger_us)", "bieger_us"),
    ("Generic (none)", None),
]
METHODS = [
    ("Regional depth, DEM shape (default)", "regional_stage"),
    ("Hybrid: bank picker where it agrees", "hybrid"),
    ("Bank picker (v0.27 and earlier)", "picker"),
]
GEOLOGY = ["Download USGS surficial geology", "None",
           "Use the geology layer below", "Download USDA SSURGO soils"]


def fail(message: str):
    arcpy.AddError(message)
    raise arcpy.ExecuteError(message)


# -- parameters ------------------------------------------------------------------

def param(label, name, datatype, required=True, category=None, default=None,
          choices=None, direction="Input"):
    p = arcpy.Parameter(displayName=label, name=name, datatype=datatype,
                        parameterType="Required" if required else "Optional",
                        direction=direction, category=category)
    if choices:
        p.filter.type = "ValueList"
        p.filter.list = list(choices)
    if default is not None:
        p.value = default
    return p


def derived(label, name, datatype="DEFeatureClass"):
    return arcpy.Parameter(displayName=label, name=name, datatype=datatype,
                           parameterType="Derived", direction="Output")


def by_name(parameters):
    return {p.name: p for p in parameters}


def text(p):
    return p.valueAsText if p is not None and p.value is not None else None


def choice(p, pairs):
    """The value behind a ValueList label."""
    label = text(p)
    for lab, val in pairs:
        if lab == label:
            return val
    return pairs[0][1]


def new_folder_ok(p):
    """An output folder may not exist yet: say so instead of refusing it."""
    if p is not None and p.value is not None and not os.path.exists(p.valueAsText):
        p.clearMessage()
        p.setWarningMessage("This folder does not exist yet. It will be "
                            "created.")


def check_run_folder(p):
    if p is None or p.value is None:
        return
    run = Path(p.valueAsText)
    if not (run / "classified_dir").exists():
        p.setErrorMessage(
            "Not a geolens run folder (no classified_dir). Pick the output "
            "folder of the whole routine or of step 1.")


def spatial_reference(p):
    """A GPSpatialReference parameter as something pyproj reads: "EPSG:n"
    when it has a code, else its WKT."""
    if p is None or p.value is None:
        return None
    sr = p.value
    code = getattr(sr, "factoryCode", None) or getattr(sr, "PCSCode", None)
    if code:
        return f"EPSG:{int(code)}"
    f = getattr(sr, "exportToString", None)
    if callable(f):
        return f().split(";")[0]        # the WKT, without Esri's tolerances
    return p.valueAsText


# -- the geolens environment ---------------------------------------------------

def find_python() -> str:
    py, tried = runner.find_python(None)
    if py:
        return py
    lines = [f"  {p}: {i.get('error') or 'missing ' + ', '.join(i.get('missing') or []) or 'no geolens'}"
             for p, i in tried.items()] or ["  (no candidates found)"]
    fail("The geolens Python environment was not found.\n"
         "Run install_windows.bat from the geolens folder, or run the "
         "'Find the geolens environment' tool and give it the environment's "
         "python.exe.\nTried:\n" + "\n".join(lines))


def remember_python(path: str) -> None:
    runner.POINTER.parent.mkdir(parents=True, exist_ok=True)
    runner.POINTER.write_text(str(path) + "\n", encoding="utf-8")


# -- running -------------------------------------------------------------------

class _Progress:
    def __init__(self, title: str):
        arcpy.SetProgressor("step", title, 0, 100, 1)
        self.label = None
        self.pos = -1

    def __call__(self, pct: float, label: str) -> None:
        pos = int(max(0, min(100, pct)))
        if label and label != self.label:
            arcpy.SetProgressorLabel(label)
            self.label = label
        if pos != self.pos:
            arcpy.SetProgressorPosition(pos)
            self.pos = pos


def _cancel_requested() -> bool:
    try:
        return bool(arcpy.env.isCancelled)
    except Exception:                                        # noqa: BLE001
        return False


def _warnings(obj, out=None):
    """Every 'warnings' list anywhere in a result, in order, once each."""
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "warnings" and isinstance(v, list):
                out.extend(str(w) for w in v if str(w) not in out)
            else:
                _warnings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _warnings(v, out)
    return out


def run(function: str, kwargs: dict, out_dir: str, title: str | None = None):
    """``geolens.api.<function>(**kwargs)`` in the geolens environment."""
    py = find_python()
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    arcpy.AddMessage(f"geolens: {function}  (environment {py})")
    arcpy.AddMessage(f"output folder: {out_dir}")
    prev = getattr(arcpy.env, "autoCancelling", None)
    try:
        arcpy.env.autoCancelling = False
    except Exception:                                        # noqa: BLE001
        pass
    try:
        res = runner.run_api(py, function, kwargs, str(out_dir),
                             on_progress=_Progress(title or function),
                             on_info=arcpy.AddMessage, on_debug=None,
                             is_cancelled=_cancel_requested)
    except runner.Cancelled:
        arcpy.AddWarning("Cancelled. Steps that finished are on disk.")
        raise arcpy.ExecuteError("Cancelled.")
    except RuntimeError as exc:
        fail(str(exc))
    finally:
        try:
            arcpy.ResetProgressor()
        except Exception:                                    # noqa: BLE001
            pass
        if prev is not None:
            try:
                arcpy.env.autoCancelling = prev
            except Exception:                                # noqa: BLE001
                pass
    for w in _warnings(res):
        arcpy.AddWarning(w)
    return res


def launch(function: str, kwargs: dict, out_dir: str, title: str) -> dict:
    """Start ``geolens.api.<function>(**kwargs)`` in a window of its own and
    return at once, for runs of hours or days: ArcGIS Pro stays free, and
    the launcher left in the folder carries the run on after a stop."""
    py = find_python()
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    try:
        info = runner.launch_detached(py, function, kwargs, str(out_dir),
                                      title=title,
                                      by="the geolens ArcGIS toolbox")
    except RuntimeError as exc:
        fail(str(exc))
    except OSError as exc:
        fail(f"Could not start the run in its own window ({exc}). The "
             f"launcher is in {out_dir}: double-click run_geolens.bat there.")
    arcpy.AddMessage(f"geolens: {function} started in its own window "
                     f"(environment {py}).")
    arcpy.AddMessage(f"project folder: {out_dir}")
    arcpy.AddMessage("Leave that window open: closing it stops the run. ArcGIS "
                     "Pro is free to use, or to close, meanwhile.")
    arcpy.AddMessage(f"After a stop, a crash or a reboot, double-click "
                     f"{info['launcher']}: it carries on where it stopped.")
    arcpy.AddMessage("How far it has got: the 'Check on a project run' tool. "
                     "When it has finished: 'Add a project's results to the "
                     "map'.")
    return info


def memory_check(dem: str, out_dir: str, force: bool, stage: str | None = None):
    """Stop before a run that will not fit in memory (unless forced)."""
    plan = run("plan_landscape_run", {"dem": dem}, out_dir,
               "checking memory")
    avail = plan.get("available_gb")
    if stage:
        need = next((v for k, v in (plan.get("per_stage_gb") or {}).items()
                     if k.startswith(stage)), None)
        fits = None if need is None or avail is None else need <= avail
        arcpy.AddMessage(f"memory: {stage} needs ~{need} GB of {avail} GB")
    else:
        fits = plan.get("fits")
        arcpy.AddMessage(f"memory: {plan.get('verdict')}")
    if fits is False and not force:
        fail("This DEM will not fit in memory:\n  "
             + "\n  ".join([str(plan.get("verdict"))]
                           + [str(a) for a in plan.get("advice", [])])
             + "\nTick 'Run anyway' to try regardless, or coarsen the DEM.")
    return plan


# -- inputs ----------------------------------------------------------------------

_RASTER_EXT = {".tif", ".tiff", ".img", ".vrt", ".asc"}


def _inputs_dir(out_dir: str) -> Path:
    d = Path(out_dir) / "_inputs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def raster_path(p, out_dir: str, name: str):
    """A file geolens can open: the layer's own file, or a GeoTIFF copy."""
    if p is None or p.value is None:
        return None
    d = arcpy.Describe(p.value)
    path = getattr(d, "catalogPath", None) or p.valueAsText
    if Path(path).suffix.lower() in _RASTER_EXT and os.path.isfile(path):
        return path
    dst = _inputs_dir(out_dir) / f"{name}.tif"
    arcpy.AddMessage(f"copying {path} to {dst}: geolens reads rasters from "
                     f"files")
    arcpy.management.CopyRaster(p.value, str(dst))
    return str(dst)


def gpkg_layers(gpkg: str) -> list[str]:
    try:
        con = sqlite3.connect(str(gpkg))
        try:
            rows = con.execute("SELECT table_name FROM gpkg_contents WHERE "
                               "data_type = 'features'").fetchall()
        finally:
            con.close()
        return [r[0] for r in rows]
    except Exception:                                        # noqa: BLE001
        return []


def vector_path(p, out_dir: str, name: str):
    """A file geolens can open. A shapefile or a one-layer GeoPackage is used
    as it is; anything else (a file geodatabase, a selection, a service) is
    copied to a GeoPackage in the output folder's _inputs."""
    if p is None or p.value is None:
        return None
    d = arcpy.Describe(p.value)
    path = str(getattr(d, "catalogPath", None) or p.valueAsText)
    selected = bool(getattr(d, "FIDSet", "") or "")
    if not selected:
        if path.lower().endswith(".shp") and os.path.isfile(path):
            return path
        low = path.lower()
        if ".gpkg" in low:
            gpkg = path[: low.index(".gpkg") + 5]
            if os.path.isfile(gpkg) and len(gpkg_layers(gpkg)) == 1:
                return gpkg
    gpkg = _inputs_dir(out_dir) / f"{name}.gpkg"
    if gpkg.exists():
        arcpy.management.Delete(str(gpkg))
    arcpy.AddMessage(f"copying {path} to {gpkg}")
    arcpy.management.CreateSQLiteDatabase(str(gpkg), "GEOPACKAGE")
    arcpy.management.CopyFeatures(p.value, os.path.join(str(gpkg), f"main.{name}"))
    return str(gpkg)


# -- outputs ---------------------------------------------------------------------

def feature_class(gpkg) -> str | None:
    """``x.gpkg`` -> ``x.gpkg\\main.<layer>``, the path ArcGIS opens."""
    if not gpkg or not os.path.isfile(str(gpkg)):
        return None
    layers = gpkg_layers(str(gpkg))
    return os.path.join(str(gpkg), f"main.{layers[0]}") if layers else str(gpkg)


# Everything postExecute needs is read back from disk, not kept in this
# module: the .pyt reloads it on import, so anything remembered between
# execute and postExecute could be gone by the time the layers are styled.
# A feature class copied into geolens.gdb keeps its QGIS style beside the
# geodatabase (_styles/<name>.qml), and has attachments when ArcGIS made
# its <name>__ATTACH table.
GDB_NAME = "geolens.gdb"
STYLES_DIR = "_styles"


def _gdb_folder(params: dict) -> str | None:
    for key in ("out_folder", "run_folder"):
        if params.get(key) is not None and params[key].value is not None:
            return params[key].valueAsText
    if params.get("folder") is not None and params["folder"].value is not None:
        return str(Path(params["folder"].valueAsText).parent)
    return None


def attach_plots(fc: str) -> int:
    """Store each feature's plot (``plot_path``) as a geodatabase attachment.

    ArcGIS adds an attachments element to a layer's default pop-up, so the
    plot shows when the feature is clicked with nothing configured -- and,
    unlike a pop-up that points at a local file, it goes with the layer when
    it is shared or published.
    """
    n = 0
    with arcpy.da.UpdateCursor(fc, ["plot_path"]) as cur:
        for row in cur:
            if row[0] and os.path.isfile(row[0]):
                n += 1
            elif row[0] is not None:
                row[0] = None               # a plot that was never drawn
                cur.updateRow(row)
    if n:
        arcpy.management.EnableAttachments(fc)
        oid = arcpy.Describe(fc).OIDFieldName
        arcpy.management.AddAttachments(fc, oid, fc, oid, "plot_path")
    return n


def has_attachments(fc: str) -> bool:
    try:
        return bool(arcpy.Exists(f"{fc}__ATTACH"))
    except Exception:                                        # noqa: BLE001
        return False


def to_gdb(gpkg, folder: str) -> str | None:
    """Copy a geolens GeoPackage layer into ``<folder>\\geolens.gdb``, plots
    attached. The GeoPackage stays, for QGIS and for geolens itself."""
    src = feature_class(gpkg)
    if not src:
        return None
    gdb = os.path.join(folder, GDB_NAME)
    if not arcpy.Exists(gdb):
        arcpy.management.CreateFileGDB(folder, GDB_NAME)
    name = gdb_name(gpkg, _dataset_key(src)[0])
    dst = os.path.join(gdb, name)
    if arcpy.Exists(dst):
        arcpy.management.Delete(dst)
    arcpy.management.CopyFeatures(src, dst)
    qml = Path(str(gpkg)).with_suffix(".qml")
    if qml.exists():
        styles = Path(folder) / STYLES_DIR
        styles.mkdir(exist_ok=True)
        shutil.copyfile(qml, styles / f"{name}.qml")
    if "plot_path" in _field_names_of(dst):
        n = attach_plots(dst)
        arcpy.AddMessage(f"{name}: {n} plot(s) attached in {GDB_NAME}")
    return dst


_STANDARD_DIRS = {"stream_types", "stability", "valley_types",
                  "valley_extent", "classified", "reclassified", "valley",
                  "network", "fans", "gullies", "riparian", "watershed",
                  "headcuts", "valley_form"}


def gdb_name(gpkg, layer: str) -> str:
    """The feature class name in geolens.gdb. A layer from a standard folder
    keeps its own name; one from a renamed run of a step
    (``stream_types_v2_dir``) is prefixed with it, so the two do not
    overwrite each other in the one geodatabase."""
    parent = Path(str(gpkg)).parent.name
    prefix = parent[:-4] if parent.endswith("_dir") else ""
    name = (layer if (not prefix or prefix in _STANDARD_DIRS or prefix == layer)
            else f"{prefix}__{layer}")
    name = re.sub(r"[^A-Za-z0-9_]", "_", name)
    return name if name[:1].isalpha() else f"g_{name}"


def _style_key(name: str) -> tuple[str | None, str]:
    """(STYLES key, run prefix) for a dataset name, prefixed or not."""
    if name in STYLES:
        return name, ""
    for k in sorted(STYLES, key=len, reverse=True):
        if name.endswith("__" + k):
            return k, name[: -len(k) - 2]
    return None, ""


_FINE = re.compile(r"^(valley_types|valley_extent)_(\d+(?:\.\d+)?)m$")


def _spec(name: str):
    """(STYLES spec, run prefix) for a dataset name. The lidar valley layers
    at any cell (valley_types_2m, valley_extent_5m) take their kind's colours
    and are named for their cell."""
    key, prefix = _style_key(name)
    if key:
        return STYLES[key], prefix
    m = _FINE.match(name or "")
    if m:
        base = STYLES[m.group(1)]
        return dict(base, name=f"{base['name']} on the lidar at "
                               f"{m.group(2)} m"), ""
    return None, ""


def _field_names_of(dataset) -> set:
    try:
        return {f.name.lower() for f in arcpy.ListFields(dataset)}
    except Exception:                                        # noqa: BLE001
        return set()


def set_output(params: dict, name: str, gpkg) -> None:
    if name not in params or not gpkg:
        return
    fc = None
    use_gdb = params.get("to_gdb") is not None and bool(params["to_gdb"].value)
    folder = _gdb_folder(params) if use_gdb else None
    if folder:
        try:
            fc = to_gdb(gpkg, folder)
        except Exception as exc:                             # noqa: BLE001
            arcpy.AddWarning(f"Could not copy {gpkg} into {GDB_NAME} "
                             f"({type(exc).__name__}: {exc}); adding the "
                             f"GeoPackage layer instead.")
    fc = fc or feature_class(gpkg)
    if fc:
        params[name].value = fc


def dig(d, *keys):
    for k in keys:
        d = d.get(k) if isinstance(d, dict) else None
        if d is None:
            return None
    return d


# -- styling (postExecute) ---------------------------------------------------------

_VALLEY_1996 = {"I": "#8c2d04", "II": "#d95f0e", "III": "#fec44f",
                "IV": "#993404", "V": "#6a51a3", "VI": "#9e9ac8",
                "VII": "#e7298a", "VIII": "#1b9e77", "IX": "#66c2a5",
                "X": "#2171b5", "XI": "#9ecae1"}
# Rosgen's 2022 fluvial landscape types (the valley type from v0.38):
# unconfined alluvial in greens and yellows, the other unconfined origins
# in blues, confined types in browns and reds.
_VALLEY = {"U-AL-AD": "#1b9e77", "U-AL-AF": "#fec44f", "U-AL-IF": "#e6a117",
           "U-AL-RD": "#66c2a5", "U-BR-BC": "#8c8c7a", "U-EO-SD": "#f1dca7",
           "U-EO-LP": "#d9c38a", "U-GL-GO": "#9fb8ba", "U-GL-TP": "#c6dbdc",
           "U-PE-CS": "#bcbddc", "U-LA-LD": "#6baed6",
           "C-AL-AD": "#a6d854", "C-AL-IG": "#4d9221", "C-BR-BC": "#6e5a5a",
           "C-EO-LH": "#c9a66b", "C-EO-SH": "#e0c08f", "C-EO-FD": "#b08d57",
           "C-RE-FD": "#993404", "C-CO-FD": "#d95f0e", "C-CO-VS": "#8c2d04",
           "C-CO-US": "#e7298a", "C-GL-GT": "#6a51a3", "C-GL-TP": "#9e9ac8",
           "C-LA-AB": "#2171b5", "C-MA-AB": "#3182bd"}
_CONFINEMENT = {"confined": "#b2182b", "partly confined": "#f4a582",
                "unconfined": "#4393c3"}
_GREY = "#b8b7b2"
_MATERIAL = {"alluvial": "#e3b505", "mass_movement": "#a0522d",
             "residual": "#8c8c7a", "eolian": "#f1dca7",
             "lacustrine": "#6baed6", "playa": "#c6b9df",
             "organic": "#4d7c3a", "glacial_till": "#9fb8ba",
             "ice_marginal": "#c6dbdc", "coastal": "#9ecae1",
             "marine": "#3182bd", "bedrock_igneous_metamorphic": "#6e5a5a",
             "bedrock_sedimentary": "#b39f8f"}

# dataset name -> how it is drawn. "qml": categories from the .qml geolens
# writes for QGIS beside the GeoPackage, so both programs show one legend.
STYLES = {
    "stream_type_sections": {"name": "Stream-type sections", "qml": True,
                             "width": 1.5},
    "stream_types": {"name": "Stream types", "qml": True, "width": 2.0},
    "stream_stability": {"name": "Stable / unstable form", "qml": True,
                         "width": 2.5},
    "centerline_sections": {"name": "Centerline sections", "qml": True,
                            "width": 1.5},
    "valley_types": {"name": "Valley types", "field": "valley_type",
                     "colours": _VALLEY, "width": 2.0},
    "valley_extent": {"name": "Valley extent", "field": "valley_type",
                      "colours": _VALLEY, "alpha": 35},
    "segments_classified": {"name": "Reaches (confinement)",
                            "field": "confinement", "colours": _CONFINEMENT,
                            "width": 2.0},
    "valley_bottom": {"name": "Valley bottom", "single": "#6baed6",
                      "alpha": 35},
    "fans": {"name": "Alluvial fans", "single": "#e6a117", "alpha": 40},
    "fan_candidates": {"name": "Fan candidates (for review)",
                       "field": "accepted",
                       "colours": {"1": "#e6a117", "True": "#e6a117",
                                   "0": _GREY, "False": _GREY},
                       "alpha": 25},
    "gullies": {"name": "Gullies", "single": "#7f2704", "width": 1.2},
    "gully_polygons": {"name": "Gully footprints", "single": "#7f2704",
                       "alpha": 40},
    "riparian": {"name": "Riparian band", "single": "#31a354", "alpha": 45},
    "watershed_boundary": {"name": "Watershed boundary", "single": "#3f3f3f",
                           "alpha": 0},
    "headcuts": {"name": "Headcuts", "single": "#d7301f", "width": 6.0},
    "soils": {"name": "Soils (SSURGO)", "field": "material_class",
              "colours": _MATERIAL, "alpha": 45},
    "major_streams": {"name": "Major streams", "single": "#08519c",
                      "width": 3.0},
    "valley_types_3m": {"name": "Valley types on the lidar at 3 m",
                        "field": "valley_type", "colours": _VALLEY,
                        "width": 2.0},
    "valley_extent_3m": {"name": "Valley extent on the lidar at 3 m",
                         "field": "valley_type", "colours": _VALLEY,
                         "alpha": 35},
    "huc12_status": {"name": "HUC12 status", "field": "level_b",
                     "colours": {"done": "#4393c3", "failed": "#b2182b",
                                 "no lidar": _GREY, "queued": "#f4a582",
                                 "upstream only": "#e0e0e0"},
                     "alpha": 30},
    "huc12_sensitivity": {"name": "HUC12 sensitivity",
                          "field": "sensitivity_class",
                          "colours": {"high": "#b2182b", "moderate": "#f4a582",
                                      "low": "#4393c3",
                                      "insufficient data": _GREY},
                          "alpha": 45},
}


def _rgb(hex_colour: str) -> list[int]:
    h = hex_colour.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def qml_categories(qml: Path):
    """(field, {value: rgb}, {value: label}, other_rgb) from a geolens QML."""
    root = ET.parse(str(qml)).getroot()
    rend = root.find("renderer-v2")
    if rend is None or rend.get("type") != "categorizedSymbol":
        return None
    colours = {}
    syms = rend.find("symbols")
    for sym in (list(syms) if syms is not None else []):
        for opt in sym.iter("Option"):
            if opt.get("name") in ("line_color", "color", "fill_color"):
                colours[sym.get("name")] = [int(x) for x in
                                            opt.get("value").split(",")[:3]]
                break
    values, labels, other = {}, {}, None
    cats = rend.find("categories")
    for cat in (list(cats) if cats is not None else []):
        rgb = colours.get(cat.get("symbol"))
        if cat.get("type") == "NULL" or not cat.get("value"):
            other = rgb
            continue
        values[cat.get("value")] = rgb
        labels[cat.get("value")] = cat.get("label") or cat.get("value")
    return rend.get("attr"), values, labels, other


def plain_source(source: str) -> str:
    """A layer's data source as a path: ArcGIS Pro reports a GeoPackage
    layer as ``Instance=S:\\x\\a.gpkg,Dataset=main.%a`` (seen on Sam's
    Kingman map, Oct 2026), which no path test matched, so GeoPackage
    outputs were never named or coloured. That form becomes
    ``S:\\x\\a.gpkg\\main.a``; anything else is returned as it is."""
    src = str(source)
    m = re.match(r"\s*Instance=(?P<db>[^,]+?\.gpkg)\s*,.*?Dataset=(?P<ds>[^,;]+)",
                 src, flags=re.IGNORECASE)
    if not m:
        return src
    ds = m.group("ds").strip()
    if ds.lower().startswith("main."):
        ds = ds[5:]
    ds = ds.lstrip("%")
    return os.path.join(m.group("db").strip(), f"main.{ds}")


def qml_for(source: str) -> Path:
    """The QGIS style geolens wrote for a layer's data: beside its
    GeoPackage, or, for a copy in geolens.gdb, in _styles beside that."""
    src = plain_source(source)
    low = src.lower()
    if ".gpkg" in low:
        return Path(src[: low.index(".gpkg") + 5]).with_suffix(".qml")
    if ".gdb" in low:
        gdb = Path(src[: low.index(".gdb") + 4])
        return gdb.parent / STYLES_DIR / f"{Path(src).name}.qml"
    return Path(src).with_suffix(".qml")


def _dataset_key(source: str) -> tuple[str, Path | None]:
    src = plain_source(source)
    low = src.lower()
    if ".gpkg" in low:
        gpkg = Path(src[: low.index(".gpkg") + 5])
        tail = src[low.index(".gpkg") + 5:].lstrip("\\/")
        name = tail.split(".", 1)[1] if tail.lower().startswith("main.") else tail
        return (name or gpkg.stem), gpkg
    return Path(src).stem, None


def _unique_values(lyr, field, colours, labels, other, width, alpha):
    sym = lyr.symbology
    if not hasattr(sym, "renderer"):
        return
    sym.updateRenderer("UniqueValueRenderer")
    sym.renderer.fields = [field]
    for grp in sym.renderer.groups:
        for itm in grp.items:
            v = str(itm.values[0][0]) if itm.values and itm.values[0] else ""
            rgb = colours.get(v) or other or _rgb(_GREY)
            itm.symbol.color = {"RGB": list(rgb) + [alpha]}
            if width:
                itm.symbol.size = width
            if v in labels:
                itm.label = labels[v]
    lyr.symbology = sym


def _single(lyr, colour, width, alpha):
    sym = lyr.symbology
    if not hasattr(sym, "renderer"):
        return
    sym.updateRenderer("SimpleRenderer")
    sym.renderer.symbol.color = {"RGB": _rgb(colour) + [alpha]}
    if width:
        sym.renderer.symbol.size = width
    lyr.symbology = sym


def _image_popup(lyr, title: str) -> None:
    """Pop-up with the feature's plot: Configure Pop-ups > Image, Source URL
    and Link URL {plot_path}, done through the layer's CIM."""
    make = arcpy.cim.CreateCIMObjectFromClassName
    cim = lyr.getDefinition("V3")
    img = make("CIMImageMediaInfo", "V3")
    img.sourceURL = "{plot_path}"
    img.linkURL = "{plot_path}"
    img.title = title
    table = make("CIMTableMediaInfo", "V3")
    pop = make("CIMPopupInfo", "V3")
    pop.title = title
    pop.mediaInfos = [img, table]
    cim.popupInfo = pop
    lyr.setDefinition(cim)


def _field_names(lyr) -> set[str]:
    try:
        return {f.name.lower() for f in arcpy.ListFields(lyr)}
    except Exception:                                        # noqa: BLE001
        return set()


def style_layer(lyr) -> None:
    name, gpkg = _dataset_key(lyr.dataSource)
    spec, prefix = _spec(name)
    if spec is None:
        return
    lyr.name = spec["name"] + (f" ({prefix})" if prefix else "")
    width = spec.get("width")
    alpha = spec.get("alpha", 100)
    qml = qml_for(lyr.dataSource)
    try:
        if spec.get("qml") and qml.exists():
            got = qml_categories(qml)
            if got:
                field, colours, labels, other = got
                _unique_values(lyr, field, colours, labels, other, width, alpha)
        elif spec.get("field"):
            if spec["field"].lower() in _field_names(lyr):
                colours = {k: _rgb(v) for k, v in spec["colours"].items()}
                _unique_values(lyr, spec["field"], colours, {}, _rgb(_GREY),
                               width, alpha)
        elif spec.get("single"):
            _single(lyr, spec["single"], width, alpha)
    except Exception:                                        # noqa: BLE001
        pass
    # A layer whose plots are attachments keeps ArcGIS's default pop-up,
    # which shows them; one without gets an image pop-up on plot_path.
    if "plot_path" in _field_names(lyr) and not has_attachments(lyr.dataSource):
        try:
            _image_popup(lyr, spec["name"])
        except Exception:                                    # noqa: BLE001
            pass


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(
        plain_source(path).replace("/", os.sep)))


def style_outputs(parameters) -> None:
    """Name, colour and pop-up the layers a tool just added to the map."""
    try:
        m = arcpy.mp.ArcGISProject("CURRENT").activeMap
    except Exception:                                        # noqa: BLE001
        return
    if m is None:
        return
    wanted = {_norm(p.valueAsText) for p in parameters
              if p.direction == "Output" and p.value is not None}
    for lyr in m.listLayers():
        try:
            if not lyr.supports("DATASOURCE") or _norm(lyr.dataSource) not in wanted:
                continue
            style_layer(lyr)
        except Exception:                                    # noqa: BLE001
            continue


# -- a project's results on the map (v0.45) -----------------------------------------
# What add_v42_layers.py did for Kingman v42, for any project folder: the
# merged layers, the valley extent polygons coloured like the valley-type
# lines, the lidar valley layers and the review sample, named, coloured and
# in a sensible order. Run again, it re-colours the layers already there
# (switched on or off as they were) instead of adding them twice.

# (dataset, where, switched on?); bottom to top. "merged" is the project's
# merged folder; "extent" the 10 m valley extent; "review" the review folder.
PROJECT_LAYERS = [("huc12_summary", "merged", False),
                  ("huc12_sensitivity", "merged", False),
                  ("huc12_status", "merged", False),
                  ("valley_extent", "extent", True),
                  ("valley_extent_fine", "merged", True),
                  ("valley_types", "merged", True),
                  ("valley_types_fine", "merged", True),
                  ("stream_types", "merged", False),
                  ("stream_stability", "merged", False),
                  ("major_streams", "merged", True),
                  ("validation_sample", "review", False)]
LABELS = {"huc12_summary": "HUC12 summary (all the numbers)",
          "valley_extent": "Valley extent (by valley type)",
          "validation_sample": "Validation sample (reaches to score)"}


def _project_json(folder: str) -> dict:
    import json

    try:
        return json.loads((Path(folder) / "project.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def project_layer_files(folder: str) -> list[tuple[str, str, bool]]:
    """(dataset, GeoPackage, switched on?) for each result a project folder
    has, bottom to top of the map."""
    root = Path(folder)
    run = ((_project_json(folder).get("steps") or {}).get("pass_a") or {}).get("run")
    extent_dirs = [Path(run)] if run else []
    extent_dirs.append(root / "A_landscape" / "run")
    where = {"merged": [root / "merged"], "review": [root / "review"],
             "extent": [d / "valley_extent_dir" for d in extent_dirs]}
    out = []
    for name, place, on in PROJECT_LAYERS:
        if name.endswith("_fine"):
            kind = name[:-5]
            found = sorted(p for p in (root / "merged").glob(f"{kind}_*m.gpkg")
                           if _FINE.match(p.stem))
            out += [(p.stem, str(p), on) for p in found]
            continue
        for d in where[place]:
            p = d / f"{name}.gpkg"
            if p.is_file():
                out.append((name, str(p), on))
                break
    return out


def _outline_like_fill(lyr, width: float = 0.5) -> None:
    """A see-through fill with a solid outline of its own colour."""
    sym = lyr.symbology
    if not hasattr(sym, "renderer") or not hasattr(sym.renderer, "groups"):
        return
    for grp in sym.renderer.groups:
        for itm in grp.items:
            rgb = list((itm.symbol.color or {}).get("RGB", [184, 183, 178]))[:3]
            itm.symbol.outlineColor = {"RGB": rgb + [100]}
            itm.symbol.outlineWidth = width
    lyr.symbology = sym


def add_project_layers(folder: str, tag: str | None = None) -> list[str]:
    """Add a project's results to the active map; returns the layer names."""
    try:
        m = arcpy.mp.ArcGISProject("CURRENT").activeMap
    except Exception:                                        # noqa: BLE001
        m = None
    if m is None:
        fail("No active map: click into a map view, then run the tool again.")
    files = project_layer_files(folder)
    if not files:
        fail(f"No results in {folder} yet: the merged layers are written at "
             f"the end of a project run.")
    tag = f" - {tag}" if tag else ""
    there = {}
    for lyr in m.listLayers():
        try:
            if lyr.supports("DATASOURCE"):
                there[_norm(lyr.dataSource)] = lyr
        except Exception:                                    # noqa: BLE001
            continue
    arcpy.SetProgressor("step", "Adding the project's results", 0, len(files), 1)
    added: dict[str, object] = {}
    names = []
    for i, (name, gpkg, on) in enumerate(files, 1):
        fc = feature_class(gpkg)
        lyr = there.get(_norm(fc)) if fc else None
        new = lyr is None
        if new:
            lyr = m.addDataFromPath(fc)
        try:
            style_layer(lyr)
            if name.startswith("valley_extent"):
                _outline_like_fill(lyr)
        except Exception as exc:                             # noqa: BLE001
            arcpy.AddWarning(f"{name}: left in ArcGIS's colours ({exc})")
        spec, _ = _spec(name)
        label = LABELS.get(name) or (spec or {}).get("name") or name
        lyr.name = label + tag
        if new:                    # a layer already there keeps its on / off
            lyr.visible = on
        added[name] = lyr
        names.append(lyr.name)
        arcpy.AddMessage(f"[{i}/{len(files)}] {lyr.name}"
                         + ("" if new else "  (already there: re-coloured)")
                         + ("" if lyr.visible else "  (off)"))
        arcpy.SetProgressorPosition(i)
    # each set of valley polygons just under the valley-type lines it matches
    for lines, polys in [("valley_types", "valley_extent")] + [
            (n, "valley_extent" + n[len("valley_types"):]) for n in added
            if n.startswith("valley_types_")]:
        if lines in added and polys in added:
            try:
                m.moveLayer(added[lines], added[polys], "AFTER")
            except Exception:                                # noqa: BLE001
                pass
    arcpy.ResetProgressor()
    return names
