"""NAIP imagery: 1 m NDVI and true-colour, streamed rather than downloaded.

WHY NAIP AND NOT SENTINEL-2
---------------------------
NAIP carries a **near-infrared band at 0.6-1 m**. That makes NDVI at 1 m, where
Sentinel-2 gives 10 m, and for the systems this matters in the difference is
decisive: a 20 m wide desert riparian corridor is two Sentinel-2 pixels across
and twenty-plus NAIP pixels. Two pixels is not a measurement, it is an edge
effect. So for :mod:`geolens.riparian` in arid country, NAIP should be the
primary NDVI source and Sentinel-2 the fallback for dates NAIP does not cover.

What NAIP gives up is time. It flies every two to three years per state, in
leaf-on summer, so there is no seasonal signal and no monitoring cadence. Use
Sentinel-2 or Landsat when the question is *when*; use NAIP when the question
is *where*, precisely.

STREAMING: YES, AND IT IS THE NORMAL PATH
------------------------------------------
NAIP on the Planetary Computer is published as **Cloud Optimised GeoTIFFs**. A
COG is internally tiled with an index at the front, so an HTTP range request can
fetch just the bytes covering one window. GDAL does this through ``/vsicurl/``
automatically: opening the remote file reads a few kilobytes of header, and a
windowed read pulls only the tiles it overlaps.

A NAIP quarter-quad is roughly 500 MB. The window over one valley is a few
megabytes. **Nothing here downloads a whole tile**, and the same pattern already
runs in :func:`geolens.view._read_decimated` and
:func:`geolens.inundation._accumulate`.

THE CACHE
---------
What gets cached is the **clipped window**, not the source tile. The window is
what is reusable for truthing, it is two orders of magnitude smaller, and it is
already in your project's CRS and grid. Whole tiles would fill a disk to store
mostly ground you are not looking at.

Cache location is ``GEOLENS_NAIP_CACHE`` if set, otherwise ``<output>/naip/``.
Files are named by STAC item id, so a second run over the same valley is a disk
read. The acquisition year travels in the filename and in the result, because
NAIP vintage is the thing that bites: comparing a 2021 image to a 2018 one and
calling the difference change is a real mistake, and the years are two states'
flight schedules apart, not a choice anyone made.

NOT TESTED AGAINST THE LIVE SERVICE
-----------------------------------
Outbound HTTPS was blocked where this was written. The NDVI arithmetic, the
mosaicking, the cache and the band handling are tested against constructed
inputs; the network path is covered by :func:`naip_selftest`, which has to run
somewhere with internet access. Run it once before trusting a result.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .safety import Guard, GeolensError, note, progress

PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
PC_SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token"
NAIP_COLLECTION = "naip"

# NAIP is 4-band in this order. Getting this wrong produces an NDVI that looks
# plausible and is meaningless, which is worse than an error.
BAND_RED = 1
BAND_GREEN = 2
BAND_BLUE = 3
BAND_NIR = 4

CACHE_ENV = "GEOLENS_NAIP_CACHE"

# A 1 m grid grows fast: 10 km square is 100 million cells, and four uint8 bands
# plus a float32 accumulator is ~1.6 GB before anything is written. This is a
# guard rail against an accidental county-wide request, not a hard capability
# limit — raise it if you have the RAM and mean it.
MAX_GRID_CELLS = 400_000_000


@dataclass
class NaipResult:
    ndvi: str | None
    rgb: str | None
    n_items: int
    n_used: int
    years: list[int]
    coverage: float                 # fraction of the AOI with imagery
    cell_size_m: float              # the grid NDVI was built on, NOT NAIP's own
    cache_dir: str
    cached_files: list[str]
    seconds: float
    warnings: list[str] = field(default_factory=list)
    rgbn: str | None = None         # all four bands, for later reference
    sources: str | None = None      # naip_sources.json: provenance + errors
    tiles: list[str] = field(default_factory=list)  # whole tiles downloaded

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


# -- cache --------------------------------------------------------------------


def naip_cache_dir(guard: Guard | None = None,
                   aoi: str | Path | None = None) -> Path:
    """Where NAIP clips and downloaded tiles live. **Beside the DEM.**

    Same reasoning as the routing cache, and the same mistake fixed the same
    way: under the output folder the imagery was thrown away by every new
    ``OUT_DIR`` -- and the output folder changes every release. Imagery is
    about the ground, so it lives with the DEM that describes that ground, in
    the hidden ``.geolens_cache`` folder geolens already keeps there.

    ``GEOLENS_NAIP_CACHE`` overrides. If the DEM's folder is read-only it
    falls back to the output folder, which is where it used to live.
    """
    env = os.environ.get(CACHE_ENV, "").strip()
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env).expanduser())
    if aoi is not None:
        candidates.append(Path(str(aoi)).parent / ".geolens_cache" / "naip")
    guard = guard or Guard.from_env()
    candidates.append(Path(guard.out_dir) / "naip")
    for p in candidates:
        try:
            p.mkdir(parents=True, exist_ok=True)
            probe = p / ".writable"
            probe.touch()
            probe.unlink()
            return p
        except OSError:
            continue
    raise GeolensError("No writable location for the NAIP cache.")


def _download(url: str, dest: Path, timeout: int = 120,
              chunk: int = 1 << 20) -> Path:
    """Download a whole file with Python's own HTTPS stack.

    The fallback for when GDAL's cannot get through. GDAL reads remote COGs
    with its bundled libcurl, which has its OWN certificate store; Python
    uses the operating system's. Behind a corporate proxy that inspects TLS
    -- the normal case on a work Windows machine -- the proxy's root
    certificate is in the Windows store and not in libcurl's, so Python can
    fetch the catalog and GDAL cannot read a single tile. A NAIP quad is a
    few hundred megabytes, which is slow but works, and the result is on
    disk for later reference -- which is also what was asked for.

    Written to a .part file and renamed, so an interrupted download is never
    mistaken for a finished one.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    part = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(
        url, headers={"User-Agent": "geolens/0.1 (+geomorphic analysis)"})
    with urllib.request.urlopen(req, timeout=timeout) as r, \
            part.open("wb") as fh:
        while True:
            buf = r.read(chunk)
            if not buf:
                break
            fh.write(buf)
    part.replace(dest)
    return dest


