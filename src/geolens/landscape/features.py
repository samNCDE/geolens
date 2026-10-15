"""Gully and headcut detection from terrain roughness.

Two erosion features, two different signals.

**Gullies** are narrow incisions. They are not simply *steep* — a hillslope is
steep and uniform. A gully is steep *and locally variable*: a bed, two walls and
the surrounding surface all inside a small window. So the primary signal is the
**standard deviation of slope in a moving window**, reinforced by:

* **negative TPI** — the feature sits below its surroundings (incised, not a ridge)
* **convergent curvature** — the surface bends inward
* **flow accumulation in a band** — enough drainage to cut, not so much that it
  is already a mapped channel

**Headcuts** are knickpoints: an abrupt steepening travelling *along* the flow
path, usually at the upstream end of a gully. Slope standard deviation does not
find these, because a headcut can be perfectly smooth across-slope. They are
found instead by walking each flow path and looking for a longitudinal gradient
break — the same slope-break logic used at reach scale, rotated 90 degrees.

Every threshold is reported with the output, and the tuning table in the module
docstring of each function says which way to move it.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..safety import Guard, GeolensError, note, progress
from ..raster import gtiff_profile
from .terrain import TerrainResult


@dataclass
class RoughnessResult:
    slope_std: str
    tpi: str
    curvature: str
    window_m: float
    parameters: dict[str, Any]
    seconds: float
    warnings: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class GullyResult:
    gully_raster: str
    gully_lines: str
    gully_polygons: str | None
    evidence_raster: str
    gully_count: int
    total_length_km: float
    gullied_area_km2: float
    gully_density_km_per_km2: float
    mean_depth_m: float
    mean_width_m: float
    eroded_volume_m3: float
    parameters: dict[str, Any]
    seconds: float
    warnings: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class HeadcutResult:
    headcuts: str
    headcut_count: int
    summary: dict[str, Any]
    parameters: dict[str, Any]
    seconds: float
    warnings: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _write_like(profile: dict, array: np.ndarray, out: Path,
                dtype: str = "float32", nodata: float | None = None) -> str:
    import rasterio

    p = gtiff_profile(profile, dtype=dtype, nodata=nodata)
    with rasterio.open(str(out), "w", **p) as dst:
        dst.write(array.astype(dtype), 1)
    return str(out)


def _nd(a: np.ndarray, fill: float = -9999.0) -> np.ndarray:
    """NaN -> the nodata value, for writing."""
    return np.where(np.isfinite(a), a, fill).astype("float32")


def _masked_uniform(arr: np.ndarray, valid: np.ndarray, size: int) -> np.ndarray:
    """Local mean over VALID cells only.

    ``uniform_filter`` has no concept of nodata, so whatever is sitting in the
    hole is averaged in as though it were terrain. Filling with the global mean
    elevation -- which is what this used to do -- turns a void into an
    artificial plateau hundreds of metres above or below its surroundings, and
    every real cell within the window then reads a huge TPI anomaly.

    The fix is the standard one: filter the zero-filled values and the validity
    mask separately, and divide. Cells whose window contains no valid data at
    all come back NaN rather than zero.
    """
    from scipy import ndimage

    a = np.where(valid, arr, 0.0).astype("float32")
    w = valid.astype("float32")
    num = ndimage.uniform_filter(a, size=size, mode="nearest")
    den = ndimage.uniform_filter(w, size=size, mode="nearest")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / den
    out[den <= 1e-6] = np.nan
    return out


# -- roughness ----------------------------------------------------------------


# The most of a landscape that may be called rough enough to hold a gully.
# Not a statistic -- a sanity bound. See the comment where it is applied.
MAX_ADMIT_FRACTION = 0.25


def suggest_slope_std_min(slope_std_path: str, max_cells: int = 20_000_000,
                          nbins: int = 256) -> "dict":
    """Pick a gully roughness threshold from the shape of the distribution.

    **Why not a percentile.** The notebook used to say "gullies usually sit
    above p90; start there". That is not a statement about gullies, it is a
    statement about how much of the landscape you are willing to call gullied:
    p90 admits 10% of the DEM by construction whatever is in it. On a real
    1.94-billion-cell run it handed 190 million cells to the remaining tests
    and came back with 3,195 km of "gullies" against a 1,767 km stream network.

    The obvious correction -- "use p99 then" -- is just as arbitrary, and
    measurably worse: on both gully fixtures here p97 and above return **zero**
    features, because the top percentiles of ``slope_std`` are the steep valley
    walls and mountain flanks, not the small incised things. A percentile is
    the wrong instrument in both directions.

    **What the distribution actually looks like.** ``slope_std`` over terrain
    containing gullies is bimodal in the log: a broad smooth-ground mode and a
    separate incised mode, with a clear gap between them.

    ======================  ========  ========  =========
    fixture                 p75       p90       gap
    ======================  ========  ========  =========
    known 14 m gullies      0.0173    0.1561    9x
    branching valley net    0.0085    0.1001    12x
    ======================  ========  ========  =========

    The threshold belongs in that gap, and where the gap falls in percentile
    terms depends on what fraction of the landscape is incised -- which is the
    thing you are trying to measure, so you cannot assume it in advance. Otsu's
    method on ``log10(slope_std)`` finds the split without being told. It lands
    at p80.5 and p77.6 on the two fixtures above and recovers the known
    gullies at a measured 16.0 m width against a built 14 m.

    **This is advisory.** It returns a number and the evidence for it; it does
    not reach into ``detect_gullies``, which keeps ``slope_std_min`` as an
    ordinary explicit argument. Print it, look at ``percentile`` and
    ``separation_decades``, and override it when it looks wrong -- a landscape
    whose roughness is not bimodal has no gap to find, and this says so rather
    than inventing one.

    Returns
    -------
    dict
        ``slope_std_min`` -- the suggested threshold.
        ``percentile`` -- where it falls in the distribution, so a suggestion of
        "p99.8" is visibly a suggestion to find almost nothing.
        ``admits_fraction`` -- fraction of cells passing this test alone. Do not
        read this as a false-positive rate: ``slope_std`` is one of four tests
        and is not meant to be selective on its own. 20% here is normal.
        ``separation_decades`` -- distance between the two class means, in
        powers of ten, and the check on whether there was a gap at all. Otsu
        always returns a split; this is what says whether the split means
        anything. Measured: 1.02 and 1.20 decades on the two gullied fixtures,
        0.068 and 0.083 on tilted planes with only noise on them. The 0.4
        cutoff for ``bimodal`` sits in that order-of-magnitude gap rather than
        being picked.
        ``bimodal`` -- whether the split is worth trusting.
        ``note`` -- one line fit to print.
    """
    import numpy as np
    import rasterio
    from skimage.filters import threshold_otsu

    with rasterio.open(slope_std_path) as src:
        h, w = src.height, src.width
        total = h * w
        step = 1 if total <= max_cells else int(np.ceil(np.sqrt(total / max_cells)))
        arr = src.read(1, masked=True,
                       out_shape=(max(1, h // step), max(1, w // step)))

    v = np.asarray(arr.compressed(), dtype="float64")
    # Zero and negative cannot be logged. Flat ground genuinely has zero slope
    # variability, and it is the smooth mode -- dropping it would bias the
    # split upward, so it is counted back in for the percentile and fraction.
    n_all = int(v.size)
    v = v[np.isfinite(v)]
    n_zero = int((v <= 0).sum())
    pos = v[v > 0]
    if pos.size < 100:
        raise GeolensError(
            f"{slope_std_path} has only {pos.size} cells with non-zero slope "
            f"variability — there is no distribution here to split. Check that "
            f"terrain_roughness ran on the DEM you think it did."
        )

    lg = np.log10(pos)
    thr_log = float(threshold_otsu(lg, nbins=nbins))
    thr = float(10 ** thr_log)
    thr_log_admits = float((v >= thr).sum()) / max(n_all, 1)

    lo, hi = lg[lg < thr_log], lg[lg >= thr_log]
    sep = float(hi.mean() - lo.mean()) if lo.size and hi.size else 0.0
    admits = float((v >= thr).sum()) / max(n_all, 1)
    pct = 100.0 * (1.0 - admits)
    bimodal = sep >= 0.4

    # A separation test is not enough on its own. Measured on 10 m Ruidoso:
    # the modes are 0.50 decades apart, so `bimodal` passes, and Otsu still
    # puts the cut at p39 -- admitting 61% of the landscape. Handing that to
    # the detector cost 6 GB and an OOM kill before it produced anything.
    #
    # Whatever the histogram says, a quarter of a landscape cannot be gully.
    # Gullies are small incised features; even badly gullied country is a few
    # percent gullied by area. slope_std is one of four tests and is not meant
    # to be selective on its own -- 20% measured here is normal -- but past
    # about a quarter it has stopped discriminating at all, and a threshold
    # that discriminates nothing is not a threshold.
    #
    # So this is a BOUND, not a measurement, and it says so: it caps how much
    # of the DEM may be called rough rather than claiming to have found a gap.
    bounded = False
    if admits > MAX_ADMIT_FRACTION:
        thr = float(np.quantile(v, 1.0 - MAX_ADMIT_FRACTION))
        admits = float((v >= thr).sum()) / max(n_all, 1)
        pct = 100.0 * (1.0 - admits)
        bounded = True

    # Two things can be wrong at once and the note has to say both: a plane
    # with noise on it is not bimodal AND its Otsu split admits most of the
    # grid. Reporting only the cap would hide that there was never a gap.
    bits = []
    if bounded:
        bits.append(
            f"CAPPED — Otsu put the split at {thr_log_admits:.0%} of all "
            f"cells, which is not a gully signal, so it was raised to admit "
            f"at most {MAX_ADMIT_FRACTION:.0%}")
    if not bimodal:
        bits.append(
            f"the roughness distribution is not clearly bimodal "
            f"({sep:.2f} decades between modes, want >= 0.4), so there is no "
            f"real gap to sit in")
    if bits:
        note_s = (f"slope_std_min = {thr:.4f} (p{pct:.1f}) — "
                  + "; ".join(bits)
                  + ". Treat this as an upper bound, not a measurement: look "
                    "at the roughness raster and set slope_std_min yourself.")
    else:
        note_s = (f"slope_std_min = {thr:.4f} (p{pct:.1f}, admits "
                  f"{admits:.1%} of cells; modes {sep:.2f} decades apart)")

    return {"slope_std_min": thr, "percentile": pct,
            "admits_fraction": admits, "separation_decades": sep,
            "bimodal": bimodal, "bounded": bounded, "n_sampled": n_all,
            "n_zero": n_zero, "note": note_s}


def _roughness_arrays(z: np.ndarray, slope: np.ndarray, valid: np.ndarray,
                      win: int, twin: int, cell_m: float
                      ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Slope std, TPI and curvature of one block. The arithmetic is the one the
    whole-grid version did, in the same order, so a block and the grid agree."""
    # Var(X) = E[X^2] - E[X]^2, over valid cells only.
    mean = _masked_uniform(slope, valid, win)
    mean_sq = _masked_uniform(slope * slope, valid, win)
    var = np.maximum(mean_sq - mean * mean, 0.0)
    del mean, mean_sq
    slope_std = np.sqrt(var)
    del var
    slope_std[~valid] = np.nan

    tpi = z - _masked_uniform(z, valid, twin)
    tpi[~valid] = np.nan

    # Laplacian of elevation, scaled to 1/m2. Negative = convergent (hollow).
    # The 5-point Laplacian is 4 * (local mean over the 3x3 cross - centre), and
    # the masked local mean already ignores nodata, so this stays defined right
    # up to the edge of the data instead of inventing a rim.
    lap = 4.0 * (_masked_uniform(z, valid, 3) - z) / (cell_m * cell_m)
    lap[~valid] = np.nan
    return slope_std, tpi, lap


