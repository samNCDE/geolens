"""MCP stdio server exposing geolens to Claude Desktop and other MCP clients.

Every tool is a thin wrapper over ``geolens.api``. Results come back as JSON;
rendered PNGs come back as image content so the model can actually look at them.

Run:  python -m geolens.mcp_server
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image

from . import api
from .safety import GeolensError

mcp = FastMCP("geolens")

# Results are cached by handle so a large section extraction is computed once and
# then plotted, exported and re-plotted without recomputing or shipping megabytes
# of coordinates back through the model's context.
_CACHE: dict[str, dict[str, Any]] = {}
_COUNTER = {"n": 0}


def _stash(result: dict[str, Any], prefix: str) -> str:
    _COUNTER["n"] += 1
    handle = f"{prefix}-{_COUNTER['n']}"
    _CACHE[handle] = result
    return handle


def _get(handle: str) -> dict[str, Any]:
    if handle not in _CACHE:
        known = ", ".join(_CACHE) or "(none yet)"
        raise GeolensError(f"Unknown handle {handle!r}. Available: {known}")
    return _CACHE[handle]


def _err(exc: Exception) -> str:
    return json.dumps({"error": str(exc), "type": type(exc).__name__}, indent=1)


def _ok(payload: Any) -> str:
    return json.dumps(payload, indent=1, default=str)


def _image(path: str) -> Image:
    return Image(data=Path(path).read_bytes(), format="png")


# -- inspection ---------------------------------------------------------------


@mcp.tool()
def list_datasets(path: str, recursive: bool = True) -> str:
    """Catalogue every geospatial dataset in a folder.

    Finds shapefiles, GeoPackages, GeoTIFFs/DEMs, and Esri File Geodatabases
    (read without ArcGIS installed). Start here when exploring unfamiliar data.
    """
    try:
        return _ok(api.list_datasets(path, recursive=recursive))
    except Exception as exc:
        return _err(exc)


@mcp.tool()
def describe_vector(path: str, layer: str | None = None) -> str:
    """Describe a vector layer: CRS, geometry types, extent, fields, data quality.

    Pass the .gdb or .gpkg path plus a layer name for multi-layer containers.
    Flags geographic CRS, null and invalid geometry.
    """
    try:
        return _ok(api.describe_vector(path, layer=layer))
    except Exception as exc:
        return _err(exc)


@mcp.tool()
def describe_raster(path: str, stats: bool = True) -> str:
    """Describe a raster or DEM: CRS, cell size, bands, nodata, statistics."""
    try:
        return _ok(api.describe_raster(path, stats=stats))
    except Exception as exc:
        return _err(exc)


@mcp.tool()
def attribute_summary(
    path: str, layer: str | None = None, fields: list[str] | None = None,
    top_n: int = 10,
) -> str:
    """Per-field statistics for a vector layer.

    Numeric fields get min/max/mean/quartiles; text fields get the most common
    values. Use this before sampling rows to know what the data contains.
    """
    try:
        return _ok(api.attribute_summary(path, layer=layer, fields=fields, top_n=top_n))
    except Exception as exc:
        return _err(exc)


@mcp.tool()
def sample_features(
    path: str, layer: str | None = None, n: int = 5, where: str | None = None,
    fields: list[str] | None = None,
) -> str:
    """Return real attribute rows from a vector layer.

    ``where`` is a pandas query expression, not SQL — for example
    ``"SLOPE > 0.02 and CLASS == 'C4'"``.
    """
    try:
        return _ok(
            api.sample_features(path, layer=layer, n=n, where=where, fields=fields)
        )
    except Exception as exc:
        return _err(exc)


@mcp.tool()
def sample_raster(path: str, points: list[list[float]], band: int = 1) -> str:
    """Sample raster values at [[x, y], ...] points in the raster's own CRS."""
    try:
        return _ok(api.sample_raster(path, points, band=band))
    except Exception as exc:
        return _err(exc)


# -- analysis -----------------------------------------------------------------


@mcp.tool()
def longitudinal_profile(
    dem: str, centerline: str, spacing: float = 10.0,
    line_layer: str | None = None, feature_index: int | None = None,
) -> str:
    """Sample DEM elevation along a centerline; returns reach slope and a handle.

    The handle can be passed to plot_result and export_csv. Only summary values
    are returned inline — the full station list stays server-side.
    """
    try:
        r = api.longitudinal_profile(
            dem, centerline, spacing=spacing, line_layer=line_layer,
            feature_index=feature_index,
        )
        h = _stash(r, "profile")
        head = {k: v for k, v in r.items() if k != "profile"}
        head["handle"] = h
        head["profile_preview"] = r["profile"][:5]
        return _ok(head)
    except Exception as exc:
        return _err(exc)


