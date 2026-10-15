"""Find geospatial datasets on disk and report what's there."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .safety import Guard, progress

VECTOR_SUFFIXES = {
    ".shp": "ESRI Shapefile",
    ".gpkg": "GeoPackage",
    ".geojson": "GeoJSON",
    ".json": "GeoJSON",
    ".kml": "KML",
    ".gml": "GML",
    ".fgb": "FlatGeobuf",
    ".tab": "MapInfo TAB",
    ".parquet": "GeoParquet",
}

RASTER_SUFFIXES = {
    ".tif": "GeoTIFF",
    ".tiff": "GeoTIFF",
    ".img": "ERDAS IMAGINE",
    ".vrt": "GDAL VRT",
    ".asc": "Arc/Info ASCII Grid",
    ".dem": "USGS DEM",
    ".bil": "BIL",
    ".nc": "NetCDF",
    ".hgt": "SRTM HGT",
    ".jp2": "JPEG 2000",
}

TABLE_SUFFIXES = {".csv": "CSV", ".xlsx": "Excel", ".dbf": "dBase"}

# Directories that are themselves datasets rather than containers to walk into.
CONTAINER_DIR_SUFFIXES = {".gdb": "Esri File Geodatabase"}

# Shapefile sidecars — noise if listed individually.
SIDECAR_SUFFIXES = {
    ".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx", ".qix",
    ".fix", ".qpj", ".shp.xml", ".aux.xml", ".ovr", ".idx",
}

SKIP_DIRS = {".git", "__pycache__", ".ipynb_checkpoints", "node_modules", ".venv"}


def _human_size(n: int) -> str:
    step = 1024.0
    val = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < step:
            return f"{val:.0f} {unit}" if unit == "B" else f"{val:.1f} {unit}"
        val /= step
    return f"{val:.1f} PB"


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    except OSError:
        pass
    return total


def _gdb_layers(path: Path) -> list[str]:
    """List layers in a File Geodatabase via GDAL's open-source OpenFileGDB."""
    try:
        import fiona

        return sorted(fiona.listlayers(str(path)))
    except Exception:
        return []


def _gpkg_layers(path: Path) -> list[str]:
    try:
        import fiona

        return sorted(fiona.listlayers(str(path)))
    except Exception:
        return []


def discover(
    path: str | Path,
    guard: Guard | None = None,
    recursive: bool = True,
    max_depth: int = 6,
) -> dict[str, Any]:
    """Walk ``path`` and catalogue every geospatial dataset found.

    Returns a JSON-serializable summary grouped by kind. Shapefile sidecars are
    collapsed into their parent ``.shp``; ``.gdb`` directories are reported as
    single datasets with their layer list rather than walked into.
    """
    guard = guard or Guard.from_env()
    root = guard.read_path(path)

    if root.is_file():
        entries = [root]
        base = root.parent
    else:
        base = root
        entries = []
        stack = [(root, 0)]
        while stack:
            cur, depth = stack.pop()
            try:
                children = sorted(cur.iterdir())
            except (OSError, PermissionError):
                continue
            for child in children:
                if child.is_dir():
                    if child.suffix.lower() in CONTAINER_DIR_SUFFIXES:
                        entries.append(child)
                    elif (
                        recursive
                        and depth < max_depth
                        and child.name not in SKIP_DIRS
                        and not child.name.startswith(".")
                    ):
                        stack.append((child, depth + 1))
                else:
                    entries.append(child)

    vectors: list[dict[str, Any]] = []
    rasters: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    containers: list[dict[str, Any]] = []
    other_count = 0

    for entry in progress(entries, label="scanning files"):
        suffix = entry.suffix.lower()
        try:
            rel = str(entry.relative_to(base))
        except ValueError:
            rel = str(entry)

        if entry.is_dir() and suffix in CONTAINER_DIR_SUFFIXES:
            layers = _gdb_layers(entry)
            containers.append(
                {
                    "path": str(entry),
                    "relative": rel,
                    "format": CONTAINER_DIR_SUFFIXES[suffix],
                    "layers": layers,
                    "layer_count": len(layers),
                    "size": _human_size(_dir_size(entry)),
                    "note": None
                    if layers
                    else "Could not read layers — may be a corrupt or v9 geodatabase.",
                }
            )
            continue

        if suffix in SIDECAR_SUFFIXES:
            continue

        try:
            size = _human_size(entry.stat().st_size)
        except OSError:
            size = "?"

        if suffix in VECTOR_SUFFIXES:
            rec: dict[str, Any] = {
                "path": str(entry),
                "relative": rel,
                "format": VECTOR_SUFFIXES[suffix],
                "size": size,
            }
            if suffix == ".gpkg":
                layers = _gpkg_layers(entry)
                rec["layers"] = layers
                rec["layer_count"] = len(layers)
            vectors.append(rec)
        elif suffix in RASTER_SUFFIXES:
            rasters.append(
                {
                    "path": str(entry),
                    "relative": rel,
                    "format": RASTER_SUFFIXES[suffix],
                    "size": size,
                }
            )
        elif suffix in TABLE_SUFFIXES:
            tables.append(
                {
                    "path": str(entry),
                    "relative": rel,
                    "format": TABLE_SUFFIXES[suffix],
                    "size": size,
                }
            )
        else:
            other_count += 1

    total = len(vectors) + len(rasters) + len(tables) + len(containers)
    return {
        "root": str(root),
        "dataset_count": total,
        "geodatabases": containers,
        "vectors": vectors,
        "rasters": rasters,
        "tables": tables,
        "other_files_ignored": other_count,
        "hint": (
            "Use describe_vector / describe_raster on any path above for full "
            "detail. For a .gdb, pass the .gdb path plus a layer name."
        ),
    }
