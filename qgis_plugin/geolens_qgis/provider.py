"""The geolens Processing provider: the tools in the Processing Toolbox."""
from pathlib import Path

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithms import ALGORITHMS


class GeolensProvider(QgsProcessingProvider):
    def id(self):
        return "geolens"

    def name(self):
        return "geolens"

    def longName(self):
        return "geolens: valley and channel geomorphology"

    def icon(self):
        p = Path(__file__).with_name("icon.png")
        return QIcon(str(p)) if p.exists() else QgsProcessingProvider.icon(self)

    def loadAlgorithms(self):
        for alg in ALGORITHMS:
            self.addAlgorithm(alg())
