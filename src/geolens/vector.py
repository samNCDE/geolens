"""Vector dataset inspection: schema, CRS, extent, attribute statistics, samples."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .safety import Guard, GeolensError


def _open(path: Path, layer: str | None = None, rows: int | None = None):
    import geopandas as gpd

    kwargs: dict[str, Any] = {}
    if layer:
        kwargs["layer"] = layer
    if rows:
        kwargs["rows"] = rows
    try:
        return gpd.read_file(str(path), **kwargs)
    except Exception as exc:
        raise GeolensError(f"Could not read {path}{f' [layer={layer}]' if layer else ''}: {exc}") from exc


def _crs_summary(gdf) -> dict[str, Any]:
    crs = gdf.crs
    if crs is None:
        return {
            "defined": False,
            "warning": (
                "No CRS defined. Coordinates cannot be trusted for measurement "
                "or overlay until this is resolved."
            ),
        }
    try:
        epsg = crs.to_epsg()
    except Exception:
        epsg = None
    units = None
    try:
        axis = crs.axis_info
        if axis:
            units = axis[0].unit_name
    except Exception:
        pass
    return {
        "defined": True,
        "name": crs.name,
        "epsg": epsg,
        "is_geographic": bool(crs.is_geographic),
        "units": units,
        "measurement_safe": not bool(crs.is_geographic),
        "warning": (
            "Geographic CRS (degrees). Lengths and areas computed in this CRS are "
            "not meaningful — reproject to a projected CRS before measuring."
            if crs.is_geographic
            else None
        ),
    }


def _jsonable(value: Any) -> Any:
    """Coerce numpy / pandas scalars into plain JSON types."""
    if value is None:
        return None
    if isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    for attr in ("item", "isoformat"):
        if hasattr(value, attr):
            try:
                out = getattr(value, attr)()
                return _jsonable(out) if attr == "item" else out
            except Exception:
                pass
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def describe_vector(
    path: str | Path,
    layer: str | None = None,
    guard: Guard | None = None,
) -> dict[str, Any]:
    """Full structural description of a vector layer.

    Reports CRS (with measurement-safety warnings), geometry types, extent,
    feature count, per-field types and null counts, and geometry validity.
    """
    guard = guard or Guard.from_env()
    p = guard.read_path(path)
    gdf = _open(p, layer)

    geom_col = gdf.geometry.name if gdf.geometry is not None else None
    n = len(gdf)

    geom_types: dict[str, int] = {}
    null_geoms = 0
    invalid = 0
    if geom_col is not None and n:
        gt = gdf.geometry.geom_type
        geom_types = {str(k): int(v) for k, v in gt.value_counts().items()}
        null_geoms = int(gdf.geometry.isna().sum())
        try:
            invalid = int((~gdf.geometry.is_valid).sum())
        except Exception:
            invalid = -1

    bounds = None
    if n and geom_col is not None:
        try:
            b = gdf.total_bounds
            bounds = {
                "minx": _jsonable(b[0]),
                "miny": _jsonable(b[1]),
                "maxx": _jsonable(b[2]),
                "maxy": _jsonable(b[3]),
            }
        except Exception:
            bounds = None

    fields = []
    for col in gdf.columns:
        if col == geom_col:
            continue
        s = gdf[col]
        nulls = int(s.isna().sum())
        fields.append(
            {
                "name": str(col),
                "dtype": str(s.dtype),
                "nulls": nulls,
                "null_pct": round(100.0 * nulls / n, 1) if n else 0.0,
                "unique": int(s.nunique(dropna=True)) if n else 0,
            }
        )

    result: dict[str, Any] = {
        "path": str(p),
        "layer": layer,
        "feature_count": n,
        "crs": _crs_summary(gdf),
        "geometry": {
            "column": geom_col,
            "types": geom_types,
            "null_geometries": null_geoms,
            "invalid_geometries": invalid,
        },
        "extent": bounds,
        "field_count": len(fields),
        "fields": fields,
    }

    warnings = []
    if result["crs"].get("warning"):
        warnings.append(result["crs"]["warning"])
    if null_geoms:
        warnings.append(f"{null_geoms} feature(s) have null geometry.")
    if invalid > 0:
        warnings.append(
            f"{invalid} feature(s) have invalid geometry (self-intersection or "
            "similar). Overlay results may be wrong until repaired."
        )
    if n == 0:
        warnings.append("Layer is empty.")
    result["warnings"] = warnings
    return result


def attribute_summary(
    path: str | Path,
    layer: str | None = None,
    fields: list[str] | None = None,
    top_n: int = 10,
    guard: Guard | None = None,
) -> dict[str, Any]:
    """Per-field statistics: numeric ranges, categorical frequencies, nulls."""
    guard = guard or Guard.from_env()
    p = guard.read_path(path)
    gdf = _open(p, layer)
    geom_col = gdf.geometry.name if gdf.geometry is not None else None
    n = len(gdf)

    cols = [c for c in gdf.columns if c != geom_col]
    if fields:
        missing = [f for f in fields if f not in cols]
        if missing:
            raise GeolensError(
                f"Field(s) not found: {', '.join(missing)}. "
                f"Available: {', '.join(map(str, cols))}"
            )
        cols = list(fields)

    import pandas as pd

    out = []
    for col in cols:
        s = gdf[col]
        nulls = int(s.isna().sum())
        rec: dict[str, Any] = {
            "name": str(col),
            "dtype": str(s.dtype),
            "nulls": nulls,
            "null_pct": round(100.0 * nulls / n, 1) if n else 0.0,
            "unique": int(s.nunique(dropna=True)) if n else 0,
        }
        nonnull = s.dropna()
        if len(nonnull) and pd.api.types.is_numeric_dtype(s):
            desc = nonnull.describe()
            rec["kind"] = "numeric"
            rec["stats"] = {
                k: _jsonable(desc.get(k))
                for k in ("mean", "std", "min", "25%", "50%", "75%", "max")
            }
        elif len(nonnull):
            rec["kind"] = "categorical"
            counts = nonnull.value_counts().head(top_n)
            rec["top_values"] = [
                {"value": _jsonable(k), "count": int(v)} for k, v in counts.items()
            ]
        else:
            rec["kind"] = "empty"
        out.append(rec)

    return {
        "path": str(p),
        "layer": layer,
        "feature_count": n,
        "fields": out,
    }


def sample_features(
    path: str | Path,
    layer: str | None = None,
    n: int = 5,
    where: str | None = None,
    fields: list[str] | None = None,
    guard: Guard | None = None,
) -> dict[str, Any]:
    """Return actual attribute rows so the real values can be inspected.

    ``where`` is a pandas ``query`` expression (e.g. ``"SLOPE > 0.02"``), not SQL.
    Geometry is summarized rather than dumped, to keep output readable.
    """
    guard = guard or Guard.from_env()
    p = guard.read_path(path)
    n = max(1, min(int(n), 200))
    gdf = _open(p, layer)
    geom_col = gdf.geometry.name if gdf.geometry is not None else None
    total = len(gdf)

    if where:
        try:
            gdf = gdf.query(where)
        except Exception as exc:
            raise GeolensError(
                f"Could not apply filter {where!r}: {exc}\n"
                "Use pandas query syntax, e.g. \"SLOPE > 0.02 and CLASS == 'C4'\"."
            ) from exc

    matched = len(gdf)
    head = gdf.head(n)

    rows = []
    for _, row in head.iterrows():
        rec: dict[str, Any] = {}
        for col in head.columns:
            if col == geom_col:
                g = row[col]
                if g is None:
                    rec["_geometry"] = None
                else:
                    rec["_geometry"] = {
                        "type": g.geom_type,
                        "bounds": [_jsonable(v) for v in g.bounds],
                    }
                continue
            if fields and col not in fields:
                continue
            rec[str(col)] = _jsonable(row[col])
        rows.append(rec)

    return {
        "path": str(p),
        "layer": layer,
        "total_features": total,
        "matched_features": matched,
        "returned": len(rows),
        "filter": where,
        "rows": rows,
    }
