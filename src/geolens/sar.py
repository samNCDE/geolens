"""Sentinel-1 radar backscatter for an area, from NASA's OPERA RTC-S1.

Why: NAIP NDVI cannot tell a cottonwood gallery from an irrigated hay field --
both are green in June. Radar sees structure and change. A woody canopy gives
steady, high cross-polarised (VH) backscatter through the year; a hay field
jumps at every cutting and irrigation. ``docs/SAR_FEASIBILITY.md`` has the
reasoning, and the experiment these statistics are for.

The data is OPERA L2 RTC-S1 (NASA JPL, distributed by ASF): Sentinel-1
backscatter, radiometrically terrain-corrected to gamma-naught power. There is
one cloud-optimised GeoTIFF per burst and polarisation, on a 30 m UTM grid,
from 2016, with a mask (0 valid; 1 shadow; 2 layover; 3 both; 255 fill).
Searching (NASA's CMR) needs no login; reading the files needs a free NASA
Earthdata login. :func:`save_login` (the toolbox's *Save Earthdata login*)
keeps it in the user's own netrc file, never in geolens.

Written to the output folder:

``sar_tiles/T<track>/<date>_T<track>.tif``
    VV and VH (gamma0, linear power) on the area's 30 m grid, one per pass,
    with shadow, layover and fill as NaN. Reused when present, so a rerun or
    a longer period only fetches what is new.
``sar_stats_T<track>.tif``
    per-pixel statistics of one track's passes
``sar_stats.tif``
    the tracks combined, each weighted by its passes
``sar_zonal.gpkg``
    the combined statistics per polygon, when polygons are given
``sar_sources.json``
    every granule used, by track and date

The statistics bands are ``vh_db`` (median VH, dB), ``vv_db`` (median VV,
dB), ``vh_vv_db`` (their difference, dB), ``vh_cv`` (VH's coefficient of
variation through the period: std / mean, linear) and ``passes``. Each track
is summarised on its own, because each sees the ground from its own angle.
A pixel needs ``min_passes`` valid passes to get a value.
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .mosaic import Grid, grid_over, read_onto, write_tile
from .raster import gtiff_profile
from .safety import Guard, GeolensError, note, progress

CMR_GRANULES = "https://cmr.earthdata.nasa.gov/search/granules.json"
COLLECTION = "C2777436413-ASF"            # OPERA_L2_RTC-S1_V1, at ASF
URS_HOST = "urs.earthdata.nasa.gov"
CELL_M = 30.0
MAX_CELLS = 20_000_000                    # ~18,000 km2 at 30 m
PAGE_SIZE = 2000
STAT_BANDS = ["vh_db", "vv_db", "vh_vv_db", "vh_cv", "passes"]
_UA = {"User-Agent": "geolens (+geomorphic analysis)"}
_NAME = re.compile(
    r"OPERA_L2_RTC-S1_T(?P<track>\d{3})-(?P<burst>\d+)-(?P<swath>IW\d)_"
    r"(?P<start>\d{8}T\d{6})Z_(?P<made>\d{8}T\d{6})Z_(?P<sensor>S1[A-Z])_")

NO_LOGIN = (
    "No NASA Earthdata login is saved on this computer. Run 0 Setup > Save "
    "Earthdata login (or geolens.sar.save_login in Python) once with your "
    "own free login from https://urs.earthdata.nasa.gov, then run this again.")
LOGIN_REFUSED = (
    "NASA Earthdata refused the saved login. Check the username and password "
    "(Save Earthdata login saves them again). If they are right, sign in "
    "once at https://search.asf.alaska.edu with that login (some accounts "
    "must approve ASF's data access the first time), then run this again.")


# -- the login: the user's own netrc file ----------------------------------------

def netrc_path() -> Path:
    """``GEOLENS_NETRC`` (a portable release sets it to a file on its own
    drive), else ``%USERPROFILE%\\_netrc`` on Windows, ``~/.netrc``
    elsewhere: where NASA's tools, curl and GDAL look for logins."""
    env = os.environ.get("GEOLENS_NETRC", "").strip()
    if env:
        return Path(env)
    return Path.home() / ("_netrc" if os.name == "nt" else ".netrc")


