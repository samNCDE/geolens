"""Work out what a run will cost BEFORE attempting it.

An out-of-memory kill gives you no message, no traceback and no clue which step
died. That is a terrible way to find out a DEM is too big. This module answers
the question up front: how many cells, how much memory, will it fit, and if not
what specifically to do about it.

The estimate is deliberately conservative. Under-promising and finishing is far
better than a silent kill forty minutes in.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..safety import Guard, GeolensError

# Peak memory of each stage in bytes per DEM cell, MEASURED (v0.25): peak RSS
# above the baseline, sampled every 50 ms, on a 27.1-million-cell run
# (Eagle Creek resampled to 1.2 m; pysheds; float32 DEM), then rounded up
# ~10%. Reproduce with scratchpad-style profiling of each pipeline call.
#
# The previous table counted arrays by reading the code and came out 2.6-3x
# low for routing and the valley bottom -- pysheds and the evidence surfaces
# work in float64 and hold intermediates. On Sam's 1.785-billion-cell Ruidoso
# grid it said "86.8 GB, TIGHT -- should fit" on a 117 GB machine; measured
# rates put the valley bottom near 220 GB, and the run paged for hours and
# then died overnight.
STAGE_BYTES_PER_CELL = {
    # v0.25 releases routing intermediates as it goes (was 98.8) and builds
    # the valley evidence in float32 in place (was 111.7), same outputs.
    "terrain (condition + flow routing)": 90.0,      # measured 80.8
    "valley bottom (evidence + EDT)": 55.0,           # measured 48.2
    "network (skeleton + trace)": 18.0,               # measured 15.5
    "classify (EDT with indices)": 36.0,              # measured 31.3
    "valley extent (local catchments)": 14.0,         # measured 12.0
    # v0.27 computes roughness in strips (was 48 B/cell whole-grid on its own);
    # what is left is restream (30.1) and the mask/skeleton/sections (34.9).
    "gullies (restream, roughness, sections)": 40.0,  # measured 34.9
}
# Kept for anything that imported it: the same numbers as float32-array
# equivalents at the default safety factor.
STAGE_ARRAYS = {k: v / (4 * 1.35) for k, v in STAGE_BYTES_PER_CELL.items()}

BYTES = {"float32": 4, "float64": 8}


def available_ram_gb() -> float | None:
    """Best-effort read of usable RAM. None when it cannot be determined."""
    try:
        import psutil  # type: ignore

        return psutil.virtual_memory().available / 1e9
    except Exception:
        pass
    try:  # Linux without psutil
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024 / 1e9
    except Exception:
        pass
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES") / 1e9
    except Exception:
        return None


@dataclass
class RunPlan:
    path: str
    width: int
    height: int
    cells: int
    cell_size: float
    crs: str | None
    area_km2: float
    area_acres: float
    dtype_used: str
    peak_gb: float
    available_gb: float | None
    fits: bool | None
    per_stage_gb: dict[str, float]
    verdict: str
    advice: list[str]
    suggested: dict[str, Any]
    output_disk_gb: float = 0.0
    classic_tiff_exceeded: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    def __str__(self) -> str:
        lines = [
            f"DEM        {Path(self.path).name}",
            f"  grid     {self.width:,} x {self.height:,} = {self.cells:,} cells "
            f"@ {self.cell_size:g} m",
            f"  area     {self.area_km2:,.0f} km²  ({self.area_acres:,.0f} acres)",
            f"  peak RAM ~{self.peak_gb:,.1f} GB  (working dtype {self.dtype_used})",
        ]
        if self.available_gb is not None:
            lines.append(f"  available {self.available_gb:,.1f} GB")
        lines.append(f"  VERDICT  {self.verdict}")
        for a in self.advice:
            lines.append(f"    - {a}")
        return "\n".join(lines)


def plan_run(
    dem: str | Path,
    dtype: str = "float32",
    stages: tuple[str, ...] | None = None,
    safety_factor: float = 1.35,
    guard: Guard | None = None,
) -> RunPlan:
    """Estimate whether a landscape pass will fit in memory, and what to do.

    Parameters
    ----------
    dtype
        Working precision. ``float32`` halves memory against ``float64`` and is
        ample for elevation in metres — a float32 carries ~7 significant digits,
        so a 4000 m peak still resolves to under a millimetre.
    safety_factor
        Headroom over the raw array arithmetic, covering temporaries inside
        library calls (pysheds and scipy both allocate intermediates).

    The advice is specific: it names the resolution or tiling that *would* fit
    rather than telling you the DEM is too big.
    """
    import rasterio

    guard = guard or Guard.from_env()
    p = guard.read_path(dem)

    with rasterio.open(str(p)) as src:
        w, h = src.width, src.height
        cell_crs = abs(src.res[0])
        crs = src.crs.to_string() if src.crs else None
        is_geo = bool(src.crs and src.crs.is_geographic)
        # Metres per CRS unit. The cell size was reported in CRS units under a
        # "m" label: a 2.5 ftUS grid read "2.5 m" and its 1,036 km2 extent
        # "11,156 km2" (Ruidoso, v24), and the resample advice below was in
        # feet too.
        mpu = 1.0
        if src.crs is not None and not is_geo:
            try:
                mpu = float(src.crs.linear_units_factor[1])
            except Exception:                                # noqa: BLE001
                mpu = 1.0
    cell = cell_crs * mpu

    cells = w * h
    nbytes = BYTES.get(dtype)
    if nbytes is None:
        raise GeolensError(f"dtype must be one of {list(BYTES)}, got {dtype!r}")

    names = stages or tuple(STAGE_BYTES_PER_CELL)
    # Measured at float32; a float64 working dtype roughly doubles the
    # raster part of it. safety_factor is relative to the default 1.35.
    scale = (nbytes / 4.0) * (safety_factor / 1.35)
    per_stage = {
        n: cells * STAGE_BYTES_PER_CELL[n] * scale / 1e9
        for n in names if n in STAGE_BYTES_PER_CELL
    }
    peak = max(per_stage.values()) if per_stage else 0.0

    # Disk, which memory planning ignores and which bites separately. A full
    # landscape pass writes roughly a dozen full-extent rasters (conditioned,
    # flow dir, accumulation, streams, HAND, slope, evidence, valley masks,
    # slope_std, TPI, curvature). Deflate on smooth terrain gets maybe 2.5x, so
    # this is deliberately a conservative estimate rather than an optimistic one.
    RASTERS_WRITTEN = 12
    COMPRESSION = 2.5
    output_disk_gb = cells * nbytes * RASTERS_WRITTEN / COMPRESSION / 1e9
    # A classic TIFF tops out at 4 GB because its offsets are 32-bit. Writes
    # above that need BIGTIFF, which gtiff_profile now always requests -- this
    # flag is here so the plan says the DEM is in that territory, since it also
    # means any tool reading the output has to cope with BigTIFF.
    classic_tiff_exceeded = bool(cells * nbytes > 4_000_000_000)

    area_km2 = cells * (cell ** 2) / 1e6 if not is_geo else float("nan")
    area_acres = area_km2 * 247.105 if math.isfinite(area_km2) else float("nan")

    avail = available_ram_gb()
    fits = None if avail is None else peak < avail * 0.8

    advice: list[str] = []
    suggested: dict[str, Any] = {}

    if is_geo:
        advice.append(
            "DEM is in a geographic CRS (degrees). Reproject to a projected CRS "
            "first — cell size, drainage area and every distance are wrong "
            "otherwise, and the area estimate above is meaningless."
        )

    if fits is False or (avail is None and peak > 32):
        verdict = "WILL NOT FIT — tile or coarsen first"
        budget = (avail * 0.8) if avail else 32.0
        # What cell size would fit, holding the same ground extent?
        ratio = math.sqrt(peak / budget) if budget > 0 else 2.0
        target_cell = cell * ratio
        n_tiles = math.ceil(peak / budget)
        advice.append(
            f"At {cell:.3g} m this needs ~{peak:,.0f} GB. Resample to about "
            f"{target_cell:.1f} m and the same area needs ~{budget:,.0f} GB. "
            f"Routing, HAND and valley form do not need lidar resolution -- "
            f"2-3 m is plenty -- so route on a resampled copy "
            f"(api.resample_dem) and pass the native DEM as dem_fine=, which "
            f"the channel cross-sections read without loading it."
        )
        advice.append(
            "Better: pick the watershed you actually care about and analyse "
            "that, with watershed(dem, pour_point='x,y') or a boundary you "
            "already have. A complete watershed has no inflow across its edge, "
            "so accumulation inside the clip is exact — and it is the unit the "
            "question is usually about anyway, valley bottom to the top of the "
            "watershed. Splitting a landscape into pieces and reassembling it "
            "is a last resort, not the first move."
        )
        advice.append(
            f"If you genuinely need the whole extent, it is roughly "
            f"{n_tiles} watershed-sized pieces. Do NOT split on an arbitrary "
            "grid: accumulation depends on everything upstream, so a "
            "rectangular boundary truncates flow paths and silently produces "
            "wrong drainage areas — measured on the landscape fixture, a "
            "rectangle over the middle third is missing 3.65x its own area in "
            "upstream contribution. derive_basins()/split_by_basins() cut on "
            "approximate divides from a coarsened DEM, which is better than a "
            "grid and still not exact."
        )
        suggested = {
            "resample_to_cell_m": round(target_cell, 1),
            "or_n_watershed_tiles": n_tiles,
            "max_cells_that_fit": int(budget * 1e9 / (
                max(STAGE_BYTES_PER_CELL.values()) * scale)),
        }
    elif fits is None:
        verdict = f"~{peak:,.1f} GB needed; available RAM unknown"
        advice.append("Install psutil for an availability check: pip install psutil")
    elif peak > avail * 0.5:
        verdict = "TIGHT — should fit, but close"
        advice.append(
            f"~{peak:,.1f} GB of ~{avail:,.1f} GB available. Close other work, or "
            "tile by watershed to be safe."
        )
    else:
        verdict = "FITS comfortably"

    if cells > 2_000_000_000:
        advice.append(
            f"{cells:,} cells is beyond what a single pass handles well "
            "regardless of RAM — scipy and pysheds both index with int32 in "
            "places. Tile it."
        )

    if dtype == "float64":
        advice.append(
            "float64 doubles memory for no benefit on elevation data — float32 "
            "resolves millimetres at 4000 m. Use the default."
        )

    if output_disk_gb >= 5:
        advice.append(
            f"A full pass writes roughly {output_disk_gb:.0f} GB of rasters. "
            f"Memory is not the only limit — check free space on GEOLENS_OUT."
        )
    if classic_tiff_exceeded:
        advice.append(
            "Individual output rasters will exceed 4 GB, past the classic "
            "GeoTIFF limit. geolens writes BigTIFF automatically; just be aware "
            "anything else reading these files has to support it. GDAL, QGIS "
            "and ArcGIS Pro all do."
        )

    return RunPlan(
        path=str(p), width=w, height=h, cells=cells, cell_size=cell, crs=crs,
        area_km2=area_km2, area_acres=area_acres, dtype_used=dtype,
        peak_gb=round(peak, 2), available_gb=round(avail, 2) if avail else None,
        fits=fits, per_stage_gb={k: round(v, 2) for k, v in per_stage.items()},
        verdict=verdict, advice=advice, suggested=suggested,
        output_disk_gb=round(output_disk_gb, 1),
        classic_tiff_exceeded=classic_tiff_exceeded,
    )
