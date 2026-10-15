"""Fetch surficial and bedrock geology for an area of interest.

Point at a shapefile, get the geology polygons underneath it, joined to a
material vocabulary that a Rosgen valley-type crosswalk can actually use.

WHERE THE DATA COMES FROM
-------------------------
The USGS **Cooperative National Geologic Map** (CNGM), released August 2025 and
revised September 2026. It is a national GeMS-schema synthesis of roughly a
hundred state and federal geologic maps, published as public ArcGIS
FeatureServers. Public domain (CC0), no API key, polygon queries supported.

Two services matter here:

* ``surface``     — Earth's Surface geology: surficial material where surficial
                    material exists, bedrock lithology where it does not. This
                    is the default, because it answers "what is the valley floor
                    made of" in one query.
* ``quaternary``  — Quaternary geology with all bedrock collapsed to two classes
                    (``Ru`` residual/pre-Quaternary, ``Rim`` igneous/metamorphic).
                    Use when you want the Quaternary reading specifically.

Semantics live in a separate ``DescriptionOfMapUnits`` table, not on the
polygons. That table is tiny — 129 rows for Earth's Surface, 37 for Quaternary —
so it is fetched once and cached, and the join happens locally. One network
round trip per area of interest, not two per polygon.

WHY NOT THE OTHER SOURCES
-------------------------
* **Macrostrat** has a good point API and reaches finer source maps, but its
  polygon endpoint (``/carto?shape=``) silently ignores the shape parameter and
  returns global results that look like success. It is usable as point
  enrichment, never as the spatial query. Its data is CC-BY, so attribution
  travels with anything cached or redistributed.
* **SGMC** (2017) is the predecessor, built from the same source maps CNGM
  ingested. Kept below as a fallback for when CNGM is unreachable.
* **Soller DS-425** surficial materials is 1:5,000,000 and download-only —
  roughly a hundred times too coarse for valley work. Not used.

WHAT THIS CANNOT TELL YOU — read before trusting a valley type
--------------------------------------------------------------
1. **Conterminous US only.** Alaska, Hawaii and the territories are absent from
   CNGM. For glacial valley types that is exactly the wrong gap, and there is
   no national substitute.
2. **Minimum mapping unit eats narrow valleys.** Source maps run 1:50,000 to
   1:1,000,000. A confined valley 80 m wide will frequently return the
   hillslope bedrock unit rather than its own alluvium, because the alluvial
   floor was never mappable at that scale. ``unit_area_ratio`` in the output is
   there to let you catch this: a reach whose geology polygon is enormous
   relative to the reach buffer is a reach whose geology was not really mapped.
3. **Colluvium is under-represented by design.** The Earth's Surface layer
   explicitly ignores soil and minor colluvial deposits, and the Quaternary
   layer lumps thin colluvium into ``Ru``. Only mappable mass-movement deposits
   appear, as ``Qls``. A colluvial valley will often not be identifiable from
   geology at all — that class has to come from DEM slope and confinement.
4. **Outwash is not a distinct class.** It sits inside ``Qic`` (ice-contact and
   ice-marginal sediment) or gets absorbed into ``Qa`` alluvium. Separating till
   from outwash means dropping to the source map's free-text description and
   pattern matching, which is lossy and varies state by state.
5. **No API contract.** These are USGS-hosted public services with no SLA and a
   ``_v2`` in the URL that will one day be ``_v3``. The endpoints are constants
   below precisely so they can be overridden without editing code paths.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .safety import Guard, GeolensError, note, progress

# -- services -----------------------------------------------------------------
#
# Overridable on purpose: see limitation 5 in the module docstring. The layer
# and table IDs differ BETWEEN services -- do not assume one set works for both.

_AGOL = "https://services.arcgis.com/v01gqwM5QqNysAAi/arcgis/rest/services"

SERVICES: dict[str, dict[str, Any]] = {
    "surface": {
        "url": f"{_AGOL}/National_Earth_Surface_v2/FeatureServer",
        "poly_layer": 6,
        "dmu_table": 8,
        "label": "CNGM Earth's Surface geology (v2)",
        "doi": "10.5066/P146VGVM",
    },
    "quaternary": {
        "url": f"{_AGOL}/National_Quaternary_v2/FeatureServer",
        "poly_layer": 5,
        "dmu_table": 7,
        "source_dmu_table": 10,
        "label": "CNGM Quaternary geology (v2)",
        "doi": "10.5066/P16SJAC6",
    },
    # Predecessor. Richer inline attributes (GENERALIZED_LITH, MAJOR1-3) but
    # 2017 vintage, and no DescriptionOfMapUnits join is needed.
    "sgmc": {
        "url": (f"{_AGOL}/SB_5888bf4fe4b05ccb964bab9d_USGS_SGMC_feature"
                "/FeatureServer"),
        "poly_layer": 3,
        "dmu_table": None,
        "label": "USGS State Geologic Map Compilation v1.1 (2017)",
        "doi": "10.5066/F7WH2N65",
    },
}

MAX_RECORDS = 2000  # the services' own maxRecordCount

# -- Rosgen-facing material classes -------------------------------------------
#
# CNGM's GeoMaterial vocabulary collapsed to the distinctions a valley-type call
# actually turns on. Deliberately coarse: inventing finer classes than the
# source supports would be making things up. `None` means "geology does not
# answer this" -- callers should fall back to DEM evidence rather than guess.

MATERIAL_CLASSES: dict[str, str] = {
    "alluvial": "Alluvial sediment",
    "glacial_till": "Glacial till",
    "ice_marginal": "Ice-contact and ice-marginal sediment",
    "lacustrine": "Lacustrine sediment",
    "mass_movement": "Debris flows, landslides, and other localized mass-movement sediment",
    "eolian": "Eolian sediment",
    "playa": "Playa",
    "organic": "Peat and muck",
    "coastal": "Coastal zone sediment",
    "marine": "Marine sediment",
    "residual": "Residual materials and pre-Quaternary rocks",
    "bedrock_igneous_metamorphic": "Pre-Quaternary igneous and metamorphic rock",
}

_MATERIAL_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("alluvial", ("alluvial", "alluvium", "fluvial", "stream deposit")),
    ("glacial_till", ("till", "glacial till", "moraine")),
    ("ice_marginal", ("ice-contact", "ice contact", "ice-marginal", "outwash", "glaciofluvial")),
    ("lacustrine", ("lacustrine", "lake deposit", "glaciolacustrine")),
    ("mass_movement", ("landslide", "debris flow", "mass-movement", "mass movement", "colluvi")),
    ("eolian", ("eolian", "aeolian", "loess", "dune")),
    ("playa", ("playa",)),
    ("organic", ("peat", "muck", "organic")),
    ("coastal", ("coastal", "beach", "estuarine")),
    ("marine", ("marine",)),
    ("residual", ("residual", "pre-quaternary rock")),
    ("bedrock_igneous_metamorphic", ("igneous", "metamorphic", "intrusive", "volcanic",
                                     "granit", "basalt", "quartzite", "gneiss", "schist")),
    ("bedrock_sedimentary", ("sandstone", "mudstone", "shale", "carbonate", "limestone",
                             "dolomite", "conglomerate", "siltstone", "sedimentary")),
]


def classify_material(geomaterial: str | None) -> str | None:
    """Map a CNGM ``GeoMaterial`` string to a coarse material class.

    Returns ``None`` when the material does not map — which is a real answer,
    not a failure. ``None`` means the geology did not resolve this reach and the
    valley type has to come from DEM evidence instead. Do not default it to
    bedrock; a silent wrong class is worse than an explicit unknown.
    """
    if not geomaterial:
        return None
    g = str(geomaterial).lower()
    for cls, keys in _MATERIAL_KEYWORDS:
        if any(k in g for k in keys):
            return cls
    return None


# -- result -------------------------------------------------------------------


@dataclass
class GeologyResult:
    service: str
    service_label: str
    polygons: str                 # path to the written GeoPackage
    units_csv: str                # path to the unit lookup table
    n_polygons: int
    n_units: int
    aoi_area_km2: float
    mapped_fraction: float        # fraction of the AOI covered by a polygon
    material_area_km2: dict[str, float]
    dominant_material: str | None
    crs: str | None
    seconds: float
    source_doi: str
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


# -- HTTP ---------------------------------------------------------------------


def _get_json(url: str, params: dict[str, Any], timeout: int = 60,
              retries: int = 3) -> dict:
    """GET a JSON payload, with retries on transient failure.

    Deliberately urllib rather than requests: this module is imported by the
    core package, and the core package does not depend on requests. One fewer
    thing that has to be installed for a geology call to work.
    """
    qs = urllib.parse.urlencode(params)
    full = f"{url}?{qs}"
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                full, headers={"User-Agent": "geolens/0.1 (+geomorphic analysis)"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - urllib raises a zoo of types
            last = e
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise GeolensError(
        f"Could not reach the geology service after {retries} attempts.\n"
        f"  url:   {url}\n"
        f"  error: {type(last).__name__}: {last}\n\n"
        f"These are public USGS services with no SLA. If this is a transient\n"
        f"outage, retry later. If your network blocks outbound HTTPS, fetch the\n"
        f"geology separately and pass it in as a file."
    ) from last


def _query_polygons(svc: dict, envelope: tuple[float, float, float, float],
                    timeout: int = 60) -> list[dict]:
    """Page through every polygon intersecting an envelope, as GeoJSON features.

    Two ArcGIS quirks, both learned by having them fail:

    * the envelope goes in as a bare comma-separated string with raw commas,
      not as JSON geometry;
    * ``where=1=1`` alongside ``geometry`` is rejected. Send the geometry alone.
    """
    minx, miny, maxx, maxy = envelope
    url = f"{svc['url']}/{svc['poly_layer']}/query"
    out: list[dict] = []
    offset = 0
    while True:
        payload = _get_json(url, {
            "geometry": f"{minx},{miny},{maxx},{maxy}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "outSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "returnGeometry": "true",
            "resultOffset": offset,
            "resultRecordCount": MAX_RECORDS,
            "f": "geojson",
        }, timeout=timeout)
        if "error" in payload:
            raise GeolensError(f"Geology service returned an error: {payload['error']}")
        feats = payload.get("features", [])
        out.extend(feats)
        # exceededTransferLimit is the ArcGIS way of saying "there is more"
        if len(feats) < MAX_RECORDS or not payload.get("properties", {}).get(
                "exceededTransferLimit", len(feats) == MAX_RECORDS):
            break
        offset += len(feats)
        if offset > 200_000:  # runaway guard
            note("geology: stopped paging at 200k polygons")
            break
    return out


def _fetch_units(svc: dict, cache: Path | None = None,
                 timeout: int = 60) -> dict[str, dict]:
    """Fetch DescriptionOfMapUnits once and return it keyed by MapUnit.

    Cached to disk because it is small, static between service versions, and
    re-fetching it on every call is the sort of thing that turns a fast local
    loop into a rate-limited one.
    """
    if svc.get("dmu_table") is None:
        return {}
    if cache and cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass  # corrupt cache is not worth failing over; refetch

    payload = _get_json(f"{svc['url']}/{svc['dmu_table']}/query", {
        "where": "1=1",
        "outFields": "MapUnit,Name,FullName,Age,Description,GeoMaterial,GeoMaterialConfidence",
        "returnGeometry": "false",
        "f": "json",
    }, timeout=timeout)
    if "error" in payload:
        raise GeolensError(f"Unit table query failed: {payload['error']}")

    units = {}
    for f in payload.get("features", []):
        a = f.get("attributes", {})
        mu = a.get("MapUnit")
        if mu:
            units[str(mu)] = a
    if cache:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(units, indent=1), encoding="utf-8")
        except Exception:
            pass
    return units


# -- public -------------------------------------------------------------------


def fetch_geology(
    aoi: str | Path,
    buffer_m: float = 1000.0,
    service: str = "surface",
    out_prefix: str = "geology",
    timeout: int = 60,
    guard: Guard | None = None,
) -> GeologyResult:
    """Download geology polygons covering a shapefile's extent.

    Parameters
    ----------
    aoi
        Any vector file geopandas can read — a watershed boundary, a centerline,
        a DEM footprint polygon. Its extent, buffered, is what gets queried.
    buffer_m
        How far beyond the AOI to fetch, in metres. The default 1 km is there so
        a valley near the edge still has its opposite wall's geology. Applied in
        a projected CRS, not in degrees.
    service
        ``"surface"`` (default), ``"quaternary"``, or ``"sgmc"``. See the module
        docstring for what each answers.

    Outputs written to the output directory
    ---------------------------------------
    ``<prefix>_dir/geology.gpkg``   polygons, clipped to the buffered AOI, with
                                    unit semantics joined on
    ``<prefix>_dir/units.csv``      the unit lookup table as fetched
    ``<prefix>_dir/units_cache_<service>.json`` cached DescriptionOfMapUnits

    These are written rather than only returned because a geology fetch is a
    network call against a service with no SLA: the expensive, unreproducible
    part is having the bytes at all.
    """
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import box

    t0 = time.monotonic()
    guard = guard or Guard.from_env()
    warnings: list[str] = []

    if service not in SERVICES:
        raise GeolensError(
            f"Unknown service {service!r}. Options: {', '.join(SERVICES)}"
        )
    svc = SERVICES[service]

    # ---- AOI, buffered in a projected CRS --------------------------------
    aoi_path = guard.read_path(aoi)
    gdf = gpd.read_file(str(aoi_path))
    if gdf.empty:
        raise GeolensError(f"AOI file has no features: {aoi_path}")
    if gdf.crs is None:
        raise GeolensError(
            f"AOI has no CRS: {aoi_path}. Geology is fetched by geographic "
            f"coordinates, so the AOI's CRS has to be known -- assign one first."
        )

    # Buffer in metres. If the AOI is geographic, project to its UTM zone first;
    # buffering degrees would make the buffer width depend on latitude.
    if gdf.crs.is_geographic:
        utm = gdf.estimate_utm_crs()
        projected = gdf.to_crs(utm)
    else:
        projected = gdf
    aoi_geom = projected.geometry.union_all() if hasattr(projected.geometry, "union_all") \
        else projected.geometry.unary_union
    buffered = aoi_geom.buffer(buffer_m) if buffer_m else aoi_geom
    aoi_area_km2 = buffered.area / 1e6

    buffered_wgs = gpd.GeoSeries([buffered], crs=projected.crs).to_crs(4326)
    minx, miny, maxx, maxy = buffered_wgs.total_bounds

    note(f"geology: querying {svc['label']} over "
         f"{maxx - minx:.3f} x {maxy - miny:.3f} degrees")

    # ---- polygons --------------------------------------------------------
    feats = _query_polygons(svc, (minx, miny, maxx, maxy), timeout=timeout)
    if not feats:
        warnings.append(
            "The geology service returned no polygons for this extent. CNGM "
            "covers the conterminous US only -- Alaska, Hawaii and the "
            "territories are not in it."
        )
    poly = gpd.GeoDataFrame.from_features(feats, crs=4326) if feats else \
        gpd.GeoDataFrame(geometry=[], crs=4326)

    # ---- unit semantics --------------------------------------------------
    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    # The service is part of the cache key. The surface, quaternary and SGMC
    # services are different FeatureServers with different DescriptionOfMapUnits
    # tables and different MapUnit code spaces — sharing one cache file meant a
    # quaternary call after a surface call joined against the wrong lookup
    # table. Where a code existed in both, the join succeeded with the WRONG
    # name, age and material, silently; where it did not, everything came back
    # "unknown" and the warning blamed the vocabulary.
    cache = out_dir / f"units_cache_{service}.json"
    units = _fetch_units(svc, cache=cache, timeout=timeout)

    if not poly.empty and units and "MapUnit" in poly.columns:
        cols = ("Name", "FullName", "Age", "Description", "GeoMaterial",
                "GeoMaterialConfidence")
        for c in cols:
            poly[c] = [units.get(str(mu), {}).get(c) for mu in poly["MapUnit"]]
        poly["material_class"] = [classify_material(g) for g in poly["GeoMaterial"]]
    elif not poly.empty:
        # SGMC carries its semantics inline; give it the same column name so
        # downstream code does not have to branch on service.
        src = "GENERALIZED_LITH" if "GENERALIZED_LITH" in poly.columns else None
        poly["GeoMaterial"] = poly[src] if src else None
        poly["material_class"] = [classify_material(g) for g in poly["GeoMaterial"]] \
            if src else None

    # ---- clip to the real AOI, not the envelope --------------------------
    if not poly.empty:
        poly = poly.to_crs(projected.crs)
        clip_gdf = gpd.GeoDataFrame(geometry=[buffered], crs=projected.crs)
        poly = gpd.clip(poly, clip_gdf)
        poly = poly[~poly.geometry.is_empty & poly.geometry.notna()]
        poly["area_km2"] = poly.geometry.area / 1e6
        # How big is this unit's polygon relative to the AOI? A ratio near 1
        # means one polygon covers everything, i.e. nothing here was really
        # mapped at valley scale. See limitation 2 in the module docstring.
        poly["unit_area_ratio"] = poly["area_km2"] / max(aoi_area_km2, 1e-9)

    # ---- summarise -------------------------------------------------------
    material_area: dict[str, float] = {}
    dominant = None
    mapped_fraction = 0.0
    if not poly.empty:
        mapped_fraction = min(1.0, float(poly["area_km2"].sum()) / max(aoi_area_km2, 1e-9))
        grp = poly.groupby("material_class", dropna=False)["area_km2"].sum()
        material_area = {("unknown" if pd.isna(k) else str(k)): float(v)
                         for k, v in grp.items()}
        known = {k: v for k, v in material_area.items() if k != "unknown"}
        dominant = max(known, key=known.get) if known else None

        unknown_frac = material_area.get("unknown", 0.0) / max(
            sum(material_area.values()), 1e-9)
        if unknown_frac > 0.25:
            warnings.append(
                f"{unknown_frac:.0%} of the mapped area has a GeoMaterial that "
                f"does not map to a material class. Check the GeoMaterial column "
                f"directly rather than relying on material_class."
            )
        big = poly[poly["unit_area_ratio"] > 0.8]
        if len(big) and mapped_fraction > 0.5:
            warnings.append(
                "A single geology polygon covers more than 80% of the area of "
                "interest. At this map scale the valley floor was probably not "
                "mapped separately from the hillslopes -- treat any valley-type "
                "call from this geology as low confidence."
            )

    # ---- write -----------------------------------------------------------
    poly_path = out_dir / "geology.gpkg"
    if not poly.empty:
        # GPKG cannot hold mixed geometry types in one layer; geology is
        # polygons but a clip can leave slivers as GeometryCollection.
        poly = poly[poly.geom_type.isin(("Polygon", "MultiPolygon"))]
        poly.to_file(str(poly_path), driver="GPKG")
    units_csv = out_dir / "units.csv"
    if units:
        pd.DataFrame(list(units.values())).to_csv(units_csv, index=False)

    return GeologyResult(
        service=service,
        service_label=svc["label"],
        polygons=str(poly_path),
        units_csv=str(units_csv),
        n_polygons=int(len(poly)),
        n_units=len(units),
        aoi_area_km2=float(aoi_area_km2),
        mapped_fraction=float(mapped_fraction),
        material_area_km2=material_area,
        dominant_material=dominant,
        crs=str(projected.crs),
        seconds=time.monotonic() - t0,
        source_doi=svc.get("doi", ""),
        warnings=warnings,
    )


def geology_selftest(timeout: int = 60) -> dict[str, Any]:
    """Check that the geology services are reachable and answering correctly.

    Runs a point query over the Colorado Front Range, where the right answer is
    known: Precambrian crystallines in the mountains, Paleozoic and Mesozoic
    clastics in the hogbacks, Quaternary alluvium in the valleys.

    This exists because the geology fetcher is the one part of geolens that
    depends on a third-party service staying up and keeping its schema. It
    could not be exercised in the environment where it was written -- outbound
    HTTPS was blocked there -- so run this once before trusting a geology call.
    """
    out: dict[str, Any] = {"ok": False, "services": {}}
    envelope = (-105.3, 40.3, -105.1, 40.5)

    for name in ("surface", "quaternary"):
        svc = SERVICES[name]
        entry: dict[str, Any] = {"url": svc["url"]}
        try:
            feats = _query_polygons(svc, envelope, timeout=timeout)
            entry["n_polygons"] = len(feats)
            mapunits = sorted({
                f.get("properties", {}).get("MapUnit") for f in feats
            } - {None})
            entry["map_units"] = mapunits[:20]
            units = _fetch_units(svc, timeout=timeout)
            entry["n_units"] = len(units)
            entry["materials"] = sorted({
                u.get("GeoMaterial") for u in units.values()
            } - {None})[:12]
            unmapped = [m for m in (u.get("GeoMaterial") for u in units.values())
                        if m and classify_material(m) is None]
            entry["geomaterials_not_classified"] = sorted(set(unmapped))[:10]
            entry["ok"] = len(feats) > 0 and len(units) > 0
        except Exception as e:
            entry["ok"] = False
            entry["error"] = f"{type(e).__name__}: {e}"
        out["services"][name] = entry

    out["ok"] = all(s.get("ok") for s in out["services"].values())
    return out