def _cache_name(item_id: str, year: int | None, kind: str) -> str:
    """Item id plus vintage plus what it holds.

    The year is in the filename deliberately. NAIP vintage differs by state and
    by flight, and a cache that hides it invites comparing a 2021 image with a
    2018 one and calling the difference change.
    """
    y = f"_{year}" if year else ""
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in item_id)
    return f"{safe}{y}_{kind}.tif"


# -- HTTP ---------------------------------------------------------------------


def _get_json(url: str, payload: dict | None = None, timeout: int = 60,
              retries: int = 3) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json",
                         "Accept": "application/geo+json",
                         "User-Agent": "geolens/0.1 (+geomorphic analysis)"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise GeolensError(
        f"Could not reach the Planetary Computer after {retries} attempts.\n"
        f"  url:   {url}\n  error: {type(last).__name__}: {last}\n\n"
        f"The free catalog has no SLA and has had reliability complaints. If "
        f"this persists, the imagery can be downloaded by hand and pointed at "
        f"with the cache directory."
    ) from last


def sign(href: str, timeout: int = 30) -> str:
    """Append an anonymous SAS token if the blob needs one.

    Most NAIP assets are public, but Planetary Computer containers can require a
    token. One is issued anonymously, expires in under an hour, and must not be
    cached — so this is called per asset rather than once per run.
    """
    if "?" in href:
        return href
    try:
        after = href.split("://", 1)[1]
        host, _, rest = after.partition("/")
        account = host.split(".")[0]
        container = rest.split("/", 1)[0]
        tok = _get_json(f"{PC_SAS}/{account}/{container}", timeout=timeout)
        t = tok.get("token")
        return f"{href}?{t}" if t else href
    except Exception:
        return href           # public blob; the plain href is correct


def search_naip(bounds_wgs84: tuple[float, float, float, float],
                start: str | None = None, end: str | None = None,
                limit: int = 100, timeout: int = 60) -> list[dict]:
    """STAC search for NAIP scenes intersecting a bounding box."""
    payload: dict[str, Any] = {
        "collections": [NAIP_COLLECTION],
        "bbox": list(bounds_wgs84),
        "limit": min(limit, 100),
    }
    if start or end:
        payload["datetime"] = f"{start or '2010-01-01'}/{end or '2100-01-01'}"
    doc = _get_json(f"{PC_STAC}/search", payload, timeout=timeout)
    if "features" not in doc:
        raise GeolensError(f"Unexpected STAC reply: {list(doc)[:6]}")
    feats = list(doc["features"])
    # Follow the "next" links. A 1,000 km2 area with five NAIP vintages is
    # more than one page, and the server does not return newest first, so
    # stopping at page one could miss the newest imagery entirely.
    for _ in range(50):
        nxt = next((ln for ln in doc.get("links", []) or []
                    if ln.get("rel") == "next"), None)
        if not nxt or not nxt.get("href"):
            break
        body = nxt.get("body") if (nxt.get("method") or "GET").upper() == "POST" \
            else None
        if body is not None and nxt.get("merge"):
            body = {**payload, **body}
        doc = _get_json(nxt["href"], body, timeout=timeout)
        feats.extend(doc.get("features") or [])
    # Newest first, so a mosaic takes the most recent imagery where scenes
    # overlap rather than whichever the server happened to list first.
    feats.sort(key=lambda f: (f.get("properties", {}).get("datetime") or ""),
               reverse=True)
    return feats


