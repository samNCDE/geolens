"""Can this computer reach the services geolens downloads from?

One small request to each, a few seconds in all, before a run that depends
on them. A failure names the service and the error, which is the difference
between "the soils step failed after an hour" and "USDA's service is not
reachable from this network" before anything starts.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

_UA = {"User-Agent": "geolens (+geomorphic analysis)"}


def _http(url: str, timeout: int, data: bytes | None = None,
          headers: dict | None = None, method: str | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=dict(_UA, **(headers or {})))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(200_000)


def _json(url: str, params: dict, timeout: int) -> Any:
    return json.loads(_http(f"{url}?{urllib.parse.urlencode(params)}",
                            timeout).decode("utf-8"))


def _tnm(timeout):
    from .dem_fetch import TNM_API, DATASET_1M

    d = _json(TNM_API, {"datasets": DATASET_1M, "max": 1,
                        "bbox": "-105.70,33.30,-105.69,33.31",
                        "outputFormat": "JSON"}, timeout)
    return f"{d.get('total', 0)} lidar tile(s) in a test box"


def _ned(timeout):
    from .dem_fetch import NED13_URL

    _http(NED13_URL.format(tile="n34w106"), timeout, method="HEAD")
    return "1/3 arc-second tiles reachable"


def _wbd(timeout):
    from .project import WBD_HUC12

    d = _json(WBD_HUC12 + "/query",
              {"geometry": "-105.69,33.33", "geometryType": "esriGeometryPoint",
               "inSR": 4326, "outFields": "huc12", "returnGeometry": "false",
               "f": "json"}, timeout)
    feats = d.get("features") or []
    if not feats:
        raise RuntimeError(f"no HUC12 at a known point ({str(d)[:120]})")
    return f"HUC12 {feats[0]['attributes'].get('huc12')}"


def _geology(timeout):
    from .geology import geology_selftest

    r = geology_selftest(timeout=timeout)
    if isinstance(r, dict) and r.get("ok") is False:
        raise RuntimeError(str(r)[:200])
    return "geologic map reachable"


def _sda(timeout):
    from .soils import sda

    rows = sda("SELECT TOP 1 areasymbol FROM legend", timeout=timeout)
    return f"answered ({len(rows)} row)"


def _naip(timeout):
    d = _json("https://planetarycomputer.microsoft.com/api/stac/v1/"
              "collections/naip", {}, timeout)
    return d.get("title") or "NAIP collection reachable"


def _cmr(timeout):
    from .sar import CMR_GRANULES, COLLECTION

    _json(CMR_GRANULES, {"collection_concept_id": COLLECTION,
                         "page_size": 1}, timeout)
    return "Sentinel-1 catalog reachable"


def _login(timeout):
    from .sar import login_status

    s = login_status()
    if not s["saved"]:
        raise RuntimeError("no Earthdata login saved (only radar needs one)")
    return f"saved for {s['username']}"


CHECKS: list[tuple[str, str, Callable[[int], str]]] = [
    ("USGS 3DEP lidar search (The National Map)", "DEM fetch", _tnm),
    ("USGS 3DEP 1/3 arc-second tiles", "DEM fetch, projects", _ned),
    ("USGS Watershed Boundary Dataset", "projects, watershed clip", _wbd),
    ("USGS national geologic map", "valley types (geology)", _geology),
    ("USDA Soil Data Access (SSURGO)", "soils", _sda),
    ("Microsoft Planetary Computer (NAIP)", "NAIP, riparian band", _naip),
    ("NASA CMR (Sentinel-1 search)", "radar", _cmr),
    ("NASA Earthdata login on this computer", "radar", _login),
]


def check_services(timeout: int = 20) -> dict[str, Any]:
    """Try each service once. ``ok`` is False for any that failed; the
    others still work, and the tools that need only those still run."""
    rows = []
    for name, used_by, fn in CHECKS:
        t0 = time.monotonic()
        try:
            detail, ok = fn(timeout), True
        except Exception as e:                               # noqa: BLE001
            detail, ok = f"{type(e).__name__}: {e}"[:300], False
        rows.append({"service": name, "used_by": used_by, "ok": ok,
                     "seconds": round(time.monotonic() - t0, 1),
                     "detail": detail})
    failed = [r["service"] for r in rows if not r["ok"]]
    return {"ok": not failed, "failed": failed, "services": rows}
