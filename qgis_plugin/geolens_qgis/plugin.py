"""Registers the geolens tools and a Set up menu entry."""
from qgis.core import QgsApplication, QgsSettings
from qgis.PyQt.QtWidgets import QAction, QFileDialog, QMessageBox

from .provider import GeolensProvider
from .runner import SETTINGS_KEY, find_python, numba_warning, probe


class GeolensPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None

    def initProcessing(self):                                # noqa: N802
        self.provider = GeolensProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):                                       # noqa: N802
        self.initProcessing()
        self.action = QAction("Set up / check the geolens environment",
                              self.iface.mainWindow())
        self.action.triggered.connect(self.setup)
        self.iface.addPluginToMenu("geolens", self.action)

    def unload(self):
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
        if self.action:
            self.iface.removePluginMenu("geolens", self.action)

    def setup(self):
        s = QgsSettings()
        current = s.value(SETTINGS_KEY, "", type=str) or None
        py, tried = find_python(current, fresh=True)
        if py:
            s.setValue(SETTINGS_KEY, py)
            v = tried[py].get("version")
            warn = numba_warning(tried[py])
            QMessageBox.information(
                self.iface.mainWindow(), "geolens",
                f"geolens {v} found:\n{py}\n\nThe tools are in the "
                f"Processing Toolbox under 'geolens'."
                + (f"\n\nWARNING: {warn}" if warn else ""))
            return
        msg = ("The geolens environment was not found.\n\n"
               "If geolens is not installed yet: unzip the geolens folder "
               "and double-click install_windows.bat, then come back here.\n\n"
               "If it is installed, choose its python.exe (usually "
               "...\\anaconda3\\envs\\geolens\\python.exe).")
        QMessageBox.warning(self.iface.mainWindow(), "geolens", msg)
        path, _ = QFileDialog.getOpenFileName(
            self.iface.mainWindow(), "geolens python.exe", "",
            "Python (python.exe python python3)")
        if not path:
            return
        info = probe(path)
        if info.get("version") and not info.get("missing"):
            s.setValue(SETTINGS_KEY, path)
            QMessageBox.information(self.iface.mainWindow(), "geolens",
                                    f"geolens {info['version']} found.")
        else:
            QMessageBox.critical(
                self.iface.mainWindow(), "geolens",
                "That Python cannot run geolens:\n"
                + (info.get("error") or "missing: "
                   + ", ".join(info.get("missing") or [])))