@mcp.tool()
def cross_sections(
    dem: str, centerline: str, spacing: float = 25.0, width: float = 60.0,
    point_spacing: float | None = None, line_layer: str | None = None,
    feature_index: int | None = None,
) -> str:
    """Cut perpendicular cross-sections along a centerline and sample the DEM.

    Returns per-section summaries (thalweg, relief, bank elevations) plus a
    handle. The full point arrays stay server-side; use export_csv to get them.
    """
    try:
        r = api.cross_sections(
            dem, centerline, spacing=spacing, width=width,
            point_spacing=point_spacing, line_layer=line_layer,
            feature_index=feature_index,
        )
        h = _stash(r, "sections")
        slim = {k: v for k, v in r.items() if k != "sections"}
        slim["handle"] = h
        slim["sections"] = [
            {k: v for k, v in s.items() if k != "points"} for s in r["sections"]
        ]
        return _ok(slim)
    except Exception as exc:
        return _err(exc)


@mcp.tool()
def delineate_channel(
    dem: str, centerline: str, spacing: float = 25.0, width: float = 160.0,
    bank_slope: float = 0.15, flat_slope: float = 0.05, valley_slope: float = 0.20,
    min_bank_height: float = 0.3, run_length: int = 2, smooth_window: int = 3,
    max_floodplain_offset: float | None = None, point_spacing: float | None = None,
    line_layer: str | None = None, feature_index: int | None = None,
) -> str:
    """Delineate channel, banks and floodplain by slope break along a reach.

    Walks outward from each thalweg through: flat bed → steep bank face → bank
    top → flat floodplain → valley wall, closing the floodplain at the valley
    wall so it does not climb confining terrain indefinitely.

    Returns bankfull width, width/depth ratio and entrenchment ratio per section
    plus reach summary statistics. All thresholds are slopes as rise/run.

    Tuning: lower ``bank_slope`` for subtle banks; raise ``min_bank_height`` if
    bed roughness registers as banks; raise ``run_length`` on noisy lidar;
    ``width`` must be wide enough to reach the valley walls or floodplain widths
    are a floor rather than a measurement.
    """
    try:
        r = api.delineate_channel(
            dem, centerline, spacing=spacing, width=width, bank_slope=bank_slope,
            flat_slope=flat_slope, valley_slope=valley_slope,
            min_bank_height=min_bank_height, run_length=run_length,
            smooth_window=smooth_window,
            max_floodplain_offset=max_floodplain_offset,
            point_spacing=point_spacing, line_layer=line_layer,
            feature_index=feature_index,
        )
        h = _stash(r, "delineation")
        slim = {k: v for k, v in r.items() if k not in ("sections", "_sections")}
        slim["handle"] = h
        slim["sections"] = [
            {
                "station": s["station"],
                "status": s["status"],
                "bankfull": s.get("bankfull"),
                "floodplain": s.get("floodplain"),
            }
            for s in r["sections"]
        ]
        return _ok(slim)
    except Exception as exc:
        return _err(exc)


# -- output -------------------------------------------------------------------


@mcp.tool()
def plot_result(
    handle: str, kind: str = "auto", station: float | None = None,
    index: int | None = None, name: str | None = None,
):
    """Render a stored result to PNG and return the image.

    ``kind``: auto, profile, cross_section, section_stack, or delineation.
    ``station`` or ``index`` picks which section for the single-section plots.
    """
    try:
        r = _get(handle)
        if kind == "auto":
            if "profile" in r:
                kind = "profile"
            elif "_sections" in r:
                kind = "delineation"
            else:
                kind = "section_stack"

        if kind == "profile":
            out = api.plot_profile(r, name=name or "profile.png")
        elif kind == "cross_section":
            src = r.get("_sections", r)
            out = api.plot_cross_section(
                src, station=station, index=index, name=name or "cross_section.png"
            )
        elif kind == "section_stack":
            src = r.get("_sections", r)
            out = api.plot_section_stack(src, name=name or "section_stack.png")
        elif kind == "delineation":
            out = api.plot_delineation(
                r, station=station, index=index, name=name or "delineation.png"
            )
        else:
            return _err(ValueError(f"Unknown kind {kind!r}"))
        return _image(out["path"])
    except Exception as exc:
        return _err(exc)


@mcp.tool()
def plot_map(
    dem: str | None = None, centerline: str | None = None,
    overlays: list[str] | None = None, name: str = "map.png",
):
    """Render a plan-view map of a DEM with a centerline and overlays."""
    try:
        out = api.plot_map(
            dem=dem, centerline=centerline, overlays=overlays, name=name
        )
        return _image(out["path"])
    except Exception as exc:
        return _err(exc)


@mcp.tool()
def export_csv(handle: str, kind: str, name: str | None = None) -> str:
    """Write a stored result to CSV. ``kind``: profile, sections, delineation."""
    try:
        return _ok(api.export_csv(_get(handle), kind, name=name))
    except Exception as exc:
        return _err(exc)


@mcp.tool()
def workspace() -> str:
    """Show where outputs are written and which folders may be read."""
    try:
        return _ok(api.workspace())
    except Exception as exc:
        return _err(exc)


def main() -> None:
    # Progress bars would corrupt the JSON-RPC stream on stdout; safety.py sends
    # them to stderr, but silence them entirely under MCP to keep logs clean.
    import os

    os.environ.setdefault("GEOLENS_NO_PROGRESS", "1")
    try:
        mcp.run()
    except KeyboardInterrupt:  # pragma: no cover
        sys.exit(0)


if __name__ == "__main__":
    main()