def _read_netrc(path: Path) -> list[dict]:
    """The machine and default entries of a netrc, in order."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    toks = " ".join(line for line in text.splitlines()
                    if not line.lstrip().startswith("#")).split()
    entries: list[dict] = []
    cur: dict | None = None
    i = 0
    while i < len(toks):
        t = toks[i]
        if t == "machine" and i + 1 < len(toks):
            cur = {"machine": toks[i + 1]}
            entries.append(cur)
            i += 2
        elif t == "default":
            cur = {"machine": None}
            entries.append(cur)
            i += 1
        elif t == "macdef":
            raise GeolensError(
                f"{path} has a macdef entry, which geolens will not rewrite. "
                f"Add this line to it yourself: machine {URS_HOST} login "
                f"<username> password <password>")
        elif t in ("login", "password", "account") and cur is not None \
                and i + 1 < len(toks):
            cur[t] = toks[i + 1]
            i += 2
        else:
            i += 1
    return entries


def earthdata_login(path: str | Path | None = None) -> tuple[str, str] | None:
    """(username, password) saved for NASA Earthdata, or None."""
    for e in _read_netrc(Path(path) if path else netrc_path()):
        if e.get("machine") == URS_HOST and e.get("login") and e.get("password"):
            return e["login"], e["password"]
    return None


def save_login(username: str, password: str,
               path: str | Path | None = None) -> str:
    """Save a NASA Earthdata login in the user's netrc file, not in geolens.

    Logins for other sites in the file are kept. Each person saves their
    own: the login is personal, and geolens is shared. Returns the path.
    """
    user, pw = str(username or "").strip(), str(password or "")
    if not user or not pw:
        raise GeolensError("Give both the Earthdata username and password.")
    if any(c.isspace() or c in "\"'" for c in user + pw):
        raise GeolensError(
            "A netrc file cannot hold a username or password containing "
            "spaces or quotes. Change the Earthdata password at "
            "https://urs.earthdata.nasa.gov and save it again.")
    p = Path(path) if path else netrc_path()
    entries = [e for e in _read_netrc(p) if e.get("machine") != URS_HOST]
    entries.insert(0, {"machine": URS_HOST, "login": user, "password": pw})
    lines = []
    for e in entries:
        head = f"machine {e['machine']}" if e.get("machine") else "default"
        rest = " ".join(f"{k} {e[k]}" for k in ("login", "password", "account")
                        if e.get(k))
        lines.append(f"{head} {rest}".strip())
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    tmp.replace(p)
    return str(p)


def login_status() -> dict[str, Any]:
    """Whether a login is saved, and for whom. Never the password."""
    got = earthdata_login()
    return {"saved": got is not None, "username": got[0] if got else None,
            "file": str(netrc_path())}


def gdal_env() -> dict[str, str]:
    """GDAL settings for reading Earthdata-protected files through /vsicurl/:
    the netrc for the login, and a cookie jar for the session the login
    redirect sets up (without it every read logs in again)."""
    home = os.environ.get("GEOLENS_HOME", "").strip()
    cookies = ((Path(home) if home else Path.home() / ".geolens")
               / "earthdata_cookies.txt")
    cookies.parent.mkdir(parents=True, exist_ok=True)
    return {"GDAL_HTTP_NETRC": "YES",
            "GDAL_HTTP_NETRC_FILE": str(netrc_path()),
            "GDAL_HTTP_COOKIEFILE": str(cookies),
            "GDAL_HTTP_COOKIEJAR": str(cookies),
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
            "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF",
            "GDAL_HTTP_MAX_RETRY": "4",
            "GDAL_HTTP_RETRY_DELAY": "2"}


# -- finding passes: NASA's CMR -------------------------------------------------------

@dataclass
class Granule:
    """One burst of one pass: its VV, VH and mask files."""

    id: str
    track: int
    burst: str
    sensor: str
    start: datetime
    made: str
    urls: dict = field(default_factory=dict)
    footprint: Any = None             # shapely polygon, lon/lat

    @property
    def date(self) -> str:
        return self.start.strftime("%Y%m%d")


def parse_granule(entry: dict) -> Granule | None:
    """A CMR granule entry as a Granule; None if it is not an RTC burst."""
    title = str(entry.get("title") or entry.get("producer_granule_id") or "")
    m = _NAME.search(title)
    if not m:
        return None
    urls: dict = {}
    for link in entry.get("links") or []:
        href = str(link.get("href") or "")
        if not href.startswith("https://"):
            continue                          # s3:// works only inside AWS
        low = href.lower()
        for key, suffix in (("VV", "_vv.tif"), ("VH", "_vh.tif"),
                            ("mask", "_mask.tif")):
            if low.endswith(suffix):
                urls.setdefault(key, href)
    footprint = None
    polys = entry.get("polygons") or []
    if polys and polys[0]:
        try:
            from shapely.geometry import Polygon

            v = [float(x) for x in str(polys[0][0]).split()]
            footprint = Polygon([(v[i + 1], v[i])          # CMR is lat lon
                                 for i in range(0, len(v) - 1, 2)])
        except Exception:                                    # noqa: BLE001
            footprint = None
    return Granule(
        id=title, track=int(m["track"]), burst=f"{m['burst']}-{m['swath']}",
        sensor=m["sensor"], made=m["made"], urls=urls, footprint=footprint,
        start=datetime.strptime(m["start"], "%Y%m%dT%H%M%S").replace(
            tzinfo=timezone.utc))


def _cmr_page(params: dict, search_after: str | None, timeout: int
              ) -> tuple[list, str | None]:
    url = CMR_GRANULES + "?" + urllib.parse.urlencode(params)
    headers = dict(_UA, Accept="application/json")
    if search_after:
        headers["CMR-Search-After"] = search_after
    last: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read().decode("utf-8"))
                return ((body.get("feed") or {}).get("entry") or [],
                        r.headers.get("CMR-Search-After"))
        except Exception as e:                               # noqa: BLE001
            last = e
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise GeolensError(
        f"Could not search NASA's catalog (CMR) for Sentinel-1 passes: "
        f"{type(last).__name__}: {last}") from last


def search(bounds_wgs84: tuple[float, float, float, float], start: str,
           end: str, timeout: int = 60) -> list[Granule]:
    """Every RTC burst touching ``bounds_wgs84`` (west, south, east, north)
    between ``start`` and ``end`` (YYYY-MM-DD). A burst reprocessed more than
    once is kept in its newest version only."""
    w, s, e, n = bounds_wgs84
    params = {"collection_concept_id": COLLECTION,
              "bounding_box": f"{w:.6f},{s:.6f},{e:.6f},{n:.6f}",
              "temporal": f"{start}T00:00:00Z,{end}T23:59:59Z",
              "page_size": PAGE_SIZE, "sort_key": "start_date"}
    found: list = []
    after: str | None = None
    while True:
        entries, after = _cmr_page(params, after, timeout)
        found.extend(entries)
        if len(entries) < PAGE_SIZE or not after:
            break
    best: dict = {}
    for entry in found:
        g = parse_granule(entry)
        if g is None:
            continue
        key = (g.track, g.burst, g.start)
        if key not in best or g.made > best[key].made:
            best[key] = g
    return sorted(best.values(), key=lambda g: (g.track, g.start, g.burst))


# -- reading: streamed, or downloaded when GDAL cannot get through ------------------

def _refused(message: str) -> bool:
    """An HTTP 401 in an error message. Not any "401": file names carry
    dates such as 20240401T..., which a bare substring test would match."""
    low = message.lower()
    return bool(re.search(r"\b401\b", low)) or "unauthorized" in low


class _Reader:
    """Reads granule files onto the grid.

    Streams them through GDAL's /vsicurl/ with the Earthdata login. If GDAL
    cannot get through -- a proxy that inspects TLS has a certificate in the
    Windows store but not in GDAL's own (see ``naip._download``) -- it
    downloads whole files with Python's HTTPS stack into ``sar_downloads``
    and reads them from disk, for the rest of the run.
    """

    def __init__(self, login: tuple[str, str], download_dir: Path,
                 timeout: int = 60):
        self.login = login
        self.download_dir = download_dir
        self.timeout = timeout
        self.mode = "stream"
        self._opener = None

    def read(self, url: str, grid: Grid, window, resampling: str) -> np.ndarray:
        if self.mode == "stream":
            try:
                import rasterio

                with rasterio.Env(**gdal_env()):
                    return read_onto(url, [1], grid, window, resampling)[0][0]
            except Exception as exc:                         # noqa: BLE001
                if _refused(str(exc)):
                    raise GeolensError(LOGIN_REFUSED) from exc
                note(f"streaming failed ({type(exc).__name__}: "
                     f"{str(exc)[:160]}); downloading whole files instead")
                self.mode = "download"
        return read_onto(str(self._download(url)), [1], grid, window,
                         resampling)[0][0]

    def _download(self, url: str) -> Path:
        dest = self.download_dir / url.rsplit("/", 1)[-1]
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        if self._opener is None:
            pm = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            pm.add_password(None, f"https://{URS_HOST}", *self.login)
            self._opener = urllib.request.build_opener(
                urllib.request.HTTPBasicAuthHandler(pm),
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        try:
            req = urllib.request.Request(url, headers=_UA)
            with self._opener.open(req, timeout=self.timeout) as r, \
                    part.open("wb") as fh:
                shutil.copyfileobj(r, fh, 1 << 20)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise GeolensError(LOGIN_REFUSED) from e
            raise GeolensError(f"Could not download {url}: HTTP {e.code} "
                               f"{e.reason}") from e
        part.replace(dest)
        return dest


# -- passes, tiles and statistics ----------------------------------------------------

def _pass_tile(granules: list[Granule], grid: Grid, aoi_wgs84, reader: _Reader,
               path: Path) -> tuple[int, list[str]]:
    """One pass's bursts onto the grid, as one tile. Returns (valid cells,
    granules used); no file is written when no cell is valid."""
    shape = (grid.height, grid.width)
    window = (0, 0, grid.height, grid.width)
    vv = np.full(shape, np.nan, dtype="float32")
    vh = np.full(shape, np.nan, dtype="float32")
    filled = np.zeros(shape, dtype=bool)
    used = []
    for g in granules:
        if g.footprint is not None and not g.footprint.intersects(aoi_wgs84):
            continue
        if not {"VV", "VH"} <= set(g.urls):
            continue                      # an HH/HV burst, or links missing
        a = reader.read(g.urls["VV"], grid, window, "bilinear")
        b = reader.read(g.urls["VH"], grid, window, "bilinear")
        with np.errstate(invalid="ignore"):
            ok = np.isfinite(a) & np.isfinite(b) & (a > 0) & (b > 0) & ~filled
        if "mask" in g.urls:
            m = reader.read(g.urls["mask"], grid, window, "nearest")
            ok &= m == 0                  # not shadow, layover or fill
        vv[ok] = a[ok]
        vh[ok] = b[ok]
        filled |= ok
        used.append(g.id)
    n = int(filled.sum())
    if n:
        write_tile(path, grid, window, np.stack([vv, vh]), "float32", np.nan,
                   band_names=["VV", "VH"])
    return n, used


def _stats(vv: np.ndarray, vh: np.ndarray, min_passes: int) -> np.ndarray:
    """(passes, rows, cols) VV and VH -> the STAT_BANDS for those rows."""
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore", RuntimeWarning)
        n = np.isfinite(vh).sum(axis=0).astype("float32")
        vh_med = np.nanmedian(vh, axis=0)
        vv_med = np.nanmedian(vv, axis=0)
        vh_db = 10 * np.log10(vh_med)
        vv_db = 10 * np.log10(vv_med)
        cv = np.nanstd(vh, axis=0) / np.nanmean(vh, axis=0)
        out = np.stack([vh_db, vv_db, vh_db - vv_db, cv, n]).astype("float32")
    out[:4, n < min_passes] = np.nan
    return out


def _profile(grid: Grid) -> dict:
    prof = gtiff_profile({"width": grid.width, "height": grid.height,
                          "crs": grid.crs, "transform": grid.transform},
                         dtype="float32", nodata=np.nan, count=len(STAT_BANDS))
    if grid.width >= 256 and grid.height >= 256:
        prof.update(tiled=True, blockxsize=256, blockysize=256)
    return prof


def _rows_per_block(grid: Grid, layers: int) -> int:
    return max(1, min(grid.height, 8_000_000 // max(1, grid.width * layers)))


def _track_stats(tiles: list[Path], grid: Grid, out: Path,
                 min_passes: int) -> str:
    import rasterio
    from rasterio.windows import Window

    rows = _rows_per_block(grid, 2 * len(tiles))
    srcs = [rasterio.open(str(t)) for t in tiles]
    try:
        with rasterio.open(str(out), "w", **_profile(grid)) as dst:
            for r0 in range(0, grid.height, rows):
                win = Window(0, r0, grid.width, min(rows, grid.height - r0))
                stack = np.stack([s.read(window=win) for s in srcs])
                dst.write(_stats(stack[:, 0], stack[:, 1], min_passes),
                          window=win)
            for i, name in enumerate(STAT_BANDS, 1):
                dst.set_band_description(i, name)
    finally:
        for s in srcs:
            s.close()
    return str(out)


def _combine(track_files: list[str], grid: Grid, out: Path) -> str:
    """The tracks' statistics, each weighted by its passes at the pixel."""
    import rasterio
    from rasterio.windows import Window

    rows = _rows_per_block(grid, len(STAT_BANDS) * len(track_files))
    srcs = [rasterio.open(f) for f in track_files]
    try:
        with rasterio.open(str(out), "w", **_profile(grid)) as dst:
            for r0 in range(0, grid.height, rows):
                win = Window(0, r0, grid.width, min(rows, grid.height - r0))
                st = np.stack([s.read(window=win) for s in srcs])
                passes = np.nan_to_num(st[:, -1])
                vals = st[:, :-1]
                w = np.where(np.isfinite(vals), passes[:, None], 0.0)
                with np.errstate(invalid="ignore", divide="ignore"):
                    mean = (np.nansum(np.nan_to_num(vals) * w, axis=0)
                            / w.sum(axis=0))
                mean[w.sum(axis=0) == 0] = np.nan
                dst.write(np.concatenate([mean, passes.sum(axis=0)[None]])
                          .astype("float32"), window=win)
            for i, name in enumerate(STAT_BANDS, 1):
                dst.set_band_description(i, name)
    finally:
        for s in srcs:
            s.close()
    return str(out)


