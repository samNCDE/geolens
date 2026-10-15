"""QGIS styles that link a feature to its picture.

A layer written by geolens that has a plot per feature -- a cross-section
line and the PNG of that section -- carries two columns:

``plot_file``
    The PNG's path relative to the layer's folder (``section_plots/xs_00012.png``).
    Survives moving or copying the whole output folder.
``plot_path``
    The absolute path when it was written. What ArcGIS Pro and anything
    else that wants one full path should use.

and a QGIS style in a ``.qml`` beside the GeoPackage with the same name,
which QGIS applies by itself when the layer is added (checked in QGIS 3.34).
With that style, in QGIS:

* **hover** -- with *View > Show Map Tips* on, resting the pointer on a
  feature of the active layer shows its plot;
* **click** -- the *Run Feature Action* tool (or right-click > Actions) opens
  the plot full size in the system image viewer;
* **identify** -- the feature form shows the plot under ``plot_path``.

The map tip and the action try ``plot_path`` first and fall back to the layer's
own folder + ``plot_file``, so they keep working after the folder moves. That
uses ``file_exists``, ``file_path`` and ``layer_property(@layer, 'path')``,
which need QGIS 3.16 or later. Verified by loading the GeoPackage in QGIS
3.34 and evaluating both expressions against its features (tests/qgis/).

No PyQGIS here: the QML is plain XML, written with the standard library.
The style is NOT stored inside the GeoPackage by default: a ``layer_styles``
table makes every ``geopandas.read_file`` of it warn about a second layer
and shows up as an extra table in ArcGIS, and the plots only work with the
folder beside them anyway, so the sidecar travels exactly as far as they do.
``embed=True`` stores it in the GeoPackage as well, the way QGIS's own
"Save as default" does.
"""
from __future__ import annotations

import sqlite3
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

# The image, resolved at display time. @p: the absolute path if that file is
# there, otherwise the layer's folder + the relative one.
_PATH = ("if(file_exists(\"plot_path\"), \"plot_path\", "
         "file_path(layer_property(@layer, 'path')) || '/' || \"plot_file\")")
# file:/// URL from a Windows or POSIX path: forward slashes, spaces
# escaped, no doubled leading slash on POSIX.
_URL = ("'file:///' || regexp_replace(replace(replace(@p, '\\\\', '/'), "
        "' ', '%20'), '^/+', '')")


def plot_map_tip(width_px: int = 760, missing: str | None = None) -> str:
    """Map tip HTML showing the feature's plot, or a note when there is none."""
    missing = missing or ("No plot for this feature yet. Render them with "
                          "geolens.api.plot_stream_sections(folder).")
    missing = missing.replace("'", "''")
    return ("[% with_variable('p', " + _PATH + ", "
            "if(file_exists(@p), '<img src=\"' || " + _URL + " || '\" width=\""
            + str(int(width_px)) + "\">', '" + missing + "')) %]")


def plot_action() -> str:
    """Action text for an Open-URL action on the feature's plot."""
    return "[% with_variable('p', " + _PATH + ", " + _URL + ") %]"


def _rgba(hex_colour: str) -> str:
    h = hex_colour.lstrip("#")
    return f"{int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},255"


def _line_symbol(parent: ET.Element, name: str, colour: str,
                 width_mm: float) -> None:
    sym = ET.SubElement(parent, "symbol", {
        "type": "line", "name": name, "alpha": "1", "clip_to_extent": "1",
        "force_rhr": "0"})
    lay = ET.SubElement(sym, "layer", {"class": "SimpleLine", "pass": "0",
                                       "locked": "0", "enabled": "1"})
    opt = ET.SubElement(lay, "Option", {"type": "Map"})
    for k, v in (("line_color", _rgba(colour)), ("line_width", f"{width_mm:g}"),
                 ("line_width_unit", "MM"), ("line_style", "solid"),
                 ("capstyle", "round"), ("joinstyle", "round")):
        ET.SubElement(opt, "Option", {"type": "QString", "name": k,
                                      "value": v})