def _item_year(item: dict) -> int | None:
    dt = (item.get("properties") or {}).get("datetime") or ""
    try:
        return int(dt[:4])
    except (ValueError, TypeError):
        return None


# -- the work -----------------------------------------------------------------


def _ndvi(red: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """(NIR - Red) / (NIR + Red), with the degenerate case handled.

    NAIP is 8-bit, so the sum is zero wherever both bands are zero — the black
    collar around a reprojected scene, and genuine shadow. Dividing there gives
    a warning and a nan; returning nan explicitly keeps it out of the composite
    rather than letting 0/0 become an accidental value.
    """
    r = red.astype("float32")
    n = nir.astype("float32")
    denom = n + r
    out = np.full(r.shape, np.nan, dtype="float32")
    ok = denom > 0
    out[ok] = (n[ok] - r[ok]) / denom[ok]
    return np.clip(out, -1.0, 1.0)


def _read_window_onto(href: str, bands: list[int], shape: tuple[int, int],
                      dst_transform, dst_crs, timeout: int = 60
                      ) -> np.ndarray | None:
    """Stream just the bands and window covering a destination grid.

    This is the whole streaming story: ``/vsicurl/`` plus a windowed,
    reprojecting read. GDAL fetches the COG header, works out which internal
    tiles the destination grid needs, and range-requests those. A 500 MB quad
    costs a few megabytes over one valley.

    The grid is passed explicitly rather than as a template raster, because the
    destination is not always an existing file: NDVI is normally built at NAIP's
    own resolution over a coarser DEM's extent.
    """
    import rasterio
    from rasterio.warp import Resampling, reproject

    out = np.zeros((len(bands),) + tuple(shape), dtype="float32")
    # Only remote URLs go through /vsicurl/. A local path — a cached window, or
    # a quad someone downloaded by hand — must open directly; prefixing it gives
    # "Missing url parameter", which reads like a network fault and is not one.
    path = href
    if href.startswith(("http://", "https://")):
        path = f"/vsicurl/{href}"
    with rasterio.open(path) as src:
        for i, b in enumerate(bands):
            if b > src.count:
                raise GeolensError(
                    f"Asset has {src.count} bands; band {b} was requested. "
                    f"NAIP should be 4-band RGB+NIR — check the asset."
                )
            reproject(
                source=rasterio.band(src, b),
                destination=out[i],
                src_transform=src.transform, src_crs=src.crs,
                dst_transform=dst_transform, dst_crs=dst_crs,
                resampling=Resampling.bilinear,
                src_nodata=src.nodata, dst_nodata=0,
            )
    return out


def naip_ndvi(
    aoi_raster: str | Path,
    start: str | None = None,
    end: str | None = None,
    want_rgb: bool = True,
    use_cache: bool = True,
    resolution_m: float | None = 1.0,
    out_prefix: str = "naip",
    timeout: int = 60,
    guard: Guard | None = None,
) -> NaipResult:
    """Build 1 m NDVI (and optionally true colour) over a DEM's extent.

    Parameters
    ----------
    aoi_raster
        A raster defining the **extent and CRS** — normally the DEM.
    resolution_m
        Cell size of the NDVI grid, in the AOI's units. Defaults to 1 m, which
        is NAIP's own resolution, **not** the DEM's.

        This default is the point of the module. Building NDVI on a 10 m DEM's
        grid averages a hundred NAIP pixels into one and throws away exactly
        the resolution that made NAIP worth streaming: a 20 m desert riparian
        corridor is two cells at 10 m and twenty at 1 m. Pass ``None`` to build
        on the AOI's own grid instead — worth doing when the DEM is already
        lidar at 1 m, or when a downstream tool needs identical grids.

        :func:`geolens.riparian.riparian_band` resamples HAND and streams onto
        whatever grid the NDVI is on, so a finer NDVI does not need a finer DEM.
    start, end
        Restrict to a flight window. Leave both unset to take the most recent
        imagery available.

    Scenes are **mosaicked newest-first**, not averaged. Averaging across NAIP
    vintages blends a 2021 flight with a 2018 one and produces an image of no
    particular date; a mosaic keeps each pixel to a single acquisition, and the
    contributing years come back in ``years`` so a mixed-vintage result is
    visible rather than silent.
    """
    import rasterio

    from .raster import gtiff_profile

    t0 = time.monotonic()
    guard = guard or Guard.from_env()
    warnings: list[str] = []

    tpl_path = str(guard.read_path(aoi_raster))
    with rasterio.open(tpl_path) as tpl:
        if tpl.crs is None:
            raise GeolensError(f"{tpl_path} has no CRS.")
        b = tpl.bounds
        profile = tpl.profile.copy()
        shape = (tpl.height, tpl.width)
        transform = tpl.transform
        crs = tpl.crs
        # Metres per CRS unit. The AOI is normally the DEM, and on EPSG:6529
        # that is US survey feet.
        try:
            mpu = float(tpl.crs.linear_units_factor[1])
        except Exception:                                    # noqa: BLE001
            mpu = 1.0
        aoi_cell_m = float(abs(tpl.transform.a)) * mpu
        import pyproj

        tf = pyproj.Transformer.from_crs(tpl.crs, "EPSG:4326", always_xy=True)
        xs, ys = tf.transform([b.left, b.right], [b.bottom, b.top])
        bbox = (min(xs), min(ys), max(xs), max(ys))

    # Resample the destination grid if asked. Anchored at the AOI's top-left and
    # rounded outward, so the NDVI covers the AOI rather than clipping it.
    #
    # resolution_m is METRES and the AOI's extent is in CRS units. They were
    # divided straight into each other, so on a foot grid a "1 m" NDVI was
    # built at 1 ft -- 10.76x the cells. Eagle Creek, 40.3 km2, came out at
    # ~434 million cells, tripped the guard below, and raised before a single
    # request was made. The notebook caught it, printed "NAIP fetch failed",
    # and suggested checking the network. The network was never reached.
    if resolution_m is not None and abs(resolution_m - aoi_cell_m) > 1e-6:
        from rasterio.transform import from_origin

        res_crs = float(resolution_m) / mpu
        w = int(math.ceil((b.right - b.left) / res_crs))
        h = int(math.ceil((b.top - b.bottom) / res_crs))
        cells = w * h
        if cells > MAX_GRID_CELLS:
            raise GeolensError(
                f"A {resolution_m:g} m grid over this extent is {w} x {h} = "
                f"{cells/1e6:.0f} million cells, more than geolens will build "
                f"in one pass ({MAX_GRID_CELLS/1e6:.0f} M). Clip the AOI, or "
                f"pass a coarser resolution_m. (This is a size limit, not a "
                f"network problem — nothing has been requested yet.)"
            )
        shape = (h, w)
        transform = from_origin(b.left, b.top, res_crs, res_crs)
        profile.update(width=w, height=h, transform=transform)
        note(f"NDVI grid {w} x {h} at {resolution_m:g} m "
             f"(AOI is {aoi_cell_m:.3g} m)")
    # TRUE metres, for the report and for every comparison below.
    cell_size = float(abs(transform.a)) * mpu

    if cell_size > 3.0:
        warnings.append(
            f"NDVI is being built at {cell_size:.3g} m, but NAIP is 0.6-1 m. "
            f"Every output cell averages ~{(cell_size/1.0)**2:.0f} NAIP pixels, "
            f"which discards the resolution NAIP was streamed for. Pass "
            f"resolution_m=1.0 unless a downstream tool needs this grid."
        )

    try:
        items = search_naip(bbox, start, end, timeout=timeout)
    except Exception as exc:
        # Leave a record before raising. Until now a failed search wrote
        # nothing at all, so the only trace of it was one line in a notebook
        # cell that had since been re-run.
        try:
            od = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
            od.mkdir(parents=True, exist_ok=True)
            with open(od / "naip_sources.json", "w", encoding="utf-8") as fh:
                json.dump({"aoi": tpl_path, "bbox_wgs84": list(bbox),
                           "stage": "catalog search", "catalog": PC_STAC,
                           "error": f"{type(exc).__name__}: {exc}"},
                          fh, indent=1, default=str)
        except Exception:                                    # noqa: BLE001
            pass
        raise
    note(f"{len(items)} NAIP scenes intersect this extent")
    if not items:
        warnings.append(
            "No NAIP scenes found. NAIP is the conterminous US only, and the "
            "date filter may be too narrow."
        )

    cache = naip_cache_dir(guard, aoi=tpl_path)
    note(f"NAIP cache: {cache}")
    # Identifies the destination grid: extent, shape and CRS. Short hash rather
    # than the numbers themselves, so the filename stays a filename.
    import hashlib

    grid_key = hashlib.sha1(
        f"{tuple(np.round(np.asarray(transform)[:6], 6))}|{shape}|{crs}"
        .encode("utf-8")
    ).hexdigest()[:10]
    ndvi_acc = np.full(shape, np.nan, dtype="float32")
    rgb_acc = np.zeros((3,) + shape, dtype="float32") if want_rgb else None
    # All four bands, kept. NDVI and RGB are derived products; the imagery
    # itself is what is worth having on disk later -- a different index, a
    # different threshold, or just looking at it -- and until now the NIR band
    # was read, used once and thrown away.
    rgbn_acc = np.zeros((4,) + shape, dtype="float32")
    years: list[int] = []
    cached: list[str] = []
    tiles: list[str] = []
    sources: list[dict] = []
    used = 0

    for item in progress(items, label="streaming NAIP"):
        href = ((item.get("assets") or {}).get("image") or {}).get("href")
        if not href:
            continue
        year = _item_year(item)
        # The DESTINATION GRID is part of the cache key, not just the
        # resolution. What is cached is a clip already reprojected onto this
        # run's grid, so two different AOIs at the same resolution that touch
        # the same NAIP quad collide on the filename -- and the second one gets
        # the first one's pixels, correctly georeferenced to the wrong place,
        # with no warning and no shape mismatch to catch it.
        cpath = cache / _cache_name(item.get("id", "item"), year,
                                    f"w{cell_size:g}m_{grid_key}")
        bands = [BAND_RED, BAND_GREEN, BAND_BLUE, BAND_NIR]
        how = "cache"
        try:
            if use_cache and cpath.exists():
                with rasterio.open(str(cpath)) as src:
                    arr = src.read().astype("float32")
            else:
                try:
                    arr = _read_window_onto(sign(href, timeout), bands,
                                            shape, transform, crs, timeout)
                    how = "streamed"
                except Exception as stream_err:              # noqa: BLE001
                    # GDAL could not read the remote file. Try Python's HTTPS
                    # stack instead, which trusts what the OS trusts -- see
                    # _download. Keep the whole tile: it is on disk for later
                    # reference, and a second run reads it locally.
                    note(f"  streaming failed for {item.get('id', '?')} "
                         f"({type(stream_err).__name__}); downloading the "
                         f"tile instead")
                    tile = cache / "tiles" / _cache_name(
                        item.get("id", "item"), year, "tile")
                    _download(sign(href, timeout), tile, timeout=max(
                        timeout, 300))
                    tiles.append(str(tile))
                    arr = _read_window_onto(str(tile), bands, shape,
                                            transform, crs, timeout)
                    how = "downloaded"
                if use_cache:
                    prof = gtiff_profile(profile, dtype="uint8", nodata=0,
                                         count=4)
                    with rasterio.open(str(cpath), "w", **prof) as dst:
                        dst.write(np.clip(arr, 0, 255).astype("uint8"))
                    cached.append(str(cpath))
        except Exception as e:  # noqa: BLE001 - one bad scene must not stop the run
            # The FULL error, not 110 characters of it. A truncated SSL error
            # is the difference between "fix your certificate bundle" and
            # "the service is down", and the user has to be able to tell.
            warnings.append(f"skipped {item.get('id', '?')}: "
                            f"{type(e).__name__}: {e}")
            sources.append({"id": item.get("id"), "year": year,
                            "used": False,
                            "error": f"{type(e).__name__}: {e}"})
            continue
        sources.append({
            "id": item.get("id"), "year": year,
            "datetime": (item.get("properties") or {}).get("datetime"),
            "href": href.split("?", 1)[0],      # never store a SAS token
            "how": how, "used": True,
        })

        red, nir = arr[0], arr[3]
        nd = _ndvi(red, nir)
        # Newest-first mosaic: only fill where nothing has been written yet.
        fill = np.isnan(ndvi_acc) & np.isfinite(nd) & ((red + nir) > 0)
        ndvi_acc[fill] = nd[fill]
        for i in range(4):
            rgbn_acc[i][fill] = arr[i][fill]
        if rgb_acc is not None:
            for i in range(3):
                rgb_acc[i][fill] = arr[i][fill]
        if year:
            years.append(year)
        used += 1
        if not np.isnan(ndvi_acc).any():
            note("extent fully covered; stopping early")
            break

    coverage = float(np.isfinite(ndvi_acc).mean())
    if 0 < coverage < 0.98:
        warnings.append(
            f"NAIP covers {coverage:.0%} of the extent. Gaps become nodata in "
            f"the NDVI, and anything derived from it inherits the hole."
        )
    uniq_years = sorted(set(years))
    if len(uniq_years) > 1:
        warnings.append(
            f"Mosaic mixes NAIP vintages {uniq_years}. Each pixel is from a "
            f"single flight, but the image as a whole is not one date — do not "
            f"read differences across a seam as change."
        )

    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)

    ndvi_path = rgb_path = rgbn_path = None
    if used:
        ndvi_path = str(out_dir / "ndvi.tif")
        with rasterio.open(ndvi_path, "w",
                           **gtiff_profile(profile, dtype="float32",
                                           nodata=-9999.0)) as dst:
            dst.write(np.nan_to_num(ndvi_acc, nan=-9999.0), 1)
        if rgb_acc is not None:
            rgb_path = str(out_dir / "rgb.tif")
            with rasterio.open(rgb_path, "w",
                               **gtiff_profile(profile, dtype="uint8",
                                               nodata=0, count=3)) as dst:
                dst.write(np.clip(rgb_acc, 0, 255).astype("uint8"))
        # The imagery itself: red, green, blue, near-infrared, mosaicked
        # newest-first onto this grid. Band descriptions are set so a GIS
        # shows "nir" rather than "Band 4", because the band order is the
        # one fact everything downstream depends on.
        rgbn_path = str(out_dir / "naip_rgbn.tif")
        with rasterio.open(rgbn_path, "w",
                           **gtiff_profile(profile, dtype="uint8",
                                           nodata=0, count=4)) as dst:
            dst.write(np.clip(rgbn_acc, 0, 255).astype("uint8"))
            for i, name in enumerate(("red", "green", "blue", "nir"), 1):
                dst.set_band_description(i, name)

    # Provenance, whether or not anything was used. What was searched, what
    # came back, which scenes were used and how, and the full error for any
    # that were not -- so the next "the fetcher is broken" comes with a file
    # that says which part broke.
    sources_path = str(out_dir / "naip_sources.json")
    with open(sources_path, "w", encoding="utf-8") as fh:
        json.dump({
            "aoi": tpl_path, "bbox_wgs84": list(bbox),
            "start": start, "end": end,
            "grid": {"shape": list(shape), "cell_size_m": cell_size,
                     "crs": str(crs)},
            "n_items": len(items), "n_used": used, "years": uniq_years,
            "coverage": coverage, "cache_dir": str(cache),
            "tiles_downloaded": tiles, "scenes": sources,
            "warnings": warnings,
        }, fh, indent=1, default=str)

    return NaipResult(
        ndvi=ndvi_path, rgb=rgb_path, n_items=len(items), n_used=used,
        years=uniq_years, coverage=coverage, cell_size_m=cell_size,
        cache_dir=str(cache),
        cached_files=cached, seconds=time.monotonic() - t0, warnings=warnings,
        rgbn=rgbn_path, sources=sources_path, tiles=tiles,
    )


