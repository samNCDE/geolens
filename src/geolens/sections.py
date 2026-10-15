"""Longitudinal profiles and perpendicular cross-sections from a DEM + centerline.

This is the core analytical workhorse for channel and corridor work. Everything
here is pure numpy + rasterio + shapely: no ArcGIS licence, no 3D Analyst, and
it runs identically on Windows, macOS, and Linux.

Conventions
-----------
* **Station** is distance along the centerline from its start, in CRS units.
* **Offset** is signed distance from the centerline on a cross-section: negative
  to the left of the direction of travel, positive to the right.
* Cross-sections are cut perpendicular to the local tangent of the centerline.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .safety import Guard, GeolensError, progress, note


def _load_centerline(path: Path, layer: str | None, feature_index: int | None):
    """Return a single shapely LineString plus the source CRS."""
    import geopandas as gpd
    from shapely.geometry import LineString, MultiLineString
    from shapely.ops import linemerge

    kwargs: dict[str, Any] = {"layer": layer} if layer else {}
    try:
        gdf = gpd.read_file(str(path), **kwargs)
    except Exception as exc:
        raise GeolensError(f"Could not read centerline {path}: {exc}") from exc

    if gdf.empty:
        raise GeolensError(f"Centerline layer is empty: {path}")

    lines = gdf[gdf.geometry.geom_type.isin(["LineString", "MultiLineString"])]
    if lines.empty:
        found = ", ".join(sorted(set(gdf.geometry.geom_type)))
        raise GeolensError(
            f"No line geometry in {path} (found: {found}). "
            "A centerline must be a LineString or MultiLineString."
        )

    if feature_index is not None:
        if feature_index < 0 or feature_index >= len(lines):
            raise GeolensError(
                f"feature_index {feature_index} out of range — "
                f"layer has {len(lines)} line feature(s)."
            )
        geom = lines.geometry.iloc[feature_index]
        merged_note = None
    elif len(lines) == 1:
        geom = lines.geometry.iloc[0]
        merged_note = None
    else:
        try:
            geom = linemerge(MultiLineString(list(lines.geometry)))
            merged_note = (
                f"{len(lines)} line features were merged into a single centerline."
            )
        except Exception as exc:
            raise GeolensError(
                f"Layer has {len(lines)} line features that could not be merged "
                f"({exc}). Pass feature_index to pick one explicitly."
            ) from exc

    if isinstance(geom, MultiLineString):
        parts = list(geom.geoms)
        merged = linemerge(geom)
        if isinstance(merged, MultiLineString):
            # The longest chain after merging, not the longest input piece:
            # a network digitized in short pieces has no long piece.
            longest = max(merged.geoms, key=lambda g: g.length)
            geom = longest
            merged_note = (
                f"Centerline is discontinuous or branched ({len(parts)} "
                f"pieces, {len(merged.geoms)} after joining); used the "
                f"longest joined line only. Pass feature_index to pick one."
            )
        else:
            geom = merged

    if not isinstance(geom, LineString) or geom.length == 0:
        raise GeolensError("Centerline resolved to an empty or invalid line.")

    return geom, gdf.crs, merged_note


def _prepare(dem_path: Path, line_path: Path, line_layer, feature_index):
    """Load DEM + centerline, reconcile CRS, and flag measurement hazards."""
    import rasterio

    try:
        src = rasterio.open(str(dem_path))
    except Exception as exc:
        raise GeolensError(f"Could not open DEM {dem_path}: {exc}") from exc

    geom, line_crs, merge_note = _load_centerline(line_path, line_layer, feature_index)

    warnings: list[str] = []
    if merge_note:
        warnings.append(merge_note)

    dem_crs = src.crs
    if dem_crs is None:
        warnings.append(
            "DEM has no CRS. Station and offset distances are in raw grid units "
            "and cannot be verified."
        )
    if line_crs is None:
        warnings.append(
            "Centerline has no CRS. Assuming it matches the DEM — verify this."
        )
    elif dem_crs is not None and line_crs != dem_crs:
        from shapely.ops import transform
        from pyproj import Transformer

        tr = Transformer.from_crs(line_crs, dem_crs, always_xy=True)
        geom = transform(lambda x, y, z=None: tr.transform(x, y), geom)
        warnings.append(
            f"Centerline was reprojected from {line_crs.to_string()} to the DEM CRS "
            f"{dem_crs.to_string()} before sampling."
        )

    if dem_crs is not None and dem_crs.is_geographic:
        warnings.append(
            "DEM is in a geographic CRS (degrees). Station spacing and section width "
            "are therefore in DEGREES, not metres or feet — results will be "
            "distorted. Reproject the DEM to a projected CRS before using these "
            "numbers for design."
        )

    return src, geom, warnings


def _tangent(line, station: float, delta: float) -> tuple[float, float]:
    """Unit tangent vector at ``station`` via a central difference."""
    a = line.interpolate(max(0.0, station - delta))
    b = line.interpolate(min(line.length, station + delta))
    dx, dy = b.x - a.x, b.y - a.y
    mag = math.hypot(dx, dy)
    if mag == 0:
        return 1.0, 0.0
    return dx / mag, dy / mag


def _stations(length: float, spacing: float) -> list[float]:
    if spacing <= 0:
        raise GeolensError("spacing must be greater than zero.")
    n = int(length // spacing)
    out = [i * spacing for i in range(n + 1)]
    if out[-1] < length - 1e-9:
        out.append(length)
    return out


def longitudinal_profile(
    dem: str | Path,
    centerline: str | Path,
    spacing: float = 10.0,
    line_layer: str | None = None,
    feature_index: int | None = None,
    band: int = 1,
    guard: Guard | None = None,
) -> dict[str, Any]:
    """Sample DEM elevation along a centerline at fixed station intervals.

    Returns station/elevation pairs plus reach slope, computed by least-squares
    fit over all valid samples (more robust than endpoint-to-endpoint).
    """
    import numpy as np

    guard = guard or Guard.from_env()
    dem_p = guard.read_path(dem)
    line_p = guard.read_path(centerline)

    src, line, warnings = _prepare(dem_p, line_p, line_layer, feature_index)

    with src:
        nodata = src.nodatavals[band - 1]
        stations = _stations(line.length, spacing)
        note(f"Sampling {len(stations)} stations along {line.length:,.1f} units of centerline")

        pts = []
        for s in stations:
            p = line.interpolate(s)
            pts.append((p.x, p.y))

        elevations: list[float | None] = []
        chunk = 2000
        chunks = [pts[i : i + chunk] for i in range(0, len(pts), chunk)]
        for c in progress(chunks, label="profile: sampling DEM"):
            for v in src.sample(c, indexes=band):
                raw = float(v[0])
                bad = math.isnan(raw) or (
                    nodata is not None
                    and (raw == nodata or (math.isnan(float(nodata)) and math.isnan(raw)))
                )
                elevations.append(None if bad else raw)

    valid = [(s, e) for s, e in zip(stations, elevations) if e is not None]
    slope = None
    drop = None
    if len(valid) >= 2:
        xs = np.array([v[0] for v in valid])
        ys = np.array([v[1] for v in valid])
        slope = float(-np.polyfit(xs, ys, 1)[0])  # positive = falling downstream
        drop = float(ys[0] - ys[-1])

    if len(valid) < len(stations):
        warnings.append(
            f"{len(stations) - len(valid)} of {len(stations)} stations fell on DEM "
            "nodata — the centerline may extend beyond DEM coverage."
        )

    return {
        "dem": str(dem_p),
        "centerline": str(line_p),
        "length": float(line.length),
        "spacing": float(spacing),
        "station_count": len(stations),
        "valid_samples": len(valid),
        "elevation": {
            "min": float(min(v[1] for v in valid)) if valid else None,
            "max": float(max(v[1] for v in valid)) if valid else None,
            "start": valid[0][1] if valid else None,
            "end": valid[-1][1] if valid else None,
            "drop": drop,
        },
        "reach_slope": slope,
        "reach_slope_pct": round(slope * 100, 4) if slope is not None else None,
        "profile": [
            {"station": round(s, 4), "elevation": e}
            for s, e in zip(stations, elevations)
        ],
        "warnings": warnings,
    }


def extract_cross_sections(
    dem: str | Path,
    centerline: str | Path,
    spacing: float = 25.0,
    width: float = 60.0,
    point_spacing: float | None = None,
    line_layer: str | None = None,
    feature_index: int | None = None,
    band: int = 1,
    guard: Guard | None = None,
) -> dict[str, Any]:
    """Cut perpendicular cross-sections along a centerline and sample the DEM.

    Parameters
    ----------
    spacing
        Distance between cross-sections along the centerline, in CRS units.
    width
        Total section width; each section runs ``width/2`` either side.
    point_spacing
        Sample interval across the section. Defaults to the DEM cell size, which
        is the finest spacing that carries real information.

    Each returned section includes its thalweg (lowest point) offset and
    elevation, and the local bank elevations at the section ends — the numbers
    most channel work starts from.
    """
    guard = guard or Guard.from_env()
    dem_p = guard.read_path(dem)
    line_p = guard.read_path(centerline)

    src, line, warnings = _prepare(dem_p, line_p, line_layer, feature_index)

    with src:
        cell = min(abs(src.res[0]), abs(src.res[1]))
        step = float(point_spacing) if point_spacing else cell
        if step <= 0:
            raise GeolensError("point_spacing must be greater than zero.")
        if point_spacing and point_spacing < cell / 2:
            warnings.append(
                f"point_spacing ({point_spacing}) is finer than half the DEM cell "
                f"size ({cell}). Extra points interpolate nothing new."
            )

        nodata = src.nodatavals[band - 1]
        stations = _stations(line.length, spacing)
        half = float(width) / 2.0
        n_per = int(round(width / step)) + 1
        offsets = [-half + i * step for i in range(n_per)]
        delta = max(step, line.length / 1000.0)

        note(
            f"Cutting {len(stations)} sections x {n_per} points "
            f"({len(stations) * n_per:,} DEM samples)"
        )

        sections: list[dict[str, Any]] = []
        for s in progress(stations, label="extracting sections"):
            origin = line.interpolate(s)
            tx, ty = _tangent(line, s, delta)
            nx, ny = -ty, tx  # left-hand normal

            coords = [(origin.x + nx * o, origin.y + ny * o) for o in offsets]
            vals = []
            for v in src.sample(coords, indexes=band):
                raw = float(v[0])
                bad = math.isnan(raw) or (
                    nodata is not None
                    and (
                        raw == nodata
                        or (math.isnan(float(nodata)) and math.isnan(raw))
                    )
                )
                vals.append(None if bad else raw)

            pairs = [(o, e) for o, e in zip(offsets, vals) if e is not None]
            thalweg = min(pairs, key=lambda t: t[1]) if pairs else (None, None)
            left_bank = pairs[0][1] if pairs else None
            right_bank = pairs[-1][1] if pairs else None
            elevs = [e for _, e in pairs]

            sections.append(
                {
                    "station": round(s, 4),
                    "center_x": origin.x,
                    "center_y": origin.y,
                    "bearing_deg": round(math.degrees(math.atan2(tx, ty)) % 360, 2),
                    "valid_points": len(pairs),
                    "thalweg_offset": thalweg[0],
                    "thalweg_elevation": thalweg[1],
                    "left_end_elevation": left_bank,
                    "right_end_elevation": right_bank,
                    "min_elevation": min(elevs) if elevs else None,
                    "max_elevation": max(elevs) if elevs else None,
                    "relief": (max(elevs) - min(elevs)) if elevs else None,
                    "points": [
                        {"offset": round(o, 4), "elevation": e}
                        for o, e in zip(offsets, vals)
                    ],
                }
            )

    empty = [s["station"] for s in sections if s["valid_points"] == 0]
    if empty:
        warnings.append(
            f"{len(empty)} section(s) returned no valid elevations — they likely "
            f"fall outside DEM coverage (first at station {empty[0]})."
        )
    partial = [s for s in sections if 0 < s["valid_points"] < n_per]
    if partial:
        warnings.append(
            f"{len(partial)} section(s) are partially outside DEM coverage; "
            "their bank elevations are clipped, not true section ends."
        )

    return {
        "dem": str(dem_p),
        "centerline": str(line_p),
        "centerline_length": float(line.length),
        "spacing": float(spacing),
        "width": float(width),
        "point_spacing": step,
        "dem_cell_size": cell,
        "section_count": len(sections),
        "points_per_section": n_per,
        "sections": sections,
        "warnings": warnings,
        "convention": (
            "Offset is signed distance from the centerline: negative = left of "
            "the direction of travel, positive = right."
        ),
    }


def sections_to_csv(
    result: dict[str, Any],
    name: str = "cross_sections.csv",
    guard: Guard | None = None,
) -> dict[str, Any]:
    """Write a cross-section result to tidy CSV (one row per sampled point)."""
    import csv

    guard = guard or Guard.from_env()
    out = guard.write_path(name)
    rows = 0
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["station", "offset", "elevation", "center_x", "center_y"])
        for sec in result.get("sections", []):
            for pt in sec["points"]:
                w.writerow(
                    [
                        sec["station"],
                        pt["offset"],
                        "" if pt["elevation"] is None else pt["elevation"],
                        sec["center_x"],
                        sec["center_y"],
                    ]
                )
                rows += 1
    return {"path": str(out), "rows": rows}


def profile_to_csv(
    result: dict[str, Any],
    name: str = "profile.csv",
    guard: Guard | None = None,
) -> dict[str, Any]:
    """Write a longitudinal profile result to CSV."""
    import csv

    guard = guard or Guard.from_env()
    out = guard.write_path(name)
    rows = 0
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["station", "elevation"])
        for pt in result.get("profile", []):
            w.writerow(
                [pt["station"], "" if pt["elevation"] is None else pt["elevation"]]
            )
            rows += 1
    return {"path": str(out), "rows": rows}