def plot_link_qml(display_expression: str,
                  category_field: str | None = None,
                  categories: Iterable[tuple[str, str, str]] = (),
                  other_colour: str = "#d03b3b",
                  line_width_mm: float = 0.6,
                  action_name: str = "Open plot",
                  map_tip_width_px: int = 760,
                  missing_note: str | None = None) -> str:
    """A QGIS style (QML) for a line layer with ``plot_file``/``plot_path``.

    ``categories`` is ``(value, "#rrggbb", label)`` for a categorized
    renderer on ``category_field``; values not listed are drawn in
    ``other_colour`` so nothing disappears. With no categories the layer keeps
    whatever symbol QGIS gives it.
    """
    cats = list(categories)
    cat_list = ["Fields", "Forms", "Actions", "MapTips"]
    if category_field and cats:
        cat_list.insert(0, "Symbology")
    root = ET.Element("qgis", {"version": "3.34.4-Prizren",
                               "styleCategories": "|".join(cat_list)})

    if category_field and cats:
        rend = ET.SubElement(root, "renderer-v2", {
            "type": "categorizedSymbol", "attr": category_field,
            "forceraster": "0", "symbollevels": "0", "enableorderby": "0",
            "referencescale": "-1"})
        cel = ET.SubElement(rend, "categories")
        syms = ET.SubElement(rend, "symbols")
        for i, (value, colour, label) in enumerate(cats):
            ET.SubElement(cel, "category", {
                "type": "string", "value": str(value), "symbol": str(i),
                "label": label, "render": "true"})
            _line_symbol(syms, str(i), colour, line_width_mm)
        # The "all other values" category: an empty value of type NULL.
        i = len(cats)
        ET.SubElement(cel, "category", {
            "type": "NULL", "value": "", "symbol": str(i),
            "label": "other", "render": "true"})
        _line_symbol(syms, str(i), other_colour, line_width_mm)

    fc = ET.SubElement(root, "fieldConfiguration")
    fld = ET.SubElement(fc, "field", {"name": "plot_path",
                                     "configurationFlags": "NoFlag"})
    ew = ET.SubElement(fld, "editWidget", {"type": "ExternalResource"})
    cfg = ET.SubElement(ET.SubElement(ew, "config"), "Option", {"type": "Map"})
    for k, t, v in (("DocumentViewer", "int", "1"),
                    ("DocumentViewerHeight", "int", "0"),
                    ("DocumentViewerWidth", "int", "0"),
                    ("FileWidget", "bool", "true"),
                    ("FileWidgetButton", "bool", "false"),
                    ("RelativeStorage", "int", "0"),
                    ("StorageMode", "int", "0")):
        ET.SubElement(cfg, "Option", {"type": t, "name": k, "value": v})

    aid = "{" + str(uuid.uuid4()) + "}"
    acts = ET.SubElement(root, "attributeactions")
    # The Canvas default is what the Run Feature Action tool runs on a click.
    ET.SubElement(acts, "defaultAction", {"key": "Canvas", "value": aid})
    act = ET.SubElement(acts, "actionsetting", {
        "type": "5",                     # Open URL
        "action": plot_action(), "capture": "0", "name": action_name,
        "isEnabledOnlyWhenEditable": "0", "notificationMessage": "",
        "shortTitle": "Plot", "id": aid, "icon": ""})
    for scope in ("Feature", "Canvas"):
        ET.SubElement(act, "actionScope", {"id": scope})

    ET.SubElement(root, "previewExpression").text = display_expression
    ET.SubElement(root, "mapTip", {"enabled": "1"}).text = plot_map_tip(
        map_tip_width_px, missing_note)
    ET.SubElement(root, "layerGeometryType").text = "1"      # line

    ET.indent(root, space=" ")
    return ("<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>\n"
            + ET.tostring(root, encoding="unicode") + "\n")


_LAYER_STYLES = (
    'CREATE TABLE IF NOT EXISTS "layer_styles" ( "id" INTEGER PRIMARY KEY '
    'AUTOINCREMENT NOT NULL, "f_table_catalog" TEXT(256), "f_table_schema" '
    'TEXT(256), "f_table_name" TEXT(256), "f_geometry_column" TEXT(256), '
    '"styleName" TEXT(30), "styleQML" TEXT, "styleSLD" TEXT, "useAsDefault" '
    'BOOLEAN, "description" TEXT, "owner" TEXT(30), "ui" TEXT(30), '
    '"update_time" DATETIME DEFAULT (strftime(\'%Y-%m-%dT%H:%M:%fZ\',\'now\')))'
)


def write_qgis_style(gpkg: str | Path, layer: str, qml: str,
                     embed: bool = False, style_name: str = "geolens",
                     description: str = "geolens: plot links") -> Path:
    """Write ``qml`` as ``<gpkg stem>.qml`` beside the GeoPackage.

    QGIS applies a same-named .qml when the layer is added. ``embed`` also
    stores it as the layer's default style inside the GeoPackage, in the same
    ``layer_styles`` table QGIS creates for "Save as default" -- re-running
    replaces geolens's row and leaves any style the user saved, but makes
    geolens's the default. Returns the .qml path.
    """
    gpkg = Path(gpkg)
    side = gpkg.with_suffix(".qml")
    side.write_text(qml, encoding="utf-8")
    if embed:
        _embed_style(gpkg, layer, qml, style_name, description)
    return side


def _embed_style(gpkg: Path, layer: str, qml: str, style_name: str,
                 description: str) -> None:
    con = sqlite3.connect(str(gpkg))
    try:
        geom = con.execute(
            "SELECT column_name FROM gpkg_geometry_columns WHERE "
            "table_name = ?", (layer,)).fetchone()
        geom = geom[0] if geom else "geom"
        con.execute(_LAYER_STYLES)
        registered = con.execute(
            "SELECT 1 FROM gpkg_contents WHERE table_name = 'layer_styles'"
        ).fetchone()
        if not registered:
            con.execute(
                "INSERT INTO gpkg_contents (table_name, data_type, identifier,"
                " description, last_change, srs_id) VALUES ('layer_styles', "
                "'attributes', 'layer_styles', '', "
                "strftime('%Y-%m-%dT%H:%M:%fZ','now'), 0)")
        con.execute("DELETE FROM layer_styles WHERE f_table_name = ? AND "
                    "styleName = ?", (layer, style_name))
        con.execute("UPDATE layer_styles SET useAsDefault = 0 WHERE "
                    "f_table_name = ?", (layer,))
        con.execute(
            "INSERT INTO layer_styles (f_table_catalog, f_table_schema, "
            "f_table_name, f_geometry_column, styleName, styleQML, styleSLD, "
            "useAsDefault, description, owner, ui) VALUES "
            "('', '', ?, ?, ?, ?, '', 1, ?, '', NULL)",
            (layer, geom, style_name, qml, description))
        con.commit()
    finally:
        con.close()