MAX_MOSAIC_CELLS = 3_000_000_000


def _aoi_geometry(aoi: str, guard: Guard):
    """(polygon in its CRS, CRS, is_raster) for a raster's extent or the union
    of a polygon layer."""
    import rasterio
    from shapely.geometry import box

    path = str(guard.read_path(aoi))
    if Path(path).suffix.lower() in (".tif", ".tiff", ".img", ".vrt", ".asc"):
        with rasterio.open(path) as src:
            if src.crs is None:
                raise GeolensError(f"{path} has no CRS.")
            return box(*src.bounds), src.crs, True
    import geopandas as gpd

    g = gpd.read_file(path)
    if g.crs is None:
        raise GeolensError(f"{path} has no CRS.")
    geom = (g.geometry.union_all() if hasattr(g.geometry, "union_all")
            else g.geometry.unary_union)
    return geom, g.crs, False


def naip_tiles(
    aoi: str | Path,
    start: str | None = None,
    end: str | None = None,
    resolution_m: float = 1.0,
    true_colour: bool = True,
    keep_imagery: bool = True,
    timeout: int = 60,
    guard: Guard | None = None,
) -> dict[str, Any]:
    """NAIP over an area, scene by scene: NDVI and imagery tiles, and mosaics.

    Everything goes in the output folder (``GEOLENS_OUT``), which the ArcGIS
    tool lets you choose:

    ``ndvi_tiles/``        one NDVI GeoTIFF per NAIP scene, clipped to the area
    ``imagery_tiles/``     the same scenes' red, green, blue and near-infrared
    ``ndvi_mosaic.tif``    all tiles, newest imagery on top
    ``true_colour.tif``    the same for red, green, blue
    ``naip_sources.json``  which scenes, which years, and why any were skipped

    ``aoi`` is a raster (its extent and CRS, normally the DEM) or a polygon
    layer. The grid is ``resolution_m`` true metres in the AOI's CRS. Scenes
    are taken newest first, and an older scene is skipped entirely when newer
    ones already cover its part of the area, so a five-vintage area costs one
    vintage of downloads. A tile already on disk from an earlier run on the
    same grid is read from disk.

    Unlike :func:`naip_ndvi` there is no 400-million-cell limit: memory is one
    scene plus a byte a cell of the area, and the mosaic is written window by
    window.
    """
    import hashlib

    import pyproj
    from shapely.geometry import shape as _shape
    from shapely.ops import transform as _transform

    from .mosaic import Mosaic, grid_over, read_onto, write_tile

    t0 = time.monotonic()
    guard = guard or Guard.from_env()
    warnings: list[str] = []
    geom, crs, _ = _aoi_geometry(str(aoi), guard)
    pcrs = pyproj.CRS.from_user_input(crs)
    if pcrs.is_geographic:
        raise GeolensError(
            "The area is in longitude/latitude. Give a DEM or a polygon in a "
            "projected CRS (UTM, State Plane): the grid is built in metres.")
    try:
        mpu = float(pcrs.axis_info[0].unit_conversion_factor)
    except Exception:                                        # noqa: BLE001
        mpu = 1.0
    grid = grid_over(geom.bounds, crs, float(resolution_m), mpu)
    if grid.cells > MAX_MOSAIC_CELLS:
        raise GeolensError(
            f"A {resolution_m:g} m grid over this area is {grid.width:,} x "
            f"{grid.height:,} = {grid.cells / 1e9:.1f} billion cells. Use a "
            f"coarser resolution_m or a smaller area (clip to a watershed).")
    to_aoi = pyproj.Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    to_wgs = pyproj.Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    bbox = _transform(to_wgs.transform, geom).bounds
    note(f"NAIP grid {grid.width:,} x {grid.height:,} at {resolution_m:g} m")

    items = search_naip(bbox, start, end, timeout=timeout)
    note(f"{len(items)} NAIP scenes intersect the area")
    if not items:
        warnings.append("No NAIP scenes found. NAIP is the conterminous US "
                        "only, and the date filter may be too narrow.")

    out = Path(guard.out_dir)
    ndvi_dir = Path(guard.write_path("ndvi_tiles"))
    img_dir = Path(guard.write_path("imagery_tiles"))
    ndvi_dir.mkdir(parents=True, exist_ok=True)
    if keep_imagery:
        img_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{crs}|{grid.res:.6f}|{grid.left:.3f}|{grid.top:.3f}|"
                       f"{grid.width}|{grid.height}".encode()).hexdigest()[:8]
    ndvi_path = str(out / "ndvi_mosaic.tif")
    rgb_path = str(out / "true_colour.tif") if true_colour else None
    ndvi_m = Mosaic(ndvi_path, grid, "float32", 1, -9999.0, ["ndvi"],
                    predictor=3)
    rgb_m = (Mosaic(rgb_path, grid, "uint8", 3, 0, ["red", "green", "blue"])
             if true_colour else None)

    covered = None
    sources: list[dict] = []
    years: list[int] = []
    tiles: list[str] = []
    used = 0
    try:
        for idx, item in enumerate(progress(items, label="NAIP scenes")):
            year = _item_year(item)
            iid = str(item.get("id", "item"))
            try:
                foot = _transform(to_aoi.transform, _shape(item["geometry"]))
                part = foot.intersection(geom)
            except Exception:                                # noqa: BLE001
                part = geom
            if part.is_empty:
                continue
            if covered is not None:
                new = part.difference(covered)
                if new.area < 0.01 * part.area:
                    sources.append({"id": iid, "year": year, "used": False,
                                    "why": "covered by newer imagery"})
                    continue
            window = grid.window_for(part.bounds)
            if window is None:
                continue
            base = f"{iid}_{year or 'na'}_{key}"
            ndvi_tile = ndvi_dir / f"{base}_ndvi.tif"
            img_tile = img_dir / f"{base}_rgbn.tif"
            how = "disk"
            try:
                if img_tile.exists():
                    arr, _ = read_onto(str(img_tile), [1, 2, 3, 4], grid, window)
                else:
                    href = ((item.get("assets") or {}).get("image") or {}).get("href")
                    if not href:
                        continue
                    try:
                        arr, _ = read_onto(sign(href, timeout), [1, 2, 3, 4],
                                           grid, window)
                        how = "streamed"
                    except Exception as stream_err:          # noqa: BLE001
                        note(f"  streaming failed for {iid} "
                             f"({type(stream_err).__name__}); downloading it")
                        whole = img_dir / "source" / _cache_name(iid, year, "tile")
                        _download(sign(href, timeout), whole,
                                  timeout=max(timeout, 300))
                        tiles.append(str(whole))
                        arr, _ = read_onto(str(whole), [1, 2, 3, 4], grid,
                                           window)
                        how = "downloaded"
            except Exception as e:                           # noqa: BLE001
                warnings.append(f"skipped {iid}: {type(e).__name__}: {e}")
                sources.append({"id": iid, "year": year, "used": False,
                                "error": f"{type(e).__name__}: {e}"})
                continue
            red, nir = arr[0], arr[3]
            valid = np.isfinite(red) & np.isfinite(nir) & ((red + nir) > 0)
            nd = np.where(valid, _ndvi(np.nan_to_num(red), np.nan_to_num(nir)),
                          np.nan).astype("float32")
            write_tile(ndvi_tile, grid, window, np.nan_to_num(nd, nan=-9999.0),
                       "float32", -9999.0, ["ndvi"])
            if keep_imagery and not img_tile.exists():
                write_tile(img_tile, grid, window,
                           np.clip(np.nan_to_num(arr), 0, 255), "uint8", 0,
                           ["red", "green", "blue", "nir"])
            ndvi_m.add(window, nd[None], valid)
            if rgb_m is not None:
                rgb_m.add(window, np.clip(np.nan_to_num(arr[:3]), 0, 255), valid)
            covered = part if covered is None else covered.union(part)
            used += 1
            if year:
                years.append(year)
            sources.append({"id": iid, "year": year, "used": True, "how": how,
                            "ndvi_tile": str(ndvi_tile),
                            "href": (((item.get("assets") or {}).get("image")
                                      or {}).get("href") or "").split("?", 1)[0]})
            if covered.buffer(grid.res).contains(geom):
                note("area fully covered; older imagery not needed")
                for rest in items[idx + 1:]:
                    sources.append({"id": rest.get("id"),
                                    "year": _item_year(rest), "used": False,
                                    "why": "covered by newer imagery"})
                break
        coverage = ndvi_m.coverage
    finally:
        ndvi_m.close()
        if rgb_m is not None:
            rgb_m.close()

    uniq = sorted(set(years))
    if len(uniq) > 1:
        warnings.append(
            f"The mosaic mixes NAIP vintages {uniq}. Each cell is from one "
            f"flight, but the image as a whole is not one date: do not read "
            f"a difference across a seam as change.")
    if 0 < coverage < 0.98 and grid.cells:
        warnings.append(f"NAIP covers {coverage:.0%} of the grid.")
    sources_path = str(out / "naip_sources.json")
    with open(sources_path, "w", encoding="utf-8") as fh:
        json.dump({"aoi": str(aoi), "bbox_wgs84": list(bbox), "start": start,
                   "end": end, "grid": {"width": grid.width,
                                        "height": grid.height,
                                        "cell_size_m": grid.cell_m,
                                        "crs": str(crs)},
                   "n_items": len(items), "n_used": used, "years": uniq,
                   "coverage": coverage, "scenes": sources,
                   "warnings": warnings}, fh, indent=1, default=str)
    note(f"NAIP: {used} scene(s), years {uniq}, {coverage:.0%} of the grid")
    return {
        "ndvi": ndvi_path if used else None,
        "rgb": rgb_path if used else None,
        "ndvi_tiles_dir": str(ndvi_dir),
        "imagery_tiles_dir": str(img_dir) if keep_imagery else None,
        "sources": sources_path, "n_items": len(items), "n_used": used,
        "years": uniq, "coverage": coverage, "cell_size_m": grid.cell_m,
        "tiles_downloaded": tiles, "seconds": time.monotonic() - t0,
        "warnings": warnings,
    }


