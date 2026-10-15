"""Valley bottom extraction from HAND and slope evidence.

Follows the VBET approach (Gilbert et al.), which is worth adopting rather than
re-deriving: its evidence curves were calibrated against thousands of points
across the US.

    evidence = HAND_evidence^w_hand  x  slope_evidence^w_slope     (w = 0.65 / 0.35)

Two details carry most of the accuracy:

1. **Drainage-area scaling.** A 2 m HAND threshold that correctly bounds a
   headwater valley floor is far too tight on a large river. Thresholds scale
   with contributing area so one parameter set works across a whole network.

2. **Inverse-distance slope weighting.** Flat ground far from any channel — ridge
   tops, plateaus, terraces above the valley — is flat but is not valley bottom.
   Down-weighting slope evidence with distance from the channel is what stops
   the extraction leaking onto uplands.

Output is a boolean valley-bottom raster plus polygons, at two levels: the full
valley bottom and a low-lying inner core.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..safety import Guard, GeolensError, note
from ..raster import gtiff_profile
from .terrain import TerrainResult


@dataclass
class ValleyResult:
    valley_raster: str
    valley_polygons: str
    core_raster: str
    core_polygons: str
    evidence_raster: str
    valley_area_km2: float
    core_area_km2: float
    landscape_area_km2: float
    valley_fraction: float
    parameters: dict[str, Any]
    seconds: float
    warnings: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _fuzzy_high(x: np.ndarray, full: np.ndarray | float,
                zero: np.ndarray | float) -> np.ndarray:
    """1 where x <= full, 0 where x >= zero, smoothstep between.

    Used for "low values are good" evidence: HAND and slope both argue for
    valley bottom when small.
    """
    # float32 throughout: evidence is a 0-1 membership, and every float64
    # temporary here was a full-grid 8 bytes a cell (see extract_valley_bottom).
    full = np.asarray(full, dtype="float32")
    zero = np.asarray(zero, dtype="float32")
    span = np.maximum(zero - full, np.float32(1e-9))
    t = (x - full) / span
    del span
    np.clip(t, 0.0, 1.0, out=t)
    out = t * t
    out *= (3.0 - 2.0 * t)
    del t
    np.subtract(1.0, out, out=out)
    return out


def extract_valley_bottom(
    terrain: TerrainResult,
    hand_full: float = 2.0,
    hand_zero: float = 12.0,
    slope_full: float = 0.02,
    slope_zero: float = 0.25,
    hand_weight: float = 0.65,
    slope_weight: float = 0.35,
    threshold: float = 0.65,
    core_threshold: float = 0.85,
    scale_with_drainage: bool = True,
    drainage_ref_km2: float = 10.0,
    scale_exponent: float = 0.35,
    max_scale: float = 6.0,
    slope_distance_decay: float = 500.0,
    min_patch_km2: float = 0.01,
    close_gaps_m: float = 250.0,
    guard: Guard | None = None,
    out_prefix: str = "valley",
) -> ValleyResult:
    """Extract the valley bottom from a terrain run.

    Parameters
    ----------
    hand_full, hand_zero
        HAND (m) at which evidence is full (1.0) and exhausted (0.0), for a
        stream of reference drainage area.
    slope_full, slope_zero
        Slope (rise/run) at which evidence is full and exhausted.
    threshold, core_threshold
        Combined-evidence cuts for the full valley bottom and the low-lying core.
    scale_with_drainage
        Scale HAND thresholds by ``(area / drainage_ref_km2) ** scale_exponent``,
        capped at ``max_scale``. Without this, one threshold cannot serve both
        headwaters and mainstems.
    slope_distance_decay
        Distance (m) over which slope evidence decays to half weight. Stops flat
        uplands far from any channel reading as valley bottom.
    min_patch_km2
        Drop valley-bottom patches smaller than this — speckle, not valleys.
    close_gaps_m
        Bridge gaps up to this wide before despeckling. This is the GAP width,
        not a structuring-element radius: the implementation uses half of it,
        because a closing of radius k bridges 2k. On flat valley floors,
        flow routing disperses into spurious parallel drainage lines; cells
        between them measure HAND to a distant outlet and punch narrow holes
        through an otherwise continuous valley bottom. Closing repairs that.
        Set to 0 to disable.
    """
    import rasterio
    from rasterio import features
    from scipy import ndimage

    guard = guard or Guard.from_env()
    t0 = time.monotonic()
    warnings: list[str] = []

    if not 0.99 < hand_weight + slope_weight < 1.01:
        raise GeolensError(
            f"hand_weight + slope_weight must sum to 1 "
            f"(got {hand_weight} + {slope_weight})."
        )
    if core_threshold <= threshold:
        raise GeolensError(
            f"core_threshold ({core_threshold}) must exceed threshold "
            f"({threshold}) — the core is the inner, stricter zone."
        )

    # float32, and every intermediate freed as soon as it is used. This step
    # was the peak of the whole landscape pass at 112 bytes a cell -- about
    # fourteen float64 grids alive at once -- which on a 1.8-billion-cell DEM
    # is ~200 GB. Evidence is a 0-1 membership and HAND and slope are compared
    # against thresholds; float32 changes neither.
    with rasterio.open(terrain.hand) as src:
        hand = src.read(1, masked=True).astype("float32").filled(np.nan)
        profile = src.profile.copy()
        transform = src.transform
        cell = abs(src.res[0])
    with rasterio.open(terrain.slope) as src:
        slope = src.read(1).astype("float32")
    with rasterio.open(terrain.streams) as src:
        streams = src.read(1).astype(bool)

    # True metres. close_gaps_m and min_patch_km2 are metric; the raster's own
    # cell size is in whatever the CRS uses, which on a US state-plane grid is
    # feet.
    _u = getattr(terrain, "units", None) or {}
    cell_m = float(_u.get("cell_size_m") or terrain.cell_size or cell)
    cell_area_km2 = float(_u.get("cell_area_m2") or (cell_m ** 2)) / 1e6
    note("building evidence layers")

    # --- drainage-area scaling ---------------------------------------------
    # Each cell's threshold scales with the drainage area of the stream it
    # belongs to. Using the cell's own accumulation is a good proxy inside the
    # valley bottom, where the whole point is proximity to that stream.
    if scale_with_drainage:
        with rasterio.open(terrain.accumulation) as src:
            scale = src.read(1).astype("float32")
        np.maximum(scale, 1.0, out=scale)
        scale *= np.float32(cell_area_km2 / drainage_ref_km2)
        np.power(scale, np.float32(scale_exponent), out=scale)
        np.clip(scale, 0.5, max_scale, out=scale)
        # Smooth the scale field so thresholds don't jump across a confluence.
        scale = ndimage.uniform_filter(scale, size=5, mode="nearest")
    else:
        scale = np.ones_like(slope)

    # HAND is in the DEM's VERTICAL unit; hand_full and hand_zero are metres.
    # On a foot DEM a 12 m threshold was applied as 12 ft = 3.66 m, so the
    # valley bottom came out a third of the size it was asked for -- measured
    # on a feet/metres fixture pair of the same ground: 0.98 km2 against
    # 1.43 km2.
    _vpu = float((getattr(terrain, "units", None) or {})
                 .get("vertical_metres_per_unit") or 1.0)
    hand_f = np.float32(hand_full / _vpu) * scale
    hand_z = np.float32(hand_zero / _vpu) * scale
    del scale
    no_hand = np.isnan(hand)
    hand_ev = _fuzzy_high(hand, hand_f, hand_z)
    del hand_f, hand_z
    hand_ev[no_hand] = 0.0
    slope_ev = _fuzzy_high(slope, slope_full, slope_zero)
    del slope

    # --- inverse-distance slope weighting -----------------------------------
    note("computing distance from channel for slope weighting")
    # sampling in TRUE metres, because slope_distance_decay is metres.
    w = ndimage.distance_transform_edt(~streams, sampling=cell_m).astype(
        "float32")
    # w = 1 / (1 + dist / decay), in place
    w *= np.float32(1.0 / max(slope_distance_decay, 1e-9))
    w += 1.0
    np.reciprocal(w, out=w)
    # Pull un-weighted slope evidence toward neutral (1.0) with distance, so far
    # ground is judged on HAND alone rather than on being flat:
    # slope_ev * w + (1 - w)  ==  1 - w * (1 - slope_ev)
    np.subtract(1.0, slope_ev, out=slope_ev)
    slope_ev *= w
    del w
    np.subtract(1.0, slope_ev, out=slope_ev)

    note("combining evidence")
    np.clip(hand_ev, 1e-6, 1.0, out=hand_ev)
    np.power(hand_ev, np.float32(hand_weight), out=hand_ev)
    np.clip(slope_ev, 1e-6, 1.0, out=slope_ev)
    np.power(slope_ev, np.float32(slope_weight), out=slope_ev)
    evidence = hand_ev
    evidence *= slope_ev
    del slope_ev
    evidence[streams] = 0.995
    evidence[no_hand & ~streams] = 0.0
    del no_hand

    valley = evidence >= threshold
    core = evidence >= core_threshold

    # Write the evidence surface now and let it go: the despeckling below
    # labels and morphs two full-grid masks, and holding a float grid through
    # that was dead weight.
    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    ev_profile = gtiff_profile(profile, dtype="float32", nodata=None)
    ev_path = out_dir / "evidence.tif"
    with rasterio.open(str(ev_path), "w", **ev_profile) as dst:
        dst.write(evidence.astype("float32", copy=False), 1)
    del evidence

    # --- clean up ------------------------------------------------------------
    min_cells = max(1, int(round(min_patch_km2 / cell_area_km2)))

    # HALF the gap width, because a morphological closing of radius k bridges
    # a gap of 2k -- the dilation advances k cells from EACH side. Using the
    # full width meant close_gaps_m=250 welded valley strips 500 m apart into
    # one polygon, doubling their valley_width_m and pushing the confinement
    # ratio across the unconfined break. Verified: radius 5 bridges 10 cells.
    close_cells = int(round(close_gaps_m / cell_m / 2.0)) if close_gaps_m > 0 else 0
    if close_cells > 0:
        note(f"closing gaps up to {close_gaps_m:g} m "
             f"(radius {close_cells} cells, which bridges {2 * close_cells})")
        structure = ndimage.generate_binary_structure(2, 2)

    def _close(mask: np.ndarray) -> np.ndarray:
        if close_cells <= 0:
            return mask
        # Dilate then erode by the same amount: bridges narrow gaps without
        # growing the outer boundary of the valley bottom.
        grown = ndimage.binary_dilation(mask, structure, iterations=close_cells)
        return ndimage.binary_erosion(
            grown, structure, iterations=close_cells, border_value=1
        )

    def _despeckle(mask: np.ndarray, label: str) -> np.ndarray:
        mask = _close(mask)
        lab, n = ndimage.label(mask)
        if n == 0:
            warnings.append(f"No {label} cells found — thresholds may be too strict.")
            return mask
        sizes = np.bincount(lab.ravel())
        sizes[0] = 0
        keep = sizes >= min_cells
        dropped = int((~keep[1:]).sum())
        out = keep[lab]
        if dropped:
            note(f"{label}: dropped {dropped:,} patches under {min_patch_km2} km²")
        # Fill interior holes — an island of high ground inside a floodplain is
        # still inside the valley bottom.
        return ndimage.binary_fill_holes(out)

    valley = _despeckle(valley, "valley bottom")
    core = _despeckle(core, "valley core")
    core = core & valley

    # --- write ---------------------------------------------------------------

    def _write_mask(mask: np.ndarray, name: str) -> str:
        p = gtiff_profile(profile, dtype="uint8", nodata=0)
        path = out_dir / name
        with rasterio.open(str(path), "w", **p) as dst:
            dst.write(mask.astype("uint8"), 1)
        return str(path)

    def _polygonize(mask: np.ndarray, name: str) -> str:
        import geopandas as gpd
        from shapely.geometry import shape

        geoms = [
            shape(g)
            for g, v in features.shapes(
                mask.astype("uint8"), mask=mask, transform=transform
            )
            if v == 1
        ]
        path = out_dir / name
        gdf = gpd.GeoDataFrame(
            {"kind": [name.split(".")[0]] * len(geoms)},
            geometry=geoms,
            crs=profile.get("crs"),
        )
        if not gdf.empty:
            gdf["area_km2"] = gdf.geometry.area / 1e6
        gdf.to_file(str(path), driver="GPKG")
        return str(path)

    note("writing rasters and polygons")

    valley_r = _write_mask(valley, "valley_bottom.tif")
    core_r = _write_mask(core, "valley_core.tif")
    valley_p = _polygonize(valley, "valley_bottom.gpkg")
    core_p = _polygonize(core, "valley_core.gpkg")

    v_area = float(valley.sum()) * cell_area_km2
    c_area = float(core.sum()) * cell_area_km2
    total = float(valley.size) * cell_area_km2
    frac = v_area / total if total else 0.0

    if frac > 0.6:
        warnings.append(
            f"Valley bottom covers {frac:.0%} of the landscape, which is "
            "implausibly high. Lower hand_zero or raise threshold."
        )
    elif frac < 0.005:
        warnings.append(
            f"Valley bottom covers only {frac:.1%} of the landscape. Raise "
            "hand_zero or lower threshold if valleys are being missed."
        )

    elapsed = time.monotonic() - t0
    note(f"valley extraction complete in {elapsed:.1f}s")

    return ValleyResult(
        valley_raster=valley_r,
        valley_polygons=valley_p,
        core_raster=core_r,
        core_polygons=core_p,
        evidence_raster=str(ev_path),
        valley_area_km2=v_area,
        core_area_km2=c_area,
        landscape_area_km2=total,
        valley_fraction=frac,
        parameters={
            "hand_full": hand_full, "hand_zero": hand_zero,
            "slope_full": slope_full, "slope_zero": slope_zero,
            "hand_weight": hand_weight, "slope_weight": slope_weight,
            "threshold": threshold, "core_threshold": core_threshold,
            "scale_with_drainage": scale_with_drainage,
            "drainage_ref_km2": drainage_ref_km2,
            "scale_exponent": scale_exponent, "max_scale": max_scale,
            "slope_distance_decay": slope_distance_decay,
            "min_patch_km2": min_patch_km2,
            "stream_threshold_km2": terrain.stream_threshold_km2,
        },
        seconds=elapsed,
        warnings=warnings,
    )
