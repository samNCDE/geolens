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

**Merged** (``merged``). Valley types, stream types, sections, stable /
unstable form and a HUC12 status layer, for the boundary, ready for the map.

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


def _get(url: str, params: dict, timeout: int) -> dict:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    last: Exception | None = None
    for attempt in range(3):
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
                max_offset: float | None = None, timeout: int = 60,
                page: int = 1000):
    """Every feature of an ArcGIS REST layer matching ``where`` (and touching
    ``bbox``, lon/lat), as a GeoDataFrame in EPSG:4326. Paged; stops when a
    page brings nothing new, so a server that ignores paging cannot loop."""
    import geopandas as gpd

    url = url.rstrip("/")
    if not url.endswith("/query"):
        url += "/query"
    params: dict[str, Any] = {"where": where, "outFields": fields,
                              "returnGeometry": "true", "outSR": 4326,
                              "f": "geojson", "resultRecordCount": page}
    if bbox is not None:
        params.update(geometry=",".join(f"{v:.6f}" for v in bbox),
                      geometryType="esriGeometryEnvelope", inSR=4326,
                      spatialRel="esriSpatialRelIntersects")
    if max_offset:
        params["maxAllowableOffset"] = max_offset
    feats: list = []
    seen: set = set()
    for offset in range(0, 200 * page, page):
        params["resultOffset"] = offset
        got = (_get(url, params, timeout).get("features") or [])
        new = []
        for f in got:
            key = f.get("id") or json.dumps(f.get("properties"), sort_keys=True)
            if key not in seen:
                seen.add(key)
                new.append(f)
        feats.extend(new)
        if len(got) < page or not new:
            break
    if not feats:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    return gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")


def fetch_boundary(source: str, where: str | None = None,
                   guard: Guard | None = None) -> str:
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
    if not g.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise GeolensError(f"{source} is not a polygon layer.")
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

def project_huc12s(boundary_wgs, timeout: int = 60):
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


# -- the run -----------------------------------------------------------------------