def _zonal(polygons: str, stats: str, out: Path) -> str:
    """The median of each statistic over each polygon's cells (every cell a
    polygon touches: riparian strips are often narrower than a 30 m cell)."""
    import geopandas as gpd
    import pandas as pd
    import rasterio
    from rasterio.features import rasterize

    g = gpd.read_file(polygons)
    with rasterio.open(stats) as src:
        data = src.read()
        transform, crs = src.transform, src.crs
    geoms = g.to_crs(crs).geometry
    ids = rasterize(((geom, i + 1) for i, geom in enumerate(geoms)
                     if geom is not None and not geom.is_empty),
                    out_shape=data.shape[1:], transform=transform, fill=0,
                    all_touched=True, dtype="int32")
    sel = ids > 0
    frame = pd.DataFrame({"id": ids[sel]})
    for k, name in enumerate(STAT_BANDS[:4]):
        frame[name] = data[k][sel]
    med = frame.groupby("id").median()
    cells = frame.groupby("id").size()
    idx = np.arange(1, len(g) + 1)
    out_g = g.copy()
    for name in STAT_BANDS[:4]:
        out_g[f"sar_{name}"] = med[name].reindex(idx).to_numpy()
    out_g["sar_cells"] = cells.reindex(idx).fillna(0).astype(int).to_numpy()
    out.parent.mkdir(parents=True, exist_ok=True)
    out_g.to_file(str(out), driver="GPKG", layer="sar_zonal")
    return str(out)


