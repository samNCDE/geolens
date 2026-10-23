"""Run geolens over a real project area: any size, anywhere in the US.

A project is a boundary -- a BLM field office, a ranch, a forest district --
and a folder. geolens does what each part of it allows and says where the
DEM limited a result:

**Level A, the landscape** (``A_landscape``). One pass over the whole area at
10 m (USGS 3DEP 1/3 arc-second), widened to every HUC12 watershed the
boundary touches and the HUC12s that drain into those, so no stream's
drainage area is cut off at the boundary line. Routing, valley bottoms,
confinement, valley form and Rosgen valley types, everywhere.

**Level B, the channels** (``B_huc12/<code>``). Stream types need the banks,
which need lidar (cells of about 1 m). The whole area at 1 m would be
billions of cells, so this runs one HUC12 at a time, on 3DEP 1 m lidar where
it covers the HUC12: the level A reaches inside it get stream types,
section plots and the stable / unstable judgement. A HUC12 without lidar is
reported as valleys only, with the reason.

**The watershed data package** (``data``). Per HUC12 inside the boundary:
fire history, SSURGO ecological sites, PRISM climate normals, NOAA Atlas 14
storm depths and Rangeland Analysis Platform cover and trends
(:mod:`geolens.wsdata`), each its own resumable step.

**Merged** (``merged``). Valley types, stream types, sections, stable /
unstable form, a HUC12 status layer and ``huc12_summary`` (one row per
HUC12: what it is made of and how it is behaving), for the boundary, ready
for the map.

``project.json`` records every step. Running the same project again skips
what is done and carries on: a long level B can be stopped and resumed, a
HUC12 at a time.

Resolution is part of every answer: each layer carries the cell size it was
measured on, valley reaches narrower than three cells are marked, and
``RESULTS.md`` says which results are screening only and why.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .safety import Guard, GeolensError, note, progress

WBD_HUC12 = ("https://hydro.nationalmap.gov/arcgis/rest/services/wbd/"
             "MapServer/6")
LEVEL_A_CELL_M = 10.0
LEVEL_B_CELL_M = 1.0
_UA = {"User-Agent": "geolens (+geomorphic analysis)"}
_MAINSTEMS = re.compile(r"colorado river|lake mead|lake mohave|lake havasu|"
                        r"rio grande|gila river|lake powell", re.I)

# Vertical accuracy of USGS 3DEP 1/3 arc-second (10 m) where there is no
# lidar: DEM minus 6,156 ICESat-2 ATL08 ground points (strong beams, blunders
# over 50 m removed) on an 11 km box of mesa country in northern Arizona,
# October 2026. Slope class, NMAD and LE90, metres. It beat Copernicus GLO-30
# in every class (0.44 / 1.38 m on 0-10%).
DEM_ACCURACY_10M = [("0 to 10%", 0.23, 0.66), ("10 to 20%", 0.99, 2.43),
                    ("over 20%", 2.66, 6.64)]

# What each result needs from the DEM. Cell sizes in metres.
RESOLUTION = [
    ("routing, valley bottom, confinement, valley types", 30.0,
     "valleys narrower than 3 cells are not resolved"),
    ("stream types and stable / unstable form", 2.0,
     "banks must span several cells: lidar"),
    ("gullies and headcuts", 2.0, "lidar only"),
]


# -- small helpers -----------------------------------------------------------------

@contextmanager
def _out(path: Path):
    """Run geolens steps with ``path`` as the output folder."""
    old = os.environ.get("GEOLENS_OUT")
    path.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(path)
    try:
        yield Guard.from_env()
    finally:
        if old is None:
            os.environ.pop("GEOLENS_OUT", None)
        else:
            os.environ["GEOLENS_OUT"] = old


class _Status:
    """``project.json``: what has been done, so a rerun carries on."""

    def __init__(self, root: Path):
        self.path = root / "project.json"
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("steps", {})
        self.data.setdefault("huc12", {})
        self.data.setdefault("warnings", [])

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, default=str),
                       encoding="utf-8")
        tmp.replace(self.path)

    def step(self, name: str) -> dict | None:
        s = self.data["steps"].get(name)
        return s if s and s.get("status") == "done" else None

    def done(self, name: str, **info) -> dict:
        info.update(status="done", at=_now())
        self.data["steps"][name] = info
        self.save()
        return info

    def warn(self, text: str) -> None:
        if text not in self.data["warnings"]:
            self.data["warnings"].append(text)
            self.save()

    def unwarn(self, text: str) -> None:
        """Drop the warnings (containing ``text``) that a step now redone or
        succeeded left behind."""
        keep = [w for w in self.data["warnings"] if text not in w]
        if len(keep) != len(self.data["warnings"]):
            self.data["warnings"] = keep
            self.save()


def _cell_m(raster: str) -> float:
    """A raster's cell size in metres, whatever its CRS units."""
    import rasterio

    with rasterio.open(raster) as src:
        f = 1.0
        try:
            if src.crs is not None:
                f = float(src.crs.linear_units_factor[1])
        except Exception:                                    # noqa: BLE001
            f = 1.0
        return round(abs(float(src.res[0])) * f, 3)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get(url: str, params: dict, timeout: int, tries: int = 3) -> dict:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    last: Exception | None = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(full, headers=_UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:                               # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise GeolensError(f"Could not query {url}: {type(last).__name__}: {last}"
                       ) from last


