"""Observed flood extent, for checking a delineated floodplain against reality.

The problem this solves: floodplain delineation from a DEM has had no
independent check. Bank tops come from a slope break, the floodplain climbs
until valley confinement stops it, and nothing in that chain has ever been
compared against where water actually went.

WHERE THE DATA COMES FROM
-------------------------
**Copernicus Global Flood Monitoring (GFM)**, via the EODC STAC API. Sentinel-1
derived, 20 m, 2015 to present, global, CC BY 4.0, and — unusually — no
registration or API key on the STAC route.

The reason it is GFM and not something easier is one detail in the product
definition. ``ensemble_flood_extent`` identifies pixels covered by floodwater
and **excludes pixels that are normally wet**, using a reference water mask
built from a five-year Sentinel-1 time series at an 85-90% occurrence
threshold. So the layer is literally the anomalous, overbank water. That is the
definition of floodplain inundation. Every other ready-made product gives you
either total water or a model.

WHY NOT THE ALTERNATIVES — three of them are circular
------------------------------------------------------
This matters more than convenience, because a circular check looks like a
passing check:

* **NOAA/OWP Flood Inundation Mapping** is HAND-based inundation driven by
  modelled discharge. HAND comes from a DEM. Validating a DEM-derived
  floodplain against it compares a model to a model.
* **FEMA NFHL** is the regulatory 1% AEP floodplain — the output of hydraulic
  modelling over a DEM, often decades old, and large stretches are approximate
  Zone A with no detailed study. It is a legal product, not an observation.
* **OPERA DSWx-S1** masks out every pixel whose HAND exceeds a threshold
  (class 250). It is partly DEM-constrained by construction. It is a reasonable
  cross-check but it is not independent, and this module says so rather than
  quietly using it.

**JRC Global Surface Water** is not circular, and is supported below as a
secondary floor, but it is weak for this purpose: occurrence is the fraction of
*cloud-free Landsat observations* that were water, and floods are cloudy. A
two-day flood has almost no chance of being caught. Its ``extent`` layer is a
sound hard lower bound -- the floodplain must at least contain it -- and
nothing more.

WHAT THIS CANNOT TELL YOU
-------------------------
Four limitations, each of which changes how the numbers should be read:

1. **Flashy streams miss their own peak.** Sentinel-1 revisit over the US is
   6-12 days. Small headwater streams -- exactly where bank slope breaks and
   valley confinement are most interesting -- rise and fall inside a day. GFM
   gives a lower bound on inundation, never the peak.
2. **The exclusion mask removes the confined-valley case specifically.** "Strong
   topography" and "radar shadow" are explicit exclusion categories, and a
   narrow steep-sided valley is where SAR layover and shadow are worst. The
   reaches you most want to check are the reaches most often unobservable. This
   is why every number here is reported alongside its valid-pixel fraction.
3. **Forested floodplains read as dry.** C-band VV barely penetrates dense
   canopy. Eastern US floodplains are often forested. GFM flags dense vegetation
   in its exclusion mask, so it tells you rather than lying, but the signal is
   thin there.
4. **20 m pixels on a 50 m floodplain is two or three pixels across.** Expect
   this to work on floodplains of 100 m and up, and to be edge-effect dominated
   below that.

ARID AND INTERMITTENT SYSTEMS: THIS IS THE WRONG TOOL
------------------------------------------------------
If your streams are ephemeral or intermittent -- the arid Southwest, the Great
Basin, most of the interior West -- do not build a validation on this. Two
independent problems compound, and the second one is worse than it looks.

**The flow is almost never there when the satellite is.** A monsoon flood in an
arroyo peaks and recedes in hours. Sentinel-1 revisit is 6-12 days. The chance
that an overpass coincides with flow is roughly hours divided by days, which is
a couple of percent per event -- and that is before cloud, look angle and
ascending/descending coverage. Over a decade you may catch a handful of the
largest events on the largest systems and nothing at all on the tributaries.

**A dry sand bed looks like water to radar.** This is the dangerous one, because
it fails in the direction of a confident wrong answer. Open water is dark in SAR
because a smooth surface reflects the pulse away from the sensor. A dry, flat,
sandy or fine-gravel channel bed is also smooth at C-band and also reads dark.
GFM lists exactly this under its exclusion categories -- "water look-alikes
(flat impervious, sandy soil)". So in a desert wash the algorithm either flags
the channel as excluded, which removes the very corridor you wanted to check, or
it does not, and a bone-dry arroyo reads as flooded.

Either way the answer is not about water.

**What actually maps a floodplain in an arid system: the vegetation.** Where
water is the limiting factor, the floodplain is where the phreatophytes are --
mesquite, cottonwood, willow, tamarisk rooted into the alluvial aquifer. That
band is far more stable and far more observable than the flow itself, it is
visible in any growing-season optical scene, and it marks the active alluvial
valley rather than one event's high-water line. Sentinel-2 NDVI at 10 m, or
LANDFIRE existing-vegetation riparian classes at 30 m, are the sources. geolens
does not implement that yet; it is the arid-region equivalent of this module
and it should be built before anyone in the Southwest relies on flood extent.

Until then, in ephemeral country: use this module to find the rare large events
if you want them, read ``arid_signature`` in the result, and calibrate from
field knowledge through :mod:`geolens.learn.loop` instead. Expert labels are a
better source than a satellite that was looking somewhere else.

THE METRIC IS ONE-SIDED, ON PURPOSE
-----------------------------------
Observed flood extent is **not** a ground-truth floodplain polygon, and scoring
it as if it were will actively damage a calibration.

* **Recall is meaningful**: of the pixels observed to flood, how many fall
  inside the delineated floodplain? A miss there is a real error.
* **Precision and IoU are not, and this module refuses to report them.** A
  correct floodplain legitimately extends far beyond any observed extent --
  no single event fills a floodplain, and SAR could not see much of it anyway.
  Penalising that drives thresholds toward under-delineation.
* Floodplain area that was observable and never seen wet is reported as
  ``unconfirmed``, which is what it is: not evidence of error.

The same asymmetry governs labels for the active-learning loop. Observed flood
pixels are positives. Ground above valley confinement is a negative. Everything
else is **unlabelled, not negative** -- treating unobserved as dry is the single
most likely way to silently bias calibration toward too-narrow floodplains.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .safety import Guard, GeolensError, note, progress

# -- services -----------------------------------------------------------------

GFM_STAC = "https://stac.eodc.eu/api/v1"
GFM_COLLECTION = "GFM"

# Asset keys on a GFM item. `ensemble_*` are the multi-algorithm consensus; the
# per-algorithm `dlr_*`, `list_*`, `tuw_*` assets are the inputs to it.
ASSET_FLOOD = "ensemble_flood_extent"      # overbank water ONLY
ASSET_WATER = "ensemble_water_extent"      # flood + reference water
ASSET_EXCLUSION = "exclusion_mask"         # where the observation is not valid
ASSET_LIKELIHOOD = "ensemble_likelihood"

# JRC Global Surface Water v1.5, 1984-2024, 30 m. Public bucket, no auth.
# 10x10 degree tiles named by their NORTH-WEST corner.
GSW_BUCKET = ("https://s3.waw4-1.cloudferro.com/swift/v1/global-surface-water/"
              "download2024/Aggregated/VER1-5")

# Value conventions. Verified by `inundation_selftest`, which reports the actual
# unique values it finds -- run it before trusting these on a new product
# version, because a silent encoding change would invert the whole result.
FLOOD_TRUE = "> 0"          # any positive value in the flood extent band
EXCLUDED_TRUE = "> 0"       # any positive value in the exclusion mask


@dataclass
class InundationResult:
    flood_envelope: str          # union of observed flood extent, as a raster
    valid_mask: str              # where at least one observation was usable
    polygons: str                # the envelope, vectorised
    n_scenes: int
    n_scenes_used: int
    flooded_km2: float
    valid_fraction: float        # of the AOI, how much was ever observable
    date_range: tuple[str, str]
    arid_signature: bool          # looks like an ephemeral system: read the docs
    source: str
    seconds: float
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class ValidationResult:
    recall: float | None           # observed flood inside the floodplain
    observed_flood_km2: float
    captured_km2: float
    missed_km2: float
    unconfirmed_km2: float         # floodplain area observable but never wet
    valid_fraction: float
    floodplain_km2: float
    verdict: str
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


# -- HTTP ---------------------------------------------------------------------


def _post_json(url: str, payload: dict, timeout: int = 60,
               retries: int = 3) -> dict:
    """POST JSON and parse the reply.

    urllib rather than requests, for the same reason as the geology module: the
    core package does not depend on requests, and a network call should not be
    the thing that makes an install fail.
    """
    body = json.dumps(payload).encode("utf-8")
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
        f"Could not reach the flood-extent service after {retries} attempts.\n"
        f"  url:   {url}\n"
        f"  error: {type(last).__name__}: {last}\n\n"
        f"EODC hosts this without an SLA. If your network blocks outbound "
        f"HTTPS, download the GFM COGs separately and pass them in."
    ) from last


def search_scenes(bounds_wgs84: tuple[float, float, float, float],
                  start: str, end: str, limit: int = 500,
                  timeout: int = 60) -> list[dict]:
    """STAC search for GFM scenes intersecting a bounding box in a date range.

    Paged: STAC returns a `next` link when there is more, and a flood archive
    over a decade for one catchment is easily more than one page.
    """
    items: list[dict] = []
    payload = {
        "collections": [GFM_COLLECTION],
        "bbox": list(bounds_wgs84),
        "datetime": f"{start}/{end}",
        "limit": min(limit, 100),
    }
    url = f"{GFM_STAC}/search"
    seen_pages = 0
    while url and seen_pages < 50:
        doc = _post_json(url, payload, timeout=timeout)
        got = doc.get("features", [])
        items.extend(got)
        if len(items) >= limit:
            break
        nxt = None
        for link in doc.get("links", []):
            if link.get("rel") == "next":
                nxt = link
                break
        if not nxt:
            break
        url = nxt.get("href", "")
        payload = nxt.get("body", payload)
        seen_pages += 1
    return items[:limit]


# -- raster accumulation ------------------------------------------------------


def _accumulate(items: list[dict], template: str, asset: str,
                timeout: int = 60
                ) -> tuple[np.ndarray, int, list[str], np.ndarray]:
    """Union one asset across scenes, reprojected onto a template grid.

    Accumulates into a single array rather than stacking, because a decade of
    scenes over a catchment would not fit in memory and the union is all that
    is wanted. GFM is on the Equi7Grid, which is nobody's project CRS, so every
    scene is warped onto the template's grid on the way in.
    """
    import rasterio
    from rasterio.warp import Resampling, reproject

    with rasterio.open(template) as tpl:
        out = np.zeros((tpl.height, tpl.width), dtype=bool)
        # Where any scene actually reached. Without this there is no way to
        # tell "looked at and clear" from "never looked at", and the two were
        # being reported identically.
        seen = np.zeros((tpl.height, tpl.width), dtype=bool)
        dst_transform = tpl.transform
        dst_crs = tpl.crs
        shape = (tpl.height, tpl.width)

    used = 0
    warnings: list[str] = []
    for item in progress(items, label=f"reading {asset}"):
        href = (item.get("assets", {}).get(asset, {}) or {}).get("href")
        if not href:
            continue
        # /vsicurl/ lets GDAL range-read the COG instead of downloading it all.
        path = href if href.startswith("/vsicurl/") else f"/vsicurl/{href}"
        try:
            with rasterio.open(path) as src:
                buf = np.zeros(shape, dtype="uint8")
                cover = np.zeros(shape, dtype="uint8")
                reproject(
                    source=rasterio.band(src, 1),
                    destination=buf,
                    src_transform=src.transform, src_crs=src.crs,
                    dst_transform=dst_transform, dst_crs=dst_crs,
                    resampling=Resampling.nearest,
                )
                # A second warp of a constant-1 source gives this scene's
                # footprint on the template grid: 1 where the scene covers,
                # 0 outside it.
                reproject(
                    source=np.ones((src.height, src.width), dtype="uint8"),
                    destination=cover,
                    src_transform=src.transform, src_crs=src.crs,
                    dst_transform=dst_transform, dst_crs=dst_crs,
                    resampling=Resampling.nearest,
                )
            out |= buf > 0
            seen |= cover > 0
            used += 1
        except Exception as e:  # noqa: BLE001 - one bad scene must not stop a decade
            warnings.append(f"skipped {item.get('id', '?')}: "
                            f"{type(e).__name__}: {str(e)[:120]}")
    return out, used, warnings, seen


# -- public -------------------------------------------------------------------


def fetch_inundation(
    aoi_raster: str | Path,
    start: str = "2015-01-01",
    end: str | None = None,
    out_prefix: str = "inundation",
    limit: int = 500,
    timeout: int = 60,
    guard: Guard | None = None,
) -> InundationResult:
    """Build an observed-flood envelope over an area, from the GFM archive.

    Parameters
    ----------
    aoi_raster
        A raster defining the grid and extent — normally the DEM the floodplain
        was delineated from, so everything lines up cell for cell.
    start, end
        Date range. The default starts at the beginning of the GFM archive,
        because a floodplain envelope wants **every** event, not one. A single
        scene tells you about one flood; the union over a decade starts to
        approximate where water goes.

    Returns rasters of the flood envelope and of the valid-observation mask.
    The second one is not optional bookkeeping: without it, a low recall cannot
    be distinguished from a reach that SAR never managed to see.
    """
    import geopandas as gpd
    import rasterio
    from rasterio import features as rfeat
    from shapely.geometry import shape as shp_shape

    from .raster import gtiff_profile

    t0 = time.monotonic()
    guard = guard or Guard.from_env()
    end = end or time.strftime("%Y-%m-%d")
    warnings: list[str] = []

    tpl_path = guard.read_path(aoi_raster)
    with rasterio.open(str(tpl_path)) as tpl:
        if tpl.crs is None:
            raise GeolensError(f"{tpl_path} has no CRS; cannot locate it on Earth.")
        bounds = tpl.bounds
        profile = tpl.profile.copy()
        cell_area_km2 = abs(tpl.res[0] * tpl.res[1]) / 1e6
        # STAC wants WGS84 degrees.
        import pyproj

        tf = pyproj.Transformer.from_crs(tpl.crs, "EPSG:4326", always_xy=True)
        xs, ys = tf.transform([bounds.left, bounds.right],
                              [bounds.bottom, bounds.top])
        bbox = (min(xs), min(ys), max(xs), max(ys))

    note(f"searching GFM {start} to {end} over {bbox}")
    items = search_scenes(bbox, start, end, limit=limit, timeout=timeout)
    note(f"{len(items)} scenes found")
    if not items:
        warnings.append(
            "No GFM scenes intersect this area and date range. GFM starts "
            "2015-01-01. If the area is correct, it may simply never have "
            "flooded during a Sentinel-1 overpass — which is itself a result, "
            "not an error."
        )

    flood, used, w1, observed = _accumulate(items, str(tpl_path), ASSET_FLOOD,
                                            timeout)
    excluded, _, w2, _ = _accumulate(items, str(tpl_path), ASSET_EXCLUSION,
                                     timeout)
    warnings.extend(w1[:5] + w2[:5])
    if len(w1) + len(w2) > 10:
        warnings.append(f"{len(w1) + len(w2)} scenes were skipped in total.")

    # A pixel is valid where at least one scene actually LOOKED at it and did
    # not exclude it.
    #
    # `~excluded` alone does not say that. `excluded` is the union of exclusion
    # masks over the scenes that were read, so ground no scene ever covered is
    # False there and `~excluded` is True -- "never looked at" came back
    # identical to "looked at and usable". With zero scenes found, which the
    # module's own warning says is a real outcome rather than an error, that
    # produced valid_fraction = 1.00 over the whole grid. validate_floodplain
    # then reported the ENTIRE floodplain as "observable but never seen wet",
    # and the low-observability caveat could not fire.
    #
    # `observed` accumulates the footprint of every scene actually read, so the
    # claim is now bounded by what was looked at.
    valid = observed & (flood | ~excluded)

    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)

    prof = gtiff_profile(profile, dtype="uint8", nodata=0)
    flood_path = out_dir / "flood_envelope.tif"
    valid_path = out_dir / "valid_mask.tif"
    with rasterio.open(str(flood_path), "w", **prof) as dst:
        dst.write(flood.astype("uint8"), 1)
    with rasterio.open(str(valid_path), "w", **prof) as dst:
        dst.write(valid.astype("uint8"), 1)

    with rasterio.open(str(tpl_path)) as tpl:
        geoms = [
            shp_shape(g) for g, v in
            rfeat.shapes(flood.astype("uint8"), mask=flood, transform=tpl.transform)
            if v
        ]
    poly_path = out_dir / "flood_envelope.gpkg"
    gdf = gpd.GeoDataFrame({"observed": [1] * len(geoms)}, geometry=geoms,
                           crs=profile["crs"])
    if not gdf.empty:
        gdf.to_file(str(poly_path), driver="GPKG")

    flooded_km2 = float(flood.sum()) * cell_area_km2
    valid_fraction = float(valid.mean()) if valid.size else 0.0
    # An ephemeral system leaves a recognisable trace: plenty of scenes over
    # many years, and almost no flood in any of them. Say so, because the
    # correct reading of "0.1% flooded" is not "the floodplain is tiny".
    scenes_with_signal = flooded_km2 > 0
    arid_signature = bool(
        used >= 20 and (not scenes_with_signal
                        or flooded_km2 / max(float(flood.size) * cell_area_km2,
                                             1e-9) < 0.005)
    )
    if arid_signature:
        warnings.append(
            "ARID / EPHEMERAL SIGNATURE: many scenes, almost no flood detected. "
            "On ephemeral streams a 6-12 day revisit essentially never coincides "
            "with a flow that lasts hours, and a dry sandy bed reads dark to "
            "radar exactly like water does. Do not treat a low recall here as "
            "evidence that the delineated floodplain is wrong -- it is evidence "
            "that SAR was looking elsewhere. See the module docstring for what "
            "to use instead in arid country."
        )

    if valid_fraction < 0.5:
        warnings.append(
            f"Only {valid_fraction:.0%} of this area was ever observable. SAR "
            f"excludes steep topography and radar shadow, which is exactly the "
            f"confined-valley case. Any agreement number over this area is "
            f"about the half it could see."
        )

    return InundationResult(
        flood_envelope=str(flood_path),
        valid_mask=str(valid_path),
        polygons=str(poly_path),
        n_scenes=len(items),
        n_scenes_used=used,
        flooded_km2=flooded_km2,
        valid_fraction=valid_fraction,
        date_range=(start, end),
        arid_signature=arid_signature,
        source="Copernicus GFM ensemble_flood_extent (CC BY 4.0, EODC)",
        seconds=time.monotonic() - t0,
        warnings=warnings,
    )


def validate_floodplain(
    floodplain: str | Path,
    inundation: InundationResult | dict,
    guard: Guard | None = None,
) -> ValidationResult:
    """Score a delineated floodplain against observed flood extent.

    Reports **recall only**, computed over pixels where the observation was
    valid. Precision and IoU are deliberately not reported: a correct floodplain
    extends well beyond any observed extent, because no single event fills a
    floodplain and SAR cannot see much of one. Scoring that as error would push
    a calibration toward under-delineation, which is the failure this whole
    module exists to catch.

    ``unconfirmed_km2`` is floodplain area that was observable and never seen
    wet. It is reported because it is interesting, and named carefully because
    it is not evidence of error.
    """
    import geopandas as gpd
    import rasterio
    from rasterio import features as rfeat

    guard = guard or Guard.from_env()
    inu = inundation if isinstance(inundation, dict) else inundation.as_dict()

    with rasterio.open(str(guard.read_path(inu["flood_envelope"]))) as src:
        flood = src.read(1).astype(bool)
        transform = src.transform
        crs = src.crs
        shape = flood.shape
        cell_area_km2 = abs(src.res[0] * src.res[1]) / 1e6
    with rasterio.open(str(guard.read_path(inu["valid_mask"]))) as src:
        valid = src.read(1).astype(bool)

    gdf = gpd.read_file(str(guard.read_path(floodplain)))
    if gdf.empty:
        raise GeolensError(f"No features in {floodplain}")
    if gdf.crs != crs:
        gdf = gdf.to_crs(crs)
    fp = rfeat.rasterize(
        [(g, 1) for g in gdf.geometry if g is not None and not g.is_empty],
        out_shape=shape, transform=transform, fill=0, dtype="uint8",
    ).astype(bool)

    obs = flood & valid
    captured = obs & fp
    missed = obs & ~fp
    unconfirmed = fp & valid & ~flood

    observed_km2 = float(obs.sum()) * cell_area_km2
    recall = float(captured.sum() / obs.sum()) if obs.any() else None

    notes = [
        "Recall only: of flood pixels that were observed AND observable, the "
        "fraction inside the delineated floodplain.",
        "Precision and IoU are not reported. A floodplain legitimately exceeds "
        "any observed extent; scoring that as error biases calibration toward "
        "under-delineation.",
    ]
    if recall is None:
        verdict = ("No usable observations. Either nothing flooded here during "
                   "a Sentinel-1 overpass, or SAR could not see this terrain.")
    elif recall >= 0.9:
        verdict = f"{recall:.0%} of observed flooding falls inside the floodplain."
    elif recall >= 0.7:
        verdict = (f"{recall:.0%} captured. The missed fraction is worth "
                   f"mapping — it is usually one reach, not a uniform error.")
    else:
        verdict = (f"Only {recall:.0%} of observed flooding is inside the "
                   f"delineated floodplain. Check the missed area before "
                   f"trusting the delineation; this is the signal that bank "
                   f"detection or the confinement cutoff is wrong.")

    vf = float(inu.get("valid_fraction", float(valid.mean())))
    if vf < 0.5:
        notes.append(
            f"Only {vf:.0%} of the area was observable, so this score describes "
            f"that fraction. Steep confined valleys are the systematic gap."
        )

    return ValidationResult(
        recall=recall,
        observed_flood_km2=observed_km2,
        captured_km2=float(captured.sum()) * cell_area_km2,
        missed_km2=float(missed.sum()) * cell_area_km2,
        unconfirmed_km2=float(unconfirmed.sum()) * cell_area_km2,
        valid_fraction=vf,
        floodplain_km2=float(fp.sum()) * cell_area_km2,
        verdict=verdict,
        notes=notes,
    )


def export_labels(
    inundation: InundationResult | dict,
    above_confinement: str | Path | None = None,
    out_prefix: str = "flood_labels",
    guard: Guard | None = None,
) -> dict[str, Any]:
    """Write positive / negative / unlabelled classes for the learning loop.

    The asymmetry is the whole point:

    * **positive** — observed flood pixels, inside the valid region
    * **negative** — ground above valley confinement, if supplied: terrain that
      cannot flood from this channel
    * **unlabelled** — everything else, INCLUDING every pixel that was simply
      never observed wet

    Treating "not observed flooded" as a negative is the one mistake that will
    quietly wreck a calibration: it would teach the model that floodplain is
    whatever SAR happened to catch during an overpass, which on a flashy stream
    is almost nothing.
    """
    import rasterio

    from .raster import gtiff_profile

    guard = guard or Guard.from_env()
    inu = inundation if isinstance(inundation, dict) else inundation.as_dict()

    with rasterio.open(str(guard.read_path(inu["flood_envelope"]))) as src:
        flood = src.read(1).astype(bool)
        profile = src.profile.copy()
        cell_area_km2 = abs(src.res[0] * src.res[1]) / 1e6
    with rasterio.open(str(guard.read_path(inu["valid_mask"]))) as src:
        valid = src.read(1).astype(bool)

    # 0 unlabelled, 1 positive, 2 negative
    labels = np.zeros(flood.shape, dtype="uint8")
    labels[flood & valid] = 1

    negatives = 0
    if above_confinement is not None:
        with rasterio.open(str(guard.read_path(above_confinement))) as src:
            outside = src.read(1).astype(bool)
        neg = outside & ~flood
        labels[neg] = 2
        negatives = int(neg.sum())

    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "labels.tif"
    prof = gtiff_profile(profile, dtype="uint8", nodata=255)
    with rasterio.open(str(path), "w", **prof) as dst:
        dst.write(labels, 1)

    pos = int((labels == 1).sum())
    return {
        "labels": str(path),
        "encoding": {"0": "unlabelled", "1": "flooded (positive)",
                     "2": "above confinement (negative)"},
        "positive_cells": pos,
        "negative_cells": negatives,
        "unlabelled_cells": int((labels == 0).sum()),
        "positive_km2": pos * cell_area_km2,
        "note": (
            "Unlabelled is not negative. Pixels never observed wet stay "
            "unlabelled on purpose — a flashy stream is almost never caught at "
            "peak by a 6-12 day revisit, so treating unobserved as dry teaches "
            "the model that floodplains are narrower than they are."
        ),
    }


def inundation_selftest(timeout: int = 60) -> dict[str, Any]:
    """Check the GFM service is reachable and its encoding is what we assume.

    Queries western North Carolina over Hurricane Helene (late September 2024),
    where there is certainly flooding to find, and reports the asset keys and
    the actual pixel values present.

    This exists because two assumptions in this module are conventions rather
    than contracts: that a positive value means flooded, and that a positive
    value in the exclusion mask means unusable. A silent encoding change would
    invert every result while everything still appeared to work. It could not
    be exercised where this was written — outbound HTTPS was blocked — so run it
    once before trusting a validation number.
    """
    out: dict[str, Any] = {"ok": False}
    bbox = (-83.5, 35.4, -82.0, 36.4)
    try:
        items = search_scenes(bbox, "2024-09-26", "2024-10-06", limit=5,
                              timeout=timeout)
        out["n_scenes"] = len(items)
        if items:
            it = items[0]
            out["item_id"] = it.get("id")
            out["assets"] = sorted((it.get("assets") or {}).keys())
            out["has_flood_asset"] = ASSET_FLOOD in (it.get("assets") or {})
            out["has_exclusion_asset"] = ASSET_EXCLUSION in (it.get("assets") or {})
            href = (it.get("assets", {}).get(ASSET_FLOOD, {}) or {}).get("href")
            if href:
                import rasterio

                with rasterio.open(f"/vsicurl/{href}") as src:
                    out["crs"] = str(src.crs)
                    out["res"] = [abs(src.res[0]), abs(src.res[1])]
                    win = src.read(1, window=((0, min(512, src.height)),
                                              (0, min(512, src.width))))
                    vals, counts = np.unique(win, return_counts=True)
                    out["flood_values_seen"] = {
                        int(v): int(c) for v, c in zip(vals[:10], counts[:10])
                    }
            out["ok"] = bool(out.get("has_flood_asset"))
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    return out