def _utm_for(lon: float, lat: float):
    from pyproj import CRS

    zone = min(60, max(1, int((lon + 180) // 6) + 1))
    return CRS.from_epsg((32600 if lat >= 0 else 32700) + zone)


def _period(start: str | None, end: str | None) -> tuple[str, str]:
    try:
        e = (datetime.strptime(end, "%Y-%m-%d").date() if end
             else date.today())
        s = (datetime.strptime(start, "%Y-%m-%d").date() if start
             else e - timedelta(days=365))
    except ValueError as exc:
        raise GeolensError(f"Dates are YYYY-MM-DD: {exc}") from exc
    if s > e:
        raise GeolensError(f"The start ({s}) is after the end ({e}).")
    return s.isoformat(), e.isoformat()


def sentinel1_stats(
    aoi: str | Path,
    start: str | None = None,
    end: str | None = None,
    polygons: str | Path | None = None,
    min_passes: int = 8,
    timeout: int = 60,
    max_cells: int = MAX_CELLS,
    guard: Guard | None = None,
) -> dict[str, Any]:
    """Sentinel-1 backscatter statistics for an area, from OPERA RTC-S1.

    ``aoi`` is a raster (its extent, normally the DEM) or a polygon layer.
    The period defaults to the year before ``end`` (default today). The grid
    is 30 m in the area's UTM zone. ``polygons`` (riparian band, fields)
    gets the statistics per polygon. Needs a saved Earthdata login.
    """
    from pyproj import Transformer
    from shapely.ops import transform as reproject_geom

    from .naip import _aoi_geometry

    guard = guard or Guard.from_env()
    t0 = time.monotonic()
    login = earthdata_login()
    if login is None:
        raise GeolensError(NO_LOGIN)
    start, end = _period(start, end)
    min_passes = max(2, int(min_passes))

    geom, crs, _ = _aoi_geometry(str(aoi), guard)
    aoi_wgs = reproject_geom(
        Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform, geom)
    c = aoi_wgs.centroid
    utm = _utm_for(c.x, c.y)
    aoi_utm = reproject_geom(
        Transformer.from_crs("EPSG:4326", utm, always_xy=True).transform,
        aoi_wgs)
    grid = grid_over(aoi_utm.bounds, utm, CELL_M)
    if grid.cells > max_cells:
        raise GeolensError(
            f"The area is {grid.cells:,} cells at 30 m (limit {max_cells:,}). "
            f"Use a smaller area: a valley or a watershed, not a landscape.")
    note(f"Sentinel-1 grid {grid.width:,} x {grid.height:,} at 30 m "
         f"({utm.to_string()}), {start} to {end}")

    granules = search(aoi_wgs.bounds, start, end, timeout)
    if not granules:
        raise GeolensError(f"No OPERA RTC-S1 passes cover this area between "
                           f"{start} and {end}.")
    passes: dict = defaultdict(list)
    for g in granules:
        passes[(g.track, g.date)].append(g)
    note(f"{len(granules)} bursts in {len(passes)} passes on "
         f"{len({k[0] for k in passes})} track(s)")

    tiles_dir = Path(guard.write_path("sar_tiles"))
    reader = _Reader(login, Path(guard.write_path("sar_downloads")), timeout)
    tiles: dict = defaultdict(list)
    sources: dict = defaultdict(dict)
    reused = empty = 0
    for (track, day), gs in progress(sorted(passes.items()),
                                     label="Sentinel-1 passes"):
        path = tiles_dir / f"T{track:03d}" / f"{day}_T{track:03d}.tif"
        none = path.with_suffix(".empty")
        if path.exists():
            tiles[track].append(path)
            reused += 1
            sources[f"T{track:03d}"][day] = "reused"
            continue
        if none.exists():
            empty += 1
            continue
        n, used = _pass_tile(gs, grid, aoi_wgs, reader, path)
        if n:
            tiles[track].append(path)
            sources[f"T{track:03d}"][day] = used
        else:
            none.parent.mkdir(parents=True, exist_ok=True)
            none.write_text("no valid cells in the area\n", encoding="utf-8")
            empty += 1

    warn: list[str] = []
    track_stats: dict = {}
    for track, ts in sorted(tiles.items()):
        name = f"T{track:03d}"
        if len(ts) < min_passes:
            warn.append(f"track {name}: {len(ts)} passes, fewer than "
                        f"{min_passes}; left out of the statistics")
            continue
        track_stats[name] = _track_stats(
            sorted(ts), grid, Path(guard.write_path(f"sar_stats_{name}.tif")),
            min_passes)
    if not track_stats:
        raise GeolensError(
            f"No track has {min_passes} passes with valid cells between "
            f"{start} and {end}. Use a longer period, or lower min_passes.")
    stats = _combine(list(track_stats.values()), grid,
                     Path(guard.write_path("sar_stats.tif")))
    zonal = (_zonal(str(guard.read_path(polygons)), stats,
                    Path(guard.write_path("sar_zonal.gpkg")))
             if polygons else None)

    record = {"collection": COLLECTION, "start": start, "end": end,
              "grid": {"crs": utm.to_string(), "cell_m": CELL_M,
                       "width": grid.width, "height": grid.height},
              "reader": reader.mode, "passes": dict(sources)}
    Path(guard.write_path("sar_sources.json")).write_text(
        json.dumps(record, indent=1), encoding="utf-8")
    note(f"Sentinel-1: {sum(len(v) for v in tiles.values())} passes "
         f"({reused} reused, {empty} with nothing in the area), "
         f"tracks {', '.join(track_stats)}")
    return {"stats": stats, "track_stats": track_stats, "zonal": zonal,
            "tiles_dir": str(tiles_dir), "start": start, "end": end,
            "passes": {k: len(v) for k, v in sorted(tiles.items())},
            "reused": reused, "empty": empty, "reader": reader.mode,
            "bands": STAT_BANDS, "warnings": warn,
            "seconds": round(time.monotonic() - t0, 1)}