def run_project(boundary: str, where: str | None = None,
                level_b: str = "pilot", pilot_count: int = 3,
                pilot_by: str = "representative",
                huc12s: str | list | None = None,
                stream_threshold_km2: float = 1.0,
                geology: str | None = "auto",
                regional_curve: str | None = "auto",
                lidar_min_fraction: float = 0.8,
                section_plots: bool = True,
                level_b_hours: float | None = None,
                soils: bool = True,
                keep_lidar_tiles: bool = False,
                timeout: int = 60,
                guard: Guard | None = None) -> dict[str, Any]:
    """Level A over the whole area, level B by HUC12, merged layers, a
    report. See the module docstring. ``level_b``: "pilot" (``pilot_count``
    HUC12s with lidar, chosen by ``pilot_by``), "all", "listed"
    (``huc12s``) or "none". ``pilot_by``: "representative" (HUC12s that
    together stand for the whole area, for landscape projects) or
    "unconfined" (the most unconfined valley, for stream projects). Resumes a
    project folder that already has a ``project.json``."""
    import geopandas as gpd

    pilot_by = (pilot_by or "representative").lower()
    if pilot_by not in PILOT_BY:
        raise GeolensError(f"pilot_by must be one of {', '.join(PILOT_BY)}, "
                           f"not {pilot_by!r}.")
    guard = guard or Guard.from_env()
    root = Path(guard.out_dir)
    root.mkdir(parents=True, exist_ok=True)
    st = _Status(root)
    t0 = time.monotonic()
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

        note("project: level A DEM (3DEP 1/3 arc-second, 10 m)")
        utm = _utm_for(bnd_wgs.centroid.x, bnd_wgs.centroid.y)
        area = gpd.GeoDataFrame(geometry=[_union(hucs)], crs=hucs.crs
                                ).to_crs(utm)              # buffered in metres
        A.mkdir(parents=True, exist_ok=True)
        area_path = A / "area.gpkg"
        area.to_file(str(area_path), driver="GPKG", layer="area")
        with _out(A / "dem"):
            d = api.fetch_dem(str(area_path), source="1/3",
                              resolution_m=LEVEL_A_CELL_M, buffer_m=300.0)
        for w in d.get("warnings") or []:
            st.warn(f"level A DEM: {w}")
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

    # 4. level A pass --------------------------------------------------------------------
    if not st.step("pass_a"):
        note(f"project: level A pass at {cell_a:g} m")
        with _out(A / "run"):
            res = api.landscape_pass(
                dem_run, stream_threshold_km2=stream_threshold_km2,
                segment_length_m=500.0, valley_types=True, stream_types=False,
                geology=geology, fans=True)
        vt = (res.get("valley_types") or {})
        for w in res.get("warnings") or []:
            st.warn(f"level A: {w}")
        st.done("pass_a", run=str(A / "run"),
                segments=(res.get("classified") or {}).get("segments"),
                valley_types=vt.get("segments"),
                geology=(res.get("geology") or {}).get("path"),
                seconds=res.get("seconds"))
    pa = st.step("pass_a")
    reaches = gpd.read_file(pa["segments"])

    # 4b. SSURGO soils over the boundary: composition, erodibility, runoff ---------
    if soils and not st.step("soils"):
        note("project: SSURGO soils over the boundary")
        try:
            from .soils import fetch_soils

            with _out(root / "soils") as gs:
                sres = fetch_soils(str(root / "boundary.gpkg"), guard=gs,
                                   max_area_km2=100_000.0)
            st.done("soils", path=sres["soils"], polygons=sres["polygons"],
                    km2_by_material=sres.get("km2_by_material"))
        except Exception as exc:                             # noqa: BLE001
            st.warn(f"SSURGO soils could not be fetched ({type(exc).__name__}: "
                    f"{str(exc)[:200]}); run the project again to retry.")

    # 5. level B, HUC12 by HUC12 --------------------------------------------------------------
    lon, lat = bnd_wgs.centroid.x, bnd_wgs.centroid.y
    curve, why_curve = choose_curve(lon, lat, regional_curve)
    inside = hucs[hucs["role"] == "boundary"]
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
            order, st.data["pilot"] = _representative_pilot(
                inside, int(pilot_count), can_run=with_lidar["huc12"],
                valley_types=_read_or_none(pa.get("valley_types")),
                reaches=reaches,
                soils=_read_or_none((st.step("soils") or {}).get("path"),
                                    columns=["hydgrpdcd", "kwfact"]))
        st.save()
        done_b = [h for h, v in st.data["huc12"].items()
                  if v.get("status") == "done"]
        want = done_b + [h for h in order if h not in done_b]
        want = want[:max(int(pilot_count), len(done_b))]
    else:
        want = []
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
            lidar_min_fraction, keep_lidar_tiles)
        st.save()

    # 6. merged layers and the report ------------------------------------------------------------
    merged = _merge(root, st, hucs, bnd, pa, cell_a, lidar_min_fraction)
    if st.step("soils"):
        merged["soils"] = st.step("soils")["path"]
    _report(root, st, merged, cell_a, curve, why_curve)
    return {"project": str(root), "status": str(st.path), **merged,
            "level_b": {h: v.get("status") for h, v in st.data["huc12"].items()},
            "warnings": st.data["warnings"],
            "seconds": round(time.monotonic() - t0, 1)}


def _level_b(code, row, root: Path, dem_a: str, pa: dict, curve,
             section_plots: bool, lidar_min: float, keep_tiles: bool) -> dict:
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
        return out
    except Exception as exc:                                 # noqa: BLE001
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:500],
                "seconds": round(time.monotonic() - t0, 1)}


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


def _report(root: Path, st: _Status, merged: dict, cell_a: float,
            curve, why_curve: str) -> None:
    b = st.data["huc12"]
    counts: dict[str, int] = {}
    for v in b.values():
        counts[v.get("status", "?")] = counts.get(v.get("status", "?"), 0) + 1
    lines = [
        "# geolens project results", "",
        f"Updated {_now()}. Re-running the project carries on from here.", "",
        "## What was run, at what resolution", "",
        "| Result | Cell size | Valid for |", "|---|---|---|",
        f"| Valley bottom, confinement, valley types (level A) | {cell_a:g} m "
        f"| the whole area; reaches with `valley_resolved` false are in "
        f"valleys under {3 * cell_a:g} m wide, below what {cell_a:g} m "
        f"cells resolve |",
        f"| Stream types, stable / unstable form (level B) | "
        f"{LEVEL_B_CELL_M:g} m lidar | only HUC12s marked done in "
        f"`huc12_status` |", "",
        f"Regional bankfull curve: `{curve}` ({why_curve}).", "",
        "## Level B by HUC12", "",
        ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "none run",
        ""]
    lines += _pilot_lines(st.data.get("pilot") or {})
    lines += ["## Layers", ""]
    lines += [f"- `{k}`: {v}" for k, v in merged.items()]
    if st.data["warnings"]:
        lines += ["", "## Warnings", ""]
        lines += [f"- {w}" for w in st.data["warnings"]]
    (root / "RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


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
