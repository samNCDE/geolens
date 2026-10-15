"""Run the geolens QGIS plugin's Processing tools in a real, headless QGIS.

    QT_QPA_PLATFORM=offscreen python3 tests/qgis/check_plugin.py <python-with-geolens> <out-dir>

Registers the provider, runs 'Check memory' and a small 'Landscape pass' on
the landscape fixture with runAndLoadResults, and checks the layers arrive in
the project with geolens's styles (map tips on the sections). Not part of
pytest: it needs PyQGIS.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parents[2]
for p in ("/usr/share/qgis/python/plugins", str(HERE / "qgis_plugin")):
    if p not in sys.path:
        sys.path.insert(0, p)

from qgis.core import (QgsApplication, QgsProcessingFeedback,  # noqa: E402
                       QgsProject, QgsSettings)

app = QgsApplication([], False)
app.initQgis()
from processing.core.Processing import Processing  # noqa: E402

Processing.initialize()
import processing  # noqa: E402

from geolens_qgis.provider import GeolensProvider  # noqa: E402

PROVIDER = GeolensProvider()          # keep a reference: sip drops the overrides otherwise
QgsApplication.processingRegistry().addProvider(PROVIDER)
QgsSettings().setValue("geolens/python", sys.argv[1])
out = sys.argv[2]
dem = str(HERE / "tests" / "landscape" / "landscape_dem.tif")
fails = []


class FB(QgsProcessingFeedback):
    last = -10
    label = ""

    def setProgressText(self, t):                            # noqa: N802
        self.label = t
        super().setProgressText(t)

    def setProgress(self, p):                                # noqa: N802
        if p - self.last >= 25 or p >= 100:
            self.last = p if p < 100 else -10
            print(f"    progress {p:5.1f}%  {self.label}")
        super().setProgress(p)

    def pushInfo(self, t):                                   # noqa: N802
        print("    info:", t)


def check(c, m):
    print(("ok   " if c else "FAIL ") + m)
    if not c:
        fails.append(m)


algs = [a.id() for a in QgsApplication.processingRegistry()
        .providerById("geolens").algorithms()]
check(set(algs) >= {"geolens:plan_run", "geolens:landscape_pass",
                    "geolens:section_plots", "geolens:stream_stability",
                    "geolens:cross_sections"}, f"tools registered: {algs}")

r = processing.run("geolens:plan_run", {"DEM": dem}, feedback=FB())
check(bool(r.get("VERDICT")), f"plan_run verdict: {r.get('VERDICT', '')[:70]}")

r = processing.runAndLoadResults("geolens:landscape_pass", {
    "DEM": dem, "GEOLOGY": 1, "CURVE": 0, "THRESHOLD": 3.0, "SEGMENT": 800.0,
    "METHOD": 0, "PLOTS": True, "OUTPUT": out}, feedback=FB())
layers = {l.name(): l for l in QgsProject.instance().mapLayers().values()}
print("    layers:", sorted(layers))
for name in ("Valley bottom", "Reaches (confinement)", "Valley types",
             "Stream types", "Stream-type sections", "Stable / unstable form"):
    check(name in layers, f"loaded: {name}")
sec = layers.get("Stream-type sections")
check(sec is not None and "with_variable('p'" in sec.mapTipTemplate(),
      "sections carry the plot map tip (style applied)")
st = layers.get("Stable / unstable form")
check(st is not None and st.renderer().type() == "categorizedSymbol",
      "stability layer coloured by status")
print("\n" + ("ALL OK" if not fails else f"{len(fails)} FAILED"))
sys.exit(1 if fails else 0)
