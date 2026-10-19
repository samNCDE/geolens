"""SSURGO soil map units for an area, from USDA-NRCS Soil Data Access.

Why: valley typing needs to know what a valley floor is made of, and the
national geologic map (``geology.py``) is compiled at 1:50,000 to
1:1,000,000, so a valley 80 m wide often comes back as the bedrock of its
hillslopes. SSURGO is mapped at about 1:24,000 and records each map unit's
parent material ("alluvium", "colluvium", "residuum") and landform ("flood
plains", "alluvial fans"). A valley floor's own alluvium is usually there.

What it writes, in ``soils_dir`` in the output folder:

``soils.gpkg``  (layer ``soils``) every map unit polygon in the area, clipped
    to it, with ``material_class`` in geology.py's vocabulary, so the layer
    can be given wherever geology is asked for. The dominant component's
    name, parent material and landform, and the map unit's flooding
    frequency, drainage class, hydrologic group and depth to bedrock come
    with it.
``soils_sources.json``  what was asked of Soil Data Access, and what came
    back.

Soil Data Access (SDA) is a public SQL service with no key. A query returns
at most 100,000 rows, so the area is asked for in tiles of
:data:`TILE_DEG` degrees, and a tile that fails is split in four and asked
again.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .geology import classify_material
from .safety import Guard, GeolensError, note, progress

SDA_URLS = ("https://sdmdataaccess.nrcs.usda.gov/Tabular/post.rest",
            "https://SDMDataAccess.sc.egov.usda.gov/Tabular/post.rest")
TILE_DEG = 0.1                 # about 9 x 11 km in New Mexico
MAX_SPLITS = 3                 # a failing tile is split at most this deep
BATCH = 400                    # keys per attribute query
MAX_AREA_KM2 = 20_000.0
_UA = {"User-Agent": "geolens (+geomorphic analysis)"}

# Parent materials geology.py's keywords would get wrong, checked first.
# "Slope alluvium" is hillslope wash, not a valley floor's alluvium; ash and
# fill say nothing about how a valley was built.
_SOIL_FIRST: list[tuple[str | None, tuple[str, ...]]] = [
    ("mass_movement", ("slope alluvium", "valley side alluvium", "colluvium",
                       "creep deposits", "mudflow", "debris flow",
                       "landslide", "mass movement")),
    ("residual", ("residuum",)),
    (None, ("volcanic ash", "tephra", "mine spoil", "earthy fill",
            "human-transported")),
]

COLUMNS = ["mukey", "musym", "muname", "compname", "comppct_r", "pmkind",
           "pmgroupname", "landform", "flodfreqdcd", "drclassdcd",
           "hydgrpdcd", "brockdepmin", "kwfact", "material_class"]


def soil_material(pmkind: str | None, pmgroupname: str | None = None
                  ) -> str | None:
    """A parent material as one of geology.py's material classes, or None
    when it does not say how the ground was laid down."""
    for text in (pmkind, pmgroupname):
        if not text:
            continue
        low = str(text).lower()
        for cls, keys in _SOIL_FIRST:
            if any(k in low for k in keys):
                return cls
        got = classify_material(low)
        if got:
            return got
    return None


# -- Soil Data Access ---------------------------------------------------------------

class SDAQueryError(GeolensError):
    """SDA answered, and the answer was an error about the query itself."""


class SDAUnreachable(GeolensError):
    """No answer at all: no network, a proxy, the service down."""


_QUERY_FAULTS = ("syntax", "invalid column", "invalid object", "incorrect",
                 "must declare", "conversion failed")


def _rows(text: str) -> list[dict]:
    text = (text or "").strip()
    if not text:
        return []
    data = json.loads(text)
    table = data.get("Table") if isinstance(data, dict) else None
    if not table:
        return []                       # SDA answers {} when nothing matches
    cols = table[0]
    return [dict(zip(cols, row)) for row in table[1:]]


def _post(url: str, query: str, as_json: bool, timeout: int) -> str:
    fields = {"query": query, "format": "JSON+COLUMNNAME"}
    if as_json:
        body = json.dumps(fields).encode("utf-8")
        ctype = "application/json"
    else:
        body = urllib.parse.urlencode(fields).encode("utf-8")
        ctype = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body,
                                 headers=dict(_UA, **{"Content-Type": ctype}))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def sda(query: str, timeout: int = 120) -> list[dict]:
    """Rows of one SDA query, as dicts.

    Sent as JSON, and as a form if the service refuses JSON; to the NRCS
    address, then the older one. A query the service rejects is raised with
    its own message, not retried.
    """
    last: Exception | None = None
    answered = False
    for url in SDA_URLS:
        for as_json in (True, False):
            try:
                return _rows(_post(url, query, as_json, timeout))
            except urllib.error.HTTPError as e:
                answered = True
                detail = ""
                try:
                    detail = e.read().decode("utf-8", "replace")[:400]
                except Exception:                            # noqa: BLE001
                    pass
                last = GeolensError(f"HTTP {e.code}: {detail or e.reason}")
                if not as_json and any(k in detail.lower()
                                       for k in _QUERY_FAULTS):
                    raise SDAQueryError(f"Soil Data Access rejected the "
                                        f"query: {detail}") from e
            except (TimeoutError, ValueError) as e:     # slow, or not JSON
                answered = True
                last = e
            except urllib.error.URLError as e:
                if isinstance(e.reason, TimeoutError):
                    answered = True
                last = e
                time.sleep(1.0)
            except Exception as e:                           # noqa: BLE001
                last = e
                time.sleep(1.0)
    kind = GeolensError if answered else SDAUnreachable
    raise kind(
        f"Could not get an answer from USDA Soil Data Access: "
        f"{type(last).__name__}: {last}") from last


# -- the polygons --------------------------------------------------------------------

def _box_wkt(w: float, s: float, e: float, n: float) -> str:
    return (f"POLYGON(({w:.6f} {s:.6f}, {e:.6f} {s:.6f}, {e:.6f} {n:.6f}, "
            f"{w:.6f} {n:.6f}, {w:.6f} {s:.6f}))")


def _tile_query(w: float, s: float, e: float, n: float) -> str:
    return ("SELECT P.mupolygonkey, P.mukey, P.mupolygongeo.STAsText() AS wkt "
            "FROM mupolygon AS P WHERE P.mupolygongeo.STIntersects("
            f"geometry::STGeomFromText('{_box_wkt(w, s, e, n)}', 4326)) = 1")


def _tile_polygons(bounds: tuple[float, float, float, float],
                   ask: Callable[[str], list[dict]], depth: int = 0
                   ) -> tuple[list[dict], int]:
    """Map unit polygons touching one tile; (rows, tiles asked). A tile the
    service cannot answer (too many rows, too long) is split in four."""
    w, s, e, n = bounds
    try:
        return ask(_tile_query(w, s, e, n)), 1
    except (SDAQueryError, SDAUnreachable):
        raise                           # splitting cannot help with these
    except GeolensError:
        if depth >= MAX_SPLITS:
            raise
    mx, my = (w + e) / 2, (s + n) / 2
    rows: list[dict] = []
    asked = 0
    for b in ((w, s, mx, my), (mx, s, e, my), (w, my, mx, n), (mx, my, e, n)):
        got, k = _tile_polygons(b, ask, depth + 1)
        rows.extend(got)
        asked += k
    return rows, asked


def _tiles(aoi_wgs84) -> list[tuple[float, float, float, float]]:
    from shapely.geometry import box

    w, s, e, n = aoi_wgs84.bounds
    out = []
    y = s
    while y < n:
        x = w
        while x < e:
            b = (x, y, min(x + TILE_DEG, e), min(y + TILE_DEG, n))
            if box(*b).intersects(aoi_wgs84):
                out.append(b)
            x += TILE_DEG
        y += TILE_DEG
    return out


# -- the attributes -------------------------------------------------------------------

def _keys(values) -> list[int]:
    """Integer keys only: they go into SQL text."""
    out = set()
    for v in values:
        try:
            out.add(int(v))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def _number(v, default=None):
    """SDA sends numbers as text."""
    try:
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _polygonal(geom):
    """The polygon parts of a clipped geometry (clipping along a shared edge
    leaves lines and points), or None."""
    from shapely.geometry import MultiPolygon

    if geom is None or geom.is_empty:
        return None
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    parts = []
    for p in getattr(geom, "geoms", []):
        if p.geom_type == "Polygon":
            parts.append(p)
        elif p.geom_type == "MultiPolygon":
            parts.extend(p.geoms)
    return MultiPolygon(parts) if parts else None


def _batched(ask, template: str, keys: list[int]) -> list[dict]:
    rows: list[dict] = []
    for i in range(0, len(keys), BATCH):
        rows.extend(ask(template.format(
            keys=",".join(str(k) for k in keys[i:i + BATCH]))))
    return rows


def _attributes(mukeys: list[int], ask) -> dict[int, dict]:
    """Per map unit: its own fields and its dominant component's."""
    out: dict[int, dict] = {}
    for r in _batched(ask, (
            "SELECT mu.mukey, mu.musym, mu.muname, ma.flodfreqdcd, "
            "ma.drclassdcd, ma.hydgrpdcd, ma.brockdepmin FROM mapunit AS mu "
            "LEFT OUTER JOIN muaggatt AS ma ON ma.mukey = mu.mukey "
            "WHERE mu.mukey IN ({keys})"), mukeys):
        out[int(r["mukey"])] = dict(r)

    dominant: dict[int, dict] = {}
    for r in _batched(ask, (
            "SELECT c.mukey, c.cokey, c.compname, c.comppct_r "
            "FROM component AS c WHERE c.mukey IN ({keys})"), mukeys):
        mk = int(r["mukey"])
        pct = float(r.get("comppct_r") or 0)
        best = dominant.get(mk)
        if best is None or pct > float(best.get("comppct_r") or 0):
            dominant[mk] = dict(r)
    cokeys = _keys(c["cokey"] for c in dominant.values())

    pm: dict[int, tuple] = {}            # cokey -> (rank, pmkind, group name)
    for r in _batched(ask, (
            "SELECT g.cokey, g.pmgroupname, g.rvindicator, p.pmkind, "
            "p.pmorder FROM copmgrp AS g LEFT OUTER JOIN copm AS p "
            "ON p.copmgrpkey = g.copmgrpkey WHERE g.cokey IN ({keys})"),
            cokeys):
        ck = int(r["cokey"])
        rank = (0 if str(r.get("rvindicator") or "").lower() == "yes" else 1,
                _number(r.get("pmorder"), 99))
        if ck not in pm or rank < pm[ck][0]:
            pm[ck] = (rank, r.get("pmkind"), r.get("pmgroupname"))

    landform: dict[int, tuple] = {}
    for r in _batched(ask, (
            "SELECT d.cokey, d.geomfname, d.rvindicator FROM cogeomordesc AS d "
            "WHERE d.geomftname = 'Landform' AND d.cokey IN ({keys})"), cokeys):
        ck = int(r["cokey"])
        rank = 0 if str(r.get("rvindicator") or "").lower() == "yes" else 1
        if ck not in landform or rank < landform[ck][0]:
            landform[ck] = (rank, r.get("geomfname"))

    # Erodibility (whole-soil K) of the dominant component's surface horizon.
    kw: dict[int, tuple] = {}
    for r in _batched(ask, (
            "SELECT ch.cokey, ch.hzdept_r, ch.kwfact FROM chorizon AS ch "
            "WHERE ch.cokey IN ({keys})"), cokeys):
        ck = int(r["cokey"])
        top = _number(r.get("hzdept_r"), 999.0)
        if ck not in kw or top < kw[ck][0]:
            kw[ck] = (top, _number(r.get("kwfact")))

    for mk, rec in out.items():
        comp = dominant.get(mk) or {}
        ck = int(comp["cokey"]) if comp.get("cokey") not in (None, "") else None
        _, kind, group = pm.get(ck, (None, None, None))
        rec["brockdepmin"] = _number(rec.get("brockdepmin"))
        rec["kwfact"] = (kw.get(ck) or (None, None))[1]
        rec.update(compname=comp.get("compname"),
                   comppct_r=_number(comp.get("comppct_r")), pmkind=kind,
                   pmgroupname=group,
                   landform=(landform.get(ck) or (None, None))[1],
                   material_class=soil_material(kind, group))
    return out


