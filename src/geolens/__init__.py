"""geolens — inspect geospatial data and extract channel geometry from a DEM.

Open-source stack throughout (GDAL/OGR, rasterio, geopandas, shapely): no ArcGIS
licence required, and Esri File Geodatabases are readable without ArcGIS
installed. Runs on Windows, macOS and Linux.
"""

__version__ = "0.36.0"

from .safety import Guard, GeolensError, PathNotAllowed, ReadOnlyViolation  # noqa: F401

__all__ = [
    "__version__",
    "Guard",
    "GeolensError",
    "PathNotAllowed",
    "ReadOnlyViolation",
    "api",
    "discover",
    "vector",
    "raster",
    "sections",
    "morphology",
    "render",
]
