"""The watershed data package: what a landscape assessment needs besides the
DEM, fetched per HUC12 for a project.

Ported from Sam's no-lidar watershed work (the `data_prep.py` package and
its notes, October 2026), where each source was learned the hard way: the
URLs that work, the paging a slow server needs, the memory a remote raster
can eat. Each source is its own step in a project run: it is fetched once,
kept in the project's ``data`` folder, logged, and a failure is a warning,
not the end of the night's run.

=================  ============================================  ==========
step               source                                        per HUC12
=================  ============================================  ==========
``fire``           NIFC InterAgency Fire Perimeter History       burned share,
                                                                 last fire
``ecosites``       SSURGO ecological sites (``coecoclass``)      dominant site
``prism``          PRISM 1991-2020 normals, 800 m                rain, monsoon
                                                                 share, temp
``atlas14``        NOAA Atlas 14 precipitation frequency (PFDS)  storm depths
``rap``            Rangeland Analysis Platform cover v3, 30 m    cover by year,
                                                                 trends
=================  ============================================  ==========

``huc12_summary`` then puts these beside what the project measured (soils,
valley and landscape types, slope, stream form) in one table per HUC12: the
subwatershed, soil and channel-type tables a vegetation management plan's
hydrologic analysis is written from.
"""
from __future__ import annotations

import ast
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .safety import GeolensError, note, progress

STEPS = ("fire", "ecosites", "prism", "atlas14", "rap")
TITLES = {"fire": "fire history", "ecosites": "ecological sites",
          "prism": "climate normals", "atlas14": "design storm depths",
          "rap": "vegetation cover"}
SOURCES = {"fire": "NIFC InterAgency Fire Perimeter History",
           "ecosites": "SSURGO components (coecoclass)",
           "prism": "PRISM 1991-2020 normals, 800 m",
           "atlas14": "NOAA Atlas 14 (PFDS), at each HUC12's centre",
           "rap": "Rangeland Analysis Platform cover v3, 30 m"}
_UA = {"User-Agent": "geolens (+geomorphic analysis)"}

FIRE_URLS = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
    "InterAgencyFirePerimeterHistory_All_Years_View/FeatureServer/0",
)
PRISM_BASE = "https://data.prism.oregonstate.edu/normals/us/800m"
ATLAS14_URL = "https://hdsc.nws.noaa.gov/cgi-bin/new/cgi_readH5.py"
ATLAS14_DURATIONS = ("5-min", "10-min", "15-min", "30-min", "60-min", "2-hr",
                     "3-hr", "6-hr", "12-hr", "24-hr", "2-day", "3-day",
                     "4-day", "7-day", "10-day", "20-day", "30-day", "45-day",
                     "60-day")
ATLAS14_ARI = (1, 2, 5, 10, 25, 50, 100, 200, 500, 1000)
RAP_URLS = (
    "https://rangeland.ntsg.umt.edu/data/rap/rap-vegetation-cover/v3/"
    "vegetation-cover-v3-{year}.tif",
    "http://rangeland.ntsg.umt.edu/data/rap/rap-vegetation-cover/v3/"
    "vegetation-cover-v3-{year}.tif",
)
RAP_BANDS = ("AFG", "BGR", "LTR", "PFG", "SHR", "TRE")
RAP_NAMES = {"AFG": "annual forb and grass", "BGR": "bare ground",
             "LTR": "litter", "PFG": "perennial forb and grass",
             "SHR": "shrub", "TRE": "tree"}
# GDAL's block cache grows to 5% of RAM by default; reading remote rasters
# that is how Sam's NAIP rerun reached 100 GB. An int, in bytes: a string
# broke every raster read.
GDAL_REMOTE = {"GDAL_CACHEMAX": 512 * 1024 * 1024,
               "GDAL_HTTP_MAX_RETRY": 4, "GDAL_HTTP_RETRY_DELAY": 2,
               "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF",
               "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR"}