# -- public ----------------------------------------------------------------------------

def fetch_soils(aoi: str | Path, buffer_m: float = 0.0, timeout: int = 120,
                guard: Guard | None = None,
                ask: Callable[[str], list[dict]] | None = None,
                max_area_km2: float = MAX_AREA_KM2) -> dict[str, Any]:
    """SSURGO map units over ``aoi`` (a raster's extent, or a polygon layer),
    with ``material_class`` for valley typing. ``buffer_m`` widens the area.
    Writes ``soils_dir/soils.gpkg`` in the AOI's coordinate system."""
    import geopandas as gpd
    from pyproj import Transformer
    from shapely import wkt as shapely_wkt
    from shapely.ops import transform as reproject_geom

    from .naip import _aoi_geometry
    from .sar import _utm_for

    guard = guard or Guard.from_env()
    ask = ask or (lambda q: sda(q, timeout))
    t0 = time.monotonic()
    geom, crs, _ = _aoi_geometry(str(aoi), guard)
    aoi_wgs = reproject_geom(
        Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform, geom)
    utm = _utm_for(aoi_wgs.centroid.x, aoi_wgs.centroid.y)
    to_utm = Transformer.from_crs("EPSG:4326", utm, always_xy=True).transform
    from_utm = Transformer.from_crs(utm, "EPSG:4326", always_xy=True).transform
    aoi_utm = reproject_geom(to_utm, aoi_wgs)
    if buffer_m:
        aoi_utm = aoi_utm.buffer(float(buffer_m))
        aoi_wgs = reproject_geom(from_utm, aoi_utm)
    km2 = aoi_utm.area / 1e6
    if km2 > max_area_km2:
        raise GeolensError(f"The area is {km2:,.0f} km2; SSURGO is fetched for "
                           f"up to {max_area_km2:,.0f} km2 at a time.")

    tiles = _tiles(aoi_wgs)
    note(f"SSURGO: {km2:,.1f} km2 in {len(tiles)} tile(s) from Soil Data Access")
    polys: dict[int, dict] = {}
    asked = 0
    for b in progress(tiles, label="SSURGO tiles"):
        rows, k = _tile_polygons(b, ask)
        asked += k
        for r in rows:
            polys.setdefault(int(r["mupolygonkey"]), r)
    if not polys:
        raise GeolensError("Soil Data Access has no SSURGO map units in this "
                           "area (it may not be surveyed).")

    attrs = _attributes(_keys(r["mukey"] for r in polys.values()), ask)
    records, geoms = [], []
    for key, r in polys.items():
        try:
            g = shapely_wkt.loads(r["wkt"])
        except Exception:                                    # noqa: BLE001
            continue
        rec = {"mupolygonkey": key}
        a = attrs.get(int(r["mukey"]), {})
        rec.update({c: a.get(c) for c in COLUMNS})
        rec["mukey"] = int(r["mukey"])
        records.append(rec)
        geoms.append(g)
    g = gpd.GeoDataFrame(records, geometry=geoms, crs="EPSG:4326")
    g["geometry"] = g.geometry.make_valid() if hasattr(g.geometry, "make_valid") \
        else g.geometry.buffer(0)
    g = gpd.clip(g, aoi_wgs)
    g["geometry"] = g.geometry.apply(_polygonal)
    g = g[g.geometry.notna()].to_crs(crs)
    g["area_km2"] = (g.to_crs(utm).geometry.area / 1e6).round(4)

    out_dir = Path(guard.write_path("soils_dir"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "soils.gpkg"
    g.to_file(str(path), driver="GPKG", layer="soils")
    by_class = (g.groupby(g["material_class"].fillna("unknown"))["area_km2"]
                .sum().round(3).to_dict())
    record = {"source": "USDA-NRCS SSURGO via Soil Data Access",
              "tiles": len(tiles), "queries_for_polygons": asked,
              "polygons": int(len(g)), "map_units": len(attrs),
              "area_km2": round(km2, 3), "km2_by_material": by_class}
    (out_dir / "soils_sources.json").write_text(json.dumps(record, indent=1),
                                                encoding="utf-8")
    note(f"SSURGO: {len(g):,} polygons, {len(attrs):,} map units; "
         f"by material (km2): {by_class}")
    return {"soils": str(path), "polygons": int(len(g)),
            "map_units": len(attrs), "km2_by_material": by_class,
            "seconds": round(time.monotonic() - t0, 1)}