def query_layer(url: str, where: str = "1=1", fields: str = "*",
                bbox: tuple[float, float, float, float] | None = None,
                max_offset: float | None = None, timeout: int = 120,
                page: int = 500, min_page: int = 50):
    """Every feature of an ArcGIS REST layer matching ``where`` (and touching
    ``bbox``, lon/lat), as a GeoDataFrame in EPSG:4326.

    Paged, and patient with slow servers (the USGS hydrography services time
    out on 1,000-feature pages): 500 features a page, and a page that fails
    is asked for again at half the size, down to ``min_page``. Paging goes on
    while the server says there is more (``exceededTransferLimit``) or fills
    the page, and stops when a page brings nothing new, so a server that
    ignores paging cannot loop."""
    import geopandas as gpd

    url = url.rstrip("/")
    if not url.endswith("/query"):
        url += "/query"
    params: dict[str, Any] = {"where": where, "outFields": fields,
                              "returnGeometry": "true", "outSR": 4326,
                              "f": "geojson"}
    if bbox is not None:
        params.update(geometry=",".join(f"{v:.6f}" for v in bbox),
                      geometryType="esriGeometryEnvelope", inSR=4326,
                      spatialRel="esriSpatialRelIntersects")
    if max_offset:
        params["maxAllowableOffset"] = max_offset
    feats: list = []
    seen: set = set()
    size, offset = max(int(page), 1), 0
    for _ in range(2000):
        params["resultOffset"] = offset
        params["resultRecordCount"] = size
        try:
            data = _get(url, params, timeout, tries=3 if size <= min_page else 1)
            if isinstance(data, dict) and data.get("error"):
                # ArcGIS reports a query it gave up on (often a timeout on
                # its side) as an "error" member, with HTTP 200.
                raise GeolensError(f"{url}: {str(data['error'])[:300]}")
        except GeolensError:
            if size <= min_page:
                raise
            size = max(min_page, size // 2)
            note(f"slow server: asking {url.split('/rest/')[-1]} for {size} "
                 f"features at a time")
            continue
        got = data.get("features") or []
        new = []
        for f in got:
            key = f.get("id") or json.dumps(f.get("properties"), sort_keys=True)
            if key not in seen:
                seen.add(key)
                new.append(f)
        feats.extend(new)
        more = bool(data.get("exceededTransferLimit")
                    or (data.get("properties") or {}).get("exceededTransferLimit"))
        if not new or not (more or len(got) >= size):
            break
        offset += len(got)
    if not feats:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    return gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")


def fetch_boundary(source: str, where: str | None = None,
                   guard: Guard | None = None, buffer_m: float = 250.0) -> str:
    """The project boundary as ``boundary.gpkg``: a polygon layer, or an
    ArcGIS REST layer URL with a ``where`` clause (e.g. a BLM field office:
    ``https://gis.blm.gov/arcgis/rest/services/admin_boundaries/
    BLM_Natl_AdminUnit/MapServer/3`` with ``ADM_UNIT_CD='AZC01000'``)."""
    import geopandas as gpd

    guard = guard or Guard.from_env()
    if str(source).lower().startswith(("http://", "https://")):
        g = query_layer(str(source), where or "1=1")
        if g.empty:
            raise GeolensError(f"No features in {source} where {where}.")
    else:
        g = gpd.read_file(str(guard.read_path(source)))
    if g.crs is None:
        raise GeolensError(f"{source} has no coordinate system.")
    g = g[g.geometry.notna() & ~g.geometry.is_empty]
    if g.empty:
        raise GeolensError(f"{source} has no geometry.")
    if not g.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        if not g.geom_type.isin(["LineString", "MultiLineString", "Point",
                                 "MultiPoint", "Polygon",
                                 "MultiPolygon"]).all():
            raise GeolensError(f"{source} is not a polygon, line or point "
                               f"layer.")
        # A stream line or a few points: the area within buffer_m of them.
        from .sar import _utm_for

        c = _union(g.to_crs(4326)).centroid
        utm = _utm_for(c.x, c.y)
        g = gpd.GeoDataFrame({"name": ["buffered boundary"]}, geometry=[
            _union(g.to_crs(utm)).buffer(float(buffer_m))], crs=utm)
        note(f"boundary: lines and points buffered by {buffer_m:g} m")
    out = Path(guard.write_path("boundary.gpkg"))
    g.to_file(str(out), driver="GPKG", layer="boundary")
    return str(out)


def _union(g):
    return (g.geometry.union_all() if hasattr(g.geometry, "union_all")
            else g.geometry.unary_union)


def _read_or_none(path, columns=None):
    """A vector layer (only ``columns``, when it has them), or None when
    there is none or it cannot be read."""
    import geopandas as gpd

    if not path or not Path(str(path)).exists():
        return None
    try:
        if columns:
            try:
                return gpd.read_file(str(path), columns=columns)
            except Exception:                                # noqa: BLE001
                pass
        return gpd.read_file(str(path))
    except Exception:                                        # noqa: BLE001
        return None


# -- HUC12s ------------------------------------------------------------------------

def project_huc12s(boundary_wgs, timeout: int = 120):
    """The HUC12s a boundary touches, plus the HUC12s that drain into them
    (within the same HUC8s), with ``role`` and ``in_boundary`` (the share of
    each HUC12 inside the boundary)."""
    import pandas as pd

    fields = "huc12,name,areasqkm,tohuc,hutype,states"
    g = query_layer(WBD_HUC12, fields=fields, bbox=boundary_wgs.bounds,
                    max_offset=0.0002, timeout=timeout)
    if g.empty:
        raise GeolensError("The Watershed Boundary Dataset has no HUC12s "
                           "here (outside the US?).")
    g = g[g.intersects(boundary_wgs)].copy()
    g["role"] = "boundary"
    huc8s = sorted({str(h)[:8] for h in g["huc12"]})
    where = " OR ".join(f"huc12 LIKE '{h}%'" for h in huc8s)
    allu = query_layer(WBD_HUC12, where=where, fields=fields,
                       max_offset=0.0002, timeout=timeout)
    down = dict(zip(allu["huc12"].astype(str), allu["tohuc"].astype(str))) \
        if not allu.empty else {}
    include = set(g["huc12"].astype(str))
    grew = True
    while grew:
        grew = False
        for h, t in down.items():
            if h not in include and t in include:
                include.add(h)
                grew = True
    extra = allu[allu["huc12"].astype(str).isin(include - set(g["huc12"]))]
    extra = extra.assign(role="upstream")
    out = pd.concat([g, extra], ignore_index=True)
    out = out.drop_duplicates("huc12")
    out["in_boundary"] = [
        round(float(geom.intersection(boundary_wgs).area / geom.area), 4)
        if geom.area > 0 else 0.0 for geom in out.geometry]
    return out


def _lidar_fraction(hucs, timeout: int) -> list[float]:
    """Share of each HUC12 under 3DEP 1 m lidar tiles (their footprints)."""
    from shapely.geometry import box
    from shapely.strtree import STRtree

    from .dem_fetch import search_1m

    w, s, e, n = _union(hucs).bounds
    items = search_1m((w, s, e, n), timeout=timeout)
    boxes = []
    for it in items:
        b = it.get("boundingBox") or {}
        try:
            boxes.append(box(float(b["minX"]), float(b["minY"]),
                             float(b["maxX"]), float(b["maxY"])))
        except (KeyError, TypeError, ValueError):
            continue
    if not boxes:
        return [0.0] * len(hucs)
    tree = STRtree(boxes)
    fracs = []
    for geom in hucs.geometry:
        hits = [boxes[i] for i in tree.query(geom)]
        if not hits or geom.area <= 0:
            fracs.append(0.0)
            continue
        from shapely.ops import unary_union

        fracs.append(round(float(unary_union(hits).intersection(geom).area
                                 / geom.area), 3))
    return fracs


# -- choices -----------------------------------------------------------------------

# Moody et al. (2003), Figure 2: the study region (boundaries approximate)
# and the dashed division between its two provinces, read off the map.
ARID_SW = (-114.0, 31.6, -104.0, 38.1)          # W, S, E, N
ARID_SW_DIVIDE_LON = -109.6


def choose_curve(lon: float, lat: float, asked: str | None = "auto"
                 ) -> tuple[str | None, str]:
    """The regional bankfull curve for a place, and why. ``asked`` other than
    "auto" is used as given. Within about a degree outside the report's
    region the nearest province is used, with that said."""
    if asked and asked != "auto":
        return asked, "chosen"
    w, s, e, n = ARID_SW
    margin = 1.0
    if not (w - margin <= lon <= e + margin and s - margin <= lat <= n + margin):
        return "bieger_us", ("outside the arid Southwest: Bieger et al. "
                             "(2015) all-US curve, generic; regional curves "
                             "for other regions are on the step list")
    inside = w <= lon <= e and s <= lat <= n
    edge = "" if inside else (" -- beyond the edge of the report's study "
                              "region, so the nearest province; check it "
                              "against local bankfull surveys")
    if lon < ARID_SW_DIVIDE_LON:
        return "arid_sw_az", ("Moody et al. (2003) central and southern "
                              "Arizona province (west of the report's "
                              "division near 109.6 W)" + edge)
    return "arid_sw_nm", ("Moody et al. (2003) eastern Arizona / New Mexico "
                          "province (east of the division near 109.6 W)" + edge)


def _pilot_order(hucs, reaches) -> list[str]:
    """HUC12s with lidar, most unconfined / partly confined valley first:
    where the channel's form is free to change and stream type matters."""
    import geopandas as gpd

    free = reaches[reaches["confinement"].isin(["unconfined",
                                                "partly confined"])]
    if free.empty:
        return list(hucs.sort_values("in_boundary", ascending=False)["huc12"])
    pts = free.copy()
    pts["geometry"] = pts.geometry.centroid
    pts["len_km"] = free.geometry.length / 1000.0
    j = gpd.sjoin(pts, hucs[["huc12", "geometry"]].to_crs(pts.crs),
                  predicate="within")
    km = j.groupby("huc12")["len_km"].sum()
    hucs = hucs.assign(score=hucs["huc12"].map(km).fillna(0.0))
    return list(hucs.sort_values("score", ascending=False)["huc12"])


# How level B's pilot is chosen. "representative" (landscape projects: a
# vegetation management plan, a watershed assessment): the HUC12s that together
# stand for the whole area, so their results scale up. "unconfined" (stream
# projects): the HUC12s with the most unconfined valley.
PILOT_BY = ("representative", "unconfined")
_HSG = {"A": "A", "B": "B", "C": "C", "D": "D",
        "A/D": "D", "B/D": "D", "C/D": "D"}       # dual groups: undrained
_K_SCALE = 0.5            # SSURGO K factor runs about 0.02 to 0.64


def _length_shares(lines, col: str, polys, prefix: str):
    """Share of each HUC12's stream length in each class of ``col`` (lines
    are placed by their midpoint; unclassed reaches count as "unresolved")."""
    import geopandas as gpd
    import numpy as np

    pts = gpd.GeoDataFrame(
        {"cls": lines[col].astype(object).where(lines[col].notna(), None),
         "len": lines.geometry.length.to_numpy()},
        geometry=lines.geometry.centroid, crs=lines.crs)
    pts["cls"] = [str(c) if c not in (None, "", "None", "nan") else "unresolved"
                  for c in pts["cls"]]
    j = gpd.sjoin(pts, polys[["huc12", "geometry"]].to_crs(pts.crs),
                  predicate="within")
    if j.empty:
        return None
    t = j.pivot_table(index="huc12", columns="cls", values="len",
                      aggfunc="sum", fill_value=0.0)
    t = t.div(t.sum(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)
    t.columns = [f"{prefix}{c}" for c in t.columns]
    return t


def _soil_shares(soils, polys, per_huc: int = 300):
    """Share of each HUC12's area in each soil hydrologic group, and the
    area-weighted surface erodibility (K), from a regular grid of about
    ``per_huc`` points per HUC12 dropped on the SSURGO polygons."""
    import geopandas as gpd
    import numpy as np
    import pandas as pd
    import shapely

    polys = polys.to_crs(soils.crs)
    xs, ys, codes = [], [], []
    for code, geom in zip(polys["huc12"].astype(str), polys.geometry):
        if geom is None or geom.is_empty:
            continue
        x0, y0, x1, y1 = geom.bounds
        step = max(((x1 - x0) * (y1 - y0) / per_huc) ** 0.5, 1e-9)
        gx, gy = np.meshgrid(np.arange(x0 + step / 2, x1, step),
                             np.arange(y0 + step / 2, y1, step))
        gx, gy = gx.ravel(), gy.ravel()
        ok = shapely.contains_xy(geom, gx, gy)
        xs.append(gx[ok])
        ys.append(gy[ok])
        codes += [code] * int(ok.sum())
    if not codes:
        return None
    pts = gpd.GeoDataFrame({"huc12": codes},
                           geometry=gpd.points_from_xy(np.concatenate(xs),
                                                       np.concatenate(ys)),
                           crs=soils.crs)
    keep = [c for c in ("hydgrpdcd", "kwfact") if c in soils.columns]
    j = gpd.sjoin(pts, soils[keep + ["geometry"]], predicate="within",
                  how="left")
    j = j[~j.index.duplicated(keep="first")]
    grp = (j["hydgrpdcd"] if "hydgrpdcd" in j else pd.Series(None, index=j.index))
    j["hsg"] = [_HSG.get(str(g).strip().upper(), "unknown") for g in grp]
    t = pd.crosstab(j["huc12"], j["hsg"], normalize="index")
    t.columns = [f"soil_hsg_{c}" for c in t.columns]
    if "kwfact" in j:
        k = pd.to_numeric(j["kwfact"], errors="coerce").groupby(j["huc12"]).mean()
        t["soil_k"] = (k / _K_SCALE).clip(0.0, 1.5).reindex(t.index)
    return t


def _huc_profiles(hucs, valley_types=None, reaches=None, soils=None):
    """What each HUC12 is made of, as numbers from 0 to about 1:

    - its stream length by Rosgen valley type (level A), or by confinement
      where there are no valley types;
    - its area by soil hydrologic group (SSURGO A to D: how much rain runs
      off) and its surface soil's erodibility (K), when soils were fetched.

    The shares in each block sum to one, so no block outweighs another.
    Returns the profiles, indexed by HUC12 code, and the blocks used."""
    import pandas as pd

    parts, basis = [], []
    if valley_types is not None and len(valley_types) \
            and "valley_type" in valley_types.columns:
        t = _length_shares(valley_types, "valley_type", hucs, "valley_")
        if t is not None:
            parts.append(t)
            basis.append("stream length by valley type")
    elif reaches is not None and len(reaches) and "confinement" in reaches.columns:
        t = _length_shares(reaches, "confinement", hucs, "confinement_")
        if t is not None:
            parts.append(t)
            basis.append("stream length by confinement")
    if soils is not None and len(soils):
        t = _soil_shares(soils, hucs)
        if t is not None:
            parts.append(t)
            basis.append("area by soil hydrologic group" +
                         (" and soil erodibility (K)" if "soil_k" in t else ""))
    index = hucs["huc12"].astype(str)
    if not parts:
        return pd.DataFrame(index=index), basis
    prof = pd.concat(parts, axis=1).reindex(index)
    # A HUC12 with no reaches or no soils data sits at the average.
    prof = prof.fillna(prof.mean()).fillna(0.0)
    return prof, basis


def _medoids(D, w, k: int, allowed=None, iters: int = 100):
    """``k`` medoids of a distance matrix with point weights (PAM: a greedy
    build, then each group's medoid moved to its most central member until
    nothing changes). Only ``allowed`` points can be medoids. Deterministic.
    Returns the medoid indices and each point's group (index into them)."""
    import numpy as np

    n = len(D)
    allowed = np.ones(n, bool) if allowed is None else np.asarray(allowed, bool)
    k = int(min(k, allowed.sum()))
    if k <= 0:
        return [], np.zeros(n, int)
    med: list[int] = []
    dmin = np.full(n, np.inf)
    for _ in range(k):
        cost = (w[:, None] * np.minimum(dmin[:, None], D)).sum(axis=0)
        cost[~allowed] = np.inf
        cost[med] = np.inf
        j = int(np.argmin(cost))
        med.append(j)
        dmin = np.minimum(dmin, D[:, j])
    for _ in range(iters):
        lab = np.argmin(D[:, med], axis=1)
        new = []
        for g in range(k):
            idx = np.flatnonzero((lab == g) & allowed)
            members = np.flatnonzero(lab == g)
            if idx.size == 0:
                new.append(med[g])
                continue
            cost = (D[np.ix_(idx, members)] * w[members][None, :]).sum(axis=1)
            new.append(int(idx[np.argmin(cost)]))
        if new == med:
            break
        med = new
    return med, np.argmin(D[:, med], axis=1)


def _representative_pilot(hucs, k: int, can_run=None, valley_types=None,
                          reaches=None, soils=None, min_inside: float = 0.5):
    """The ``k`` HUC12s that together best stand for ``hucs``.

    The HUC12s are sorted into ``k`` groups of similar watersheds by their
    profiles (``_huc_profiles``), each weighted by its area inside the
    boundary, and each group's most typical HUC12 (its medoid) is a pilot.
    Only a HUC12 in ``can_run`` (lidar) and at least ``min_inside`` inside
    the boundary can stand for a group. Returns the order (pilots first,
    largest group first) and the plan: each pilot, the HUC12s it stands for,
    and their area."""
    import numpy as np

    hucs = hucs.copy()
    hucs["huc12"] = hucs["huc12"].astype(str)
    hucs = hucs.sort_values("huc12").reset_index(drop=True)
    km2 = (hucs["areasqkm"].astype(float).fillna(0.0)
           * hucs["in_boundary"].astype(float).fillna(0.0)).to_numpy()
    runnable = set(hucs["huc12"]) if can_run is None else \
        {str(c) for c in can_run}
    allowed = np.array([c in runnable for c in hucs["huc12"]])
    mostly_in = allowed & (hucs["in_boundary"].astype(float).to_numpy()
                           >= min_inside)
    if mostly_in.any():
        allowed = mostly_in
    prof, basis = _huc_profiles(hucs, valley_types, reaches, soils)
    X = prof.to_numpy(dtype=float) if prof.shape[1] else \
        np.zeros((len(hucs), 1))
    D = np.sqrt(((X[:, None, :] - X[None, :, :]) ** 2).sum(axis=-1))
    w = km2 / km2.sum() if km2.sum() > 0 else np.full(len(hucs), 1 / len(hucs))
    med, lab = _medoids(D, w, k, allowed)
    codes = list(hucs["huc12"])
    names = dict(zip(codes, hucs.get("name", hucs["huc12"]).astype(str)))
    groups = []
    for g, m in enumerate(med):
        members = [codes[i] for i in np.flatnonzero(lab == g)]
        area = float(km2[lab == g].sum())
        groups.append({"huc12": codes[m], "name": names[codes[m]],
                       "members": members, "km2": round(area, 1),
                       "share": round(area / km2.sum(), 3) if km2.sum() else None})
    groups.sort(key=lambda r: -r["km2"])
    pilots = [r["huc12"] for r in groups]
    rest = [codes[i] for i in np.argsort(-km2, kind="stable")
            if allowed[i] and codes[i] not in pilots]
    plan = {"by": "representative", "basis": basis or ["area only"],
            "groups": groups,
            "group_of": {c: r["huc12"] for r in groups for c in r["members"]}}
    return pilots + rest, plan


# -- the watershed data package -----------------------------------------------------

def data_steps(data: str | list | tuple | None) -> list[str]:
    """Which watershed data steps a project runs: "all" (the default),
    "none", or some of ``wsdata.STEPS`` (a list, or comma-separated)."""
    from .wsdata import STEPS

    if data is None or data is False:
        return []
    if isinstance(data, str):
        s = data.strip().lower()
        if s in ("", "none", "no", "off", "false"):
            return []
        if s in ("all", "yes", "true"):
            return list(STEPS)
        items = [x.strip() for x in s.replace(";", ",").split(",") if x.strip()]
    elif data is True:
        return list(STEPS)
    else:
        items = [str(x).strip().lower() for x in data if str(x).strip()]
    bad = [x for x in items if x not in STEPS]
    if bad:
        raise GeolensError(f"Unknown watershed data step(s) {', '.join(bad)}: "
                           f"choose from {', '.join(STEPS)}, 'all' or 'none'.")
    return [x for x in STEPS if x in items]


def _data_step(step: str, root: Path, inside, st: "_Status",
               timeout: int) -> dict:
    """Run one watershed data step for the HUC12s inside the boundary; its
    per-HUC12 table goes to ``data/<step>_huc12.csv``."""
    from . import wsdata

    ddir = root / "data"
    ddir.mkdir(parents=True, exist_ok=True)
    info: dict[str, Any] = {}
    if step == "fire":
        r = wsdata.fire_history(inside, ddir, timeout=timeout)
        info.update(path=r["path"], perimeters=r["perimeters"],
                    dropped_unrepairable=r.get("dropped_unrepairable", 0))
    elif step == "ecosites":
        soils = (st.step("soils") or {}).get("path")
        if not soils or not Path(soils).exists():
            raise GeolensError("needs the SSURGO soils over the boundary, "
                               "which did not download")
        r = wsdata.ecological_sites(soils, ddir, inside, timeout=timeout)
        info.update(path=r["path"], map_units=r["map_units"],
                    with_site=r["with_site"])
    elif step == "prism":
        r = wsdata.prism_normals(inside, ddir / "prism")
        info.update(source=r["source"])
    elif step == "atlas14":
        r = wsdata.atlas14_depths(inside, ddir / "atlas14.json")
        info.update(failed=r["failed"])
        if r["failed"]:
            info["incomplete"] = True        # the rerun asks only for these
    elif step == "rap":
        r = wsdata.rap_cover(inside, ddir)
        years = r["years"]
        info.update(path=r["path"], first_year=min(years),
                    last_year=max(years), missing_years=r["missing_years"])
        # The latest year is often not published yet; a gap is worth a rerun.
        if [y for y in r["missing_years"] if y < max(years)]:
            info["incomplete"] = True
    else:                                                    # pragma: no cover
        raise GeolensError(f"unknown watershed data step {step!r}")
    table = r.get("table")
    if table is not None and len(table):
        t = table.copy()
        t.index = t.index.astype(str)
        t.index.name = "huc12"
        csv = ddir / f"{step}_huc12.csv"
        t.to_csv(csv)
        info["table"] = str(csv)
        info["columns"] = list(t.columns)
    return info


# The valley-type classification's revision. A project whose level A was
# typed under an older one is re-typed when it is run again: the terrain,
# valley form and fans are kept, only the crosswalk is redone (minutes, not
# hours), and the level B stability of every HUC12 already done is judged
# again against the new types.
#   1  v0.35: Rosgen 1996 types plus the 2022 landscape types
#   2  v0.37.1: a U-shape on alluvium or colluvium is not a glacial trough;
#      a broad gentle floor under a hillslope's map unit is alluvial (VIII);
#      the 2022 card: an unconfined floor is stream-built, partly confined
#      leans unconfined
#   3  v0.39: fan surfaces from the soils map's landform (fan terraces,
#      alluvial fans ...): Type III, U-AL-IF / U-AL-AF; the 2022 type is
#      the valley type (v0.38) and confinement is from the measured floor
TYPING = 3


def _retype_valleys(root: Path, A: Path, st: "_Status", pa: dict) -> dict:
    """Redo the valley types of a level A pass under the current TYPING,
    from its saved valley form and fans, and re-judge the stable / unstable
    form of the HUC12s level B has done. Returns the updated pass record."""
    from .landscape.rosgen import rosgen_valley_types
    from .landscape.stability import stream_stability

    run = A / "run"
    vform = run / "valley_form_dir" / "valley_form_segments.csv"
    fans = run / "fans_dir" / "fans.gpkg"
    note(f"project: valley types again under classification {TYPING} "
         f"(was {pa.get('typing') or 1})")
    with _out(run) as g:
        vt = rosgen_valley_types(
            pa["segments"], geology=pa.get("geology"),
            fans=str(fans) if fans.exists() else None,
            valley_form=str(vform) if vform.exists() else None, guard=g)
    for old in ("Material resolved for only", "could not be resolved to a "
                "single type", "valley form matched only"):
        st.unwarn(old)
    for w in vt.warnings:
        st.warn(f"level A: {w}")
    pa = dict(pa, valley_types=vt.segments, typing=TYPING)
    try:
        from .landscape.pipeline import _valley_alloc_for
        from .landscape.valleyform import valley_polygons

        with _out(run) as g:
            valley_polygons(_valley_alloc_for(pa["segments"]),
                            attributes=vt.segments, guard=g)
    except Exception as exc:                                 # noqa: BLE001
        st.warn(f"The valley extent polygons keep the earlier valley types "
                f"(rebuilding them failed: {type(exc).__name__}: "
                f"{str(exc)[:160]}).")
    for code, rec in st.data["huc12"].items():
        if rec.get("status") != "done" or not rec.get("stream_types"):
            continue
        if not Path(rec["stream_types"]).exists():
            continue
        try:
            with _out(root / "B_huc12" / code) as g:
                stab = stream_stability(vt.segments, rec["stream_types"],
                                        guard=g)
            rec["stability"] = stab["stability"]
            rec["counts"] = stab.get("counts")
        except Exception as exc:                             # noqa: BLE001
            st.warn(f"HUC12 {code}: stable / unstable form could not be "
                    f"judged again ({type(exc).__name__}: {str(exc)[:160]}).")
    st.data["steps"]["pass_a"] = pa
    st.save()
    return pa


# -- confinement calibrated to the lidar --------------------------------------------

# Rosgen's entrenchment-ratio classes, measured on the lidar sections:
# entrenched (< 1.4), moderately entrenched (1.4 to 2.2), slightly (> 2.2).
ER_BREAKS = (1.4, 2.2)
_ER_TO_CONF = ("confined", "partly confined", "unconfined")
PUBLISHED_BREAKS = (2.0, 5.0)


def _confinement_agreement(ratio, lidar, weight, breaks) -> float:
    """Length-weighted share of reaches whose 10 m class under ``breaks``
    matches the lidar's."""
    import numpy as np

    b1, b2 = breaks
    cls = np.where(ratio <= b1, 0, np.where(ratio <= b2, 1, 2))
    return float((weight * (cls == lidar)).sum() / weight.sum())


def fit_confinement_breaks(ratio, lidar_class, length=None,
                           min_reaches: int = 30, min_per_class: int = 5
                           ) -> dict:
    """Breaks on the 10 m confinement ratio that agree best with the lidar's
    entrenchment classes (0 confined, 1 partly, 2 unconfined) of the same
    reaches. Returns the breaks, the agreement before (published breaks) and
    after, and the confusion table, or ``skipped`` with the reason when the
    lidar does not cover enough.

    The two breaks are fitted by coordinate descent from the published
    ones: with one fixed, every cut between two neighbouring ratios is
    scored at once by cumulative sums, so tens of thousands of reaches take
    a moment. Length-weighted: a long reach counts for its length."""
    import numpy as np

    r = np.asarray(ratio, dtype="float64")
    c = np.asarray(lidar_class, dtype="int64")
    w = np.ones_like(r) if length is None else np.asarray(length, dtype="float64")
    ok = np.isfinite(r) & np.isfinite(w) & (w > 0)
    r, c, w = r[ok], c[ok], w[ok]
    counts = [int((c == k).sum()) for k in range(3)]
    if len(r) < min_reaches or min(counts) < min_per_class:
        return {"skipped": f"{len(r)} reaches measured on lidar with "
                           f"{counts[0]} entrenched, {counts[1]} moderately and "
                           f"{counts[2]} slightly entrenched: at least "
                           f"{min_reaches} with {min_per_class} in each class "
                           f"are needed", "n": int(len(r))}
    before = _confinement_agreement(r, c, w, PUBLISHED_BREAKS)
    order = np.argsort(r, kind="stable")
    rs, cs, ws = r[order], c[order], w[order]
    # cut k (0..n) = between rs[k-1] and rs[k]: reaches [:k] are at or below
    cum = [np.concatenate([[0.0], np.cumsum(ws * (cs == m))]) for m in range(3)]
    total = w.sum()
    cuts = np.concatenate([[rs[0] - 1.0], (rs[:-1] + rs[1:]) / 2.0, [rs[-1] + 1.0]])

    def idx(b):                       # the cut index for a break value
        return int(np.searchsorted(rs, b, side="right"))

    def scan_b1(k2):                  # best k1 <= k2 with k2 fixed
        k = np.arange(0, k2 + 1)
        score = cum[0][k] + (cum[1][k2] - cum[1][k]) + (cum[2][-1] - cum[2][k2])
        best = int(np.argmax(score))
        return best, float(score[best])

    def scan_b2(k1):                  # best k2 >= k1 with k1 fixed
        k = np.arange(k1, len(rs) + 1)
        score = cum[0][k1] + (cum[1][k] - cum[1][k1]) + (cum[2][-1] - cum[2][k])
        best = int(np.argmax(score))
        return k1 + best, float(score[best])

    k1, k2 = idx(PUBLISHED_BREAKS[0]), idx(PUBLISHED_BREAKS[1])
    best = -1.0
    for _ in range(6):
        k1, s1 = scan_b1(k2)
        k2, s2 = scan_b2(k1)
        if s2 <= best + 1e-9:
            break
        best = s2
    b1, b2 = float(cuts[k1]), float(cuts[k2])
    after = best / total
    cls = np.where(r <= b1, 0, np.where(r <= b2, 1, 2))
    table = {_ER_TO_CONF[k]: {_ER_TO_CONF[m]: int(((c == k) & (cls == m)).sum())
                              for m in range(3)} for k in range(3)}
    return {"breaks": [round(b1, 2), round(b2, 2)], "n": int(len(r)),
            "agreement_before": round(before, 3), "agreement_after": round(after, 3),
            "table": table, "lidar_classes": counts}


def _lidar_entrenchment(st: "_Status") -> "pd.DataFrame | None":
    """seg_id, entrenchment ratio and length of every reach level B measured
    on lidar sections (not from the regional curve alone)."""
    import pandas as pd

    parts = []
    for code, rec in st.data["huc12"].items():
        p = rec.get("stream_types") if rec.get("status") == "done" else None
        if not p or not Path(p).exists():
            continue
        g = _read_or_none(p, columns=["seg_id", "entrenchment_ratio",
                                      "stream_basis", "n_sections_measured",
                                      "length_m"])
        if g is None or "entrenchment_ratio" not in g.columns:
            continue
        er = pd.to_numeric(g["entrenchment_ratio"], errors="coerce")
        keep = er.notna()
        if "stream_basis" in g.columns:
            keep &= g["stream_basis"].astype(str) == "measured"
        if "n_sections_measured" in g.columns:
            keep &= pd.to_numeric(g["n_sections_measured"],
                                  errors="coerce").fillna(0) >= 3
        if keep.any():
            parts.append(pd.DataFrame({
                "seg_id": g.loc[keep, "seg_id"].astype("int64"),
                "er": er[keep], "huc12": code,
                "length_m": pd.to_numeric(g.loc[keep, "length_m"],
                                          errors="coerce")
                if "length_m" in g.columns else 1.0}))
    return pd.concat(parts, ignore_index=True) if parts else None


def _calibrate_confinement(st: "_Status", pa: dict) -> dict | None:
    """The 10 m confinement breaks fitted to the lidar entrenchment classes
    of the reaches level B has measured (see fit_confinement_breaks)."""
    import numpy as np

    lid = _lidar_entrenchment(st)
    if lid is None:
        return None
    seg = _read_or_none(pa["segments"], columns=["seg_id", "confinement_ratio"])
    if seg is None:
        return None
    j = lid.merge(seg[["seg_id", "confinement_ratio"]].assign(
        seg_id=lambda d: d["seg_id"].astype("int64")), on="seg_id")
    j = j[j["confinement_ratio"].notna()]
    if j.empty:
        return None
    lidar = np.where(j["er"] < ER_BREAKS[0], 0,
                     np.where(j["er"] <= ER_BREAKS[1], 1, 2))
    out = fit_confinement_breaks(j["confinement_ratio"], lidar,
                                 j["length_m"].fillna(1.0))
    out["huc12s"] = sorted(set(j["huc12"]))
    out["basis"] = ("Rosgen entrenchment ratio measured on the 1 m lidar "
                    "sections (entrenched < 1.4 = confined, 1.4 to 2.2 = "
                    "partly confined, > 2.2 = unconfined) against the 10 m "
                    "confinement ratio of the same reaches")
    return out


def _confinement_lines(cal: dict | None) -> list[str]:
    """RESULTS.md: how the confinement breaks were set."""
    if not cal:
        return []
    out = ["## Confinement at 10 m, against the lidar", ""]
    if cal.get("skipped"):
        out += [f"The published breaks (confined at or under {PUBLISHED_BREAKS[0]:g}, "
                f"partly confined at or under {PUBLISHED_BREAKS[1]:g} times the channel "
                f"width) were kept: {cal['skipped']}.", ""]
        return out
    b1, b2 = cal["breaks"]
    applied = cal.get("applied")
    out += [f"{cal['n']} reaches in {len(cal.get('huc12s') or [])} HUC12s "
            f"were measured on 1 m lidar as well as classed at 10 m. "
            f"{cal['basis']}. The published breaks ({PUBLISHED_BREAKS[0]:g} "
            f"and {PUBLISHED_BREAKS[1]:g}) agree with the lidar on "
            f"{100 * cal['agreement_before']:.0f}% of their length; breaks of "
            f"{b1:g} and {b2:g} agree on {100 * cal['agreement_after']:.0f}%."
            + (" **The fitted breaks were applied to every level A reach**, "
               "the valleys re-typed and the stable / unstable form judged "
               "again." if applied else
               " The gain is too small to act on, so the published breaks "
               "stand."), "",
            "| Lidar says | 10 m confined | 10 m partly confined | "
            "10 m unconfined |", "|---|---|---|---|"]
    for k in _ER_TO_CONF:
        row = (cal.get("table") or {}).get(k) or {}
        out.append(f"| {k} | " + " | ".join(str(row.get(m, 0))
                                             for m in _ER_TO_CONF) + " |")
    out += ["", "Entrenchment is a channel measure and confinement a valley "
            "one, so this is a calibration of the 10 m classes to the best "
            "lidar evidence there is, not a proof. A 10 m valley bottom is "
            "wider than the channel's room on a piedmont, which is what the "
            "fit corrects.", ""]
    return out


def _slope_raster(pa: dict, A: Path) -> str | None:
    """The level A slope raster: from the pass record, or found in its run
    folder (a pass made before v0.37 did not record it)."""
    p = pa.get("slope")
    if p and Path(p).exists():
        return str(p)
    found = sorted((A / "run").rglob("slope.tif")) if (A / "run").exists() \
        else []
    return str(found[0]) if found else None


# -- the run -----------------------------------------------------------------------

def run_project(boundary: str, where: str | None = None,
                level_b: str = "pilot", pilot_count: int = 3,
                pilot_by: str = "representative",
                huc12s: str | list | None = None,
                stream_threshold_km2: float = 1.0,
                geology: str | None = "ssurgo",
                regional_curve: str | None = "auto",
                lidar_min_fraction: float = 0.8,
                section_plots: bool = True,
                level_b_hours: float | None = None,
                soils: bool = True,
                data: str | list | None = "all",
                calibrate_confinement: bool = True,
                keep_lidar_dem: bool | None = None,
                keep_lidar_tiles: bool = False,
                major_streams_km2: float = 25.0,
                sensitivity_weights: dict | str | None = None,
                level_a_cell_m: float | None = None,
                fine_valleys: str = "none",
                fine_huc12s: str | list | None = None,
                fine_count: int = 3,
                fine_cell_m: float = 3.0,
                fine_workers: int | None = None,
                copy_from: str | None = None,
                timeout: int = 120,
                guard: Guard | None = None) -> dict[str, Any]:
    """Level A over the whole area, level B by HUC12, merged layers, a
    report. See the module docstring. ``level_b``: "pilot" (``pilot_count``
    HUC12s with lidar, chosen by ``pilot_by``), "all", "listed"
    (``huc12s``) or "none". ``pilot_by``: "representative" (HUC12s that
    together stand for the whole area, for landscape projects) or
    "unconfined" (the most unconfined valley, for stream projects).
    ``data``: the watershed data fetched per HUC12 (``wsdata``): "all",
    "none", or some of fire, ecosites, prism, atlas14, rap.
    ``calibrate_confinement``: fit the 10 m confinement breaks to the
    entrenchment the lidar measured in level B, and re-type the valleys
    with them. ``keep_lidar_dem``: keep each HUC12's 1 m mosaic after its
    channels are measured (default: only when level B is a pilot or a
    list; "all" drops them, about 300 MB each). ``major_streams_km2``: the
    drainage at the mouth a stream needs to be in ``major_streams``.
    ``sensitivity_weights``: the HUC12 sensitivity table's weights, a dict
    or a JSON file of summary column -> weight (``synthesis.COMPONENTS``
    by default). ``level_a_cell_m``: the level A grid (default 10 m, the
    1/3 arc-second DEM's own; 20 or 30 m for a reconnaissance run on a
    laptop or a cloud runner, where the DEM is fetched straight onto the
    coarser grid; valleys narrower than three cells are not resolved).
    ``fine_valleys``: "none" (default), "all" (every HUC12 with lidar, the
    pilot first, ``fine_workers`` at a time, by default as many as memory
    and cores allow; the 1 m DEMs kept when ``keep_lidar_dem``), "pilot" (``fine_count`` headwater
    HUC12s with lidar, one per setting: mountain, fans and piedmonts,
    alluvial valley) or "listed" (``fine_huc12s``): level A's valley pass
    again on those HUC12s' lidar at ``fine_cell_m`` (3 m), compared with
    the 10 m pass in RESULTS.md and ``A_fine/comparison_3m.csv``, merged as
    ``valley_types_3m`` and ``valley_extent_3m``.
    ``copy_from``: an earlier project folder to start a new one from (its
    level A, level B, soils and data are copied, never changed; this run
    carries on from them). Resumes a project folder that already has a
    ``project.json``."""
    import geopandas as gpd

    pilot_by = (pilot_by or "representative").lower()
    if pilot_by not in PILOT_BY:
        raise GeolensError(f"pilot_by must be one of {', '.join(PILOT_BY)}, "
                           f"not {pilot_by!r}.")
    want_data = data_steps(data)                  # a typo fails before the run
    guard = guard or Guard.from_env()
    root = Path(guard.out_dir)
    root.mkdir(parents=True, exist_ok=True)
    _take_lock(root)
    if copy_from and not (root / "project.json").exists():
        copy_project(guard.read_path(copy_from), root)
    elif copy_from:
        note(f"project: {root} already has a project.json; carrying on from "
             f"it (the folder to start from, {copy_from}, is not used)")
    st = _Status(root)
    t0 = time.monotonic()
    _keep_awake()
    from . import api

    # 0. boundary -----------------------------------------------------------------
    if not (st.step("boundary") and (root / "boundary.gpkg").exists()):
        note("project: boundary")
        with _out(root) as g0:
            path = fetch_boundary(boundary, where, guard=g0)
        st.done("boundary", path=path, source=str(boundary), where=where)
    bnd = gpd.read_file(str(root / "boundary.gpkg"))
    bnd_wgs = _union(bnd.to_crs(4326))

    # 1. HUC12s ---------------------------------------------------------------------
    huc_path = root / "huc12.gpkg"
    if not (st.step("huc12") and huc_path.exists()):
        note("project: HUC12 watersheds (USGS WBD)")
        hucs = project_huc12s(bnd_wgs, timeout=timeout)
        try:
            hucs["lidar_frac"] = _lidar_fraction(hucs, timeout)
        except Exception as exc:                             # noqa: BLE001
            hucs["lidar_frac"] = float("nan")
            st.warn(f"3DEP lidar coverage could not be checked ({exc}); "
                    f"level B will find out HUC12 by HUC12.")
        mains = sorted({str(n) for n in hucs["name"] if _MAINSTEMS.search(str(n))})
        if mains:
            st.warn("Drainage areas along large rivers entering from outside "
                    "the project (" + ", ".join(mains[:6]) + ") are local only: "
                    "their basins are not in the project area.")
        hucs.to_file(str(huc_path), driver="GPKG", layer="huc12")
        st.done("huc12", path=str(huc_path), n=int(len(hucs)),
                n_boundary=int((hucs["role"] == "boundary").sum()),
                km2=round(float(hucs["areasqkm"].astype(float).sum()), 1))
    hucs = gpd.read_file(str(huc_path))

    # 2. level A DEM -----------------------------------------------------------------
    A = root / "A_landscape"
    if not st.step("dem_a"):
        from .sar import _utm_for

        cell_asked = float(level_a_cell_m or LEVEL_A_CELL_M)
        if cell_asked < LEVEL_A_CELL_M:
            raise GeolensError(f"level_a_cell_m is {cell_asked:g} m; the "
                               f"level A DEM is the 1/3 arc-second product, "
                               f"{LEVEL_A_CELL_M:g} m at best.")
        note(f"project: level A DEM (3DEP 1/3 arc-second, {cell_asked:g} m)")
        utm = _utm_for(bnd_wgs.centroid.x, bnd_wgs.centroid.y)
        area = gpd.GeoDataFrame(geometry=[_union(hucs)], crs=hucs.crs
                                ).to_crs(utm)              # buffered in metres
        A.mkdir(parents=True, exist_ok=True)
        area_path = A / "area.gpkg"
        area.to_file(str(area_path), driver="GPKG", layer="area")
        with _out(A / "dem"):
            d = api.fetch_dem(str(area_path), source="1/3",
                              resolution_m=cell_asked, buffer_m=300.0)
        for w in d.get("warnings") or []:
            st.warn(f"level A DEM: {w}")
        if cell_asked > LEVEL_A_CELL_M:
            st.warn(f"Level A was asked for at {cell_asked:g} m (a "
                    f"reconnaissance run): valleys narrower than "
                    f"{3 * cell_asked:g} m are not resolved.")
        st.done("dem_a", dem=d["dem"], cell_m=d.get("cell_size_m"),
                coverage=d.get("coverage"), crs=d.get("crs"))
    dem_a = st.step("dem_a")["dem"]

    # 3. memory, and a coarser copy if the 10 m DEM will not fit ------------------------
    if not st.step("plan_a"):
        dem_run = dem_a
        cell = _cell_m(dem_a)
        for _ in range(3):
            with _out(A / "dem"):
                plan = api.plan_landscape_run(dem_run)
            if plan.get("fits") is not False:
                break
            cell *= 2
            st.warn(f"The level A DEM does not fit in memory at "
                    f"{cell / 2:g} m: using {cell:g} m. Valleys narrower "
                    f"than {3 * cell:g} m are not resolved.")
            with _out(A / "dem"):
                dem_run = api.resample_dem(dem_a, cell)["path"]
        st.done("plan_a", dem=dem_run, cell_m=cell,
                peak_gb=plan.get("peak_gb"), verdict=plan.get("verdict"))
    plan_a = st.step("plan_a")
    dem_run, cell_a = plan_a["dem"], float(plan_a["cell_m"])

    # 4. SSURGO soils over the boundary: composition, erodibility, runoff, and
    # (geology="ssurgo") the material the valleys are typed by ----------------------
    use_soils = (str(geology or "").lower() == "ssurgo")
    if (soils or use_soils) and not st.step("soils"):
        note("project: SSURGO soils over the boundary")
        try:
            from .soils import fetch_soils

            with _out(root / "soils") as gs:
                sres = fetch_soils(str(root / "boundary.gpkg"), guard=gs,
                                   max_area_km2=100_000.0)
            st.done("soils", path=sres["soils"], polygons=sres["polygons"],
                    km2_by_material=sres.get("km2_by_material"))
            st.unwarn("SSURGO soils could not be fetched")
        except Exception as exc:                             # noqa: BLE001
            st.warn(f"SSURGO soils could not be fetched ({type(exc).__name__}: "
                    f"{str(exc)[:200]}); run the project again to retry.")

    # 4b. NHDPlus HR flowlines: names and drainage areas for the major streams ---------
    if not st.step("nhd"):
        note("project: NHDPlus HR flowlines (names for the major streams)")
        try:
            from .wsdata import nhd_flowlines

            nres = nhd_flowlines(hucs, root / "data", min_km2=major_streams_km2,
                                 timeout=timeout)
            st.done("nhd", **nres)
            st.unwarn("The NHDPlus HR flowlines could not be fetched")
        except Exception as exc:                             # noqa: BLE001
            st.warn(f"The NHDPlus HR flowlines could not be fetched "
                    f"({type(exc).__name__}: {str(exc)[:200]}); the major "
                    f"streams will have no names. Run the project again to "
                    f"retry.")

    # 5. level A pass --------------------------------------------------------------------
    if not st.step("pass_a"):
        geo_a = geology
        if use_soils:
            if st.step("soils"):
                geo_a = st.step("soils")["path"]       # the boundary's soils
            else:
                geo_a = "auto"
                st.warn("Valley materials come from USGS geology, not SSURGO "
                        "(the soils download failed). USGS geology tends to "
                        "call valleys bedrock; run the project again once "
                        "SSURGO answers, after deleting pass_a from "
                        "project.json.")
        for attempt in range(3):
            note(f"project: level A pass at {cell_a:g} m")
            try:
                with _out(A / "run"):
                    res = api.landscape_pass(
                        dem_run, stream_threshold_km2=stream_threshold_km2,
                        segment_length_m=500.0, valley_types=True,
                        stream_types=False, geology=geo_a, fans=True)
                break
            except MemoryError:
                # Left to run on its own, a project coarsens and carries on
                # rather than stopping for the night.
                if attempt == 2:
                    raise
                cell_a *= 2
                st.warn(f"Level A ran out of memory at {cell_a / 2:g} m and "
                        f"was run again at {cell_a:g} m. Valleys narrower "
                        f"than {3 * cell_a:g} m are not resolved.")
                with _out(A / "dem"):
                    dem_run = api.resample_dem(dem_a, cell_a)["path"]
                st.done("plan_a", dem=dem_run, cell_m=cell_a, peak_gb=None,
                        verdict="coarsened after running out of memory")
        vt = (res.get("valley_types") or {})
        for w in res.get("warnings") or []:
            st.warn(f"level A: {w}")
        st.done("pass_a", run=str(A / "run"),
                segments=(res.get("classified") or {}).get("segments"),
                valley_types=vt.get("segments"),
                geology=(res.get("geology") or {}).get("path"),
                slope=(res.get("terrain") or {}).get("slope"),
                typing=TYPING, seconds=res.get("seconds"))
    pa = st.step("pass_a")
    # Soils that arrived after the pass (Soil Data Access was down the
    # night the v0.42 Kingman run started, and the pass typed its valleys
    # from USGS geology): the valleys are re-typed from the saved valley
    # form with the soils, in minutes, instead of the pass being redone.
    soils_path = (st.step("soils") or {}).get("path")
    if (use_soils and soils_path and pa.get("valley_types")
            and pa.get("geology") != soils_path):
        note("project: SSURGO soils arrived after the level A pass; "
             "re-typing the valleys with them")
        pa = dict(pa, geology=soils_path, typing=0)
        st.data["steps"]["pass_a"] = pa
        st.unwarn("Valley materials come from USGS geology, not SSURGO")
        st.save()
    if (pa.get("typing") or 1) < TYPING and pa.get("valley_types"):
        try:
            pa = _retype_valleys(root, A, st, pa)
        except Exception as exc:                             # noqa: BLE001
            st.warn(f"The valley types could not be redone under "
                    f"classification {TYPING} ({type(exc).__name__}: "
                    f"{str(exc)[:200]}); they are as first typed.")
    reaches = gpd.read_file(pa["segments"])
    inside = hucs[hucs["role"] == "boundary"]

    # 6. the watershed data package, per HUC12: fire, ecological sites, climate,
    # storm depths, vegetation cover. Each is its own step: a failure is a
    # warning, and the next run tries again ------------------------------------------
    from .wsdata import TITLES

    for step in want_data:
        rec = st.step(f"data_{step}")
        if rec and not rec.get("incomplete"):
            continue
        note(f"project: watershed data: {TITLES[step]}")
        try:
            st.done(f"data_{step}", **_data_step(step, root, inside, st, timeout))
            st.unwarn(f"Watershed data, {TITLES[step]}:")
        except Exception as exc:                             # noqa: BLE001
            st.warn(f"Watershed data, {TITLES[step]}: {type(exc).__name__}: "
                    f"{str(exc)[:200]}. Run the project again to retry.")

    # 7. level B, HUC12 by HUC12 --------------------------------------------------------------
    lon, lat = bnd_wgs.centroid.x, bnd_wgs.centroid.y
    curve, why_curve = choose_curve(lon, lat, regional_curve)
    with_lidar = inside[inside["lidar_frac"].fillna(1.0) >= lidar_min_fraction]
    mode = (level_b or "none").lower()
    if mode == "listed":
        want = [h.strip() for h in (huc12s.split(",") if isinstance(huc12s, str)
                                    else (huc12s or [])) if str(h).strip()]
    elif mode == "all":
        want = list(with_lidar["huc12"])
    elif mode == "pilot":
        if pilot_by == "unconfined":
            order = _pilot_order(with_lidar, reaches)
            st.data["pilot"] = {"by": "unconfined"}
        else:
            note("project: choosing pilot HUC12s that represent the area")
            try:
                order, st.data["pilot"] = _representative_pilot(
                    inside, int(pilot_count), can_run=with_lidar["huc12"],
                    valley_types=_read_or_none(pa.get("valley_types")),
                    reaches=reaches,
                    soils=_read_or_none((st.step("soils") or {}).get("path"),
                                        columns=["hydgrpdcd", "kwfact"]))
            except Exception as exc:                         # noqa: BLE001
                # The pilot's choice must never stop a night's run.
                st.warn(f"The representative pilot could not be chosen "
                        f"({type(exc).__name__}: {str(exc)[:200]}); the "
                        f"pilot is the HUC12s with the most unconfined "
                        f"valley instead.")
                order = _pilot_order(with_lidar, reaches)
                st.data["pilot"] = {"by": "unconfined"}
        st.save()
        done_b = [h for h, v in st.data["huc12"].items()
                  if v.get("status") == "done"]
        want = done_b + [h for h in order if h not in done_b]
        want = want[:max(int(pilot_count), len(done_b))]
    else:
        want = []
    # Every HUC12's 1 m mosaic is about 300 MB; over a whole field office
    # that is tens of GB for rasters whose sections are already measured.
    keep_dem = keep_lidar_dem if keep_lidar_dem is not None else mode != "all"
    b_t0 = time.monotonic()
    for code in progress(want, label="level B HUC12s"):
        rec = st.data["huc12"].get(code) or {}
        if rec.get("status") in ("done", "no lidar"):
            continue
        if level_b_hours and (time.monotonic() - b_t0) / 3600 > level_b_hours:
            st.warn(f"Level B stopped at its time limit ({level_b_hours} h); "
                    f"run the project again to carry on.")
            break
        row = hucs[hucs["huc12"] == code]
        if row.empty:
            st.data["huc12"][code] = {"status": "failed",
                                      "error": "not a HUC12 of this project"}
            st.save()
            continue
        known = row.iloc[0].get("lidar_frac")
        if known is not None and known == known and known < 0.5:
            st.data["huc12"][code] = {
                "status": "no lidar", "lidar": float(known),
                "note": "3DEP has 1 m lidar over under half of this HUC12: "
                        "valley types only (level A)"}
            st.save()
            continue
        st.data["huc12"][code] = _level_b(
            code, row, root, dem_run, pa, curve, section_plots,
            lidar_min_fraction, keep_lidar_tiles, keep_dem)
        st.save()

    # 8. confinement at 10 m, calibrated to what the lidar measured ------------------------------
    if calibrate_confinement:
        try:
            cal = _calibrate_confinement(st, pa)
        except Exception as exc:                             # noqa: BLE001
            cal = None
            st.warn(f"The confinement calibration against the lidar failed "
                    f"({type(exc).__name__}: {str(exc)[:200]}).")
        if cal is None:
            cal = {"skipped": "no reach has been measured on lidar sections "
                              "yet (level B)", "n": 0}
        if cal:
            done = (st.data.get("confinement") or {}).get("breaks")
            gain = cal.get("agreement_after", 0) - cal.get("agreement_before", 0)
            worth = (cal.get("breaks") and gain >= 0.05
                     and cal["breaks"] != list(PUBLISHED_BREAKS))
            cal["applied"] = bool(worth)
            if worth and cal["breaks"] != done:
                from .landscape.classify import reclassify_confinement

                b1, b2 = cal["breaks"]
                note(f"project: confinement breaks {b1:g} and {b2:g} from the "
                     f"lidar (agreement {100 * cal['agreement_before']:.0f}% "
                     f"-> {100 * cal['agreement_after']:.0f}%)")
                with _out(A / "run") as g:
                    rc = reclassify_confinement(pa["segments"], b1, b2, guard=g)
                pa = dict(pa, segments=rc.segments, confinement_breaks=[b1, b2],
                          typing=0)                     # forces the re-typing
                st.data["steps"]["pass_a"] = pa
                st.data["confinement"] = cal
                st.save()
                pa = _retype_valleys(root, A, st, pa)
                reaches = gpd.read_file(pa["segments"])
            elif worth:
                cal["applied"] = True                  # applied on an earlier run
                st.data["confinement"] = cal
            else:
                st.data["confinement"] = cal
            st.save()

    # 8b. valleys on the lidar at a finer cell, HUC12 by HUC12 (a pilot) -----------------------
    fine_merged: dict[str, str] = {}
    fmode = (fine_valleys or "none").lower()
    if fmode in ("pilot", "listed", "all"):
        try:
            fine_merged = _fine_valleys(
                root, st, hucs, pa, dem_run, fmode, fine_huc12s,
                int(fine_count), float(fine_cell_m), stream_threshold_km2,
                keep_tiles=keep_lidar_tiles,
                keep_1m=bool(keep_lidar_dem) if keep_lidar_dem is not None
                else fmode != "all", workers=fine_workers)
            st.unwarn(f"The {float(fine_cell_m):g} m valley pass failed")
        except Exception as exc:                             # noqa: BLE001
            st.warn(f"The {float(fine_cell_m):g} m valley pass failed "
                    f"({type(exc).__name__}: {str(exc)[:200]}); the 10 m "
                    f"results are unaffected. Run the project again to retry.")

    # 9. merged layers, the HUC12 summary and the report ----------------------------------------
    merged = _merge(root, st, hucs, bnd, pa, cell_a, lidar_min_fraction)
    merged.update(fine_merged)
    if st.step("soils"):
        merged["soils"] = st.step("soils")["path"]
    for step, key in (("ecosites", "soils_ecosites"), ("fire", "fire_perimeters")):
        p = (st.step(f"data_{step}") or {}).get("path")
        if p and Path(p).exists():
            merged[key] = p
    note("project: HUC12 summary")
    try:
        from .wsdata import huc12_summary

        s = huc12_summary(
            root / "merged", inside, valley_types=merged.get("valley_types"),
            soils=(st.step("soils") or {}).get("path"),
            stability=merged.get("stability"), slope=_slope_raster(pa, A),
            data_dir=root / "data",
            pilot_group=(st.data.get("pilot") or {}).get("group_of"))
        merged["huc12_summary"] = s["path"]
        st.unwarn("The HUC12 summary could not be built")
    except Exception as exc:                                 # noqa: BLE001
        st.warn(f"The HUC12 summary could not be built ({type(exc).__name__}: "
                f"{str(exc)[:200]}).")
    # 10. stream power, major streams, the sensitivity table -----------------------------------
    note("project: stream power, major streams and the sensitivity table")
    try:
        from .synthesis import synthesize

        syn = synthesize(root / "merged", valley_types=pa["valley_types"],
                         curve=curve, merged=merged["valley_types"],
                         summary_csv=root / "merged" / "huc12_summary.csv",
                         hucs=inside, min_drainage_km2=major_streams_km2,
                         weights=sensitivity_weights, cell_m=cell_a,
                         flowlines=(st.step("nhd") or {}).get("path"))
        merged["major_streams"] = syn["major_streams"]
        if syn.get("sensitivity_layer"):
            merged["huc12_sensitivity"] = syn["sensitivity_layer"]
        st.data["synthesis"] = {k: v for k, v in syn.items()
                                if k not in ("reaches",)}
        st.unwarn("The stream power and sensitivity step failed")
    except Exception as exc:                                 # noqa: BLE001
        st.warn(f"The stream power and sensitivity step failed "
                f"({type(exc).__name__}: {str(exc)[:200]}).")
    st.save()
    _report(root, st, merged, cell_a, curve, why_curve, want_data)
    return {"project": str(root), "status": str(st.path), **merged,
            "level_b": {h: v.get("status") for h, v in st.data["huc12"].items()},
            "warnings": st.data["warnings"],
            "seconds": round(time.monotonic() - t0, 1)}


def _level_b(code, row, root: Path, dem_a: str, pa: dict, curve,
             section_plots: bool, lidar_min: float, keep_tiles: bool,
             keep_dem: bool = True) -> dict:
    """Stream types and stability for one HUC12, on lidar."""
    import geopandas as gpd

    from . import api
    from .landscape import rosgen_stream_types
    from .landscape.stability import stream_stability

    t0 = time.monotonic()
    hdir = root / "B_huc12" / code
    hdir.mkdir(parents=True, exist_ok=True)
    try:
        import rasterio

        with rasterio.open(dem_a) as src:
            dem_crs = src.crs
        poly = row.iloc[[0]][["huc12", "geometry"]].to_crs(dem_crs)
        poly_path = hdir / "huc12.gpkg"
        poly.to_file(str(poly_path), driver="GPKG", layer="huc12")
        with _out(hdir / "dem"):
            d = api.fetch_dem(str(poly_path), source="1m",
                              resolution_m=LEVEL_B_CELL_M, crs=dem_a,
                              buffer_m=100.0)
        if not keep_tiles:
            shutil.rmtree(Path(d["dem"]).parent / "dem_tiles",
                          ignore_errors=True)
        # Coverage is of the HUC12's bounding box, which an irregular HUC12
        # never fills; the footprint check before the download decided.
        frac = float(row.iloc[0].get("lidar_frac") or 0.0)
        if float(d.get("coverage") or 0.0) < 0.05:
            return {"status": "no lidar", "lidar": 0.0,
                    "note": "the 1 m download came back empty: valley "
                            "types only (level A)"}
        reaches = gpd.read_file(pa["segments"])
        inside = reaches[reaches.geometry.centroid.within(
            _union(poly.to_crs(reaches.crs)))]
        if inside.empty:
            return {"status": "done", "reaches": 0, "lidar": round(frac, 3),
                    "seconds": round(time.monotonic() - t0, 1)}
        seg_path = hdir / "reaches.gpkg"
        inside.to_file(str(seg_path), driver="GPKG")
        with _out(hdir) as g:
            stres = rosgen_stream_types(
                dem_a, str(seg_path), dem_fine=d["dem"],
                geology=pa.get("geology"), regional_curve=curve,
                section_plots=section_plots, guard=g).as_dict()
            stab = stream_stability(pa["valley_types"], stres["segments"],
                                    guard=g)
        out = {"status": "done", "reaches": int(len(inside)),
               "lidar": round(frac, 3), "lidar_partial": frac < lidar_min,
               "stream_types": stres["segments"],
               "sections": stres.get("sections"),
               "stability": stab["stability"], "counts": stab.get("counts"),
               "seconds": round(time.monotonic() - t0, 1)}
        if not keep_dem:
            # The sections are measured and the plots drawn: the mosaic has
            # done its work. Its tiles went already.
            removed = 0
            for p in Path(d["dem"]).parent.glob(Path(d["dem"]).name + "*"):
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
            out["lidar_dem_removed"] = bool(removed)
        return out
    except Exception as exc:                                 # noqa: BLE001
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:500],
                "seconds": round(time.monotonic() - t0, 1)}