def _get_bytes(url: str, timeout: int = 120, tries: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:                               # noqa: BLE001
            last = e
            time.sleep(2.0 * (attempt + 1))
    raise GeolensError(f"Could not download {url}: {type(last).__name__}: "
                       f"{last}") from last


def _utm(hucs):
    from .sar import _utm_for

    c = hucs.to_crs(4326).geometry.union_all().centroid \
        if hasattr(hucs.geometry, "union_all") else \
        hucs.to_crs(4326).geometry.unary_union.centroid
    return _utm_for(c.x, c.y)


def _shares_by_huc(layer, hucs, value_col: str, utm, prefix: str):
    """Area share of each value of ``value_col`` per HUC12 (overlay)."""
    import geopandas as gpd

    a = gpd.overlay(hucs[["huc12", "geometry"]].to_crs(utm),
                    layer[[value_col, "geometry"]].to_crs(utm),
                    how="intersection", keep_geom_type=True)
    if a.empty:
        return None
    a["km2"] = a.geometry.area / 1e6
    t = a.pivot_table(index="huc12", columns=value_col, values="km2",
                      aggfunc="sum", fill_value=0.0)
    total = hucs.set_index("huc12").to_crs(utm).geometry.area / 1e6
    t = t.div(total.reindex(t.index), axis=0).clip(upper=1.0)
    t.columns = [f"{prefix}{c}" for c in t.columns]
    return t


# -- fire -----------------------------------------------------------------------------

def fire_history(hucs, out_dir: Path, timeout: int = 120,
                 query: Callable | None = None) -> dict[str, Any]:
    """NIFC fire perimeters over the HUC12s, and per HUC12 the share burned
    (all years, since 2000, in the last 10 years), the number of fires and
    the last fire year. Perimeters are kept in ``data/fire_perimeters.gpkg``.
    """
    import geopandas as gpd
    import pandas as pd

    if query is None:
        from .project import query_layer as query
    wgs = hucs.to_crs(4326)
    bounds = tuple(wgs.total_bounds)
    got, last = None, None
    for url in FIRE_URLS:
        try:
            got = query(url, "1=1", "*", bbox=bounds, max_offset=0.0005,
                        timeout=timeout)
            break
        except Exception as exc:                             # noqa: BLE001
            last = exc
    if got is None:
        raise GeolensError(f"No fire perimeter service answered ({last}).")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "fire_perimeters.gpkg"
    utm = _utm(hucs)
    hu = hucs[["huc12", "geometry"]].to_crs(utm)
    rows = []
    if got.empty:
        # No perimeters: every HUC12 is unburned, and there is no layer.
        for code in hu["huc12"]:
            rows.append({"huc12": code, "fire_burned_share": 0.0,
                         "fire_burned_share_2000": 0.0,
                         "fire_burned_share_10yr": 0.0, "fire_count": 0,
                         "fire_last_year": None})
        return {"path": None, "perimeters": 0,
                "table": pd.DataFrame(rows).set_index("huc12")}
    cols = {c.lower(): c for c in got.columns}
    ycol = cols.get("fire_year_int") or cols.get("fire_year")
    ncol = cols.get("incident") or cols.get("incident_name")
    g = got.copy()
    g["fire_year"] = (pd.to_numeric(g[ycol], errors="coerce") if ycol
                      else pd.Series(float("nan"), index=g.index))
    g["incident"] = g[ncol] if ncol else ""
    g = g[g.geometry.notna() & ~g.geometry.is_empty]
    g["geometry"] = g.geometry.make_valid() if hasattr(g.geometry, "make_valid") \
        else g.geometry.buffer(0)
    g = g[g.geom_type.isin(["Polygon", "MultiPolygon", "GeometryCollection"])]
    g[["fire_year", "incident", "geometry"]].to_file(path, driver="GPKG",
                                                      layer="fire_perimeters")
    gu = g.to_crs(utm)
    now = time.gmtime().tm_year
    sindex = gu.sindex
    for code, geom in zip(hu["huc12"], hu.geometry):
        idx = list(sindex.query(geom, predicate="intersects"))
        sub = gu.iloc[idx]
        area = geom.area or 1.0

        def share(mask):
            s = sub[mask]
            if s.empty:
                return 0.0
            return round(float(s.geometry.union_all().intersection(geom).area
                               if hasattr(s.geometry, "union_all") else
                               s.geometry.unary_union.intersection(geom).area)
                         / area, 4)

        yr = sub["fire_year"]
        names = {(y, str(n).strip().lower()) for y, n in
                 zip(yr.fillna(-1), sub["incident"].fillna(""))}
        rows.append({
            "huc12": code,
            "fire_burned_share": share(pd.Series(True, index=sub.index)),
            "fire_burned_share_2000": share(yr >= 2000),
            "fire_burned_share_10yr": share(yr >= now - 10),
            "fire_count": len(names),
            "fire_last_year": int(yr.max()) if yr.notna().any() else None})
    return {"path": str(path), "perimeters": int(len(g)),
            "table": pd.DataFrame(rows).set_index("huc12")}


# -- ecological sites -----------------------------------------------------------------

def ecological_sites(soils_path: str, out_dir: Path, hucs=None,
                     ask: Callable | None = None, timeout: int = 120
                     ) -> dict[str, Any]:
    """Each SSURGO map unit's ecological site: that of its largest major
    component that has one (NRCS sites preferred). Writes
    ``data/soils_ecosites.gpkg`` (the soils layer plus ``ecoclassid``,
    ``ecoclassname``, ``ecoclasstypename``, ``ecosite_pct``) and, per HUC12,
    the dominant site and its share."""
    import geopandas as gpd
    import pandas as pd

    from .soils import _batched, _keys, sda

    ask = ask or (lambda q: sda(q, timeout))
    soils = gpd.read_file(soils_path)
    soils = soils[soils["mukey"].notna()].copy()
    soils["mukey"] = soils["mukey"].astype("int64")
    mukeys = _keys(soils["mukey"])
    best: dict[int, tuple] = {}
    for r in _batched(ask, (
            "SELECT c.mukey, c.cokey, c.comppct_r, c.majcompflag, "
            "e.ecoclassid, e.ecoclassname, e.ecoclasstypename "
            "FROM component AS c INNER JOIN coecoclass AS e "
            "ON e.cokey = c.cokey WHERE c.mukey IN ({keys})"), mukeys):
        mk = int(r["mukey"])
        nrcs = str(r.get("ecoclasstypename") or "").upper().startswith("NRCS")
        major = str(r.get("majcompflag") or "").lower().startswith("y")
        pct = float(r.get("comppct_r") or 0)
        rank = (major, nrcs, pct)
        if mk not in best or rank > best[mk][0]:
            best[mk] = (rank, r.get("ecoclassid"), r.get("ecoclassname"),
                        r.get("ecoclasstypename"), pct)
    soils["ecoclassid"] = [best.get(int(m), (None,) * 5)[1] for m in soils["mukey"]]
    soils["ecoclassname"] = [best.get(int(m), (None,) * 5)[2] for m in soils["mukey"]]
    soils["ecoclasstypename"] = [best.get(int(m), (None,) * 5)[3]
                                 for m in soils["mukey"]]
    soils["ecosite_pct"] = [best.get(int(m), (None,) * 5)[4] for m in soils["mukey"]]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "soils_ecosites.gpkg"
    soils.to_file(path, driver="GPKG", layer="soils_ecosites")
    table = None
    if hucs is not None and soils["ecoclassid"].notna().any():
        utm = _utm(hucs)
        named = soils[soils["ecoclassid"].notna()].copy()
        named["site"] = (named["ecoclassid"].astype(str) + " "
                         + named["ecoclassname"].fillna("").astype(str)).str.strip()
        sh = _shares_by_huc(named, hucs, "site", utm, "")
        if sh is not None:
            top = sh.idxmax(axis=1)
            table = pd.DataFrame({
                "ecosite_dominant": top,
                "ecosite_dominant_share": sh.max(axis=1).round(3),
                "ecosite_count": (sh > 0.01).sum(axis=1)})
    return {"path": str(path), "map_units": len(mukeys),
            "with_site": int(sum(1 for v in best.values() if v[1])),
            "table": table}


# -- PRISM ----------------------------------------------------------------------------

def _prism_urls(var: str, month: int | None) -> list[str]:
    tag = f"2020{month:02d}" if month else "2020"
    name = f"prism_{var}_us_30s_{tag}_avg_30y.zip"
    folders = ("monthly",) if month else ("annual", "monthly")
    return [f"{PRISM_BASE}/{var}/{f}/{name}" for f in folders]


def _zip_raster(zpath: Path) -> str:
    import zipfile

    with zipfile.ZipFile(zpath) as z:
        names = [n for n in z.namelist()
                 if n.lower().endswith((".tif", ".bil", ".asc"))]
    if not names:
        raise GeolensError(f"{zpath.name} holds no raster.")
    return f"/vsizip/{zpath.as_posix()}/{names[0]}"


def _window(src, bounds):
    """The raster window over ``bounds``, whole cells, inside the raster."""
    import math

    from rasterio.windows import Window, from_bounds

    win = from_bounds(*bounds, transform=src.transform)
    c0 = max(0, math.floor(win.col_off))
    r0 = max(0, math.floor(win.row_off))
    c1 = min(src.width, math.ceil(win.col_off + win.width))
    r1 = min(src.height, math.ceil(win.row_off + win.height))
    if c1 <= c0 or r1 <= r0:
        raise GeolensError("The raster does not cover the HUC12s.")
    return Window(c0, r0, c1 - c0, r1 - r0)


def _zonal_means(raster: str, hucs, band: int = 1,
                 max_cells: int = 30_000_000) -> dict[str, float]:
    """Mean of one band per HUC12 (cells whose centre is in the HUC12),
    read in the window that covers them."""
    import numpy as np
    import rasterio
    from affine import Affine
    from rasterio.features import rasterize

    with rasterio.Env(**GDAL_REMOTE), rasterio.open(raster) as src:
        h = hucs.to_crs(src.crs)
        win = _window(src, h.total_bounds)
        rows, cols = int(win.height), int(win.width)
        step = max(1, int(np.ceil(np.sqrt(rows * cols / max_cells))))
        shape = (max(1, rows // step), max(1, cols // step))
        arr = src.read(band, window=win, out_shape=shape,
                       masked=True).astype("float64")
        tr = src.window_transform(win) * \
            Affine.scale(cols / shape[1], rows / shape[0])
    labels = rasterize(((g, i + 1) for i, g in enumerate(h.geometry)),
                       out_shape=shape, transform=tr, fill=0, dtype="int32")
    ok = (~np.ma.getmaskarray(arr)) & (labels > 0)
    vals = np.ma.getdata(arr)
    n = len(h) + 1
    s = np.bincount(labels[ok], weights=vals[ok], minlength=n)
    c = np.bincount(labels[ok], minlength=n)
    out = {}
    for i, code in enumerate(h["huc12"]):
        out[str(code)] = float(s[i + 1] / c[i + 1]) if c[i + 1] else None
    return out


def prism_normals(hucs, cache_dir: Path, get: Callable | None = None
                  ) -> dict[str, Any]:
    """PRISM 1991-2020 normals per HUC12: annual precipitation (mm), the
    share that falls July to September (the monsoon), and mean annual
    temperature (C). The zips are kept in ``data/prism`` (direct downloads:
    the PRISM web service does not serve normals)."""
    import pandas as pd

    get = get or _get_bytes
    cache_dir.mkdir(parents=True, exist_ok=True)

    def raster(var, month=None):
        for url in _prism_urls(var, month):
            z = cache_dir / url.rsplit("/", 1)[-1]
            if not z.exists():
                try:
                    data = get(url)
                except Exception:                            # noqa: BLE001
                    continue
                tmp = z.with_suffix(".part")
                tmp.write_bytes(data)
                tmp.replace(z)
            return _zip_raster(z)
        raise GeolensError(f"PRISM {var} {month or 'annual'} normal could "
                           f"not be downloaded.")

    ppt = _zonal_means(raster("ppt"), hucs)
    jas = [_zonal_means(raster("ppt", m), hucs) for m in (7, 8, 9)]
    rows = {}
    for code, p in ppt.items():
        summer = [j.get(code) for j in jas]
        share = (sum(summer) / p) if p and all(v is not None for v in summer) \
            else None
        rows[code] = {"prism_ppt_mm": None if p is None else round(p, 1),
                      "prism_ppt_jas_share": None if share is None
                      else round(share, 3)}
    try:
        tmean = _zonal_means(raster("tmean"), hucs)
        for code, t in tmean.items():
            rows.setdefault(code, {})["prism_tmean_c"] = \
                None if t is None else round(t, 2)
    except GeolensError as exc:
        note(f"  PRISM temperature skipped: {exc}")
    return {"table": pd.DataFrame.from_dict(rows, orient="index"),
            "source": "PRISM Climate Group, 1991-2020 normals, 800 m"}


# -- NOAA Atlas 14 -----------------------------------------------------------------

def parse_pfds(text: str) -> dict[str, dict[int, float]]:
    """``quantiles = [[...], ...]`` from a PFDS answer, as duration -> ARI ->
    depth (the units asked for)."""
    m = re.search(r"quantiles\s*=\s*(\[\s*\[.*?\]\s*\])\s*;", text, re.S)
    if not m:
        raise GeolensError("The Atlas 14 answer has no quantiles (outside "
                           "Atlas 14 coverage?).")
    table = ast.literal_eval(m.group(1))
    out: dict[str, dict[int, float]] = {}
    for dur, row in zip(ATLAS14_DURATIONS, table):
        out[dur] = {ari: float(v) for ari, v in zip(ATLAS14_ARI, row)}
    return out


def atlas14_depths(hucs, cache: Path, get: Callable | None = None,
                   pause_s: float = 0.5) -> dict[str, Any]:
    """NOAA Atlas 14 partial-duration depths at each HUC12's centroid: 1-hour
    and 24-hour storms at 2, 10, 25 and 100-year recurrence, in mm. Answers
    are kept in ``data/atlas14.json``, so a rerun asks only for what is
    missing."""
    import pandas as pd

    get = get or (lambda u: _get_bytes(u, timeout=60).decode("utf-8",
                                                             "replace"))
    known = json.loads(cache.read_text()) if cache.exists() else {}
    pts = hucs.to_crs(4326).geometry.representative_point()
    rows, failed = {}, 0
    for code, p in progress(list(zip(hucs["huc12"], pts)), label="Atlas 14"):
        key = f"{p.y:.4f},{p.x:.4f}"
        if key not in known:
            url = ATLAS14_URL + "?" + urllib.parse.urlencode(
                {"lat": f"{p.y:.4f}", "lon": f"{p.x:.4f}", "type": "pf",
                 "data": "depth", "units": "english", "series": "pds"})
            try:
                known[key] = parse_pfds(get(url))
                cache.write_text(json.dumps(known), encoding="utf-8")
            except Exception:                                # noqa: BLE001
                failed += 1
                continue
            time.sleep(pause_s)
        q = known[key]
        rec = {}
        for dur, tag in (("60-min", "1h"), ("24-hr", "24h")):
            for ari in (2, 10, 25, 100):
                v = (q.get(dur) or {}).get(ari, (q.get(dur) or {}).get(str(ari)))
                rec[f"atlas14_{tag}_{ari}yr_mm"] = None if v is None \
                    else round(float(v) * 25.4, 1)
        rows[str(code)] = rec
    if not rows:
        raise GeolensError("NOAA Atlas 14 answered for none of the HUC12s.")
    return {"table": pd.DataFrame.from_dict(rows, orient="index"),
            "failed": failed}


# -- RAP vegetation cover --------------------------------------------------------------

def rap_cover(hucs, out_dir: Path, years: list[int] | None = None,
              open_url: Callable[[int], str] | None = None,
              max_cells: int = 4_000_000) -> dict[str, Any]:
    """Rangeland Analysis Platform cover (v3, 30 m, 1986 on) per HUC12 and
    year: annual and perennial forb and grass, shrub, tree, litter and bare
    ground, in %. Read from the remote rasters a window at a time, at the
    coarsest resolution that keeps a year under ``max_cells`` cells (a HUC12
    mean needs no more), with GDAL's cache capped. Writes
    ``data/rap_cover_huc12.csv`` and returns per-HUC12 recent means and
    trends per decade."""
    import numpy as np
    import pandas as pd
    import rasterio
    from affine import Affine
    from rasterio.features import rasterize

    now = time.gmtime().tm_year
    years = list(years or range(1986, now))
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "rap_cover_huc12.csv"
    long = pd.read_csv(csv_path, dtype={"huc12": str}) if csv_path.exists() \
        else pd.DataFrame(columns=["huc12", "year", *RAP_BANDS])
    done = set(long["year"].astype(int)) if len(long) else set()
    h = hucs.to_crs(4326)
    labels = None
    missing = []
    for year in progress([y for y in years if y not in done], label="RAP years"):
        src_path = None
        for tmpl in ([open_url(year)] if open_url else
                     [u.format(year=year) for u in RAP_URLS]):
            try:
                with rasterio.Env(**GDAL_REMOTE), rasterio.open(tmpl):
                    src_path = tmpl
                    break
            except Exception:                                # noqa: BLE001
                continue
        if src_path is None:
            missing.append(year)
            continue
        with rasterio.Env(**GDAL_REMOTE), rasterio.open(src_path) as src:
            win = _window(src, h.to_crs(src.crs).total_bounds)
            rows_, cols_ = int(win.height), int(win.width)
            step = max(1, int(np.ceil(np.sqrt(rows_ * cols_ / max_cells))))
            shape = (max(1, rows_ // step), max(1, cols_ // step))
            arr = src.read(window=win, out_shape=(src.count, *shape))
            tr = src.window_transform(win) * \
                Affine.scale(cols_ / shape[1], rows_ / shape[0])
            names = [str(d or "").upper() for d in (src.descriptions or ())]
        order = [names.index(b) if b in names else i
                 for i, b in enumerate(RAP_BANDS)]
        if labels is None or labels.shape != shape:
            labels = rasterize(((g, i + 1) for i, g in
                                enumerate(h.to_crs(src.crs).geometry)),
                               out_shape=shape, transform=tr, fill=0,
                               dtype="int32")
        n = len(h) + 1
        recs = {str(c): {"huc12": str(c), "year": year} for c in h["huc12"]}
        for b, i in zip(RAP_BANDS, order):
            band = arr[i].astype("float64")
            ok = (band <= 100) & (labels > 0)
            s = np.bincount(labels[ok], weights=band[ok], minlength=n)
            c = np.bincount(labels[ok], minlength=n)
            for j, code in enumerate(h["huc12"]):
                recs[str(code)][b] = round(float(s[j + 1] / c[j + 1]), 2) \
                    if c[j + 1] else None
        long = pd.concat([long, pd.DataFrame(recs.values())], ignore_index=True)
        long.to_csv(csv_path, index=False)          # resumable, year by year
    if long.empty:
        raise GeolensError("No RAP cover year could be read.")
    long["year"] = long["year"].astype(int)
    last = int(long["year"].max())
    table = {}
    for code, g in long.groupby("huc12"):
        g = g.sort_values("year")
        rec = {}
        recent = g[g["year"] > last - 5]
        for b in RAP_BANDS:
            v = pd.to_numeric(g[b], errors="coerce")
            ok = v.notna()
            rec[f"rap_{b.lower()}_recent"] = round(float(
                pd.to_numeric(recent[b], errors="coerce").mean()), 1) \
                if recent[b].notna().any() else None
            if ok.sum() >= 5:
                slope = np.polyfit(g["year"][ok], v[ok], 1)[0]
                rec[f"rap_{b.lower()}_per_decade"] = round(float(slope) * 10, 2)
            else:
                rec[f"rap_{b.lower()}_per_decade"] = None
        table[str(code)] = rec
    return {"path": str(csv_path), "years": sorted(set(long["year"])),
            "missing_years": missing,
            "table": pd.DataFrame.from_dict(table, orient="index")}


# -- the HUC12 summary --------------------------------------------------------------

def _zonal_slope(raster: str, hucs, max_cells: int = 30_000_000
                 ) -> dict[str, dict]:
    """Mean slope (%) and the share of ground steeper than 20% per HUC12,
    from the level A slope raster (rise over run)."""
    import numpy as np
    import rasterio
    from affine import Affine
    from rasterio.features import rasterize

    with rasterio.open(raster) as src:
        h = hucs.to_crs(src.crs)
        win = _window(src, h.total_bounds)
        rows, cols = int(win.height), int(win.width)
        step = max(1, int(np.ceil(np.sqrt(rows * cols / max_cells))))
        shape = (max(1, rows // step), max(1, cols // step))
        arr = src.read(1, window=win, out_shape=shape, masked=True)
        tr = src.window_transform(win) * \
            Affine.scale(cols / shape[1], rows / shape[0])
    labels = rasterize(((g, i + 1) for i, g in enumerate(h.geometry)),
                       out_shape=shape, transform=tr, fill=0, dtype="int32")
    vals = np.ma.getdata(arr).astype("float64")
    ok = (~np.ma.getmaskarray(arr)) & (labels > 0) & (vals >= 0)
    n = len(h) + 1
    c = np.bincount(labels[ok], minlength=n)
    s = np.bincount(labels[ok], weights=vals[ok], minlength=n)
    steep = np.bincount(labels[ok], weights=(vals[ok] > 0.20), minlength=n)
    out = {}
    for i, code in enumerate(h["huc12"]):
        if c[i + 1]:
            out[str(code)] = {
                "slope_mean_pct": round(100 * float(s[i + 1] / c[i + 1]), 1),
                "slope_over_20pct_share": round(float(steep[i + 1] / c[i + 1]),
                                                3)}
    return out


def _top(shares, k: int = 3) -> str:
    items = sorted(((v, c) for c, v in shares.items() if v and v >= 0.01),
                   reverse=True)[:k]
    return ", ".join(f"{c} {100 * v:.0f}%" for v, c in items)


def huc12_summary(out_dir: Path, hucs, valley_types: str | None = None,
                  soils: str | None = None, stability: str | None = None,
                  slope: str | None = None, data_dir: Path | None = None,
                  pilot_group: dict | None = None) -> dict[str, Any]:
    """One row per HUC12: what it is made of and how it is behaving.

    Columns, each only when its source exists:

    - the HUC12: name, km2, share inside the boundary, lidar share, the
      pilot HUC12 that stands for it;
    - streams (level A): km, shares by confinement, the main valley types
      (1996) and fluvial landscape types (Rosgen 2022), and the share of
      stream length in alluvial landscape types (``*-AL-*``);
    - soils (SSURGO): shares by material and hydrologic group, area-weighted
      surface erodibility K, the dominant ecological site;
    - slope (level A DEM): mean, and the share steeper than 20%;
    - stream form (level B, pilot HUC12s): km stable / unstable / check by
      the 1996 table and by the 2022 card;
    - the watershed data steps: fire, PRISM climate, Atlas 14 storms, RAP
      cover now and its trend per decade.

    Writes ``huc12_summary.gpkg`` (with the HUC12 polygons) and
    ``huc12_summary.csv``. Nothing is weighted into a score: which of these
    make a HUC12 sensitive is the analyst's call, and the table is built to
    make it.
    """
    import geopandas as gpd
    import pandas as pd

    from .sar import _utm_for

    h = hucs.copy()
    h["huc12"] = h["huc12"].astype(str)
    wgs = h.to_crs(4326)
    c = wgs.geometry.union_all().centroid if hasattr(wgs.geometry, "union_all") \
        else wgs.geometry.unary_union.centroid
    utm = _utm_for(c.x, c.y)
    t = pd.DataFrame(index=h["huc12"])
    t["name"] = list(h.get("name", h["huc12"]))
    t["km2"] = [round(float(v), 2) for v in h.get("areasqkm",
                                                  h.to_crs(utm).area / 1e6)]
    if "in_boundary" in h:
        t["in_boundary_share"] = list(h["in_boundary"])
    if "lidar_frac" in h:
        t["lidar_share"] = list(h["lidar_frac"])
    if pilot_group:
        t["pilot_group"] = [pilot_group.get(code) for code in t.index]

    if valley_types and Path(valley_types).exists():
        vt = gpd.read_file(valley_types)
        if "huc12" in vt.columns:
            vt = vt[vt["huc12"].notna()].copy()
            vt["huc12"] = vt["huc12"].astype(str)
            vt["_km"] = vt.to_crs(utm).geometry.length / 1000.0
            km = vt.groupby("huc12")["_km"].sum()
            t["stream_km"] = km.reindex(t.index).round(1)
            for col, prefix in (("confinement", "conf_"),):
                if col in vt.columns:
                    sh = (vt.pivot_table(index="huc12", columns=col,
                                         values="_km", aggfunc="sum")
                          .div(km, axis=0))
                    for k in sh.columns:
                        t[f"{prefix}{str(k).replace(' ', '_')}_share"] = \
                            sh[k].reindex(t.index).round(3)
            for col, name in (("valley_type", "valley_types_1996"),
                              ("landscape_type", "landscape_types_2022")):
                if col in vt.columns:
                    sh = (vt.assign(_v=vt[col].fillna("unresolved"))
                          .pivot_table(index="huc12", columns="_v",
                                       values="_km", aggfunc="sum")
                          .div(km, axis=0))
                    t[name] = [_top(sh.loc[i].to_dict()) if i in sh.index
                               else None for i in t.index]
            if "landscape_type" in vt.columns:
                al = vt[vt["landscape_type"].fillna("").str.contains("-AL-")]
                t["alluvial_landscape_share"] = (
                    al.groupby("huc12")["_km"].sum() / km
                ).reindex(t.index).fillna(0.0).round(3)

    if soils and Path(soils).exists():
        from .project import _HSG

        so = gpd.read_file(soils)
        cols = [c for c in ("material_class", "hydgrpdcd", "kwfact")
                if c in so.columns]
        if cols:
            # One overlay of the soils with the HUC12s serves all three.
            a = gpd.overlay(h[["huc12", "geometry"]].to_crs(utm),
                            so[[*cols, "geometry"]].to_crs(utm),
                            how="intersection", keep_geom_type=True)
            a["_w"] = a.geometry.area
            total = h.set_index("huc12").to_crs(utm).geometry.area

            def shares(values, prefix):
                p = a.assign(_v=values).pivot_table(
                    index="huc12", columns="_v", values="_w", aggfunc="sum",
                    fill_value=0.0)
                p = p.div(total.reindex(p.index), axis=0).clip(upper=1.0)
                for k in p.columns:
                    t[f"{prefix}{k}_share"] = p[k].reindex(t.index).round(3)

            if "material_class" in cols and len(a):
                shares(a["material_class"].fillna("unknown").replace(
                    {"mass_movement": "colluvial"}), "soil_")
            if "hydgrpdcd" in cols and len(a):
                shares([_HSG.get(str(g).strip().upper(), "unknown")
                        for g in a["hydgrpdcd"]], "hsg_")
            if "kwfact" in cols:
                k = a.assign(_k=pd.to_numeric(a["kwfact"], errors="coerce"))
                k = k[k["_k"].notna()]
                if len(k):
                    kw = (k["_k"] * k["_w"]).groupby(k["huc12"]).sum() / \
                        k["_w"].groupby(k["huc12"]).sum()
                    t["soil_k_mean"] = kw.reindex(t.index).round(3)

    if slope and Path(slope).exists():
        try:
            for code, rec in _zonal_slope(slope, h).items():
                for k, v in rec.items():
                    t.loc[code, k] = v
        except Exception as exc:                             # noqa: BLE001
            note(f"  slope summary skipped: {exc}")

    if stability and Path(stability).exists():
        stb = gpd.read_file(stability)
        if "huc12" in stb.columns:
            stb["huc12"] = stb["huc12"].astype(str)
            stb["_km"] = stb.to_crs(utm).geometry.length / 1000.0
            for col, prefix in (("form_status", "form1996_"),
                                ("landscape_form", "form2022_")):
                if col in stb.columns:
                    p = stb.pivot_table(index="huc12", columns=col,
                                        values="_km", aggfunc="sum")
                    for k in p.columns:
                        t[f"{prefix}{k}_km"] = p[k].reindex(t.index).round(2)

    if data_dir is not None:
        for step in STEPS:
            f = Path(data_dir) / f"{step}_huc12.csv"
            if f.exists():
                d = pd.read_csv(f, dtype={"huc12": str}).set_index("huc12")
                for k in d.columns:
                    t[k] = d[k].reindex(t.index)

    t.index.name = "huc12"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv = out_dir / "huc12_summary.csv"
    t.to_csv(csv)
    g = gpd.GeoDataFrame(t.reset_index().merge(
        h[["huc12", "geometry"]], on="huc12", how="left"),
        geometry="geometry", crs=h.crs)
    gpkg = out_dir / "huc12_summary.gpkg"
    g.to_file(gpkg, driver="GPKG", layer="huc12_summary")
    return {"path": str(gpkg), "csv": str(csv), "rows": int(len(t)),
            "columns": list(t.columns)}
