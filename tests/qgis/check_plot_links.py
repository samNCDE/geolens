"""Check geolens's plot-link styles in a real QGIS. Not part of pytest.

Run with a Python that has PyQGIS (QGIS's own, or OSGeo4W shell):

    python tests/qgis/check_plot_links.py <stream_types_dir> [--moved]

Loads stream_type_sections.gpkg and stream_types.gpkg as QGIS would, and
checks the default style came with them: categorized renderer, map tip,
Open-plot action as the canvas default, image widget on plot_path. Then
evaluates the map tip and the action for every feature in the same
expression context QGIS uses, and checks each resolves to a PNG on disk.
``--moved`` means the folder has been moved since it was written, so the
absolute plot_path must NOT exist and the relative fallback must work.
"""
import os
import re
import sys
from urllib.parse import unquote

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from qgis.core import (QgsApplication, QgsExpression, QgsExpressionContext,  # noqa: E402
                       QgsExpressionContextUtils, QgsFeature, QgsRenderContext,
                       QgsVectorLayer)

app = QgsApplication([], False)
app.initQgis()
folder = sys.argv[1]
moved = "--moved" in sys.argv
fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
    print(("ok   " if cond else "FAIL ") + msg)


def url_to_path(u):
    p = unquote(u[len("file:///"):])
    return p if re.match(r"^[A-Za-z]:/", p) else "/" + p


for name, n_min in (("stream_type_sections", 50), ("stream_types", 10)):
    lyr = QgsVectorLayer(os.path.join(folder, name + ".gpkg"), name, "ogr")
    check(lyr.isValid(), f"{name}: loads")
    tip = lyr.mapTipTemplate()
    check("with_variable('p'" in tip, f"{name}: default style gave a map tip")
    r = lyr.renderer()
    check(r.type() == "categorizedSymbol", f"{name}: categorized renderer "
          f"({r.type()}, attr={getattr(r, 'classAttribute', lambda: '')()})")
    acts = lyr.actions().actions()
    check(len(acts) == 1 and int(acts[0].type()) == 5,
          f"{name}: one Open-URL action ({[a.name() for a in acts]})")
    dflt = lyr.actions().defaultAction("Canvas")
    check(dflt.isValid() and dflt.id() == acts[0].id(),
          f"{name}: action is the canvas default (Run Feature Action tool)")
    ix = lyr.fields().indexOf("plot_path")
    ws = lyr.editorWidgetSetup(ix)
    check(ws.type() == "ExternalResource" and ws.config().get(
        "DocumentViewer") == 1, f"{name}: image widget on plot_path")
    check(bool(lyr.displayExpression()), f"{name}: display name "
          f"'{lyr.displayExpression()[:40]}...'")

    ctx = QgsExpressionContext(
        QgsExpressionContextUtils.globalProjectLayerScopes(lyr))
    n = n_ok = n_abs_exists = 0
    rc = QgsRenderContext()
    rc.setExpressionContext(ctx)
    r.startRender(rc, lyr.fields())
    unstyled = 0
    for f in lyr.getFeatures():
        n += 1
        ctx.setFeature(f)
        html = QgsExpression.replaceExpressionText(tip, ctx)
        act = QgsExpression.replaceExpressionText(acts[0].command(), ctx)
        m = re.search(r'<img src="([^"]+)"', html)
        ok = bool(m) and m.group(1) == act and os.path.exists(
            url_to_path(act))
        n_ok += ok
        n_abs_exists += os.path.exists(f["plot_path"])
        if n <= 1 or not ok and n_ok + 3 > n:
            print("     ", html[:150], "|", act[:90])
        if r.symbolForFeature(f, rc) is None:
            unstyled += 1
    r.stopRender(rc)
    check(n >= n_min and n_ok == n,
          f"{name}: map tip and action resolve to an existing PNG for "
          f"{n_ok}/{n} features")
    check(unstyled == 0, f"{name}: every feature has a symbol "
          f"({unstyled} without)")
    if moved:
        check(n_abs_exists == 0, f"{name}: absolute paths gone after the "
              f"move ({n_abs_exists} still exist), so this tested the "
              f"relative fallback")
    # a value the style has no category for must still be drawn
    fld = "status" if name == "stream_type_sections" else "stream_letter"
    f = QgsFeature(lyr.fields())
    f[fld] = "error: something new"
    ctx.setFeature(f)
    r.startRender(rc, lyr.fields())
    check(r.symbolForFeature(f, rc) is not None,
          f"{name}: an unlisted {fld} falls in 'other'")
    r.stopRender(rc)

print("\n" + ("ALL OK" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
