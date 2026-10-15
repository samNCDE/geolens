"""Fetch a watershed boundary from the USGS Watershed Boundary Dataset.

The convenient option, and the least reliable: it is a network call to a
service geolens does not control. It is kept behind its own module and has a
selftest, like the geology and NAIP fetchers, so "is the service up" is a
question with an answer rather than a failed run.

**Not verified in the environment this was written in** — outbound access to
the ArcGIS services is blocked there. The request shape follows the same
pattern as ``geology.py``, which was verified. Run ``huc_selftest()`` before
relying on it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..safety import Guard, GeolensError, note

# The WBD is published as an ArcGIS MapServer. Layer 12 is HUC12.
WBD_URL = ("https://hydro.nationalmap.gov/arcgis/rest/services/wbd/"
           "MapServer/{layer}/query")
_LAYERS = {"huc2": 1, "huc4": 2, "huc6": 3, "huc8": 4, "huc10": 5, "huc12": 6}


def _extent_4326(dem: str) -> tuple[float, float, float, float]:
    import rasterio
    from rasterio.warp import transform_bounds

    with rasterio.open(str(dem)) as src:
        if src.crs is None:
            raise GeolensError(
                f"{dem} has no CRS, so its extent cannot be expressed in "
                f"longitude/latitude to query the WBD."
            )
        return transform_bounds(src.crs, "EPSG:4326", *src.bounds)


def _get_json(url: str, params: dict, timeout: int = 60) -> tuple[int, dict]:
    """GET a JSON reply with the standard library. ``requests`` was used here
    and is not a geolens dependency: on a clean install the HUC option failed
    with ModuleNotFoundError before reaching the network."""
    import urllib.parse
    import urllib.request

    req = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": "geolens (+geomorphic analysis)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return int(getattr(r, "status", 200)), json.loads(r.read().decode("utf-8"))


def fetch_huc(dem: str, huc: str = "auto", level: str = "huc12",
              guard: Guard | None = None) -> tuple[Any, dict[str, Any]]:
    """Fetch the watershed boundary covering a DEM's extent.

    ``huc`` is ``"auto"`` to take whatever covers the DEM's centre, or an
    explicit HUC code. ``level`` picks the WBD layer.

    Returns ``(GeoDataFrame, metadata)``.
    """
    import geopandas as gpd

    guard = guard or Guard.from_env()
    if level not in _LAYERS:
        raise GeolensError(
            f"level={level!r} is not a WBD layer. Choose from "
            f"{sorted(_LAYERS)}."
        )
    w, s, e, n = _extent_4326(str(guard.read_path(dem)))
    params: dict[str, Any] = {
        "f": "geojson", "outFields": "*", "returnGeometry": "true",
        "outSR": 4326,
    }
    if huc and huc != "auto":
        field = level.upper()
        params["where"] = f"{field}='{huc}'"
    else:
        cx, cy = (w + e) / 2.0, (s + n) / 2.0
        params["geometry"] = json.dumps(
            {"x": cx, "y": cy, "spatialReference": {"wkid": 4326}})
        params["geometryType"] = "esriGeometryPoint"
        params["inSR"] = 4326
        params["spatialRel"] = "esriSpatialRelIntersects"

    url = WBD_URL.format(layer=_LAYERS[level])
    note(f"querying the WBD for {level} over the DEM's extent")
    try:
        _, payload = _get_json(url, params, timeout=60)
    except Exception as exc:
        raise GeolensError(
            f"Could not reach the USGS Watershed Boundary Dataset ({exc}). "
            f"This is a network call to a service geolens does not control — "
            f"run geolens.landscape.huc_selftest() to check it, or download "
            f"the HUC once and pass it with boundary= instead, which needs no "
            f"network at all."
        ) from exc

    feats = payload.get("features") or []
    if not feats:
        raise GeolensError(
            f"The WBD returned no {level} polygon for this DEM. If the DEM is "
            f"outside the United States the WBD does not cover it; supply a "
            f"boundary with boundary= or delineate one with pour_point=."
        )
    gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")
    meta = {"level": level, "features": len(gdf), "requested": huc,
            "extent_4326": [w, s, e, n]}
    return gdf, meta


def huc_selftest(guard: Guard | None = None) -> dict[str, Any]:
    """Is the WBD service reachable and returning what we expect?

    Queries a known point (the Rio Ruidoso, New Mexico) and reports what came
    back. Separate from any analysis so a failure here is unambiguous: the
    service, not the data or the code.
    """
    out: dict[str, Any] = {"service": WBD_URL.format(layer=_LAYERS["huc12"])}
    params = {
        "f": "geojson", "outFields": "*", "returnGeometry": "false",
        "geometry": json.dumps({"x": -105.67, "y": 33.33,
                                "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryPoint", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
    }
    try:
        status, doc = _get_json(out["service"], params, timeout=45)
        out["status_code"] = status
        feats = (doc.get("features") or [])
        out["ok"] = bool(feats)
        out["features"] = len(feats)
        if feats:
            props = feats[0].get("properties", {})
            out["example"] = {k: props.get(k) for k in
                              ("huc12", "HUC12", "name", "NAME") if k in props}
    except Exception as exc:
        out["ok"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["advice"] = (
            "Download the HUC once from the National Map and pass it with "
            "boundary= — that path needs no network."
        )
    return out