def naip_selftest(timeout: int = 60) -> dict[str, Any]:
    """Check the catalog is reachable and NAIP is still 4-band RGB+NIR.

    Queries a small box in southern Arizona. Reports the asset keys, the band
    count and the band order, because the NDVI arithmetic assumes band 1 is red
    and band 4 is near-infrared. If that ever changes, NDVI keeps computing and
    silently means nothing — which is exactly the failure worth a selftest.
    """
    out: dict[str, Any] = {"ok": False}
    bbox = (-111.0, 32.1, -110.9, 32.2)
    try:
        items = search_naip(bbox, timeout=timeout)
        out["n_items"] = len(items)
        if items:
            it = items[0]
            out["item_id"] = it.get("id")
            out["datetime"] = (it.get("properties") or {}).get("datetime")
            out["assets"] = sorted((it.get("assets") or {}).keys())
            out["gsd"] = (it.get("properties") or {}).get("gsd")
            href = ((it.get("assets") or {}).get("image") or {}).get("href")
            if href:
                import rasterio

                with rasterio.open(f"/vsicurl/{sign(href, timeout)}") as src:
                    out["band_count"] = src.count
                    out["dtype"] = src.dtypes[0]
                    out["crs"] = str(src.crs)
                    out["res"] = [abs(src.res[0]), abs(src.res[1])]
                    out["overviews"] = src.overviews(1)
                    out["is_tiled"] = src.profile.get("tiled", False)
                    out["band_descriptions"] = list(src.descriptions)
                out["four_band"] = out.get("band_count") == 4
                out["streamable"] = bool(out.get("is_tiled"))
            out["ok"] = bool(out.get("four_band"))
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    return out