# Working memory per block, and the bytes each cell of a block costs while its
# three surfaces are built (inputs, mask, filter intermediates, outputs).
ROUGHNESS_BLOCK_BYTES = 3e9
_ROUGHNESS_BYTES_PER_CELL = 75.0


def terrain_roughness(
    terrain: TerrainResult,
    window_m: float = 90.0,
    tpi_window_m: float | None = None,
    guard: Guard | None = None,
    out_prefix: str = "roughness",
    block_rows: int | None = None,
) -> RoughnessResult:
    """Slope standard deviation, topographic position index, and curvature.

    Parameters
    ----------
    window_m
        Moving-window size for slope standard deviation. **This sets the size of
        feature you detect.** It should be a small multiple of the width of the
        gullies you care about: too small and the window sits entirely inside
        one wall (low variance, nothing found); too large and the gully is
        diluted by surrounding terrain. 3-5x expected gully width is a good
        start; at 30 m cells, 90-150 m.
    tpi_window_m
        Window for topographic position. Defaults to 3x ``window_m`` — TPI needs
        to see beyond the feature to know the feature is low.
    block_rows
        Rows per strip. Default: as many as fit ``ROUGHNESS_BLOCK_BYTES``
        (3 GB) of working memory.

    **Computed in horizontal strips.** Every output cell depends only on the
    cells within half a window of it, so each strip is read with that much
    overlap above and below, computed, and only its own rows written. The
    result is the whole-grid computation, cell for cell (the tests compare
    them), and the memory is one strip whatever the size of the DEM. Done
    whole, the ten or so full-size arrays came to ~50-75 bytes a cell: 90+ GB
    on Ruidoso's 1.785 billion cells, which is where the overnight run died.

    Returns paths to three rasters. All are reusable evidence, not decisions.
    """
    import rasterio
    from rasterio.windows import Window

    guard = guard or Guard.from_env()
    t0 = time.monotonic()
    warnings: list[str] = []

    with rasterio.open(terrain.dem) as src:
        profile = src.profile.copy()
        cell = abs(src.res[0])
        h, w = src.height, src.width

    # cell is in CRS units; window_m is metres. On a foot grid those differ.
    _mpu = (terrain.units or {}).get("metres_per_unit", 1.0) or 1.0
    cell_m = cell * _mpu

    # Test the requested window BEFORE clamping: `max(3, ...)` meant the
    # `win < 3` check below could never be true, so an under-resolved window
    # was silently widened and the caller was told nothing.
    win_requested = int(round(window_m / cell_m))
    win = max(3, win_requested)
    if win % 2 == 0:
        win += 1
    tpi_m = tpi_window_m if tpi_window_m else window_m * 3
    twin = max(3, int(round(tpi_m / cell_m)))
    if twin % 2 == 0:
        twin += 1

    if win_requested < 3:
        warnings.append(
            f"window_m ({window_m:g}) is {win_requested} cell(s) at "
            f"{cell_m:g} m resolution, so it was widened to {win} cells "
            f"({win * cell_m:g} m). Slope standard deviation needs at least a "
            f"3x3 neighbourhood — the window you get is not the one you asked "
            f"for, and the features it detects are that size."
        )

    # Overlap: half the widest window. Rows further than that from a strip's
    # own rows cannot reach them through any of the three filters.
    halo = max(win, twin, 3) // 2 + 1
    if block_rows is None:
        fit = int(ROUGHNESS_BLOCK_BYTES / (max(w, 1) * _ROUGHNESS_BYTES_PER_CELL))
        block_rows = max(256, (fit - 2 * halo) // 256 * 256)
    block_rows = max(1, int(block_rows))

    note(f"slope standard deviation in a {win}x{win} window ({win * cell_m:g} m), "
         f"TPI in {twin}x{twin} ({twin * cell_m:g} m), curvature; "
         f"{-(-h // block_rows)} strip(s) of {block_rows} rows")

    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {k: out_dir / f"{k}.tif" for k in ("slope_std", "tpi", "curvature")}
    p = gtiff_profile(profile, dtype="float32", nodata=-9999.0)

    with rasterio.open(terrain.dem) as sz, \
            rasterio.open(terrain.slope) as ss, \
            rasterio.open(str(paths["slope_std"]), "w", **p) as d_std, \
            rasterio.open(str(paths["tpi"]), "w", **p) as d_tpi, \
            rasterio.open(str(paths["curvature"]), "w", **p) as d_lap:
        for r0 in progress(range(0, h, block_rows), label="roughness strips",
                           total=-(-h // block_rows)):
            nr = min(block_rows, h - r0)
            a0, a1 = max(0, r0 - halo), min(h, r0 + nr + halo)
            rw = Window(0, a0, w, a1 - a0)
            _z = sz.read(1, window=rw, masked=True)
            z_valid = ~np.ma.getmaskarray(_z)
            z = np.asarray(_z.filled(np.nan), dtype="float32")
            del _z
            _s = ss.read(1, window=rw, masked=True)
            s_valid = ~np.ma.getmaskarray(_s)
            slope = np.asarray(_s.filled(np.nan), dtype="float32")
            del _s
            # The slope raster carries nodata (it used to write 0.0 with no
            # tag). Both masks should be the same set; if they are not, use
            # the intersection so nothing downstream reads a statistic built
            # from a fabricated value.
            valid = z_valid & s_valid
            del z_valid, s_valid

            sstd, tpi, lap = _roughness_arrays(z, slope, valid, win, twin,
                                               cell_m)
            del z, slope, valid
            core = slice(r0 - a0, r0 - a0 + nr)
            ow = Window(0, r0, w, nr)
            d_std.write(_nd(sstd[core]), 1, window=ow)
            d_tpi.write(_nd(tpi[core]), 1, window=ow)
            d_lap.write(_nd(lap[core]), 1, window=ow)
            del sstd, tpi, lap

    res = RoughnessResult(
        slope_std=str(paths["slope_std"]),
        tpi=str(paths["tpi"]),
        curvature=str(paths["curvature"]),
        window_m=win * cell_m,
        parameters={
            "window_m": win * cell_m, "window_cells": win,
            "tpi_window_m": twin * cell_m, "tpi_window_cells": twin,
            "cell_size": cell_m, "cell_size_crs": cell,
            "block_rows": block_rows, "halo_rows": halo,
        },
        seconds=time.monotonic() - t0,
        warnings=warnings,
    )
    note(f"roughness complete in {res.seconds:.1f}s")
    return res


# -- gullies ------------------------------------------------------------------


def _gully_scale_warnings(gdf, section_width_m: float, total_km: float,
                          network_km: float) -> list[str]:
    """Is what was found actually gully-scale?

    The detector warned when the DEM was too coarse to HOLD a gully. It said
    nothing about the far more common failure: correct code, wrong parameters,
    and an output full of creeks.

    Measured on a real 1.94-billion-cell run: 10,739 features totalling
    3,195 km against a 1,767 km stream network -- 1.8x the entire drainage
    network -- median width 31 m, depths to 37 m, and 28% of them draining more
    than 1 km2. Only 2.7% were gully-scale. Every number in that table was
    computed correctly and none of it was gullies.

    Separated from ``detect_gullies`` so the conditions can be tested on their
    own: the fixtures are small enough that several of them cannot be provoked
    from a real run.
    """
    out: list[str] = []
    if gdf is None or not len(gdf):
        return out

    w = gdf["width_m"].astype(float)
    d = gdf["drainage_km2_max"].astype(float)
    bs = gdf["bank_sections"].astype(float)

    # 1. Width saturating at the section width means no bank was found inside
    #    the section, so the reported width is a floor, not a measurement.
    meas = w.dropna()
    if len(meas) and float(meas.max()) > 0.8 * section_width_m:
        sat = float((meas > float(meas.max()) * 0.99).mean())
        if sat > 0.05:
            out.append(
                f"{sat:.0%} of gullies report almost exactly the same width "
                f"({float(meas.max()):.1f} m), which is the width of the "
                f"cross-section itself. No bank was found inside the section "
                f"for those, so their width and depth are limits of the "
                f"measurement rather than measurements. Raise section_width_m "
                f"well above the feature width."
            )

    # 2. Drainage area at stream scale means these are channels.
    if d.notna().any():
        big = float((d > 1.0).mean())
        if big > 0.10:
            out.append(
                f"{big:.0%} of detected gullies drain more than 1 km2, and the "
                f"median is {float(d.median()):.2f} km2. A gully is a "
                f"headwater feature — anything draining a square kilometre is "
                f"a creek. Set max_drainage_km2 -- it defaults to 0.5 and is "
                f"the only criterion here that means small -- or raise "
                f"slope_std_min to p99 or above, or raise stream_threshold_km2 "
                f"above the area your gullies reach and set "
                f"exclude_streams=True."
            )

    # 3. More gully length than stream network means it found the network.
    #    Only meaningful against a network long enough for the ratio to mean
    #    anything: on a small extent with a high stream threshold the network
    #    can be a couple of hundred metres and every honest run "exceeds" it.
    if network_km >= 1.0 and total_km > 0.8 * network_km:
        out.append(
            f"Detected gully length ({total_km:,.0f} km) is "
            f"{total_km / network_km:.1f}x the mapped stream network "
            f"({network_km:,.0f} km). The detector is mapping the drainage "
            f"network, not gullies within it."
        )

    # 4. Barely-measured features.
    if bs.notna().any():
        thin = float((bs < 3).mean())
        if thin > 0.5:
            out.append(
                f"{thin:.0%} of gullies yielded fewer than 3 cross-sections "
                f"with banks on both sides, so their width, depth and volume "
                f"rest on one or two samples and most got no polygon. Treat "
                f"those rows as locations, not geometry."
            )
    return out


def _gully_footprint(dem_src, line, max_half_width: float, min_depth_z: float,
                     mpu: float, vpu: float, min_area_cells: int = 6):
    """A gully's footprint from the ground itself, not from bank picks.

    Reconstruct the surface as it was before the gully cut it, subtract the
    DEM, and keep the cells that are deep enough and connected to the gully's
    own thalweg. The reconstruction is a morphological CLOSING: a maximum
    filter then a minimum filter of the same size, which fills any depression
    narrower than the window up to the level of its rims and leaves broader
    ground alone. Depth below that surface is the incision.

    Why not the bank picks. The previous footprint joined the bank tops of a
    handful of cross-sections into a ring. On Eagle Creek (0.61 m, EPSG:6529)
    that gave: a median of 2 usable sections per gully, only 55 footprints
    for 117 gullies, a median of 56% of each gully's own centreline inside
    its own polygon (5% at worst), straight square-ended slabs beside the
    line, and a figure-of-eight spanning two parallel incisions where one
    section picked up the neighbour's banks. Drawn over a hillshade none of
    them looked like the gully.

    What this guarantees instead: edges where the ground actually drops, the
    thalweg inside by construction (it is the deepest cell), no reliance on a
    bank detector that fails in exactly the confined channels gullies are,
    and a volume that is a sum of measured depths rather than
    width x depth x length x a shape factor.

    ``max_half_width`` (CRS units) sizes the closing window and caps how far
    from the centreline the footprint may reach, so a neighbouring incision
    beyond it cannot be absorbed. Returns ``(polygon, stats)`` or
    ``(None, reason)``.
    """
    from rasterio import features as rfeat
    from rasterio.windows import from_bounds
    from scipy import ndimage
    from shapely.geometry import shape as shp_shape
    from shapely.ops import unary_union

    import math

    from shapely.geometry import LineString, MultiLineString, Polygon

    pad = max_half_width * 1.5
    x0, y0, x1, y1 = line.bounds
    win = from_bounds(x0 - pad, y0 - pad, x1 + pad, y1 + pad,
                      transform=dem_src.transform)
    win = win.round_offsets().round_lengths()
    z = dem_src.read(1, window=win, boundless=True, masked=True)
    z = np.ma.filled(z.astype("float64"), np.nan)
    if z.size == 0 or not np.isfinite(z).any():
        return None, "no DEM under the gully"
    wt = dem_src.window_transform(win)
    inv = ~wt
    cell = abs(wt.a)
    valid = np.isfinite(z).astype("float64")
    zf = np.where(np.isfinite(z), z, 0.0)

    parts = ([line] if isinstance(line, LineString)
             else list(line.geoms) if isinstance(line, MultiLineString) else [])
    offs = np.arange(-max_half_width, max_half_width + cell / 2.0, cell)
    centre = int(np.argmin(np.abs(offs)))
    thal_reach = max(3.0 * cell, max_half_width / 4.0)
    look = max(3, int(round(2.0 / (cell * mpu))))       # ~2 m look-ahead

    def shoulder(p, i0, step):
        """Walk out from the thalweg; stop where the ground stops rising.

        The first point not exceeded within a short look-ahead -- the same
        rule the valley divide search uses. Walking to the section's highest
        point instead is what put the footprint halfway up the valley wall.
        Returns the index, or the last index if it never stops rising (the
        uphill side of a gully at the foot of a slope).
        """
        i = i0
        last = len(p) - 1 if step > 0 else 0
        while i != last:
            j = i + step
            ahead = p[j:j + step * look:step] if step > 0 else \
                p[max(0, j + step * look + 1):j + 1][::-1]
            if p[j] < p[i] and (ahead.size == 0
                                or np.nanmax(ahead) <= p[i]):
                return i
            i = j
        return last

    footprint = np.zeros(z.shape, dtype=bool)
    H_, W_ = z.shape
    widths, areas_m2, depths, maxd, flat = [], [], [], [], []
    run: list[tuple] = []           # (x, y, nx, ny, lo, hi, ps, rim)

    def flush():
        """Burn one contiguous run of stations into the footprint mask.

        Edges are median-filtered along the run first. Each station's edge is
        one noisy measurement, and a single station whose rim crossing lands
        far out drew a spike across the hillside. A running median over up
        to seven stations removes isolated spikes and keeps any real widening
        that lasts longer than that. Runs of fewer than three stations are
        dropped: two cross-sections 1.2 m apart are noise, not a gully reach.

        The footprint is burned as CELLS, not joined as a ring. A ring built
        from left and right edge points fans out on every sharp bend, because
        the section normals diverge on the outside of the curve -- on Eagle
        Creek that drew wedges tens of metres across the slope. Cells covered
        by each station's own cross-profile follow the ground through a bend
        with nothing to fan.
        """
        nonlocal run
        if len(run) >= 3:
            lo = np.array([r[4] for r in run])
            hi = np.array([r[5] for r in run])
            k = min(7, len(run) - (1 - len(run) % 2))
            if k >= 3:
                lo = ndimage.median_filter(lo, size=k, mode="nearest")
                hi = ndimage.median_filter(hi, size=k, mode="nearest")
            for (x, y, nx, ny, *_), a, b in zip(run, lo, hi):
                # Burn only the cells whose CENTRE the band covers. Marking
                # every cell the band touched added up to a cell on each side.
                o = np.arange(a, b + cell / 4.0, cell / 2.0)
                cc, rr = inv * (x + nx * o, y + ny * o)
                rr = np.asarray(rr)
                cc = np.asarray(cc)
                ri = np.floor(rr).astype(int)
                ci = np.floor(cc).astype(int)
                # distance from each sample to the centre of its cell, along
                # the section, must be within the band
                cx_, cy_ = wt * (ci + 0.5, ri + 0.5)
                along = (np.asarray(cx_) - x) * nx + (np.asarray(cy_) - y) * ny
                keep = ((ri >= 0) & (ri < H_) & (ci >= 0) & (ci < W_)
                        & (along >= a - 1e-9) & (along <= b + 1e-9))
                footprint[ri[keep], ci[keep]] = True
            for (x, y, nx, ny, _, _, ps_, rim_), a, b in zip(run, lo, hi):
                inside = (offs >= a) & (offs <= b)
                dd = np.clip(rim_ - ps_[inside], 0.0, None) * vpu
                widths.append((b - a) * mpu)
                areas_m2.append(float(dd.sum()) * cell * mpu)
                if dd.size:
                    depths.append(float(dd.mean()))
                    maxd.append(float(dd.max()))
                    # How much of the section sits near its deepest point. A
                    # V is ~0.2, a parabola ~0.45, a flat road bed 0.7-0.9.
                    if dd.size >= 3 and dd.max() > 0:
                        flat.append(float((dd >= 0.8 * dd.max()).mean()))
        run = []

    for part in parts:
        if part.length <= 0:
            continue
        step_s = 2.0 * cell
        stations = np.append(np.arange(0.0, part.length, step_s), part.length)
        delta = max(3.0 * cell, part.length / 20.0)   # smooth the pixel stairs

        for s in stations:
            o_pt = part.interpolate(float(s))
            a = part.interpolate(max(0.0, s - delta))
            b = part.interpolate(min(part.length, s + delta))
            tx, ty = b.x - a.x, b.y - a.y
            n = math.hypot(tx, ty)
            if n == 0:
                continue
            nx, ny = -ty / n, tx / n
            xs = o_pt.x + nx * offs
            ys = o_pt.y + ny * offs
            cols, rows = inv * (xs, ys)
            coords = np.vstack([np.asarray(rows) - 0.5,
                                np.asarray(cols) - 0.5])
            p = ndimage.map_coordinates(zf, coords, order=1, mode="nearest")
            ok = ndimage.map_coordinates(valid, coords, order=1,
                                         mode="constant", cval=0.0) > 0.99
            if ok.sum() < 5:
                flush()
                continue
            p = np.where(ok, p, np.nan)
            # Light smoothing so one noisy lidar return is not a shoulder.
            ps = np.convolve(np.nan_to_num(p, nan=np.nanmax(p)),
                             np.ones(3) / 3.0, mode="same")
            near = np.flatnonzero(np.abs(offs) <= thal_reach)
            ti = int(near[np.nanargmin(ps[near])])
            li = shoulder(ps, ti, -1)
            ri = shoulder(ps, ti, +1)
            # Water leaves at the LOWER shoulder. At the foot of a slope the
            # uphill side never tops out, so the rim is the downhill one and
            # the footprint stays in the gully instead of filling the corner.
            rim = min(ps[li], ps[ri])
            if rim - ps[ti] < min_depth_z:
                flush()
                continue

            # The crossing is found on the RAW profile. Smoothing is for
            # finding the shoulder, where one noisy return must not count as
            # a crest; applied to the crossing it smears every edge outward by
            # about a cell -- 2 m a side on a 2 m grid, which on a fixture of
            # known 14 m gullies read 25 m.
            pr = np.where(np.isfinite(p), p, rim)
            # The edge is where the incision becomes NEGLIGIBLE -- 5% of this
            # section's depth -- not where it reaches exactly zero. Exactly
            # zero is a level the flat ground either side only reaches by
            # noise, and a rim taken from a smoothed shoulder sits a hair
            # above the plane, so the first raw point to clear it could be
            # well out on the flat. With both effects the footprint read 17.5
            # m on known 14 m gullies; this is where the ground stops being
            # part of the gully in any sense that matters.
            edge_z = rim - 0.05 * (rim - ps[ti])

            def edge(i_from, i_to, step):
                i = i_from
                while i != i_to:
                    j = i + step
                    if pr[j] >= edge_z:
                        f = (edge_z - pr[i]) / max(pr[j] - pr[i], 1e-12)
                        return offs[i] + min(max(f, 0.0), 1.0) * (offs[j]
                                                                  - offs[i])
                    i = j
                return offs[i_to]

            lo, hi = edge(ti, li, -1), edge(ti, ri, +1)
            run.append((o_pt.x, o_pt.y, nx, ny, lo, hi, ps, rim))
        flush()

    if not footprint.any():
        return None, "no station deeper than min_depth_m below its lower rim"
    # Stations are two cells apart, so close the one-cell gaps between their
    # profiles, then keep only what connects to the gully's own centreline.
    footprint = ndimage.binary_closing(footprint, structure=np.ones((3, 3)),
                                       iterations=1)
    seed = rfeat.rasterize([(line, 1)], out_shape=z.shape, transform=wt,
                           fill=0, all_touched=True, dtype="uint8") > 0
    footprint = ndimage.binary_propagation(seed & footprint,
                                           mask=footprint,
                                           structure=np.ones((3, 3), bool))
    if int(footprint.sum()) < min_area_cells:
        return None, "footprint smaller than a few cells"
    polys = [shp_shape(g) for g, v in rfeat.shapes(
        footprint.astype("uint8"), mask=footprint, transform=wt) if v]
    poly = unary_union(polys)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty:
        return None, "footprint collapsed"
    stats = {
        "footprint_area_m2": float(poly.area * mpu * mpu),
        "footprint_mean_depth_m": float(np.median(depths)) if depths else
        float("nan"),
        "footprint_max_depth_m": float(np.max(maxd)) if maxd else float("nan"),
        # Cross-sectional area times station spacing, summed: a volume from
        # measured depths, not width x depth x length x a shape factor.
        "footprint_volume_m3": float(np.sum(areas_m2) * 2.0 * cell * mpu),
        # Area over length: the width the drawn footprint actually implies.
        # The per-station median came out at 45 m for a footprint visibly
        # 10-20 m wide, because stations at a junction measure the merged
        # floor; it is kept, labelled, for comparison.
        "footprint_width_m": float(poly.area * mpu * mpu
                                   / max(line.length * mpu, 1e-9)),
        "station_width_median_m": (float(np.median(widths)) if widths
                                   else float("nan")),
        "centreline_inside": float(line.intersection(poly).length
                                   / max(line.length, 1e-9)),
        "stations_used": len(widths),
        # Shape descriptors. These were added to tell a gully from a road --
        # a road has a flat bed and an engineered width -- and on the one
        # real road in hand they DO NOT: Eagle Creek gully 84 has bed
        # flatness 0.33, 17th of 110 and inside the gully range, and a
        # typical width_cv. The detected line follows the road's inside
        # DITCH, and a ditch is a small V. What flags it is depth (0.23 m ->
        # footprint_quality "doubtful"). Kept as diagnostics, deliberately
        # not turned into a road flag on one example.
        "bed_flatness": float(np.median(flat)) if flat else float("nan"),
        "width_cv": (float(np.std(widths) / np.mean(widths))
                     if len(widths) >= 3 and np.mean(widths) > 0
                     else float("nan")),
    }
    return poly, stats


def _gully_polygon(secs: dict, dl: dict, min_sections: int = 3):
    """Close a polygon through the MEASURED bank tops of one gully.

    The detector already cuts cross-sections along every centreline and runs
    the verified reach-scale bank detection on them -- that is where
    ``width_m`` and ``mean_depth_m`` come from. The bank positions were then
    reduced to a median and thrown away.

    Keeping them gives a footprint with real edges instead of a buffered line:
    left bank tops in station order, right bank tops in reverse, closed. Where
    the gully narrows or widens, the polygon does too, because every vertex is
    a measurement rather than an offset.

    World coordinates are rebuilt with the same convention
    :func:`geolens.sections.extract_cross_sections` used to cut the section --
    origin at ``center_x``/``center_y``, left-hand normal from ``bearing_deg``
    -- so left and right cannot silently swap and tie the polygon in a bow.
    """
    import math

    from shapely.geometry import Polygon

    by_station = {round(float(x["station"]), 3): x
                  for x in (secs.get("sections") or [])}

    left_pts: list[tuple[float, float, float]] = []
    right_pts: list[tuple[float, float, float]] = []
    for d in (dl.get("sections") or []):
        geom = by_station.get(round(float(d.get("station", -1)), 3))
        if geom is None:
            continue
        L = d.get("left") or {}
        R = d.get("right") or {}
        lo, ro = L.get("bank_top_offset"), R.get("bank_top_offset")
        if lo is None or ro is None:
            continue
        cx, cy = geom.get("center_x"), geom.get("center_y")
        bearing = geom.get("bearing_deg")
        if cx is None or cy is None or bearing is None:
            continue
        br = math.radians(float(bearing))
        tx, ty = math.sin(br), math.cos(br)
        nx, ny = -ty, tx                      # left-hand normal, as cut
        st = float(d["station"])
        left_pts.append((st, cx + nx * float(lo), cy + ny * float(lo)))
        right_pts.append((st, cx + nx * float(ro), cy + ny * float(ro)))

    if len(left_pts) < min_sections:
        return None, len(left_pts)

    left_pts.sort(key=lambda t: t[0])
    right_pts.sort(key=lambda t: t[0])
    ring = ([(x, y) for _, x, y in left_pts]
            + [(x, y) for _, x, y in reversed(right_pts)])
    poly = Polygon(ring)
    if not poly.is_valid:
        # A tight meander can cross the two banks over. buffer(0) resolves the
        # self-intersection into valid geometry rather than dropping the gully.
        poly = poly.buffer(0)
    if poly.is_empty or poly.area <= 0:
        return None, len(left_pts)
    return poly, len(left_pts)


def _windowed_gully_mask(
    slope_std_path: str, tpi_path: str, slope_path: str, acc_path: str,
    slope_std_min: float, tpi_max: float, min_slope: float,
    cell_area_km2: float, min_drainage_km2: float, max_drainage_km2: float,
    block: int = 2048,
) -> tuple[np.ndarray, dict[str, int]]:
    """Build the gully mask a block at a time.

    Every criterion here is **elementwise** -- four thresholds on four rasters,
    no neighbourhood term -- so the mask tiles perfectly with no overlap and no
    edge effect. That makes the memory cost a choice rather than a constraint.

    It was not being treated as one. The four rasters were each read whole and
    cast to float64: 8 bytes x 1.94 billion cells is **15.5 GB each, 62 GB for
    the four**, plus a fifth for the drainage-area array. On the DEM this was
    written for, that is the difference between running and being killed after
    the two expensive steps before it have already succeeded.

    Windowed, the peak is one block per raster plus the output mask, and the
    mask is bool: 1 byte per cell rather than 8.

    Also returns how many cells each criterion admitted on its own, because
    "no gullies found" is nearly always one criterion doing all the rejecting
    and there is no way to tell which without counting.
    """
    import rasterio
    from rasterio.windows import Window

    with rasterio.open(slope_std_path) as src:
        h, w = src.height, src.width

    mask = np.zeros((h, w), dtype=bool)
    counts = {"slope_std": 0, "tpi": 0, "slope": 0, "drainage": 0, "all": 0}

    srcs = {k: rasterio.open(v) for k, v in (
        ("sstd", slope_std_path), ("tpi", tpi_path),
        ("slope", slope_path), ("acc", acc_path))}
    try:
        for r0 in range(0, h, block):
            nr = min(block, h - r0)
            for c0 in range(0, w, block):
                nc = min(block, w - c0)
                win = Window(c0, r0, nc, nr)

                a = srcs["sstd"].read(1, window=win, masked=True)
                m_sstd = np.ma.filled(a >= slope_std_min, False)
                a = srcs["tpi"].read(1, window=win, masked=True)
                m_tpi = np.ma.filled(a <= tpi_max, False)
                a = srcs["slope"].read(1, window=win, masked=True)
                m_slope = np.ma.filled(a >= min_slope, False)
                a = srcs["acc"].read(1, window=win, masked=True).astype("float32")
                area = np.ma.filled(np.maximum(a, 1.0) * cell_area_km2, 0.0)
                m_area = (area >= min_drainage_km2) & (area <= max_drainage_km2)

                counts["slope_std"] += int(m_sstd.sum())
                counts["tpi"] += int(m_tpi.sum())
                counts["slope"] += int(m_slope.sum())
                counts["drainage"] += int(m_area.sum())

                block_mask = m_sstd & m_tpi & m_slope & m_area
                counts["all"] += int(block_mask.sum())
                mask[r0:r0 + nr, c0:c0 + nc] = block_mask
    finally:
        for src in srcs.values():
            src.close()
    return mask, counts


def detect_gullies(
    terrain: TerrainResult,
    roughness: RoughnessResult | None = None,
    slope_std_min: float = 0.04,
    tpi_max: float = -0.3,
    min_drainage_km2: float = 0.005,
    max_drainage_km2: float | None = 0.5,
    min_slope: float = 0.02,
    min_length_m: float = 60.0,
    window_m: float = 90.0,
    exclude_streams: bool = False,
    merge_snap_m: float | None = None,
    merge_max_angle_deg: float = 40.0,
    section_spacing_m: float = 20.0,
    section_width_m: float = 60.0,
    bank_slope_gully: float = 0.12,
    flat_slope_gully: float = 0.05,
    min_depth_m: float = 0.25,
    exclude_valley_raster: str | None = None,
    exclude: str | None = None,
    exclude_buffer_m: float = 10.0,
    polygons: bool = False,
    guard: Guard | None = None,
    out_prefix: str = "gullies",
) -> GullyResult:
    """Detect gullies from slope variability, incision and drainage.

    A cell is gullied when ALL hold:

    * slope standard deviation >= ``slope_std_min`` (locally variable relief)
    * TPI <= ``tpi_max`` (sits below its surroundings)
    * slope >= ``min_slope`` (actually on a grade)
    * drainage area between ``min_drainage_km2`` and ``max_drainage_km2``

    Parameters
    ----------
    slope_std_min
        The primary control. Raise it to keep only sharply incised features;
        lower it to catch subtle or partly healed gullies. Look at the
        ``slope_std`` raster's own distribution before guessing — a percentile
        of that raster is a far better starting point than a literature number.
    tpi_max
        Must be negative. Near zero admits any rough ground; more negative keeps
        only clearly incised features.
    max_drainage_km2
        Upper bound on contributing area, and **the only criterion here that
        says "small"**. Everything else — slope variability, TPI, slope — is
        equally true of a creek, a river and a gully, so without this bound the
        detector cannot tell them apart and will happily return the drainage
        network. It defaults to 0.5 km2, which is generous enough for a
        Southwest arroyo.

        The cost is real and you should know it: a gully that collects more
        than the ceiling is truncated at the point where it does, so its
        downstream end is missing. That is a measurement error. Returning
        53 km2 creeks is a category error, and the category error is worse.
        Pass ``None`` to disable the bound deliberately — then pair it with
        ``exclude_streams=True`` and a ``stream_threshold_km2`` set above the
        area your gullies reach, or you will get the network back.
    merge_snap_m, merge_max_angle_deg
        Fragments whose ends fall within ``merge_snap_m`` and continue each
        other within ``merge_max_angle_deg`` are joined into one gully.
        Skeletonizing leaves short barbs, and each barb is a junction that plain
        endpoint merging cannot resolve — without this a single gully is
        reported as several. ``merge_snap_m`` defaults to 3 cells. Raise the
        angle to join more aggressively; lower it if separate gullies are being
        welded together at confluences.
    section_spacing_m, section_width_m
        Cross-sections are cut along each detected centreline to measure real
        width and depth. ``section_width_m`` must comfortably exceed the gully
        width — it needs to reach undisturbed ground either side.
    bank_slope_gully, flat_slope_gully, min_depth_m
        Bank-detection thresholds for those sections. Gully walls are steeper
        than river banks but shorter, so ``min_depth_m`` is the guard that keeps
        surface roughness from reading as a gully rim.
    exclude_streams
        Subtract the terrain run's stream network before detecting. **This
        interacts with ``stream_threshold_km2`` and is off by default for that
        reason.** If the threshold is low enough that your gullies are already
        classified as streams, turning this on deletes exactly what you are
        looking for. Use it only when the threshold is set well above the
        drainage area your gullies reach — otherwise raise
        ``stream_threshold_km2`` in ``build_terrain`` instead.
    exclude_valley_raster
        Optional valley-bottom mask to subtract. Useful when floodplain
        micro-topography is registering as gullies.
    polygons
        Also write ``gully_polygons.gpkg``. **Off by default (v0.27):** in
        steep terrain the footprint climbs the walls of the V-shaped hollow
        the gully sits in -- on Cherokee Bill (0.61 m lidar) the median
        footprint was 31 m wide and 11 m deep for gullies draining under
        0.5 km2 -- and Sam judged them "not great". The width, depth and
        volume columns on the lines come from the same footprints and carry
        the same caveat.
    """
    import rasterio
    from scipy import ndimage
    from skimage.morphology import skeletonize

    # The body builds a list called `polygons`; keep the switch under its
    # own name before that happens.
    write_polygons = bool(polygons)
    guard = guard or Guard.from_env()
    t0 = time.monotonic()
    warnings: list[str] = []

    if tpi_max >= 0:
        raise GeolensError(
            f"tpi_max must be negative — a gully sits BELOW its surroundings "
            f"(got {tpi_max})."
        )

    if roughness is None:
        roughness = terrain_roughness(terrain, window_m=window_m, guard=guard)
        warnings.extend(roughness.warnings)

    # A gully is a few metres wide. On a coarse DEM there is nothing to find,
    # and what IS found is slope noise dressed up as features. Say so rather
    # than returning hundreds of confident-looking artefacts.
    _cell = terrain.cell_size
    if _cell > 5.0:
        warnings.append(
            f"DEM cell size is {_cell:g} m. Gullies are typically 2-15 m wide, so "
            "at this resolution they are smaller than a cell and anything "
            "detected is more likely slope noise than a gully. Gully work needs "
            "lidar (<= 2 m). Treat these results as unreliable."
        )
    _pts = int(round(section_width_m / _cell)) + 1
    if _pts < 11:
        warnings.append(
            f"section_width_m ({section_width_m:g}) spans only {_pts} samples at "
            f"{_cell:g} m cells — too few for bank detection, so width and depth "
            f"will be NaN. Raise section_width_m to at least {_cell * 12:.0f}."
        )

    with rasterio.open(roughness.slope_std) as src:
        profile = src.profile.copy()
        cell = abs(src.res[0])
        n_cells = src.height * src.width

    # True metres, so window sizes in metres stay metres on a foot grid.
    cell_m = terrain.cell_size
    cell_area_km2 = ((terrain.units or {}).get("cell_area_m2", cell_m ** 2)) / 1e6

    # Metres -> CRS units. `cell` above is in CRS units and every *_m
    # parameter is in metres, and the cross-section cutter takes CRS units.
    # They were handed straight across: on EPSG:6529 (US survey feet) a
    # section asked for at 60 m was cut at 60 ft = 18.3 m, which on a 10 m
    # grid is "2 sections x 3 points" -- observed, in a real run -- and bank
    # detection on three samples returns nothing. Every gully width and depth
    # measured on a foot grid was wrong by this factor.
    _mpu = float((terrain.units or {}).get("metres_per_unit", 0.0)) or (
        (cell_m / cell) if cell else 1.0)
    def _to_units(metres: float) -> float:
        return float(metres) / _mpu

    # Elevations carry their own unit, which is not always the horizontal one.
    _vpu = float((terrain.units or {}).get("vertical_metres_per_unit", 0.0)) \
        or _mpu
    _m2 = _mpu * _mpu

    # The drainage ceiling is the only test here that means "small". Slope
    # variability, TPI and slope are all just as true of a river as of a gully,
    # so with the ceiling off the detector returns the drainage network -- on a
    # real 1.94-billion-cell run, 3,195 km of "gullies" against a 1,767 km
    # network. Disabling it is a deliberate choice, and it gets a warning.
    unbounded = max_drainage_km2 is None
    if unbounded:
        max_drainage_km2 = float("inf")
        warnings.append(
            "max_drainage_km2 is None, so contributing area is unbounded and "
            "nothing in the remaining criteria distinguishes a gully from a "
            "creek or a river -- slope variability, TPI and slope are all true "
            "of any incised channel. Expect the drainage network back unless "
            "exclude_streams is on and stream_threshold_km2 sits above the "
            "area your gullies reach."
        )

    # Say what the rest of this will cost BEFORE spending it. The mask build is
    # windowed, but thinning and labelling are whole-grid operations on a bool
    # array and there is no way to know that from the outside.
    _mask_gb = n_cells / 1e9
    _peak_gb = _mask_gb * 4          # mask + skeleton + label + one working copy
    if _peak_gb > 8.0:
        warnings.append(
            f"This grid is {n_cells / 1e9:.2f} billion cells. The evidence "
            f"combination below is windowed and cheap, but thinning and "
            f"labelling are whole-grid operations on a boolean array: expect a "
            f"peak around {_peak_gb:.0f} GB. If that is more RAM than you have, "
            f"clip the DEM to the area you actually care about — gully work is "
            f"a reach-scale question and a 60 km tile is not."
        )
    note(f"combining gully evidence over {n_cells:,} cells "
         f"(mask {_mask_gb:.2f} GB; expect ~{_peak_gb:.0f} GB peak)")
    mask, hits = _windowed_gully_mask(
        roughness.slope_std, roughness.tpi, terrain.slope, terrain.accumulation,
        slope_std_min=slope_std_min, tpi_max=tpi_max, min_slope=min_slope,
        cell_area_km2=cell_area_km2, min_drainage_km2=min_drainage_km2,
        max_drainage_km2=max_drainage_km2,
    )
    # Report each test as a FRACTION of the grid, not just a count. A count of
    # 190 million means nothing on its own; "19% of the landscape" is instantly
    # recognisable as too much ground to be calling gullied, and it is the one
    # number that makes a percentile threshold's consequence visible before the
    # expensive half of the run.
    note("  cells passing each test on its own: "
         + ", ".join(f"{k} {v:,} ({v / max(n_cells, 1):.1%})"
                     for k, v in hits.items() if k != "all")
         + f"; all four {hits['all']:,} "
           f"({hits['all'] / max(n_cells, 1):.1%})")

    # NOT a warning on the slope_std pass rate. I added one -- "gullies are
    # under 1% of a landscape, so warn above 2%" -- and then measured it: on
    # both gully fixtures the threshold that correctly recovers the known
    # gullies admits 19-22% of cells on this test ALONE, and the four tests
    # together plus skeletonizing and min_length cut that to six features.
    # slope_std is not meant to be selective by itself, so a rate warning on it
    # fires on good runs. The per-test fractions above are the honest form:
    # information, not a verdict.

    # exclude_streams and max_drainage_km2 have to agree about where the line
    # between a gully and a stream is, or they fight: cells between the stream
    # threshold and the ceiling are gully-eligible AND mapped as streams, so
    # turning exclude_streams on deletes exactly what is being looked for. Set
    # both to the same number and the two sets are disjoint by construction.
    #
    # The other direction is fine: a stream threshold ABOVE the ceiling means
    # no stream cell was ever gully-eligible, and exclusion only trims the
    # 3-cell buffer around mapped channels.
    _st = float(getattr(terrain, "stream_threshold_km2", 0.0) or 0.0)
    if exclude_streams and 0.0 < _st < max_drainage_km2:
        warnings.append(
            f"exclude_streams is on with stream_threshold_km2={_st:g} km2 below "
            f"max_drainage_km2={max_drainage_km2:g} km2, so every cell draining "
            f"between the two is both gully-eligible and a mapped stream — and "
            f"the exclusion wins. That silently removes the largest gullies. "
            f"Set stream_threshold_km2 to {max_drainage_km2:g} as well, or turn "
            f"exclude_streams off."
        )

    if exclude_streams:
        with rasterio.open(terrain.streams) as src:
            stream_mask = src.read(1).astype(bool)
    else:
        stream_mask = np.zeros_like(mask)
    if exclude_streams and stream_mask.any():
        # Dilate a little so the channel's own banks are not counted as gullies.
        # Dilate 3 cells so the channel's own banks are not counted as gullies.
        stream_buf = ndimage.binary_dilation(stream_mask, np.ones((3, 3)),
                                             iterations=3)
        removed = int((mask & stream_buf).sum())
        mask = mask & ~stream_buf
        if removed:
            note(f"excluded {removed:,} cells on the mapped stream network")

    if exclude_valley_raster:
        vp = guard.read_path(exclude_valley_raster)
        with rasterio.open(str(vp)) as src:
            vb = src.read(1).astype(bool)
        removed = int((mask & vb).sum())
        mask = mask & ~vb
        if removed:
            note(f"excluded {removed:,} cells inside the valley bottom")

    if not mask.any():
        # Name the criterion that did the rejecting. "No gullies found" is
        # nearly always one test admitting almost nothing, and without the
        # per-test counts there is no way to tell which one.
        tests = {k: v for k, v in hits.items() if k != "all"}
        tightest = min(tests, key=tests.get)
        limits = {"slope_std": f"slope_std_min={slope_std_min}",
                  "tpi": f"tpi_max={tpi_max}",
                  "slope": f"min_slope={min_slope}",
                  "drainage": f"min_drainage_km2={min_drainage_km2}"}
        warnings.append(
            f"No gully cells found. The tightest test was {tightest} "
            f"({tests[tightest]:,} cells passed it, against "
            f"{max(tests.values()):,} for the loosest) — start by relaxing "
            f"{limits[tightest]}. Per-test counts: "
            + ", ".join(f"{k} {v:,}" for k, v in tests.items())
        )
        if exclude_streams:
            warnings.append(
                "exclude_streams is on: if stream_threshold_km2 is low enough "
                "that your gullies count as streams, this removed them. Turn it "
                "off, or raise stream_threshold_km2 in build_terrain."
            )

    note("thinning to gully centrelines")
    # Close single-cell gaps so a gully broken by noise stays one feature.
    mask = ndimage.binary_closing(mask, np.ones((3, 3)), border_value=0)
    skel = skeletonize(mask)

    note("vectorizing")
    from .network import _trace_skeleton, merge_fragments

    paths = _trace_skeleton(skel)
    import geopandas as gpd
    from rasterio.transform import xy as _rc_to_xy
    from shapely.geometry import LineString

    with rasterio.open(roughness.slope_std) as src:
        transform = src.transform
        crs = src.crs

    raw_lines = []
    for path in paths:
        rows = [r for r, _ in path]
        cols = [c for _, c in path]
        xs_, ys_ = _rc_to_xy(transform, rows, cols, offset="center")
        if len(xs_) >= 2:
            raw_lines.append(LineString(list(zip(xs_, ys_))))

    note(f"merging {len(raw_lines):,} traced fragments")
    lines_out = merge_fragments(
        raw_lines, min_length_m=_to_units(min_length_m),
        # merge_fragments works in the geometry's own units, so a metres
        # parameter has to be converted; the cell-based default already is.
        snap_tol_m=(_to_units(merge_snap_m) if merge_snap_m is not None
                    else cell * 3),
        max_angle_deg=merge_max_angle_deg,
    )

    # --- set aside features the user says are not gullies -------------------
    #
    # Roads are the standing false positive: incised, steep-sided, linear --
    # everything this detector looks for. On Eagle Creek one of the detected
    # "gullies" is a road, followed along its inside ditch. Shape alone did
    # not separate it (a ditch is a small V, just like a gully -- measured,
    # see _gully_footprint), so the honest filter is a map of where the roads
    # ARE: TIGER, OSM, or a county layer. A line with most of its length
    # within exclude_buffer_m of one is set aside -- written to its own layer
    # for review, never silently dropped.
    excluded_lines: list = []
    if exclude is not None and lines_out:
        ex = gpd.read_file(str(guard.read_path(exclude)))
        if ex.crs is not None and crs is not None and ex.crs != crs:
            ex = ex.to_crs(crs)
        from shapely.ops import unary_union as _uu
        zone = _uu([g.buffer(_to_units(exclude_buffer_m))
                    for g in ex.geometry if g is not None and not g.is_empty])
        kept = []
        for ln in lines_out:
            share = (ln.intersection(zone).length / ln.length
                     if ln.length > 0 and not zone.is_empty else 0.0)
            (excluded_lines if share > 0.5 else kept).append((ln, share))
        lines_out = [ln for ln, _ in kept]
        if excluded_lines:
            note(f"  set aside {len(excluded_lines)} line(s) lying along "
                 f"{Path(str(exclude)).name}")

    from rasterio.transform import rowcol as _xy_to_rc

    note("allocating gully cells to centrelines")
    id_r = np.zeros(mask.shape, dtype="int32")
    line_cells: list[tuple[np.ndarray, np.ndarray]] = []
    for i, line in enumerate(lines_out):
        xs_l, ys_l = line.xy
        rr, cc = _xy_to_rc(transform, list(xs_l), list(ys_l))
        rr = np.clip(np.asarray(rr), 0, mask.shape[0] - 1)
        cc = np.clip(np.asarray(cc), 0, mask.shape[1] - 1)
        id_r[rr, cc] = i + 1
        line_cells.append((rr, cc))

    if (id_r > 0).any():
        # return_distances=False: see the note in classify.py. The distance
        # array is full-size float64 and nothing reads it.
        ar, ac = ndimage.distance_transform_edt(
            id_r == 0, sampling=cell, return_indices=True,
            return_distances=False
        )
        alloc = id_r[ar, ac]
        area_counts = np.bincount(alloc[mask].ravel(),
                                  minlength=len(lines_out) + 1)
    else:
        area_counts = np.zeros(len(lines_out) + 1, dtype=int)

    # --- true cross-section geometry ---------------------------------------
    #
    # The detection MASK hugs the steep walls; it is not the gully section, so
    # its area/length is not gully width. Measure the real thing by cutting
    # perpendicular sections along each centreline and running the same
    # bank-detection used at reach scale — code already verified against a
    # known compound channel.
    from ..morphology import delineate as _delineate
    from ..sections import extract_cross_sections as _xsect

    tmp_dir = Path(guard.write_path("gully_tmp_dir")).with_suffix("")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # slope_std and drainage area are needed only ON the centrelines — a few
    # thousand cells out of billions — so they are read by bounding window per
    # gully rather than held as two full-size arrays.
    _sstd_src = rasterio.open(roughness.slope_std)
    _acc_src = rasterio.open(terrain.accumulation)
    _dem_src = rasterio.open(terrain.dem)
    # try/finally, not two close() calls at the end. In Jupyter -- which
    # is how this is actually run -- a raised exception is kept alive by
    # sys.last_traceback, which keeps this frame alive, which keeps these
    # two datasets open. On Windows an open handle blocks rewriting the
    # file, so one failed gully run locked slope_std and accumulation
    # until the kernel was restarted, and the obvious next move -- re-run
    # the step that writes them -- was exactly what could not work.
    try:

        def _sample(src, rr, cc, scale=1.0):
            from rasterio.windows import Window

            if len(rr) == 0:
                return np.array([], dtype="float32")
            r0, r1 = int(rr.min()), int(rr.max()) + 1
            c0, c1 = int(cc.min()), int(cc.max()) + 1
            arr = src.read(1, window=Window(c0, r0, c1 - c0, r1 - r0),
                           masked=True).astype("float32")
            vals = np.ma.filled(arr[rr - r0, cc - c0], np.nan)
            return vals * scale

        records = []
        polygons: list[Any] = []
        for i, line in enumerate(progress(lines_out, label="measuring gullies")):
            rr, cc = line_cells[i]
            seg_sstd = _sample(_sstd_src, rr, cc)
            seg_area = np.maximum(_sample(_acc_src, rr, cc), 1.0) * cell_area_km2
            length = float(line.length) * _mpu        # true metres
            # cell_area_km2 is true metres (see above); `cell` is CRS units, so
            # using it here reported ft2 as m2 on a foot grid -- 10.76x too small,
            # sitting in the same table as measured_area_m2 and gullied_area_km2,
            # which are both metric.
            gully_area = float(area_counts[i + 1]) * cell_area_km2 * 1e6

            width_m = depth_m = max_depth_m = float("nan")
            poly, n_bank_sections = None, 0
            one = tmp_dir / f"_gully_{i}.gpkg"
            try:
                gpd.GeoDataFrame({"id": [i]}, geometry=[line], crs=crs).to_file(
                    str(one), driver="GPKG"
                )
                secs = _xsect(
                    terrain.dem, str(one),
                    spacing=max(_to_units(section_spacing_m), cell * 2),
                    width=_to_units(section_width_m),
                    point_spacing=cell,
                    guard=guard,
                )
                dl = _delineate(
                    secs, bank_slope=bank_slope_gully, flat_slope=flat_slope_gully,
                    valley_slope=max(bank_slope_gully * 1.3, flat_slope_gully * 2),
                    # Vertical: a 0.25 m minimum bank height became 0.25 ft
                    # on a foot-elevation DEM, three times shallower than
                    # asked for.
                    min_bank_height=min_depth_m / _vpu,
                    run_length=1, smooth_window=1,
                )
                ok_secs = [r for r in dl["sections"] if r.get("bankfull")]
                if ok_secs:
                    widths = [r["bankfull"]["width"] for r in ok_secs]
                    depths = [r["bankfull"]["max_depth"] for r in ok_secs]
                    # Widths come back in CRS units and depths in the DEM's
                    # z unit. Both were written straight into columns named
                    # _m; on a US survey foot grid every gully width, depth
                    # and volume in the output was a foot value labelled
                    # metres.
                    width_m = float(np.median(widths)) * _mpu
                    depth_m = float(np.median(depths)) * _vpu
                    max_depth_m = float(np.max(depths)) * _vpu
                poly, n_bank_sections = _gully_polygon(secs, dl)
            except Exception as exc:          # a gully too short/steep to section
                warnings.append(f"gully {i}: section measurement failed ({exc})")

            finally:
                # Delete it now, not at the end. `_xsect` takes a path, so each
                # gully needs its centreline on disk for one call -- but the run
                # that found 10,739 of them left 10,739 GeoPackages behind, in the
                # user's output folder, which on Windows is typically under
                # Documents and therefore cloud-synced. Peak file count is 1.
                try:
                    one.unlink(missing_ok=True)
                except OSError:
                    pass

            # The footprint from the ground: depth below the lower rim,
            # measured densely along the whole centreline. This replaces the
            # bank-pick ring as the footprint wherever it succeeds -- see
            # _gully_footprint for what the ring did on real terrain. The
            # section-based numbers above stay in the table as section_*.
            # NOT `section_width_m`: that is the parameter, and reassigning it
            # here would hand every later gully this one's measured width as
            # its section width.
            sec_w_m, sec_d_m = width_m, depth_m
            method, inside = ("bank_sections" if poly is not None else None,
                              float("nan"))
            try:
                fp, st = _gully_footprint(
                    _dem_src, line, _to_units(section_width_m / 2.0),
                    min_depth_m / _vpu, _mpu, _vpu)
            except Exception as exc:                         # noqa: BLE001
                fp, st = None, f"{type(exc).__name__}: {exc}"
            if fp is not None:
                poly = fp
                method = "lower_rim_profiles"
                width_m = st["footprint_width_m"]
                depth_m = st["footprint_mean_depth_m"]
                max_depth_m = st["footprint_max_depth_m"]
                inside = st["centreline_inside"]
                fp_volume = st["footprint_volume_m3"]
            else:
                fp_volume = float("nan")
                if poly is not None:
                    inside = float(line.intersection(poly).length
                                   / max(line.length, 1e-9))
            records.append(
                {
                    "gully_id": i,
                    "length_m": length,
                    "width_m": width_m,
                    "mean_depth_m": depth_m,
                    "max_depth_m": max_depth_m,
                    # Summed from measured depths when the footprint came from
                    # the ground; the old width x depth x length x 0.667
                    # parabola only as a fallback.
                    "volume_m3": (
                        fp_volume if np.isfinite(fp_volume)
                        else width_m * depth_m * length * 0.667
                        if np.isfinite(width_m) and np.isfinite(depth_m)
                        else float("nan")
                    ),
                    "footprint_method": method,
                    "centreline_inside": inside,
                    # For filtering, not a verdict. A detected line that is
                    # mostly NOT inside an incision (a hummocky slope, a slope
                    # break) or a footprint barely deeper than min_depth_m (a
                    # road bed, a stock trail) is flagged doubtful. On Eagle
                    # Creek that separated the real gullies from a road and
                    # two wandering slope lines at a glance.
                    "footprint_quality": (
                        "none" if method is None
                        else "doubtful" if (not np.isfinite(inside)
                                            or inside < 0.5
                                            or not np.isfinite(depth_m)
                                            or depth_m < 2.0 * min_depth_m)
                        else "partial" if inside < 0.8
                        else "good"),
                    "section_width_m": sec_w_m,
                    "section_depth_m": sec_d_m,
                    # The cells the DETECTOR admitted, allocated to this gully.
                    # This is NOT the gully's footprint and must not be used as
                    # one: the thresholds (slope variability + negative TPI) select
                    # the steepest, most incised part of the section, which is a
                    # ribbon near the centreline. On a fixture of known 14 m
                    # gullies it implies a width of 2.45 m — about one cell —
                    # understating the real footprint roughly six-fold. Use
                    # measured_area_m2.
                    "mask_area_m2": gully_area,
                    # MEASURED from bank top to bank top on the cross-sections.
                    # This is the footprint. On the same fixture it implies 15.75 m
                    # against a known 14 m, which is the one-sample bank-top offset
                    # already documented at reach scale.
                    "measured_area_m2": (float(poly.area) * _m2
                                         if poly is not None
                                         else float("nan")),
                    "bank_sections": n_bank_sections,
                    # nan-aware. _sample fills masked cells with NaN by design,
                    # and a skeleton can sit on a nodata cell near a DEM hole or
                    # clip edge -- one such cell turned the WHOLE gully's stats to
                    # NaN while every other column stayed fine.
                    "mean_slope_std": (float(np.nanmean(seg_sstd))
                                       if np.isfinite(seg_sstd).any() else float("nan")),
                    "drainage_km2_max": (float(np.nanmax(seg_area))
                                         if np.isfinite(seg_area).any() else float("nan")),
                    "geometry": line,
                }
            )
            polygons.append(poly)

        gdf = gpd.GeoDataFrame(
            records, crs=crs,
            columns=["gully_id", "length_m", "width_m", "mean_depth_m", "max_depth_m",
                     "volume_m3", "footprint_method", "centreline_inside",
                     "footprint_quality",
                     "mask_area_m2", "measured_area_m2", "section_width_m",
                     "section_depth_m", "bank_sections", "mean_slope_std",
                     "drainage_km2_max", "geometry"],
        )

        out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
        out_dir.mkdir(parents=True, exist_ok=True)
        lines_path = out_dir / "gullies.gpkg"
        gdf.to_file(str(lines_path), driver="GPKG")

        if excluded_lines:
            gpd.GeoDataFrame(
                {"share_along_excluded": [s for _, s in excluded_lines],
                 "length_m": [ln.length * _mpu for ln, _ in excluded_lines]},
                geometry=[ln for ln, _ in excluded_lines], crs=crs,
            ).to_file(str(out_dir / "gullies_excluded.gpkg"), driver="GPKG")
            warnings.append(
                f"{len(excluded_lines)} detected line(s) lay mostly within "
                f"{exclude_buffer_m:g} m of the exclude layer and were set "
                f"aside, not measured. They are in gullies_excluded.gpkg — "
                f"check them, because a gully running beside a road is still "
                f"a gully."
            )

        # --- the same gullies as 2D footprints -----------------------------------
        #
        # Built from the measured bank tops, not by buffering the line: where a
        # gully widens the polygon widens, because every vertex came off a section.
        # Same gully_id, so it joins straight back to the lines and the sections.
        polys_path = out_dir / "gully_polygons.gpkg"
        poly_rows = [(rec, g) for rec, g in zip(records, polygons) if g is not None]
        if not write_polygons:
            poly_rows = []
        if poly_rows:
            pg = gpd.GeoDataFrame(
                [{k: v for k, v in rec.items() if k != "geometry"}
                 for rec, _ in poly_rows],
                geometry=[g for _, g in poly_rows], crs=crs,
            )
            pg.to_file(str(polys_path), driver="GPKG")
            n_poly = len(pg)
        else:
            n_poly = 0
            polys_path = None

        if write_polygons and n_poly < len(records):
            warnings.append(
                f"{len(records) - n_poly} of {len(records)} gullies got no polygon: "
                f"fewer than 3 of their cross-sections yielded banks on both sides. "
                f"Their lines are still in gullies.gpkg. Widen section_width_m, or "
                f"lower min_depth_m if the gullies are shallow."
            )

    finally:
        _sstd_src.close()
        _acc_src.close()
        _dem_src.close()
        # The per-gully centrelines are deleted as they go; this removes
        # the directory itself, including any left by a failed iteration.
        shutil.rmtree(tmp_dir, ignore_errors=True)

    mask_path = _write_like(profile, mask.astype("uint8"),
                            out_dir / "gully_mask.tif", dtype="uint8", nodata=0)
    # The slope_std raster already exists — terrain_roughness wrote it. Copying
    # it into the gully directory duplicated a multi-gigabyte file for nothing.
    ev_path = roughness.slope_std


    total_km = float(gdf["length_m"].sum()) / 1000.0 if len(gdf) else 0.0

    # Gullied area from the MEASURED footprints, not from the detector mask.
    # The mask is a near-centreline ribbon (see mask_area_m2 above) and using
    # it here reported roughly a sixth of the real gullied area.
    _measured = gdf["measured_area_m2"] if len(gdf) else None
    if _measured is not None and np.isfinite(_measured).any():
        gullied_km2 = float(np.nansum(_measured)) / 1e6
        _mask_km2 = float(mask.sum()) * cell_area_km2
        if _mask_km2 > 0 and gullied_km2 / _mask_km2 > 2.0:
            note(f"gullied area from measured footprints: {gullied_km2:.4f} km2 "
                 f"(the detector mask alone would say {_mask_km2:.4f})")
    else:
        gullied_km2 = float(mask.sum()) * cell_area_km2
        warnings.append(
            "No gully yielded enough cross-sections with banks on both sides, "
            "so gullied_area_km2 falls back to the detector mask — which is a "
            "near-centreline ribbon and understates the real footprint several "
            "fold. Treat it as a lower bound."
        )
    # Count REAL ground, not the bounding box. A DEM clipped to a watershed can
    # be a third nodata, and dividing by the box understates gully density by
    # that fraction while looking perfectly reasonable.
    with rasterio.open(terrain.dem) as _src:
        _valid_cells = 0
        from rasterio.windows import Window as _W
        for _r in range(0, _src.height, 4096):
            _nr = min(4096, _src.height - _r)
            _a = _src.read(1, window=_W(0, _r, _src.width, _nr), masked=True)
            _valid_cells += int((~np.ma.getmaskarray(_a)).sum())
    landscape_km2 = float(_valid_cells or mask.size) * cell_area_km2
    density = total_km / landscape_km2 if landscape_km2 else 0.0

    # The mapped stream network's length, for the scale check below. Counting
    # stream cells is enough: it is a skeleton, so cells x cell size is its
    # length to within the usual diagonal factor.
    network_km = 0.0
    try:
        with rasterio.open(terrain.streams) as _src:
            _n = 0
            from rasterio.windows import Window as _W
            for _r in range(0, _src.height, 4096):
                _nr = min(4096, _src.height - _r)
                _n += int((_src.read(1, window=_W(0, _r, _src.width, _nr)) > 0).sum())
        network_km = _n * cell_m / 1000.0
    except Exception:                                   # noqa: BLE001
        network_km = 0.0

    warnings.extend(_gully_scale_warnings(
        gdf, section_width_m=section_width_m, total_km=total_km,
        network_km=network_km))

    elapsed = time.monotonic() - t0
    note(f"{len(gdf):,} gullies, {total_km:.2f} km, in {elapsed:.1f}s")

    mean_depth = float(gdf["mean_depth_m"].mean()) if len(gdf) else float("nan")
    mean_width = float(gdf["width_m"].mean()) if len(gdf) else float("nan")
    volume = float(gdf["volume_m3"].sum()) if len(gdf) else float("nan")

    return GullyResult(
        gully_raster=mask_path,
        gully_lines=str(lines_path),
        gully_polygons=(str(polys_path) if polys_path else None),
        evidence_raster=ev_path,
        gully_count=len(gdf),
        total_length_km=total_km,
        gullied_area_km2=gullied_km2,
        gully_density_km_per_km2=density,
        mean_depth_m=mean_depth,
        mean_width_m=mean_width,
        eroded_volume_m3=volume,
        parameters={
            # Every argument that changes the answer, not just the detection
            # thresholds. The measurement half -- section width and spacing,
            # the bank-detection limits -- decides width, depth, volume and
            # whether a gully gets a polygon at all, and none of it was
            # recorded. A run log that cannot answer "what section width
            # produced these widths" is not a run log.
            "slope_std_min": slope_std_min, "tpi_max": tpi_max,
            "min_slope": min_slope, "min_drainage_km2": min_drainage_km2,
            "max_drainage_km2": max_drainage_km2, "min_length_m": min_length_m,
            "exclude_streams": exclude_streams,
            "exclude_valley_raster": exclude_valley_raster,
            "stream_threshold_km2": getattr(terrain, "stream_threshold_km2", None),
            "section_spacing_m": section_spacing_m,
            "section_width_m": section_width_m,
            "bank_slope_gully": bank_slope_gully,
            "flat_slope_gully": flat_slope_gully,
            "min_depth_m": min_depth_m,
            "merge_snap_m": merge_snap_m,
            "merge_max_angle_deg": merge_max_angle_deg,
            "exclude": str(exclude) if exclude is not None else None,
            "exclude_buffer_m": exclude_buffer_m,
            "n_excluded": len(excluded_lines),
            **roughness.parameters,
        },
        seconds=elapsed,
        warnings=warnings,
    )


# -- headcuts -----------------------------------------------------------------


def detect_headcuts(
    dem: str | Path,
    lines: str | Path | None = None,
    stream_threshold_km2: float = 0.005,
    tolerance_m: float = 1.0,
    window_m: float = 12.0,
    min_excess_drop_m: float = 0.8,
    min_spacing_m: float = 60.0,
    line_buffer_m: float = 15.0,
    one_per_line: bool = True,
    guard: Guard | None = None,
    out_prefix: str = "headcuts",
) -> HeadcutResult:
    """Find headcuts (knickpoints) using TopoToolbox's published algorithm.

    This replaces a hand-rolled gradient walker that gave wrong answers. The old
    one traced flow paths across the raster and measured gradient assuming
    consecutive path points were adjacent cells; when a trace merged
    disconnected fragments it reported 380 m drops on a DEM with 85 m of total
    relief. The fix is not a better tracer — it is to walk the stream *graph*,
    where an edge is by construction a real connection, and to let
    ``topotoolbox.StreamObject.knickpointfinder`` (Schwanghart & Scherler) find
    the convexities.

    How it works
    ------------
    1. ``knickpointfinder`` fits a strictly concave profile to each stream and
       flags the nodes where the real profile departs from it by more than
       ``tolerance_m``. That gives candidates — on a noisy DEM, many of them.
    2. For each candidate, walk ``window_m`` downstream **along the graph** and
       measure the elevation lost.
    3. Subtract what the reach immediately below would have lost over the same
       distance. That difference — ``excess_drop_m`` — is the headcut's own
       height, with the background gradient removed. On a fixture with 2.2 m
       headcuts on a 4.5% hillslope it recovers 2.0–2.8 m; raw drop reads
       3.1–3.3 m because it includes the hillslope's own fall.
    4. If ``lines`` is given, keep only candidates on those lines, and take the
       **upstream-most** one per line.

    Why upstream-most and not largest
    ---------------------------------
    A headcut is the migrating head of an incising channel, so it is the
    furthest-upstream abrupt step, not the biggest one. This matters more than
    it sounds: on the gully fixture, selecting the largest step per gully scores
    1 of 6 with 17% precision, while selecting the upstream-most scores **6 of 6
    with 100% precision**, every point within 3–6 m of its known position. The
    large steps further down are real convexities in the gully floor; they are
    simply not the headcut.

    Parameters
    ----------
    dem
        Path to the DEM. TopoToolbox does its own depression handling, so pass
        the raw DEM rather than a conditioned one.
    lines
        Line layer to restrict the search to — pass ``gully_lines`` from
        :func:`detect_gullies`. **Strongly recommended.** Without it, every
        convexity in the whole network is a candidate: on the gully fixture that
        is 29 reported features for 6 real headcuts, about 20% precision. The
        recall is the same either way; what ``lines`` buys is precision.
    stream_threshold_km2
        Contributing area at which a cell becomes a stream. For gully-scale work
        this must be small — the default 0.005 km² is 5000 m², because a gully
        head drains a hillslope, not a catchment. Too large and the upstream
        gully heads are simply not in the network to be found: at 0.01 km² the
        fixture loses one of its six.
    tolerance_m
        Passed to ``knickpointfinder``: how far the real profile must depart
        from a concave fit. TopoToolbox defaults to 100 m, which is meant for
        mountain rivers and finds nothing at gully scale. Set it near the
        smallest step you care about.
    window_m
        Distance over which the drop is measured. Should bracket the expected
        step length — the fixture's headcuts gain 2.2 m over an 8 m ramp, so 12
        m spans them with a little room. Too long and the step is averaged into
        the hillslope.
    min_excess_drop_m
        Minimum background-corrected drop. This is the real sensitivity knob.
    one_per_line
        Keep only the upstream-most headcut per line. Turn off to see every
        candidate on each line, ranked.

    Notes
    -----
    Requires ``topotoolbox``: ``pip install topotoolbox``, or
    ``pip install "geolens[headcuts]"``.
    """
    import geopandas as gpd
    import pandas as pd
    import rasterio
    from shapely.geometry import Point

    try:
        import topotoolbox as tt3
    except ImportError as e:  # pragma: no cover - environment dependent
        raise GeolensError(
            "detect_headcuts needs topotoolbox.\n"
            "  pip install topotoolbox\n\n"
            "It implements the published Schwanghart & Scherler knickpoint "
            "method. geolens does not reimplement it -- the version that tried "
            "gave wrong answers."
        ) from e

    t0 = time.monotonic()
    guard = guard or Guard.from_env()
    warnings: list[str] = []

    # A TerrainResult may be passed instead of a path; take its source DEM.
    dem_path = getattr(dem, "dem", None) or getattr(dem, "conditioned", None) or dem
    dem_path = guard.read_path(dem_path)

    with rasterio.open(str(dem_path)) as src:
        cell = abs(src.transform.a)
        crs = src.crs
        bounds = src.bounds
    cell_area = cell * cell
    threshold_px = max(1, int(stream_threshold_km2 * 1e6 / cell_area))

    note(f"headcuts: stream threshold {stream_threshold_km2} km2 "
         f"= {threshold_px} cells at {cell:g} m")

    grid = tt3.read_tif(str(dem_path))
    flow = tt3.FlowObject(grid)
    net = tt3.StreamObject(flow, threshold=threshold_px, units="pixels")
    # NOTE: do NOT call net.removeedgeeffects(flow) here. It removes every
    # stream that touches the boundary, and on the gully fixture that takes the
    # gully heads with it -- recall goes from 6/6 to 0/6. Edge artefacts are
    # handled by the edge_m attribute below, which lets the caller filter
    # rather than silently deleting real features.

    z = np.asarray(net.ezgetnal(grid), dtype=float)
    xs = np.asarray(net.x, dtype=float)
    ys = np.asarray(net.y, dtype=float)
    acc_nal = np.asarray(net.ezgetnal(flow.flow_accumulation()), dtype=float)

    # The downstream map. net.source / net.target are the edge list; the
    # similarly named source_indices / target_indices are (row, col) pairs and
    # are NOT the graph -- using those produces a map that is almost all -1 and
    # a walk that stops immediately.
    down = np.full(len(z), -1, dtype=np.int64)
    down[np.asarray(net.source)] = np.asarray(net.target)

    def _walk(start: int, distance: float) -> tuple[int, float]:
        """Follow the stream graph downstream until ``distance`` is covered."""
        travelled, j = 0.0, start
        while travelled < distance:
            k = down[j]
            if k < 0:
                break
            travelled += float(np.hypot(xs[k] - xs[j], ys[k] - ys[j]))
            j = k
        return j, travelled

    flags = np.asarray(net.knickpointfinder(grid, tolerance=tolerance_m))
    candidates = np.flatnonzero(flags)
    note(f"headcuts: {len(candidates)} raw knickpoints on {len(z)} stream nodes")

    rows: list[dict[str, Any]] = []
    for idx in progress(candidates, label="measuring knickpoints"):
        i = int(idx)
        below, travelled = _walk(i, window_m)
        if travelled <= 0:
            continue
        further, travelled2 = _walk(below, window_m)
        background = (z[below] - z[further]) / travelled2 if travelled2 > 0 else 0.0
        drop = float(z[i] - z[below])
        rows.append({
            "x": float(xs[i]), "y": float(ys[i]),
            "elevation_m": float(z[i]),
            "drop_m": drop,
            "excess_drop_m": drop - background * travelled,
            "window_m": float(travelled),
            "gradient": drop / travelled,
            "background_gradient": float(background),
            "drainage_km2": float(acc_nal[i] * cell_area / 1e6),
            "edge_m": float(min(xs[i] - bounds.left, bounds.right - xs[i],
                                ys[i] - bounds.bottom, bounds.top - ys[i])),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df[df["excess_drop_m"] >= min_excess_drop_m]

    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "headcuts.gpkg"
    all_path = out_dir / "knickpoint_candidates.gpkg"

    if df.empty:
        gpd.GeoDataFrame({"drop_m": []}, geometry=[], crs=crs).to_file(
            str(out_path), driver="GPKG")
        return HeadcutResult(
            headcuts=str(out_path), headcut_count=0,
            summary={"note": "no knickpoints passed the excess-drop threshold"},
            parameters={"tolerance_m": tolerance_m, "window_m": window_m,
                        "min_excess_drop_m": min_excess_drop_m,
                        "stream_threshold_km2": stream_threshold_km2},
            seconds=time.monotonic() - t0,
            warnings=warnings + ["No headcuts found. Lower min_excess_drop_m or "
                                 "tolerance_m, or lower stream_threshold_km2 so "
                                 "the upstream ends are in the network at all."],
        )

    pts = gpd.GeoDataFrame(
        df, geometry=[Point(a, b) for a, b in zip(df["x"], df["y"])], crs=crs)
    # Everything that passed the threshold, written whether or not it survives
    # the per-line selection: the candidates are expensive to recompute and
    # useful for choosing a threshold.
    pts.to_file(str(all_path), driver="GPKG")

    if lines is not None:
        line_gdf = gpd.read_file(str(guard.read_path(lines)))
        if line_gdf.crs != pts.crs:
            line_gdf = line_gdf.to_crs(pts.crs)
        line_gdf = line_gdf[["geometry"]].reset_index(names="line_id")
        pts = gpd.sjoin_nearest(pts, line_gdf, max_distance=line_buffer_m,
                                how="inner").drop(columns=["index_right"],
                                                  errors="ignore")
        if pts.empty:
            warnings.append(
                f"No knickpoint fell within {line_buffer_m} m of the given "
                f"lines. Either the lines are not on the stream network, or "
                f"line_buffer_m is too small for this cell size."
            )
        elif one_per_line:
            # Upstream-most = HIGHEST. Elevation is the only unambiguous
            # measure here, and the two obvious alternatives both fail:
            #
            #   * smallest contributing area scores 0/6 on the fixture. Short
            #     headwater stubs of unrelated streams sit within the join
            #     distance of a gully's downstream end and carry less drainage
            #     than the gully's own head, so they win.
            #   * smallest x scores 6/6 here only because these gullies happen
            #     to run west to east. It is not a rule, it is the fixture.
            #
            # Highest elevation scores 6/6, median 3 m from the true positions,
            # and holds whichever way the channel points.
            pts = (pts.sort_values("elevation_m", ascending=False)
                      .groupby("line_id", as_index=False, sort=False)
                      .head(1))
    else:
        warnings.append(
            "No `lines` given, so every convexity in the network is reported. "
            "Recall is the same either way, but precision is low -- on the "
            "gully fixture this is 29 features for 6 real headcuts. Pass "
            "gully_lines from detect_gullies to get one headcut per gully."
        )
        # Greedy spatial thinning so one step is not reported as a cluster.
        pts = pts.sort_values("excess_drop_m", ascending=False)
        kept: list[tuple[float, float]] = []
        mask = []
        for _, r in pts.iterrows():
            ok = all(np.hypot(r["x"] - a, r["y"] - b) > min_spacing_m
                     for a, b in kept)
            mask.append(ok)
            if ok:
                kept.append((r["x"], r["y"]))
        pts = pts[mask]

    if not pts.empty:
        pts.to_file(str(out_path), driver="GPKG")
    else:
        # ALWAYS write, even when empty. The result returns this path
        # unconditionally, and Guard.write_path reuses the output directory
        # across runs — so a run that found nothing used to hand back the
        # PREVIOUS run's headcuts, reported as this run's, with
        # headcut_count=0 sitting next to a file full of features. On a first
        # run the path simply did not exist and reading it raised.
        empty = gpd.GeoDataFrame(
            {c: pd.Series(dtype=pts[c].dtype) for c in pts.columns
             if c != "geometry"},
            geometry=[], crs=pts.crs,
        )
        empty.to_file(str(out_path), driver="GPKG")

    ex = pts["excess_drop_m"] if not pts.empty else pd.Series(dtype=float)
    return HeadcutResult(
        headcuts=str(out_path),
        headcut_count=int(len(pts)),
        summary={
            "candidates_file": str(all_path),
            "raw_knickpoints": int(len(candidates)),
            "passed_threshold": int(len(df)),
            "reported": int(len(pts)),
            "excess_drop_m": {
                "min": float(ex.min()) if len(ex) else None,
                "median": float(ex.median()) if len(ex) else None,
                "max": float(ex.max()) if len(ex) else None,
            },
            "method": ("topotoolbox knickpointfinder (Schwanghart & Scherler), "
                       "background gradient removed, upstream-most per line"),
        },
        parameters={
            "stream_threshold_km2": stream_threshold_km2,
            "tolerance_m": tolerance_m,
            "window_m": window_m,
            "min_excess_drop_m": min_excess_drop_m,
            "min_spacing_m": min_spacing_m,
            "line_buffer_m": line_buffer_m,
            "one_per_line": one_per_line,
            "restricted_to_lines": lines is not None,
        },
        seconds=time.monotonic() - t0,
        warnings=warnings,
    )