# -- a project started from an earlier one (v0.43) -----------------------------------
#
# Sam, 23 Oct 2026: "don't overwrite 42, call the new version 43". The
# fixes and the 3 m pilot need none of level A's two hours or level B's
# twenty, so a new folder can start from an earlier project's measurements:
# copied, with every path in project.json moved, the earlier folder only
# read. The caches, the 1 m tiles, the merged layers, the logs and the
# report are not copied; this run rebuilds them.

# The plots are most of a level B folder's files (Kingman: ~1,800 per
# HUC12, ~190,000 in all) and nothing after level B reads them: they stay
# in the earlier folder, where the layers' plot_path still points.
COPY_SKIP_DIRS = {"dem_tiles", ".geolens_cache", "__pycache__", "runs",
                  "section_plots", "reach_plots"}
COPY_SKIP_TOP = {"merged", "review", "A_fine"}


def _pid_alive(pid: int) -> bool:
    """Whether a process is running, without signalling it (os.kill on
    Windows terminates the process whatever the signal)."""
    if pid <= 0:
        return False
    try:
        import psutil  # type: ignore

        return bool(psutil.pid_exists(pid))
    except ImportError:
        pass
    if os.name == "nt":
        try:
            import ctypes

            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, pid)    # QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = k.GetExitCodeProcess(h, ctypes.byref(code))
            k.CloseHandle(h)
            return bool(ok) and code.value == 259     # STILL_ACTIVE
        except Exception:                                    # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _take_lock(root: Path) -> None:
    """One run per project folder: a second one started while the first
    still runs (a double-click twice, the toolbox and a .bat) would write
    the same project.json. A lock left by a run that died is taken over."""
    lock = root / "run.lock"
    try:
        pid = int(lock.read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        pid = 0
    if pid and pid != os.getpid() and _pid_alive(pid):
        raise GeolensError(
            f"Another geolens run (process {pid}) is working in {root}. "
            f"Let it finish, or close it, before starting this folder again.")
    lock.write_text(f"{os.getpid()} {_now()}\n", encoding="utf-8")

    def release():
        try:
            if lock.read_text(encoding="utf-8").split()[0] == str(os.getpid()):
                lock.unlink()
        except (OSError, IndexError):
            pass

    import atexit

    atexit.register(release)


def _keep_awake() -> None:
    """Ask Windows not to sleep while this process runs (the screen may
    still turn off); cleared when it ends. A multi-day run otherwise stops
    whenever the PC goes to sleep."""
    if os.name != "nt":
        return
    try:
        import atexit
        import ctypes

        es_continuous, es_system_required = 0x80000000, 0x00000001
        k = ctypes.windll.kernel32
        k.SetThreadExecutionState(es_continuous | es_system_required)
        atexit.register(lambda: k.SetThreadExecutionState(es_continuous))
    except Exception:                                        # noqa: BLE001
        pass


def _moved_paths(obj, src: Path, root: Path):
    """Every string in a project.json that is a path under ``src``, moved
    under ``root`` (Windows paths compared without case)."""
    a, b = str(src).rstrip("\\/"), str(root)
    al = a.lower().replace("/", "\\")

    def move(s):
        if isinstance(s, str):
            sl = s.lower().replace("/", "\\")
            if sl.startswith(al) and (len(s) == len(a) or s[len(a)] in "\\/"):
                return b + s[len(a):]
        return s

    if isinstance(obj, dict):
        return {k: _moved_paths(v, src, root) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_moved_paths(v, src, root) for v in obj]
    return move(obj)


def copy_project(src: str | Path, root: str | Path) -> dict:
    """Start the project folder ``root`` from the earlier project ``src``:
    its boundary, HUC12s, level A, level B, soils and watershed data are
    copied and its project.json written here with every path moved, so the
    next run carries on from them. ``src`` is only read."""
    src, root = Path(src), Path(root)
    rs, rr = src.resolve(), root.resolve()
    if rs == rr:
        raise GeolensError("A project cannot be started from itself.")
    if rs in rr.parents:
        raise GeolensError(f"The new project folder {root} is inside the "
                           f"one it is copied from ({src}).")
    pj = src / "project.json"
    if not pj.exists():
        raise GeolensError(f"{src} has no project.json: not a geolens project "
                           f"folder to start from.")
    if (root / "project.json").exists():
        raise GeolensError(f"{root} already has a project.json; it carries on "
                           f"from that, not from {src}.")
    old = json.loads(pj.read_text(encoding="utf-8"))
    # The paths inside are written as that run saw its folder (a drive
    # letter, not the network path it may resolve to): take its own root.
    first = ((old.get("steps") or {}).get("boundary") or {}).get("path")
    old_root = os.path.dirname(first) if first else str(src)
    files = list(_project_files(src))
    total = sum(p.stat().st_size for p in files)
    note(f"project: copying {len(files):,} files ({total / 1e9:.1f} GB) from "
         f"{src} (that folder is only read)")
    done = 0
    for p in progress(files, label="copying the earlier project"):
        dst = root / p.relative_to(src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
        done += 1
    new = _moved_paths(old, Path(old_root), root)
    for k in ("synthesis", "fine_valleys", "copied_from"):
        new.pop(k, None)
    rec = {"project": str(src), "at": _now(), "files": done,
           "gb": round(total / 1e9, 2)}
    new["copied_from"] = rec
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.json").write_text(json.dumps(new, indent=1, default=str),
                                       encoding="utf-8")
    return rec


def _project_files(src: Path):
    for dirpath, dirnames, filenames in os.walk(src):
        here = Path(dirpath)
        top = here == src
        dirnames[:] = [d for d in dirnames if d not in COPY_SKIP_DIRS
                       and not (top and (d in COPY_SKIP_TOP or d.startswith(".")))]
        for f in filenames:
            if top and not f.lower().endswith(".gpkg"):
                continue              # project.json is rewritten; the rest is this run's
            yield here / f


def _copied_lines(rec: dict | None) -> list[str]:
    if not rec:
        return []
    return [f"Level A, level B, the soils and the watershed data were copied "
            f"from `{rec.get('project')}` on {rec.get('at')} "
            f"({rec.get('files', 0):,} files, {rec.get('gb')} GB) and carried on "
            f"from here; that folder was not changed. The level B plots were "
            f"not copied: `plot_path` in the stream layers still points to "
            f"them there.", ""]


# -- valleys on the lidar at a finer cell (v0.43) -------------------------------------
#
# Level A's valleys are measured on the 10 m DEM: 11% of Kingman's stream
# length is in valleys narrower than three 10 m cells, the 10 m confined
# class held 0.1% of the length while the lidar sections found 13% of the
# measured reaches entrenched, and the valley polygons are 10 m stair-steps
# at 1:8,000. A whole field office at 1 m is ~23 billion cells (the 10 m
# pass needed ~70 GB for 0.4 billion), so the finer pass runs HUC12 by
# HUC12 on the lidar resampled to a few metres. A tile cut to one HUC12
# only knows the drainage it holds, so the pilot takes headwater HUC12s,
# which hold all of theirs, and compares each with the 10 m pass.

FINE_CELL_M = 3.0
SETTINGS = (("mountain (confined valley types)",
             lambda lt: lt.str.startswith("C-")),
            ("fans and piedmonts", lambda lt: lt.isin(["U-AL-IF", "U-AL-AF"])),
            ("alluvial valley", lambda lt: lt == "U-AL-AD"))


def _fine_pilot(hucs, st: _Status, pa: dict, count: int = 3
                ) -> tuple[list[str], dict]:
    """Headwater HUC12s whose channels level B measured on lidar, one per
    setting: the most confined, the most fan and piedmont, the most
    alluvial valley (by 10 m stream length); more than three fill with the
    HUC12s with the most stream."""
    import geopandas as gpd

    codes = hucs["huc12"].astype(str)
    lidar = {h for h, v in st.data["huc12"].items()
             if v.get("status") == "done" and float(v.get("lidar") or 0) >= 0.95}
    fed = (set(hucs["tohuc"].dropna().astype(str))
           if "tohuc" in hucs.columns else set())
    cands = [c for c in codes if c in lidar and c not in fed]
    basis = "headwater HUC12s with lidar"
    if not cands:
        cands, basis = sorted(lidar), "HUC12s with lidar (no headwater one)"
    if not cands:
        return [], {"basis": "no HUC12 has had its channels measured on lidar"}
    vt = gpd.read_file(pa["valley_types"], columns=["landscape_type"])
    poly = hucs[codes.isin(cands)][["huc12", "geometry"]].to_crs(vt.crs)
    poly["huc12"] = poly["huc12"].astype(str)
    pts = gpd.GeoDataFrame(
        {"lt": vt["landscape_type"].fillna("").astype(str),
         "km": vt.geometry.length / 1000.0},
        geometry=vt.geometry.centroid, crs=vt.crs)
    j = gpd.sjoin(pts, poly, how="inner", predicate="within")
    tot = j.groupby("huc12")["km"].sum()
    if tot.empty:
        return [], {"basis": f"{basis}, but none with 10 m reaches in it"}
    # enough stream to say anything (20 km; less on a small project)
    tot = tot[tot >= min(20.0, 0.5 * float(tot.max()))]
    chosen: list[str] = []
    why: dict[str, str] = {}
    for name, rule in SETTINGS:
        if len(chosen) >= count:
            break
        s = (j[rule(j["lt"])].groupby("huc12")["km"].sum() / tot) \
            .reindex(tot.index).fillna(0.0).drop(labels=chosen, errors="ignore")
        if s.empty or s.max() <= 0:
            continue
        code = str(s.idxmax())
        chosen.append(code)
        why[code] = f"{name}: {100 * s.max():.0f}% of its stream length"
    for code in tot.sort_values(ascending=False).index:
        if len(chosen) >= count:
            break
        if code not in chosen:
            chosen.append(str(code))
            why[str(code)] = f"{tot[code]:.0f} km of stream"
    return chosen, {"basis": basis, "why": why}


def _compare_valleys(coarse_vt: str, fine_vt: str, poly, cell_coarse: float,
                     cell_fine: float, coarse_extent: str | None = None,
                     fine_extent: str | None = None) -> dict:
    """The fine pass against the 10 m one inside one HUC12: stream length,
    valley type and confinement agreement (each fine reach against the
    nearest 10 m reach within three 10 m cells, by length), confined and
    unresolved shares, valley width, valley extent area."""
    import geopandas as gpd

    c = gpd.read_file(coarse_vt, bbox=tuple(poly.to_crs(
        gpd.read_file(coarse_vt, rows=1).crs).total_bounds))
    f = gpd.read_file(fine_vt).to_crs(c.crs)
    area = poly.to_crs(c.crs).geometry.iloc[0]
    c = c[c.geometry.centroid.within(area)].copy()
    f = f[f.geometry.centroid.within(area)].copy()
    out: dict[str, Any] = {}
    for tag, g, cell in (("10m", c, cell_coarse), ("fine", f, cell_fine)):
        g["_km"] = g.geometry.length / 1000.0
        km = float(g["_km"].sum())
        out[f"km_{tag}"] = round(km, 1)
        if km and "confinement" in g.columns:
            for k in ("confined", "partly confined", "unconfined"):
                out[f"{k.replace(' ', '_')}_{tag}"] = round(
                    float(g.loc[g["confinement"] == k, "_km"].sum() / km), 3)
        if km and "valley_width_m" in g.columns:
            w = g["valley_width_m"].astype(float)
            out[f"unresolved_{tag}"] = round(
                float(g.loc[~(w >= 3 * cell), "_km"].sum() / km), 3)
    if len(f) and len(c):
        mid = f.copy()
        mid["geometry"] = f.geometry.interpolate(0.5, normalized=True)
        keep = [k for k in ("landscape_type", "confinement", "valley_width_m")
                if k in c.columns]
        j = gpd.sjoin_nearest(mid, c[[*keep, "geometry"]], how="left",
                              max_distance=3 * cell_coarse, distance_col="_d",
                              lsuffix="f", rsuffix="c")
        j = j[~j.index.duplicated()]
        m = j["_d"].notna()
        w = j.loc[m, "_km"]
        out["matched_share"] = round(float(w.sum() / j["_km"].sum()), 3) \
            if j["_km"].sum() else None
        if w.sum():
            for k in ("landscape_type", "confinement"):
                if f"{k}_f" in j.columns and f"{k}_c" in j.columns:
                    a = j.loc[m, f"{k}_f"].fillna("none").astype(str)
                    b = j.loc[m, f"{k}_c"].fillna("none").astype(str)
                    out[f"{k}_agreement"] = round(float(w[a == b].sum() / w.sum()), 3)
            if "landscape_type_f" in j.columns and "landscape_type_c" in j.columns:
                a = j.loc[m, "landscape_type_c"].fillna("none").astype(str)
                b = j.loc[m, "landscape_type_f"].fillna("none").astype(str)
                ch = a != b
                pairs = (j.loc[m][ch].assign(_p=(a + " -> " + b)[ch])
                         .groupby("_p")["_km"].sum().sort_values(ascending=False))
                out["type_changes"] = [f"{p} ({km:.1f} km)"
                                       for p, km in pairs.head(3).items()]
            if "valley_width_m_f" in j.columns:
                ratio = (j.loc[m, "valley_width_m_f"].astype(float)
                         / j.loc[m, "valley_width_m_c"].astype(float))
                ratio = ratio[(ratio > 0) & (ratio < 1e6)]
                if len(ratio):
                    out["valley_width_ratio_median"] = round(float(ratio.median()), 2)
    for tag, p in (("10m", coarse_extent), ("fine", fine_extent)):
        if p and Path(p).exists():
            try:
                e = gpd.read_file(p, bbox=tuple(poly.to_crs(
                    gpd.read_file(p, rows=1).crs).total_bounds))
                e = gpd.clip(e.to_crs(c.crs), area)
                out[f"valley_km2_{tag}"] = round(float(e.geometry.area.sum()) / 1e6, 2)
            except Exception:                                # noqa: BLE001
                pass
    return out


def _inflow_correct(tile, coarse, breaks, exp: float = 0.35,
                    max_dist_m: float = 40.0, max_angle: float = 35.0,
                    min_factor: float = 1.5, min_gain_km2: float = 5.0):
    """A HUC12 tile only routes the drainage inside it, so a main stem
    entering from upstream starts with a fraction of its real drainage, a
    channel width to match and too wide a confinement ratio. Where a tile
    reach lies along a 10 m reach (midpoint within ``max_dist_m``, running
    the same way within ``max_angle`` degrees) that drains much more
    (``min_factor`` times and ``min_gain_km2`` more), it takes the 10 m
    drainage; the channel width scales as A**``exp`` (classify's relation),
    the ratio by the inverse, and the class follows with ``breaks``.
    Returns the corrected layer and how many reaches changed."""
    import geopandas as gpd
    import numpy as np
    import pandas as pd

    from .landscape.classify import (_confinement_class, _gradient_class,
                                     _rosgen_candidates)

    t = tile.copy()
    t["drainage_from_10m"] = False
    if t.empty or coarse.empty or "drainage_km2" not in coarse.columns:
        return t, 0
    c = coarse[["drainage_km2", "geometry"]].to_crs(t.crs).reset_index(drop=True)
    mid = gpd.GeoDataFrame({"_i": np.arange(len(t))},
                           geometry=t.geometry.interpolate(0.5, normalized=True),
                           crs=t.crs)
    j = gpd.sjoin_nearest(mid, c, how="left", max_distance=max_dist_m,
                          distance_col="_d")
    j = j[~j["_i"].duplicated()].set_index("_i")

    def bearing(g):
        try:
            xy = np.asarray(g.coords) if g.geom_type == "LineString" else \
                np.asarray(list(g.geoms)[0].coords)
            dx, dy = xy[-1][0] - xy[0][0], xy[-1][1] - xy[0][1]
            return float(np.degrees(np.arctan2(dy, dx)))
        except Exception:                                    # noqa: BLE001
            return np.nan

    a_t = pd.to_numeric(t["drainage_km2"], errors="coerce").to_numpy("float64")
    a_c = pd.to_numeric(j["drainage_km2"], errors="coerce").reindex(
        np.arange(len(t))).to_numpy("float64")
    idx = j["index_right"].reindex(np.arange(len(t)))
    b_t = np.array([bearing(g) for g in t.geometry])
    b_c = np.array([bearing(c.geometry.iloc[int(k)]) if pd.notna(k) else np.nan
                    for k in idx])
    diff = np.abs(((b_t - b_c + 90.0) % 180.0) - 90.0)   # undirected, 0..90
    ok = (np.isfinite(a_c) & np.isfinite(a_t) & (diff <= max_angle)
          & (a_c > min_factor * np.maximum(a_t, 1e-6))
          & (a_c - a_t >= min_gain_km2))
    n = int(ok.sum())
    if not n:
        return t, 0
    scale = np.ones(len(t))
    scale[ok] = (np.maximum(a_t[ok], 1e-6) / a_c[ok]) ** exp   # ratio factor
    t.loc[ok, "drainage_km2"] = a_c[ok]
    if "channel_width_m" in t.columns:
        cw = pd.to_numeric(t["channel_width_m"], errors="coerce").to_numpy("float64")
        t["channel_width_m"] = np.where(ok, cw / scale, cw)
    for col in ("confinement_ratio", "confinement_ratio_hand"):
        if col in t.columns:
            r = pd.to_numeric(t[col], errors="coerce").to_numpy("float64")
            t[col] = np.where(ok, r * scale, r)
    b1, b2 = breaks
    t.loc[ok, "confinement"] = [_confinement_class(r, b1, b2)
                                for r in t.loc[ok, "confinement_ratio"]]
    if "gradient_class" not in t.columns:
        t["gradient_class"] = [_gradient_class(g) for g in t["gradient"]]
    ros = [_rosgen_candidates(cc, g, w) for cc, g, w in
           zip(t.loc[ok, "confinement"], t.loc[ok, "gradient_class"],
               t.loc[ok, "valley_width_m"])]
    if "rosgen_candidates" in t.columns:
        t.loc[ok, "rosgen_candidates"] = [r[0] for r in ros]
    if "needs_geology" in t.columns:
        t.loc[ok, "needs_geology"] = [r[1] for r in ros]
    t.loc[ok, "drainage_from_10m"] = True
    return t, n


def _carry_inflow(res: dict, pa: dict, breaks, run_dir: Path) -> dict:
    """Correct a tile pass's drainage from the 10 m network and, where any
    reach changed, type its valleys again and rebuild its valley polygons."""
    import geopandas as gpd

    seg = (res.get("classified") or {}).get("segments")
    if not seg or not Path(seg).exists():
        return {"corrected": 0}
    tile = gpd.read_file(seg)
    coarse = gpd.read_file(pa["valley_types"], bbox=tuple(tile.total_bounds),
                           columns=["drainage_km2"])
    fixed, n = _inflow_correct(tile, coarse, breaks)
    out = {"corrected": n,
           "corrected_km": round(float(fixed.loc[fixed["drainage_from_10m"]]
                                       .geometry.length.sum()) / 1000.0, 1)}
    if not n:
        return out
    idir = run_dir / "inflow_dir"
    idir.mkdir(parents=True, exist_ok=True)
    corrected = idir / "segments_classified.gpkg"
    fixed.to_file(str(corrected), driver="GPKG")
    from .landscape.pipeline import _valley_alloc_for
    from .landscape.rosgen import rosgen_valley_types
    from .landscape.valleyform import valley_polygons

    fans = run_dir / "fans_dir" / "fans.gpkg"
    vform = run_dir / "valley_form_dir" / "valley_form_segments.csv"
    with _out(run_dir) as g:
        vt = rosgen_valley_types(
            str(corrected), geology=pa.get("geology"),
            fans=str(fans) if fans.exists() else None,
            valley_form=str(vform) if vform.exists() else None, guard=g)
    out["valley_types"] = vt.segments
    try:
        with _out(run_dir) as g:
            out["valley_extent"] = valley_polygons(
                _valley_alloc_for(str(corrected)), attributes=vt.segments,
                guard=g)
    except Exception as exc:                                 # noqa: BLE001
        out["extent_failed"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    return out


def _clean_tile(fdir: Path) -> float:
    """Delete a finished tile's working rasters and routing cache (the 1 m
    and fine DEMs and every vector output stay); returns GB freed."""
    freed = 0
    for p in sorted(fdir.rglob("*"), key=lambda q: len(q.parts), reverse=True):
        try:
            in_dem = p.parent.name == "dem" or p.parent == fdir / "dem"
            if p.is_dir() and p.name in (".geolens_cache", "dem_tiles"):
                freed += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                shutil.rmtree(p, ignore_errors=True)
            elif p.is_file() and p.suffix.lower() in (".tif", ".tiff") \
                    and "run" in p.relative_to(fdir).parts and not in_dem:
                freed += p.stat().st_size
                p.unlink()
        except OSError:
            continue
    return round(freed / 1e9, 2)


def _fine_valley_huc12(code: str, row, root: str, dem_a: str, pa: dict,
                       cell_m: float, threshold_km2: float, breaks,
                       keep_1m: bool = True, keep_tiles: bool = False) -> dict:
    """One HUC12 on the lidar: the 1 m mosaic (kept if asked), resampled to
    ``cell_m``, the landscape pass on it, the 10 m drainage carried in, the
    comparison with the 10 m pass, the working rasters deleted. Runs in a
    worker process too: everything it needs comes in its arguments."""
    from . import api

    t0 = time.monotonic()
    root = Path(root)
    fdir = root / "A_fine" / code
    fdir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {}
    try:
        import rasterio

        with rasterio.open(dem_a) as src:
            dem_crs = src.crs
        poly = row.iloc[[0]][["huc12", "geometry"]].to_crs(dem_crs)
        poly_path = fdir / "huc12.gpkg"
        poly.to_file(str(poly_path), driver="GPKG", layer="huc12")
        # A 1 m mosaic kept from an earlier attempt (a crash, a reboot) is
        # used again rather than downloaded again, if it reads.
        kept = fdir / "dem" / "dem_mosaic.tif"
        d = {"dem": str(kept), "coverage": 1.0} if _readable(kept) else None
        if d is None:
            with _out(fdir / "dem"):
                d = api.fetch_dem(str(poly_path), source="1m",
                                  resolution_m=LEVEL_B_CELL_M, crs=dem_a,
                                  buffer_m=250.0)
        else:
            out["dem_reused"] = True
        if not keep_tiles:
            shutil.rmtree(Path(d["dem"]).parent / "dem_tiles", ignore_errors=True)
        if float(d.get("coverage") or 0.0) < 0.05:
            return {"status": "no lidar", "seconds": round(time.monotonic() - t0, 1)}
        dem1 = d["dem"]
        cell = float(cell_m)
        dem_run = dem1
        if _cell_of(dem1) < cell:
            with _out(fdir / "dem"):
                dem_run = api.resample_dem(dem1, cell)["path"]
        if keep_1m:
            out["dem_1m"] = dem1
        elif dem_run != dem1:
            for p in Path(dem1).parent.glob(Path(dem1).name + "*"):
                try:
                    p.unlink()
                except OSError:
                    pass
        b1, b2 = breaks
        res = None
        for attempt in range(3):
            try:
                with _out(fdir / "run"):
                    res = api.landscape_pass(
                        dem_run, stream_threshold_km2=threshold_km2,
                        segment_length_m=500.0, confined_max=b1,
                        partly_confined_max=b2, valley_types=True,
                        stream_types=False, geology=pa.get("geology"), fans=True)
                break
            except MemoryError:
                if attempt == 2:
                    raise
                cell *= 2
                note(f"HUC12 {code}: out of memory, again at {cell:g} m")
                with _out(fdir / "dem"):
                    dem_run = api.resample_dem(dem_run, cell)["path"]
                out["coarsened_for_memory"] = True
        cell = _cell_of(dem_run)                 # what the pass really ran on
        out["cell_m"] = cell
        out["dem"] = dem_run
        vt = (res.get("valley_types") or {}).get("segments")
        warns = [str(w)[:300] for w in (res.get("warnings") or [])]
        if not vt:
            return {**out, "status": "failed",
                    "seconds": round(time.monotonic() - t0, 1),
                    "error": ("the pass made no valley types: "
                              + "; ".join(warns[:2]))[:500]}
        extent = res.get("valley_extent")
        try:
            inflow = _carry_inflow(res, pa, breaks, fdir / "run")
        except Exception as exc:                             # noqa: BLE001
            inflow = {"corrected": 0,
                      "failed": f"{type(exc).__name__}: {str(exc)[:200]}"}
        vt = inflow.pop("valley_types", None) or vt
        extent = inflow.pop("valley_extent", None) or extent
        out.update(status="done", valley_types=vt, valley_extent=extent,
                   inflow=inflow, warnings=warns[:6])
        try:
            extent_10m = Path(pa["valley_types"]).parent.parent \
                / "valley_extent_dir" / "valley_extent.gpkg"
            out["compare"] = _compare_valleys(
                pa["valley_types"], vt, poly, _cell_of(dem_a), cell,
                coarse_extent=str(extent_10m) if extent_10m.exists() else None,
                fine_extent=extent)
        except Exception as exc:                             # noqa: BLE001
            out["compare_failed"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        out["freed_gb"] = _clean_tile(fdir)
        out["seconds"] = round(time.monotonic() - t0, 1)
        return out
    except Exception as exc:                                 # noqa: BLE001
        return {**out, "status": "failed",
                "error": f"{type(exc).__name__}: {exc}"[:500],
                "seconds": round(time.monotonic() - t0, 1)}


def _readable(path: Path) -> bool:
    """A raster that exists and reads (a download cut off by a crash does
    not; it is deleted so the tile fetches it again)."""
    if not path.exists():
        return False
    try:
        import rasterio
        from rasterio.windows import Window

        with rasterio.open(str(path)) as src:
            src.read(1, window=Window(0, 0, min(64, src.width), min(64, src.height)))
            src.read(1, window=Window(max(0, src.width - 64), max(0, src.height - 64),
                                      min(64, src.width), min(64, src.height)))
        return True
    except Exception:                                        # noqa: BLE001
        try:
            path.unlink()
        except OSError:
            pass
        return False


def _cell_of(raster: str) -> float:
    try:
        return float(_cell_m(raster))
    except Exception:                                        # noqa: BLE001
        return 10.0


def _free_gb(path: Path) -> float:
    try:
        return shutil.disk_usage(str(path)).free / 1e9
    except OSError:
        return float("inf")


def fine_workers(cell_m: float, tile_km2: float = 200.0, cap: int = 6) -> int:
    """How many HUC12s to run at once: a tile of ``tile_km2`` at ``cell_m``
    peaks near 170 bytes a cell (the Kingman 10 m pass: 70 GB for 0.41
    billion cells); use 60% of the memory free now and half the cores
    (pysheds and numpy use more than one), at most ``cap``."""
    from .landscape.plan import available_ram_gb

    per_gb = max(2.0, tile_km2 * 1e6 / (cell_m ** 2) * 170 / 1e9)
    avail = available_ram_gb()
    by_mem = int((0.6 * avail) // per_gb) if avail else 2
    by_cpu = max(1, ((os.cpu_count() or 2) - 2) // 2)
    return max(1, min(cap, by_mem, by_cpu))


def _fine_valleys(root: Path, st: _Status, hucs, pa: dict, dem_a: str,
                  mode: str, listed, count: int, cell_m: float,
                  threshold_km2: float, keep_tiles: bool = False,
                  keep_1m: bool = True, workers: int | None = None,
                  min_free_gb: float = 30.0) -> dict:
    """Run the finer valley pass over the pilot, the listed or all the
    HUC12s with lidar (the pilot first, so its comparison comes early),
    several at once when ``workers`` > 1, and merge what it made."""
    import geopandas as gpd
    import pandas as pd

    rec = st.data.get("fine_valleys") or {}
    if rec.get("cell_m") != cell_m:
        rec = {"cell_m": cell_m, "huc12": {}}
    rec["mode"] = mode
    if mode == "listed":
        want = [h.strip() for h in (listed.split(",") if isinstance(listed, str)
                                    else (listed or [])) if str(h).strip()]
        rec["chosen"], rec["why"] = want, {"basis": "listed"}
    elif not rec.get("chosen"):
        note(f"project: choosing headwater HUC12s for the {cell_m:g} m valley pilot")
        rec["chosen"], rec["why"] = _fine_pilot(hucs, st, pa, count)
    order = list(rec["chosen"])
    if mode == "all":
        lidar = sorted(h for h, v in st.data["huc12"].items()
                       if v.get("status") in ("done", "failed"))
        order += [h for h in lidar if h not in order]
    st.data["fine_valleys"] = rec
    st.save()
    breaks = (st.data.get("confinement") or {}).get("breaks") \
        if (st.data.get("confinement") or {}).get("applied") else None
    breaks = list(breaks or pa.get("confinement_breaks") or PUBLISHED_BREAKS)
    rec["breaks"] = breaks
    codes = hucs["huc12"].astype(str)
    todo = [c for c in order
            if (rec["huc12"].get(c) or {}).get("status") not in ("done", "no lidar")]
    n_workers = int(workers) if workers else fine_workers(cell_m)
    n_workers = max(1, min(n_workers, len(todo) or 1))
    rec["workers"] = n_workers
    free = _free_gb(root)
    need = len(todo) * (0.45 if keep_1m else 0.15)
    if free < need + min_free_gb:
        st.warn(f"The {cell_m:g} m valley pass needs about {need:.0f} GB more "
                f"for {len(todo)} HUC12s ({'with' if keep_1m else 'without'} "
                f"the 1 m DEMs); {free:.0f} GB is free. It stops by itself, "
                f"resumable, if the drive gets under {min_free_gb:g} GB.")
    note(f"project: valleys at {cell_m:g} m on the lidar: {len(todo)} HUC12s "
         f"to do, {n_workers} at a time")
    rec.pop("stopped", None)

    def args(code):
        return (code, hucs[codes == code], str(root), dem_a, pa, cell_m,
                threshold_km2, breaks, keep_1m, keep_tiles)

    def record(code, r):
        prev = rec["huc12"].get(code) or {}
        if r.get("status") == "failed" and prev.get("status") == "failed":
            r["retried"] = True
        rec["huc12"][code] = r
        done_n = sum(1 for v in rec["huc12"].values() if v.get("status") == "done")
        note(f"project: {cell_m:g} m valleys, HUC12 {code}: {r.get('status')} "
             f"({r.get('seconds', 0):.0f} s); {done_n} of {len(order)} done")
        st.save()

    for round_ in ("first", "retry"):
        if round_ == "retry":
            todo = [c for c in order if (rec["huc12"].get(c) or {}).get("status")
                    == "failed" and not rec["huc12"][c].get("retried")]
            if not todo or rec.get("stopped"):
                break
            note(f"project: retrying {len(todo)} {cell_m:g} m HUC12s that failed")
        if n_workers <= 1:
            for code in todo:
                if _free_gb(root) < min_free_gb:
                    rec["stopped"] = f"under {min_free_gb:g} GB free"
                    break
                record(code, _fine_valley_huc12(*args(code)))
        else:
            import multiprocessing as mp
            from concurrent.futures import (FIRST_COMPLETED, ProcessPoolExecutor,
                                            wait)

            queue = list(todo)
            # spawn, as Windows always does: a forked worker inherits the
            # parent's threads (GDAL, numba) and can die at its first step.
            ctx = mp.get_context("spawn")
            with ProcessPoolExecutor(max_workers=n_workers, mp_context=ctx) as ex:
                running = {}

                def fill():
                    while queue and len(running) < n_workers:
                        if _free_gb(root) < min_free_gb:
                            rec["stopped"] = f"under {min_free_gb:g} GB free"
                            queue.clear()
                            return
                        code = queue.pop(0)
                        running[ex.submit(_fine_valley_huc12, *args(code))] = code

                fill()
                while running:
                    finished, _ = wait(list(running), return_when=FIRST_COMPLETED)
                    for fut in finished:
                        code = running.pop(fut)
                        try:
                            r = fut.result()
                        except Exception as exc:             # noqa: BLE001
                            r = {"status": "failed",
                                 "error": f"{type(exc).__name__}: {exc}"[:500]}
                        record(code, r)
                    fill()
    if rec.get("stopped"):
        st.warn(f"The {cell_m:g} m valley pass stopped: the drive is "
                f"{rec['stopped']}. Free some space and run the project again "
                f"to carry on.")
    else:
        st.unwarn(f"The {cell_m:g} m valley pass stopped")
    tag = f"{cell_m:g}m"
    merged: dict[str, str] = {}
    for kind in ("valley_types", "valley_extent"):
        parts = []
        for code in order:
            r = rec["huc12"].get(code) or {}
            p = r.get(kind)
            if r.get("status") == "done" and p and Path(p).exists():
                g = gpd.read_file(p)
                g["huc12"] = code
                g["cell_m"] = r.get("cell_m", cell_m)
                parts.append(g)
        if parts:
            g = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True),
                                 geometry="geometry", crs=parts[0].crs)
            path = root / "merged" / f"{kind}_{tag}.gpkg"
            path.parent.mkdir(parents=True, exist_ok=True)
            g.to_file(str(path), driver="GPKG", layer=f"{kind}_{tag}")
            merged[f"{kind}_{tag}"] = str(path)
    rows = []
    why = (rec.get("why") or {}).get("why") or {}
    for code in order:
        r = rec["huc12"].get(code)
        if not r:
            continue
        rows.append({"huc12": code, "status": r.get("status"),
                     "pilot": code in rec["chosen"], "setting": why.get(code),
                     "cell_m": r.get("cell_m"),
                     "inflow_corrected": (r.get("inflow") or {}).get("corrected"),
                     "seconds": r.get("seconds"), **(r.get("compare") or {})})
    if rows:
        t = pd.DataFrame(rows)
        if "type_changes" in t.columns:
            t["type_changes"] = t["type_changes"].apply(
                lambda v: "; ".join(v) if isinstance(v, list) else v)
        csv = root / "A_fine" / f"comparison_{tag}.csv"
        csv.parent.mkdir(parents=True, exist_ok=True)
        t.to_csv(csv, index=False)
        rec["comparison"] = str(csv)
        rec["summary"] = _fine_summary(t)
    st.data["fine_valleys"] = rec
    st.save()
    return merged


def _fine_summary(t) -> dict:
    """Length-weighted agreement and shares over every HUC12 compared."""
    import pandas as pd

    d = t[t["status"] == "done"].copy()
    out: dict[str, Any] = {"done": int(len(d)),
                           "failed": int((t["status"] == "failed").sum())}
    if d.empty or "km_fine" not in d.columns:
        return out
    w = pd.to_numeric(d["km_fine"], errors="coerce").fillna(0.0)
    w10 = pd.to_numeric(d.get("km_10m"), errors="coerce").fillna(0.0)
    out["km_10m"] = round(float(w10.sum()), 1)
    out["km_fine"] = round(float(w.sum()), 1)
    for col, weight in (("landscape_type_agreement", w),
                        ("confinement_agreement", w), ("confined_fine", w),
                        ("unresolved_fine", w), ("confined_10m", w10),
                        ("unresolved_10m", w10)):
        if col in d.columns:
            v = pd.to_numeric(d[col], errors="coerce")
            m = v.notna() & (weight > 0)
            if m.any():
                out[col] = round(float((v[m] * weight[m]).sum() / weight[m].sum()), 3)
    for col in ("valley_km2_10m", "valley_km2_fine"):
        if col in d.columns:
            out[col] = round(float(pd.to_numeric(d[col], errors="coerce").sum()), 1)
    if "inflow_corrected" in d.columns:
        out["inflow_corrected"] = int(pd.to_numeric(d["inflow_corrected"],
                                                    errors="coerce").fillna(0).sum())
    return out


def _fine_lines(rec: dict | None) -> list[str]:
    """RESULTS.md: the finer valley pass against the 10 m one."""
    if not rec or (not rec.get("huc12") and not rec.get("stopped")):
        return []
    cell = rec.get("cell_m", FINE_CELL_M)
    basis = str((rec.get("why") or {}).get("basis") or "")
    mode = rec.get("mode", "pilot")
    many = mode == "all" or len(rec["huc12"]) > 12
    caveat = (" None qualified here, so a main stem entering one of these "
              "HUC12s carries only the drainage inside it, which narrows its "
              "channel and widens its confinement ratio."
              if "no headwater" in basis and not many else "")
    lines = [f"## Valleys on the lidar at {cell:g} m ({mode})", "",
             f"Level A's valley pass run again on the 3DEP lidar resampled to "
             f"{cell:g} m, HUC12 by HUC12 ({rec.get('workers', 1)} at a time), "
             f"with the same stream threshold and the confinement breaks "
             f"{rec.get('breaks')}. A tile cut to one HUC12 only knows the "
             f"drainage inside it: where a reach runs along a 10 m reach that "
             f"drains much more, it takes the 10 m drainage (channel width and "
             f"confinement follow) and its valley is typed again. The pilot "
             f"HUC12s, chosen from {basis}, go first.{caveat} Agreement is by "
             f"stream length, each {cell:g} m reach against the nearest 10 m "
             f"reach. Layers `valley_types_{cell:g}m` and "
             f"`valley_extent_{cell:g}m`; every HUC12 is in "
             f"`A_fine/comparison_{cell:g}m.csv`.", ""]

    def pct(v):
        return "" if v is None else f"{100 * v:.0f}%"

    s = rec.get("summary") or {}
    if many and s:
        lines += [f"Done {s.get('done', 0)} HUC12s, failed {s.get('failed', 0)}; "
                  f"stream km 10 m / {cell:g} m: {s.get('km_10m')} / "
                  f"{s.get('km_fine')}; valley type agreement "
                  f"{pct(s.get('landscape_type_agreement'))}, confinement "
                  f"agreement {pct(s.get('confinement_agreement'))}; confined "
                  f"{pct(s.get('confined_10m'))} at 10 m, "
                  f"{pct(s.get('confined_fine'))} at {cell:g} m; unresolved "
                  f"{pct(s.get('unresolved_10m'))} at 10 m, "
                  f"{pct(s.get('unresolved_fine'))} at {cell:g} m; valley area "
                  f"{s.get('valley_km2_10m')} km2 / {s.get('valley_km2_fine')} km2; "
                  f"{s.get('inflow_corrected', 0)} reaches took the 10 m "
                  f"drainage.", ""]
    lines += ["| HUC12 | Chosen for | Status | Stream km 10 m / fine | Valley type "
              "agreement | Confinement agreement | Confined 10 m / fine | "
              "Unresolved 10 m / fine | Valley width, fine / 10 m | Valley km2 10 m / fine |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    why = (rec.get("why") or {}).get("why") or {}
    show = [c for c in rec["huc12"] if not many or c in (rec.get("chosen") or [])]
    for code in show:
        r = rec["huc12"][code]
        c = r.get("compare") or {}
        lines.append(
            f"| {code} | {why.get(code, '')} | {r.get('status')} | "
            f"{c.get('km_10m', '')} / {c.get('km_fine', '')} | "
            f"{pct(c.get('landscape_type_agreement'))} | "
            f"{pct(c.get('confinement_agreement'))} | "
            f"{pct(c.get('confined_10m'))} / {pct(c.get('confined_fine'))} | "
            f"{pct(c.get('unresolved_10m'))} / {pct(c.get('unresolved_fine'))} | "
            f"{c.get('valley_width_ratio_median', '')} | "
            f"{c.get('valley_km2_10m', '')} / {c.get('valley_km2_fine', '')} |")
    changes = [f"{code}: {', '.join(rec['huc12'][code]['compare']['type_changes'])}"
               for code in show
               if (rec["huc12"][code].get("compare") or {}).get("type_changes")]
    if changes:
        lines += ["", "Largest valley type changes, 10 m -> fine:", ""]
        lines += [f"- {c}" for c in changes]
    failed = [f"{code}: {r.get('error')}" for code, r in rec["huc12"].items()
              if r.get("status") == "failed"]
    if failed:
        lines += ["", f"Failed ({len(failed)}):", ""] + [f"- {f}" for f in failed[:20]]
        if len(failed) > 20:
            lines.append(f"- and {len(failed) - 20} more (project.json)")
    if rec.get("stopped"):
        lines += ["", f"Stopped early: the drive is {rec['stopped']}; run the "
                      f"project again to carry on."]
    return lines + [""]


def _merge(root: Path, st: _Status, hucs, bnd, pa: dict, cell_a: float,
           lidar_min: float = 0.8) -> dict[str, str]:
    import geopandas as gpd
    import pandas as pd

    mdir = root / "merged"
    mdir.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    vt = gpd.read_file(pa["valley_types"])
    bnd_u = _union(bnd.to_crs(vt.crs))
    vt = vt[vt.geometry.centroid.within(bnd_u)].copy()
    h = hucs.to_crs(vt.crs)[["huc12", "geometry"]]
    cent = vt.copy()
    cent["geometry"] = cent.geometry.centroid
    j = gpd.sjoin(cent, h, predicate="within", how="left")
    vt["huc12"] = j["huc12"].groupby(level=0).first().reindex(vt.index)
    vt["dem_cell_m"] = cell_a
    if "valley_width_m" in vt.columns:
        vt["valley_resolved"] = vt["valley_width_m"].astype(float) >= 3 * cell_a
    path = mdir / "valley_types.gpkg"
    vt.to_file(str(path), driver="GPKG", layer="valley_types")
    out["valley_types"] = str(path)

    for key, layer in (("stream_types", "stream_types"),
                       ("sections", "stream_type_sections"),
                       ("stability", "stream_stability")):
        parts = []
        for code, rec in st.data["huc12"].items():
            p = rec.get(key)
            if rec.get("status") == "done" and p and Path(p).exists():
                try:
                    g = gpd.read_file(p, layer=layer)
                except Exception:                            # noqa: BLE001
                    g = gpd.read_file(p)
                g["huc12"] = code
                g["fine_dem_cell_m"] = LEVEL_B_CELL_M
                g["lidar_frac"] = rec.get("lidar")
                parts.append(g)
        if parts:
            g = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True),
                                 crs=parts[0].crs)
            path = mdir / f"{layer}.gpkg"
            g.to_file(str(path), driver="GPKG", layer=layer)
            out[key] = str(path)
            # The HUC12 runs' QGIS style (also what colours the ArcGIS layer).
            for code, rec in st.data["huc12"].items():
                qml = Path(str(rec.get(key) or "")).with_suffix(".qml")
                if qml.is_file():
                    shutil.copyfile(qml, path.with_suffix(".qml"))
                    break

    status = hucs.copy()
    def _state(code, role, frac):
        if role != "boundary":
            return "upstream only"
        if code in st.data["huc12"]:
            return st.data["huc12"][code].get("status", "?")
        if frac == frac and frac is not None and frac < lidar_min:
            return "no lidar"
        return "queued"

    status["level_b"] = [_state(c, r, f) for c, r, f in zip(
        status["huc12"], status["role"], status.get(
            "lidar_frac", [float("nan")] * len(status)))]
    status["reaches_b"] = [st.data["huc12"].get(c, {}).get("reaches")
                           for c in status["huc12"]]
    group_of = (st.data.get("pilot") or {}).get("group_of") or {}
    if group_of:
        # The pilot HUC12 whose level B results stand for this one.
        status["pilot_group"] = [group_of.get(str(c)) for c in status["huc12"]]
    path = mdir / "huc12_status.gpkg"
    status.to_file(str(path), driver="GPKG", layer="huc12_status")
    out["huc12_status"] = str(path)
    return out


def _data_lines(st: _Status, want: list[str]) -> list[str]:
    """RESULTS.md: the watershed data steps, what each fetched, and what it
    is for."""
    from .wsdata import SOURCES, TITLES

    if not want:
        return []
    out = ["## Watershed data by HUC12", "",
           "Fetched for the HUC12s inside the boundary; each is a column set "
           "in `huc12_summary` and a CSV in `data/`.", "",
           "| Data | Source | Status |", "|---|---|---|"]
    for step in want:
        rec = st.step(f"data_{step}")
        if not rec:
            status = "not fetched (see warnings)"
        elif step == "fire":
            status = f"{rec.get('perimeters', 0):,} fire perimeters"
        elif step == "ecosites":
            status = (f"{rec.get('with_site', 0):,} of {rec.get('map_units', 0):,} "
                      f"map units have an ecological site")
        elif step == "rap":
            status = f"{rec.get('first_year')} to {rec.get('last_year')}"
            if rec.get("missing_years"):
                status += (", missing " +
                           ", ".join(str(y) for y in rec["missing_years"]))
        elif step == "atlas14":
            status = "done" if not rec.get("failed") else \
                f"{rec['failed']} HUC12s unanswered (rerun to retry)"
        else:
            status = "done"
        out.append(f"| {TITLES[step]} | {SOURCES[step]} | {status} |")
    return out + [""]


def _report(root: Path, st: _Status, merged: dict, cell_a: float,
            curve, why_curve: str, want_data: list[str] | None = None) -> None:
    b = st.data["huc12"]
    counts: dict[str, int] = {}
    for v in b.values():
        counts[v.get("status", "?")] = counts.get(v.get("status", "?"), 0) + 1
    lines = [
        "# geolens project results", "",
        f"Updated {_now()}. Re-running the project carries on from here.", "",
        "## What was run, at what resolution", "",
        "| Result | Cell size | Valid for |", "|---|---|---|",
        f"| Valley bottom, confinement, valley types (level A; Rosgen 2022 "
        f"landscape types, the 1996 types beside them) | {cell_a:g} m "
        f"| the whole area; reaches with `valley_resolved` false are in "
        f"valleys under {3 * cell_a:g} m wide, below what {cell_a:g} m "
        f"cells resolve |",
        f"| Stream types, stable / unstable form (level B) | "
        f"{LEVEL_B_CELL_M:g} m lidar | only HUC12s marked done in "
        f"`huc12_status` |", "",
        f"Regional bankfull curve: `{curve}` ({why_curve}).", "",
        *_copied_lines(st.data.get("copied_from")),
        "## How far to trust the 10 m DEM", "",
        "Measured where 3DEP has no lidar: the 10 m DEM against 6,156 "
        "ICESat-2 ground points in northern Arizona (October 2026).", "",
        "| Slope | NMAD, m | LE90, m |", "|---|---|---|",
        *[f"| {c} | {n:.2f} | {e:.2f} |" for c, n, e in DEM_ACCURACY_10M],
        "",
        "Good enough for watershed-scale work, not for channel design: "
        "bankfull geometry needs lidar, drone photogrammetry with RTK "
        "checkpoints, or a survey. Where there is no lidar the 10 m DEM comes "
        "from older photogrammetry or contours, so it does not show recent "
        "incision or headcuts: field-check active reaches before using a 10 m "
        "long profile. The steep-slope figures are pessimistic (ICESat-2 "
        "smooths steep ground over 100 m).", "",
        "## Level B by HUC12", "",
        ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "none run",
        ""]
    lines += _pilot_lines(st.data.get("pilot") or {})
    lines += _confinement_lines(st.data.get("confinement"))
    lines += _fine_lines(st.data.get("fine_valleys"))
    lines += _data_lines(st, want_data or [])
    if merged.get("huc12_summary"):
        lines += ["## The HUC12 summary", "",
                  "`huc12_summary` (and `merged/huc12_summary.csv`) has one "
                  "row per HUC12 inside the boundary: stream km and the main "
                  "valley and fluvial landscape types, soil material, "
                  "hydrologic group and erodibility, slope, stream form where "
                  "level B ran, and the watershed data above. It is the "
                  "subwatershed table a plan's hydrologic section is written "
                  "from; nothing in it is weighted into a score.", ""]
    lines += _synthesis_lines(st.data.get("synthesis"))
    lines += ["## Layers", ""]
    lines += [f"- `{k}`: {v}" for k, v in merged.items()]
    if st.data["warnings"]:
        lines += ["", "## Warnings", ""]
        lines += [f"- {w}" for w in st.data["warnings"]]
    (root / "RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _synthesis_lines(syn: dict | None) -> list[str]:
    """RESULTS.md: stream power, the major streams and the sensitivity
    table -- what was computed, from what, and the first few rows."""
    if not syn:
        return []
    from .synthesis import LABELS

    tr = syn.get("tracing") or {}
    out = ["## Stream power, major streams and the sensitivity table", "",
           f"Every reach in `valley_types` now has a bankfull discharge "
           f"from the regional curve ({syn.get('power_basis', '')}), the "
           f"total stream power Omega = rho g Q S (`stream_power_w_m`, W/m) and the "
           f"unit stream power omega = Omega / W over the curve's bankfull width "
           f"(`unit_stream_power_w_m2`, W/m²). They rank reaches by how "
           f"much work the channel can do; the slope is the 10 m reach "
           f"gradient and the discharge a curve, so compare, do not design.",
           "",
           f"Each reach also has a `stream_id`: from each of the "
           f"{tr.get('outlets', 0):,} outlets the main stem was followed "
           f"upstream, taking the larger drainage at every junction, and "
           f"each tributary numbered in turn ({tr.get('streams', 0):,} "
           f"streams; `dist_to_mouth_km`, `flows_into`, "
           f"`stream_km2_at_mouth`). "
           f"{tr.get('bridged', 0):,} reaches were joined across a gap in "
           f"the traced network and {tr.get('inherited_drainage', 0):,} "
           f"tributary mouths had inherited the river's drainage at the "
           f"junction cell (`drainage_inherited`; `drainage_km2_used` is "
           f"the corrected figure)."]
    n = syn.get("major_streams_n", 0)
    out += ["",
            f"`major_streams` has the {n:,} streams draining at least "
            f"{syn.get('min_drainage_km2', 25):g} km² at the mouth, one line "
            f"each, with the drainage at the mouth, length, gradient, stream "
            f"power, dominant landscape type and the shares on fans and "
            f"unconfined."]
    if syn.get("named") is not None:
        ratio = syn.get("drainage_ratio_median")
        out[-1] += (f" {syn['named']:,} of them carry a GNIS name from the "
                    f"NHDPlus HR flowlines within 150 m (`name`; the name "
                    f"covering most of the stream, others in `other_names`), "
                    f"and `nhd_totdasqkm` is the NHD's drainage at the mouth: "
                    f"`drainage_ratio` (traced over NHD) is "
                    f"{ratio if ratio is not None else '?'} at the median; "
                    f"{syn.get('entering_from_outside', 0):,} streams are "
                    f"under 0.5, rivers entering from outside the project's "
                    f"HUC8s, whose traced drainage is local only.")
    elif syn.get("naming_failed"):
        out[-1] += f" Naming from the NHD failed ({syn['naming_failed']})."
    else:
        out[-1] += " No NHD flowlines were fetched, so no names."
    if syn.get("largest"):
        out += ["", "| Stream | Name | Drainage at mouth, km² | NHD drainage, "
                "km² | Length, km | Main landscape type | HUC12 at mouth |",
                "|---|---|---|---|---|---|---|"]
        for r in syn["largest"]:
            nhd = r.get("nhd_totdasqkm")
            out.append(f"| {r['stream_id']} | {r.get('name') or ''} | "
                       f"{r['drainage_km2']:,.0f} | "
                       f"{'' if nhd is None or nhd != nhd else f'{nhd:,.0f}'} | "
                       f"{r['length_km']:,.1f} | {r.get('landscape_main') or ''} "
                       f"| {r.get('huc12_mouth') or ''} |")
    sens = syn.get("sensitivity")
    if sens and sens.get("skipped"):
        out += ["", f"The HUC12 sensitivity table was not built: "
                f"{sens['skipped']}"]
    elif sens:
        out += ["",
                "`huc12_sensitivity` ranks the HUC12s inside the boundary. "
                "Each component below is a percentile rank among them (1 = "
                "the most of it), the score is the weighted mean of the ranks "
                "a HUC12 has, and the class cuts the scored HUC12s into "
                "thirds. The weights are a first proposal, in "
                "`merged/sensitivity_weights.json`: change them and run the "
                "project again to re-rank. `sensitivity_drivers` says which "
                "components put each HUC12 where it is.", "",
                "| Component | Column | Weight |", "|---|---|---|"]
        for col, w in sens.get("weights", {}).items():
            out.append(f"| {LABELS.get(col, col)} | `{col}` | {w:.2f} |")
        if sens.get("dropped"):
            out += ["", "Not used this run: " + "; ".join(
                f"{LABELS.get(k, k)} ({v})" for k, v in sens["dropped"].items())
                + "."]
        c = sens.get("classes", {})
        out += ["", f"Classes: high {c.get('high', 0)}, moderate "
                f"{c.get('moderate', 0)}, low {c.get('low', 0)}, "
                f"insufficient data {c.get('insufficient data', 0)}."]
        if syn.get("most_sensitive"):
            out += ["", "| HUC12 | Name | Score | Drivers |", "|---|---|---|---|"]
            for r in syn["most_sensitive"]:
                out.append(f"| {r['huc12']} | {r.get('name') or ''} | "
                           f"{r['score']:.2f} | {r.get('drivers') or ''} |")
    return out + [""]


def _pilot_lines(plan: dict) -> list[str]:
    """RESULTS.md: how the pilot HUC12s were chosen, and what each stands for."""
    if plan.get("by") == "unconfined":
        return ["## Pilot HUC12s", "",
                "The HUC12s with lidar and the most unconfined or partly "
                "confined valley, where the channel is free to change form.", ""]
    groups = plan.get("groups") or []
    if plan.get("by") != "representative" or not groups:
        return []
    basis = " and ".join(plan.get("basis") or ["area"])
    out = ["## Pilot HUC12s", "",
           f"The HUC12s inside the boundary were sorted into {len(groups)} "
           f"group{'s' if len(groups) != 1 else ''} of similar watersheds by "
           f"{basis}, each weighted by its "
           f"area inside the boundary. Each pilot is its group's most typical "
           f"HUC12 with lidar. Until the rest are run, read a pilot's level B "
           f"results as the first estimate for its group (`pilot_group` in "
           f"`huc12_status`).", "",
           "| Pilot HUC12 | Name | Stands for | Share of the area |",
           "|---|---|---|---|"]
    for g in groups:
        share = "" if g.get("share") is None else f"{100 * g['share']:.0f}% "
        n = len(g["members"])
        out.append(f"| {g['huc12']} | {g['name']} | {n} "
                   f"HUC12{'s' if n != 1 else ''} | {share}({g['km2']:,.0f} km²) |")
    return out + [""]
