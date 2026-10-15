"""DEM conditioning, flow routing and HAND at landscape scale.

Two interchangeable backends:

* **pysheds** (pure Python + numba) — no external binary, comfortable to roughly
  50 million cells. The default, and what the test suite verifies.
* **WhiteboxTools** (Rust binary, ``pip install whitebox``) — faster and far more
  memory-efficient on very large grids, with least-cost depression breaching
  that preserves valley floors better than filling. Preferred above ~50M cells.

HAND (Height Above Nearest Drainage) is the vertical distance from each cell to
the stream cell it drains into, measured along the flow path. It is the single
most useful primitive for valley-bottom delineation: low HAND means "close to
the channel in the vertical sense that matters for inundation and deposition",
which is what a valley bottom actually is.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

import numpy as np

from ..safety import Guard, GeolensError, note
from ..raster import gtiff_profile


Backend = Literal["auto", "pysheds", "whitebox"]


def available_backends() -> dict[str, bool]:
    """Report which flow-routing backends can actually be used here."""
    out = {}
    try:
        # pysheds.grid, NOT pysheds. pysheds/__init__.py contains one line
        # (`__version__ = "0.5"`) and imports nothing, so `import pysheds`
        # succeeds even when none of its dependencies are installed --
        # geojson and looseversion are pulled by pysheds.grid. Probing the
        # top-level package reports a backend as available that then fails on
        # first use, which is a worse outcome than reporting it missing.
        from pysheds.grid import Grid  # noqa: F401

        out["pysheds"] = True
    except Exception:
        out["pysheds"] = False
    # Probe for the binary on disk WITHOUT constructing WhiteboxTools() — the
    # constructor tries to download it, which hangs or errors noisily behind a
    # proxy. Availability means "already installed", never "could be fetched".
    try:
        import importlib.util

        spec = importlib.util.find_spec("whitebox")
        if spec is None or not spec.submodule_search_locations:
            out["whitebox"] = False
        else:
            pkg = Path(list(spec.submodule_search_locations)[0])
            out["whitebox"] = any(
                (pkg / "WBT" / name).exists()
                for name in ("whitebox_tools", "whitebox_tools.exe")
            )
    except Exception:
        out["whitebox"] = False
    return out


def backend_notes() -> dict[str, str]:
    """Why a backend is or is not available, in words.

    WhiteboxTools is a pip package that does NOT ship its binary: the binary is
    fetched on first construction of ``WhiteboxTools()``. So `pip install
    whitebox` succeeding tells you nothing, and behind a proxy or an offline
    machine the download fails with a 403 that surfaces much later, during an
    analysis.
    """
    avail = available_backends()
    notes = {}
    notes["pysheds"] = (
        "ready" if avail["pysheds"] else
        "not installed — pip install pysheds"
    )
    if avail["whitebox"]:
        notes["whitebox"] = "ready (binary present)"
    else:
        try:
            import importlib.util

            has_pkg = importlib.util.find_spec("whitebox") is not None
        except Exception:                                  # noqa: BLE001
            has_pkg = False
        notes["whitebox"] = (
            "the python package is installed but its BINARY is not. It is "
            "downloaded on first use from whiteboxgeo.com; run "
            "`python -c \"import whitebox; whitebox.WhiteboxTools()\"` once on a "
            "machine with internet. Behind a proxy this fails with 403."
            if has_pkg else
            "not installed — pip install whitebox (MIT licensed, Rust binary; "
            "strongly preferred above ~50M cells)"
        )
    return notes


def _pick(backend: Backend, cells: int) -> str:
    avail = available_backends()
    if backend != "auto":
        if not avail.get(backend):
            raise GeolensError(
                f"Backend {backend!r} is not available here. "
                f"Available: {[k for k, v in avail.items() if v]}. "
                "Install with `pip install pysheds` or `pip install whitebox`."
            )
        return backend
    # Above ~50M cells pysheds gets memory-hungry; prefer the Rust binary.
    if cells > 50_000_000 and avail.get("whitebox"):
        return "whitebox"
    if avail.get("pysheds"):
        return "pysheds"
    if avail.get("whitebox"):
        return "whitebox"
    raise GeolensError(
        "No flow-routing backend available. Install one:\n"
        "  pip install pysheds     (pure Python, good to ~50M cells)\n"
        "  pip install whitebox    (Rust binary, better for larger grids)"
    )


@dataclass
class TerrainResult:
    """Paths to the rasters produced by a terrain run, plus run metadata."""

    dem: str
    conditioned: str
    flow_dir: str
    accumulation: str
    streams: str
    hand: str
    slope: str
    backend: str
    cell_size: float               # TRUE metres (see geolens.units)
    crs: str | None
    cells: int
    stream_threshold_cells: int
    stream_threshold_km2: float
    seconds: float
    warnings: list[str]
    cell_size_crs: float = 0.0     # cell size in the CRS's own units
    units: dict = field(default_factory=dict)
    # What the routing cache did on this call: whether it was enabled,
    # where it lives, whether it hit, what was reused, and on a miss
    # why. Without this the only evidence is a status line on stderr.
    routing_cache: dict = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _write_like(src_path: Path, array: np.ndarray, out: Path, dtype: str = "float32",
                nodata: float | None = -9999.0) -> str:
    import rasterio

    with rasterio.open(str(src_path)) as src:
        profile = src.profile.copy()
    profile = gtiff_profile(profile, dtype=dtype, nodata=nodata)
    with rasterio.open(str(out), "w", **profile) as dst:
        dst.write(array.astype(dtype), 1)
    return str(out)


def _axis_gradient(z: np.ndarray, valid: np.ndarray, h: float,
                   axis: int) -> np.ndarray:
    """Centred difference where both neighbours exist, one-sided where one does.

    ``np.gradient`` on an array with NaN for nodata propagates the NaN one cell
    into real data, so the ring of valid cells around every hole loses its
    slope. Filling the hole first is worse: a nearest-value fill makes the
    centred difference at the ring (z[i+1] - z[i]) / 2h, which is HALF the true
    one-sided slope, and a global-mean fill invents a cliff.

    So: use a real one-sided difference at the ring, and return NaN only where
    the cell itself has no data.
    """
    n = z.shape[axis]
    if n < 2:
        return np.zeros_like(z)

    def sl(a, b):
        out = [slice(None), slice(None)]
        out[axis] = slice(a, b)
        return tuple(out)

    g = np.full(z.shape, np.nan, dtype="float32")

    # forward difference z[i+1] - z[i], defined where both are valid
    fwd = np.full(z.shape, np.nan, dtype="float32")
    ok = valid[sl(1, None)] & valid[sl(0, -1)]
    np.subtract(z[sl(1, None)], z[sl(0, -1)], out=fwd[sl(0, -1)],
                where=ok, casting="unsafe")
    fwd[sl(0, -1)][~ok] = np.nan
    fwd /= h

    # backward difference is the forward difference shifted by one
    bwd = np.full(z.shape, np.nan, dtype="float32")
    bwd[sl(1, None)] = fwd[sl(0, -1)]

    both = np.isfinite(fwd) & np.isfinite(bwd)
    g[both] = 0.5 * (fwd[both] + bwd[both])
    only_f = np.isfinite(fwd) & ~both
    g[only_f] = fwd[only_f]
    only_b = np.isfinite(bwd) & ~both
    g[only_b] = bwd[only_b]
    g[~valid] = np.nan
    return g


def _slope(dem_path: Path, out: Path) -> str:
    """Slope as rise/run, honouring cell size, nodata and the CRS's units.

    Three things were wrong here and all three were silent:

    * nodata became slope **0.0**, and the raster was written with no nodata
      tag, so every consumer read flat ground where there is no data. On a real
      DEM that was 630 million fabricated zeros.
    * the ring of VALID cells around each hole also became 0.0, because NaN
      propagates one cell through ``np.gradient``. ``valley._fuzzy_high`` reads
      slope 0.0 as maximum evidence for "flat valley floor" -- so a closed ring
      of strongest-possible valley evidence formed around every void, and
      ``binary_fill_holes`` could then swallow the void into the valley bottom.
    * float64 throughout, which is 7.2 GB per temporary on a 900M-cell grid,
      for a quantity that needs nothing like that precision.
    """
    import rasterio

    with rasterio.open(str(dem_path)) as src:
        arr = src.read(1, masked=True)
        rx, ry = abs(src.res[0]), abs(src.res[1])
        crs_obj = src.crs

    valid = ~np.ma.getmaskarray(arr)
    z = np.asarray(arr.filled(np.nan), dtype="float32")
    del arr

    # Slope is rise over run, so BOTH have to be in the same unit. On a ftUS
    # grid the run is in feet; the rise is too (same unit), so the ratio is
    # dimensionless and correct without conversion -- but only because they
    # match. If z were metres over a foot grid the slope would be 3.28x wrong,
    # which is why units are resolved rather than assumed.
    from ..units import unit_info

    u = unit_info(crs_obj, rx, ry)
    run_x = rx * u.metres_per_unit
    run_y = ry * u.metres_per_unit
    rise = u.vertical_metres_per_unit

    gx = _axis_gradient(z, valid, run_x / rise, axis=1)
    gy = _axis_gradient(z, valid, run_y / rise, axis=0)
    del z

    s = np.hypot(gx, gy, dtype="float32")
    del gx, gy
    s[~valid] = -9999.0
    s[~np.isfinite(s)] = -9999.0
    return _write_like(dem_path, s, out, nodata=-9999.0)


# -- pysheds backend ----------------------------------------------------------


def _pysheds_numpy2_shim() -> None:
    """pysheds 0.5 calls ``np.in1d``, removed in NumPy 2.0.

    Restore it as an alias of ``np.isin``, which is the documented replacement
    and behaves identically for the 1-D integer case pysheds uses. Narrow and
    reversible; drop this once pysheds ships a NumPy 2 release.
    """
    if not hasattr(np, "in1d"):
        np.in1d = np.isin  # type: ignore[attr-defined]



def _streams_from_acc(acc: np.ndarray, threshold_cells: int, dem_path: Path,
                      warnings: list[str]) -> np.ndarray:
    """Cells above the contributing-area threshold, **restricted to real ground**.

    A nodata cell still collects flow from its valid neighbours. On a clipped
    watershed -- which is what most real DEMs are -- the collar around the data
    is exactly where all that flow ends up, so those cells sail past any stream
    threshold and get marked as streams. Their elevation is the nodata value.

    HAND then measures height above them. ``_hand_from_flow`` walks downslope
    to the first stream cell and subtracts its elevation, so every cell draining
    to the collar reports ``elevation - (-9999)`` = ``elevation + 9999``. That
    is a plausible-looking number in a plausible-looking raster, and the only
    symptom the user sees is that the map "doesn't look right" -- which is
    exactly how it was reported.

    Measured on a synthetic clipped basin (300x300, elliptical nodata collar):
    **794 of 1,053 stream cells, 75%, were on nodata**, and 26,547 cells came
    back with HAND above the DEM's own 96.2 m relief, the worst 11,997.
    ``_sanitise_hand`` caught and masked those, but catching it downstream is
    not the same as not doing it -- the masked cells are real ground whose HAND
    was simply lost.

    A cell whose flow path leaves the DEM without passing a stream has no
    defined HAND, and after this it gets NaN rather than a fabricated number.
    That is the honest answer, and it is why this is done here rather than by
    widening the tolerance in ``_sanitise_hand``.
    """
    import rasterio

    raw = acc > threshold_cells
    with rasterio.open(str(dem_path)) as src:
        z = src.read(1, masked=True)
    valid = ~np.ma.getmaskarray(z)

    if valid.shape != raw.shape:
        # Should not happen; if it does, the grids are already inconsistent and
        # silently skipping the mask is better than crashing on a shape error.
        warnings.append(
            f"Stream mask ({raw.shape}) and DEM ({valid.shape}) have different "
            f"shapes, so streams could not be restricted to valid ground. HAND "
            f"may be measured above nodata — check hand.tif against the DEM."
        )
        return raw

    streams = raw & valid
    dropped = int((raw & ~valid).sum())
    if dropped:
        pct = 100.0 * dropped / max(int(raw.sum()), 1)
        warnings.append(
            f"{dropped:,} cells ({pct:.0f}% of everything above the stream "
            f"threshold) exceeded it while sitting on nodata — flow collecting "
            f"in the collar around a clipped DEM. They were dropped from the "
            f"stream network; left in, HAND would have been measured above the "
            f"nodata value. Cells that drain only to the collar now get no HAND "
            f"rather than a wrong one."
        )
    return streams


def _sanitise_hand(hand: np.ndarray, dem_path: Path,
                   warnings: list[str]) -> np.ndarray:
    """Mask HAND to real ground and enforce the one invariant it has.

    **HAND cannot exceed the relief of the DEM.** It is a height above a
    drainage cell that lies within the same grid, so the largest possible value
    is highest point minus lowest point. Anything above that is not a tall
    hillslope, it is a measurement against something that is not ground.

    That is exactly what happened on a real run: HAND came back with a maximum
    of 20,628 on a DEM whose entire relief is 5,908. The excess was 9,999 --
    the nodata value -- because flow paths ran off into nodata and the height
    was taken above a cell holding -9999 instead of above a stream. Every
    affected cell reported `elevation + 9999`, which is a plausible-looking
    number in a plausible-looking raster, and the only visible symptom was that
    the map "didn't look right".

    A cheap arithmetic check catches it outright, so it is done here rather than
    left to the eye.
    """
    import rasterio

    with rasterio.open(str(dem_path)) as src:
        z = src.read(1, masked=True)
    valid = ~np.ma.getmaskarray(z)
    if valid.any():
        relief = float(z.max()) - float(z.min())
    else:
        relief = float("inf")

    # HAND is only defined where there is ground.
    outside = np.isfinite(hand) & ~valid
    if outside.any():
        warnings.append(
            f"{int(outside.sum()):,} cells had a HAND value where the DEM has "
            f"no data; they were masked out."
        )
    if hand.dtype.kind == "f" and hand.flags.writeable:
        hand[~valid] = np.nan               # in place: no second full grid
    else:
        hand = np.where(valid, hand, np.nan)
    del z, valid

    impossible = np.isfinite(hand) & (hand > relief * 1.001 + 1e-6)
    if impossible.any():
        n = int(impossible.sum())
        worst = float(np.nanmax(np.where(impossible, hand, np.nan)))
        warnings.append(
            f"{n:,} cells had HAND above the DEM's own relief ({relief:,.1f}), "
            f"the worst {worst:,.1f}. That is arithmetically impossible: HAND "
            f"is a height above a cell in this same grid. It means flow paths "
            f"ran into nodata and the height was taken above the nodata value "
            f"rather than above a stream. Those cells are masked out. If they "
            f"are more than a few percent of the grid, the routing is wrong "
            f"and the whole result should be distrusted -- check that the DEM "
            f"is a single connected area with a real outlet."
        )
        hand = np.where(impossible, np.nan, hand)

    neg = int(np.nansum(hand < -0.01))
    if neg:
        warnings.append(
            f"{neg:,} cells returned slightly negative HAND (conditioning "
            "artefacts); clamped to zero."
        )
    hand = np.where(np.isfinite(hand), np.maximum(hand, 0.0), -9999.0)
    return hand


def _block_windows(path: Path, block: int = 2048):
    """Square windows over a raster, for the out-of-core backend's checks."""
    import rasterio
    from rasterio.windows import Window

    with rasterio.open(str(path)) as src:
        H, W = src.height, src.width
    for r in range(0, H, block):
        for c in range(0, W, block):
            yield Window(c, r, min(block, W - c), min(block, H - r))


def _mask_streams_to_valid_file(streams_path: Path, dem_path: Path,
                                warnings: list[str], block: int = 2048) -> int:
    """``_streams_from_acc``, for a backend that works in files not arrays.

    WhiteboxTools writes its stream raster straight to disk and then measures
    elevation above it, so the in-memory guard never sees it. The failure is
    identical: flow collects in the nodata collar, those cells pass the stream
    threshold, and HAND is measured above the nodata value.

    Done window by window so the out-of-core backend stays out-of-core -- the
    whole reason to choose it is a DEM too big to hold, and a full read here
    would throw that away.
    """
    import rasterio

    dropped = 0
    total = 0
    with rasterio.open(str(dem_path)) as dsrc, \
            rasterio.open(str(streams_path), "r+") as ssrc:
        if (dsrc.height, dsrc.width) != (ssrc.height, ssrc.width):
            warnings.append(
                f"Stream raster ({ssrc.height}x{ssrc.width}) and DEM "
                f"({dsrc.height}x{dsrc.width}) differ in shape, so streams "
                f"could not be restricted to valid ground."
            )
            return 0
        for win in _block_windows(streams_path, block):
            st = ssrc.read(1, window=win)
            on = st > 0
            if not on.any():
                continue
            total += int(on.sum())
            z = dsrc.read(1, window=win, masked=True)
            valid = ~np.ma.getmaskarray(z)
            bad = on & ~valid
            if bad.any():
                dropped += int(bad.sum())
                st[bad] = 0
                ssrc.write(st, 1, window=win)

    if dropped:
        pct = 100.0 * dropped / max(total, 1)
        warnings.append(
            f"{dropped:,} cells ({pct:.0f}% of everything above the stream "
            f"threshold) exceeded it while sitting on nodata — flow collecting "
            f"in the collar around a clipped DEM. They were dropped from the "
            f"stream network; left in, HAND would have been measured above the "
            f"nodata value."
        )
    return dropped


def _check_hand_file(hand_path: Path, dem_path: Path, warnings: list[str],
                     block: int = 2048) -> int:
    """The HAND invariant, checked windowed rather than on a whole-grid read.

    Reports only. The in-memory backends repair what they find; here the point
    is that a user on the out-of-core backend is not left without the check at
    all, which is what they had. If this fires after
    ``_mask_streams_to_valid_file`` has run, something other than the collar is
    wrong and the run deserves a proper look rather than a silent repair.
    """
    import rasterio

    lo, hi = float("inf"), float("-inf")
    with rasterio.open(str(dem_path)) as src:
        for win in _block_windows(dem_path, block):
            z = src.read(1, window=win, masked=True)
            if z.count():
                lo = min(lo, float(z.min()))
                hi = max(hi, float(z.max()))
    if not np.isfinite(lo) or not np.isfinite(hi):
        return 0
    relief = hi - lo

    n, worst = 0, float("-inf")
    with rasterio.open(str(hand_path)) as src:
        for win in _block_windows(hand_path, block):
            h = src.read(1, window=win, masked=True)
            a = np.ma.getdata(h)
            ok = ~np.ma.getmaskarray(h) & np.isfinite(a)
            bad = ok & (a > relief * 1.001 + 1e-6)
            if bad.any():
                n += int(bad.sum())
                worst = max(worst, float(a[bad].max()))

    if n:
        warnings.append(
            f"{n:,} cells have HAND above the DEM's own relief ({relief:,.1f}), "
            f"the worst {worst:,.1f}. HAND is a height above a cell in this "
            f"same grid, so that is arithmetically impossible. This backend "
            f"writes HAND itself and the values are left as they are — treat "
            f"hand.tif as untrustworthy and check that the DEM is a single "
            f"connected area with a real outlet."
        )
    return n


def _run_pysheds(dem_path: Path, out_dir: Path, threshold_cells: int,
                 warnings: list[str], units=None) -> dict[str, str]:
    _pysheds_numpy2_shim()
    from pysheds.grid import Grid

    note("conditioning DEM (pit fill -> depression fill -> flat resolution)")
    grid = Grid.from_raster(str(dem_path))
    dem = grid.read_raster(str(dem_path))

    # Every stage is released as soon as the next exists, and each product is
    # written and dropped as soon as nothing further reads it. Holding them
    # all to the end of the function -- DEM, pit-filled, depression-filled,
    # conditioned, flow direction, accumulation, then HAND in float64 twice --
    # measured 99 bytes a cell, ~176 GB on Sam's 1.8-billion-cell Ruidoso
    # grid.
    filled = grid.fill_pits(dem)
    del dem
    flooded = grid.fill_depressions(filled)
    del filled
    conditioned = grid.resolve_flats(flooded)
    del flooded

    note("computing flow direction and accumulation")
    fdir = grid.flowdir(conditioned)
    acc = grid.accumulation(fdir)

    note(f"extracting streams above {threshold_cells:,} contributing cells")
    streams_arr = _streams_from_acc(np.asarray(acc), threshold_cells, dem_path,
                                    warnings)
    if not streams_arr.any():
        raise GeolensError(
            f"No cells exceed the stream threshold of {threshold_cells:,} cells. "
            "Lower stream_threshold_km2."
        )
    paths = {"accumulation": _write_like(dem_path, np.asarray(acc),
                                         out_dir / "accumulation.tif")}
    del acc
    paths["streams"] = _write_like(dem_path, streams_arr.astype("uint8"),
                                   out_dir / "streams.tif", dtype="uint8",
                                   nodata=0)
    paths["conditioned"] = _write_like(dem_path, np.asarray(conditioned),
                                       out_dir / "conditioned.tif")
    z32 = np.asarray(conditioned, dtype="float32")
    del conditioned
    fdir_arr = np.asarray(fdir)
    paths["flow_dir"] = _write_like(dem_path, fdir_arr,
                                    out_dir / "flow_dir.tif", dtype="int32",
                                    nodata=0)

    note("computing HAND (height above nearest drainage)")
    # geolens' own tracer rather than grid.compute_hand. The two compute the
    # same quantity -- they agree to 1e-4 m wherever both return a value -- but
    # pysheds leaves cells unresolved that demonstrably do drain to a stream:
    #
    #   fixture            pysheds   geolens tracer
    #   landscape_dem       99.5%        100.0%
    #   compound_dem        89.8%         95.3%
    #   convergent basin    76.0%        100.0%
    #
    # On the last one, 400 sampled "unresolved" cells were walked by hand along
    # the same flow-direction raster and every one of them reached a stream
    # cell. Using the tracer here also means `build_terrain` and `restream`
    # produce the same HAND from the same routing, which they did not before:
    # restream has always used the tracer, so the two backends silently
    # disagreed about coverage on identical inputs.
    hand = _hand_from_flow(z32, streams_arr.astype(bool), fdir_arr)
    del z32, fdir_arr, fdir, streams_arr
    # float32 is what is written; the float64 copy and the np.where copy of it
    # were two more full grids for nothing.
    hand = np.asarray(hand, dtype="float32")
    hand[~np.isfinite(hand)] = np.nan

    hand = _sanitise_hand(hand, dem_path, warnings)
    paths["hand"] = _write_like(dem_path, hand, out_dir / "hand.tif")
    return paths


# -- whitebox backend ---------------------------------------------------------


def _run_whitebox(dem_path: Path, out_dir: Path, threshold_cells: int,
                  warnings: list[str], units=None) -> dict[str, str]:
    import whitebox

    wbt = whitebox.WhiteboxTools()
    wbt.verbose = False
    wbt.set_working_dir(str(out_dir))

    cond = out_dir / "conditioned.tif"
    fdir = out_dir / "flow_dir.tif"
    acc = out_dir / "accumulation.tif"
    streams = out_dir / "streams.tif"
    hand = out_dir / "hand.tif"

    note("breaching depressions (least-cost)")
    # Breaching carves outlets rather than flooding basins, which preserves
    # valley-floor elevations that filling would raise.
    wbt.breach_depressions_least_cost(str(dem_path), str(cond), dist=100)

    note("computing flow direction and accumulation")
    # esri_pntr=True. WhiteboxTools' native D8 scheme (1=NE, 2=E, 4=SE, ...) is
    # rotated 45 degrees from the ESRI scheme (1=E, 2=SE, 4=S, ...) that
    # pysheds writes and that `_hand_from_flow` decodes. Without this flag the
    # two backends write incompatible pointers under the same filename, and
    # `restream()` -- which happily accepts any TerrainResult -- walks every
    # cell 45 degrees off its true flow direction. It does not crash, and the
    # HAND invariant cannot catch it because a rotated walk almost never
    # exceeds the DEM's relief. One convention everywhere is the only safe
    # arrangement.
    wbt.d8_pointer(str(cond), str(fdir), esri_pntr=True)
    wbt.d8_flow_accumulation(str(cond), str(acc), out_type="cells")

    note(f"extracting streams above {threshold_cells:,} contributing cells")
    # WhiteboxTools' ExtractStreams applies `>=`; geolens' own paths apply `>`.
    # Left alone, the same DEM through two backends puts the channel head one
    # cell apart, which shifts every segment index downstream of it. Because
    # accumulation is an integer cell count, `>= t+1` is exactly `> t`, so the
    # two agree by construction rather than by luck.
    wbt.extract_streams(str(acc), str(streams), threshold=threshold_cells + 1)

    # Same collar problem as every other backend, and it has to be fixed BEFORE
    # elevation_above_stream runs -- once HAND has been measured above a nodata
    # cell there is nothing left to repair, only to detect. Windowed, so the
    # out-of-core backend stays out-of-core.
    _mask_streams_to_valid_file(Path(streams), dem_path, warnings)

    note("computing HAND (elevation above stream)")
    wbt.elevation_above_stream(str(cond), str(streams), str(hand))

    # The in-memory backends run _sanitise_hand; this one never did, so the
    # invariant that caught the original bug did not apply here at all.
    _check_hand_file(Path(hand), dem_path, warnings)

    return {
        "conditioned": str(cond), "flow_dir": str(fdir), "accumulation": str(acc),
        "streams": str(streams), "hand": str(hand),
    }


# -- public -------------------------------------------------------------------


# Bump when conditioning, D8 direction or accumulation change MEANING. Stream
# extraction and HAND are not part of this -- they are derived after, and a
# change to them invalidates nothing cached here.
_ROUTING_VERSION = 2


def _routing_cache_dir(guard: Guard, dem_p: Path, backend: str) -> Path:
    """Where the conditioned DEM, flow direction and accumulation live.

    Conditioning is the expensive step and it is *deterministic*: the same DEM
    through the same backend gives the same filled surface every time. On a
    1.94-billion-cell DEM that is hours, and it was being paid again on every
    fresh session -- open a notebook the next morning and the first cell
    re-fills every depression it filled yesterday.

    The key is the DEM's identity (resolved path and size) plus the backend
    and ``_ROUTING_VERSION``, so a different backend or a change to the routing
    code miss cleanly. Whether the *contents* changed is settled on read,
    against hashes in the manifest -- see ``_read_routing_manifest``.
    """
    import hashlib

    st = dem_p.stat()
    # The key deliberately does NOT include the modification time.
    #
    # It did, and that is the difference between a cache that works and one
    # that looks like it does not exist. A cloud sync client touches files; so
    # does copying a DEM to another folder, re-downloading it, or restoring it
    # from a backup. Every one of those changed the key and silently threw
    # away hours of conditioning, with nothing in the output to say why.
    #
    # Size alone is not enough to be safe, because editing elevations in place
    # leaves the size identical. So mtime and a sampled content hash are stored
    # in the manifest and checked on read: a changed mtime prompts the hash
    # check rather than a miss, and only a genuine content change misses.
    payload = f"{dem_p.resolve()}|{st.st_size}|{backend}|{_ROUTING_VERSION}"
    key = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return _cache_root(dem_p, guard) / f"{backend}_{key}"


def _dem_sample_hash(dem_p: Path, chunks: int = 8,
                     chunk_bytes: int = 1 << 20) -> str:
    """A cheap fingerprint of a DEM, used ONLY as a fast *negative*.

    Eight megabytes sampled at even offsets costs milliseconds. If it differs
    from what the manifest recorded, the file certainly changed and the cache
    misses without reading anything else.

    If it matches, that proves nothing -- on a 7.7 GB DEM this is 0.1% of the
    bytes, and burning a 100x100 block of channel into it would very likely
    land between the samples. So a match is inconclusive and the full hash
    below settles it. Do not use this on its own to decide a cache is valid.
    """
    import hashlib as _h

    size = dem_p.stat().st_size
    h = _h.sha256()
    h.update(str(size).encode())
    if size <= 0:
        return h.hexdigest()
    step = max(1, size // max(chunks, 1))
    with dem_p.open("rb") as fh:
        for i in range(chunks):
            off = min(i * step, max(0, size - chunk_bytes))
            fh.seek(off)
            h.update(fh.read(chunk_bytes))
    return h.hexdigest()


def _dem_full_hash(dem_p: Path, block: int = 1 << 22) -> str:
    """Hash every byte of the DEM. The only answer that is actually safe.

    A stale cache here is not a slow run, it is hours of analysis against a
    surface that is not the one on disk -- and nothing downstream would look
    wrong. That is worth a full read.

    The cost is a streaming read: measured 0.99 GB/s here, so ~8 s for a
    7.7 GB DEM against the hours of conditioning it protects (slower from a
    network share, but still not comparable to re-filling every depression).
    It is paid once when the routing is cached, and after that only when the
    DEM's timestamp has moved AND the sampled hash still matches -- which is
    the touched-but-unchanged case (sync client, copy, restore). When that
    check passes, the manifest's timestamp is refreshed so the next run is
    back to a stat() call.
    """
    import hashlib as _h

    h = _h.sha256()
    with dem_p.open("rb") as fh:
        while True:
            buf = fh.read(block)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def _cache_root(dem_p: Path, guard: Guard) -> Path:
    """Where cached routing lives. **Beside the DEM, not in the output folder.**

    It was under the output directory, which meant it was thrown away by the
    most ordinary things anyone does: a new results folder, a new session with
    a different ``OUT_DIR``, unzipping the next release next to the last one.
    The whole point is to pay for conditioning once per DEM, and keying it to
    the output folder meant paying once per *run configuration* instead.

    The DEM is what the cache is about, so it lives with the DEM. That also
    makes it obvious and deletable: one ``.geolens_cache`` folder next to the
    data it belongs to.

    Order of preference:

    1. ``GEOLENS_CACHE`` if set — an explicit answer beats any guess, and a
       shared cache on a fast local disk is worth having when the data sits on
       a network share.
    2. ``<dem folder>/.geolens_cache`` when that folder is writable.
    3. A per-user cache directory, because research data very often lives on a
       read-only mount and the alternative is silently never caching at all.
    """
    import os
    import tempfile

    env = os.environ.get("GEOLENS_CACHE")
    if env:
        root = Path(env).expanduser()
        try:
            root.mkdir(parents=True, exist_ok=True)
            return root
        except OSError:
            pass

    beside = dem_p.parent / ".geolens_cache"
    try:
        beside.mkdir(parents=True, exist_ok=True)
        probe = beside / ".writable"
        probe.touch()
        probe.unlink()
        return beside
    except OSError:
        pass

    home = Path(os.environ.get("LOCALAPPDATA")
                or os.environ.get("XDG_CACHE_HOME")
                or (Path.home() / ".cache"))
    for candidate in (home / "geolens" / "routing",
                      Path(tempfile.gettempdir()) / "geolens_routing"):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    # Last resort: the output directory, which is where it used to live.
    return Path(guard.write_path("routing_cache_dir")).with_suffix("")


def _read_routing_manifest(cache_dir: Path,
                           dem_p: Path | None = None,
                           why: "list[str] | None" = None,
                           ) -> "dict[str, Any] | None":
    """Return the cached routing for this DEM, or None with a stated reason.

    Three questions, cheapest first, and each one can only miss:

    1. Is the cache itself intact? A half-written cache is worse than none.
    2. Is it the same file? Size is free and settles most edits.
    3. Are the contents the same? The timestamp answers this when it matches.
       When it does not -- a sync client, a copy, a re-download, a restore --
       the sampled hash can still rule a change IN cheaply, and only if that
       is inconclusive is every byte read. A verified match then refreshes the
       timestamp, so the expensive path is paid once per touch, not per run.

    ``why`` collects a plain-language reason for a miss; the caller puts it in
    the result, because a cache that misses silently is indistinguishable from
    one that does not exist -- which is exactly how this looked from outside.
    """
    import json

    def _no(reason: str) -> None:
        if why is not None:
            why.append(reason)
        return None

    mf = cache_dir / "manifest.json"
    if not mf.exists():
        return _no("nothing cached for this DEM yet")
    try:
        meta = json.loads(mf.read_text(encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        return _no("the cache manifest is unreadable")
    for k in ("conditioned", "flow_dir", "accumulation"):
        if not meta.get(k) or not Path(meta[k]).exists():
            return _no("the cache is incomplete — one of its rasters is gone "
                       "(moved, cleaned up, or still syncing)")

    if dem_p is not None:
        try:
            st = dem_p.stat()
        except OSError:
            return _no("the DEM could not be read")
        if int(meta.get("dem_size", -1)) != st.st_size:
            return _no("the DEM has changed size since it was cached")
        if int(meta.get("dem_mtime_ns", -1)) != st.st_mtime_ns:
            if meta.get("dem_sample_sha") != _dem_sample_hash(dem_p):
                return _no("the DEM's contents have changed since it was "
                           "cached")
            full = meta.get("dem_sha256")
            if not full:
                return _no("the cache predates content checking and the DEM's "
                           "timestamp has moved — recomputing rather than "
                           "risk a stale surface")
            if full != _dem_full_hash(dem_p):
                return _no("the DEM's contents have changed since it was "
                           "cached")
            _refresh_manifest_mtime(cache_dir, meta, st.st_mtime_ns)
    return meta


def _refresh_manifest_mtime(cache_dir: Path, meta: dict, mtime_ns: int) -> None:
    """Record that this timestamp belongs to contents already verified.

    Without this, a file a sync client keeps touching pays a full hash on
    every single run forever. Only ever called after the contents have been
    confirmed byte-for-byte, so it cannot bless a changed file.
    """
    import json

    meta["dem_mtime_ns"] = mtime_ns
    try:
        (cache_dir / "manifest.json").write_text(
            json.dumps(meta, indent=1), encoding="utf-8")
    except OSError:                       # read-only cache: correct, just slow
        pass


def _write_routing_manifest(cache_dir: Path, paths: dict, backend: str,
                            dem_p: Path, threshold_cells: int) -> None:
    import json

    st = dem_p.stat()
    meta = {
        "conditioned": paths["conditioned"],
        "flow_dir": paths["flow_dir"],
        "accumulation": paths["accumulation"],
        "streams": paths.get("streams"),
        "hand": paths.get("hand"),
        "threshold_cells": threshold_cells,
        "backend": backend,
        "routing_version": _ROUTING_VERSION,
        "dem": str(dem_p.resolve()),
        "dem_size": st.st_size,
        "dem_mtime_ns": st.st_mtime_ns,
        "dem_sample_sha": _dem_sample_hash(dem_p),
        "dem_sha256": _dem_full_hash(dem_p),
    }
    (cache_dir / "manifest.json").write_text(
        json.dumps(meta, indent=1), encoding="utf-8")


def _streams_and_hand(dem_p: Path, out_dir: Path, paths: dict,
                      threshold_cells: int, warnings: list) -> dict:
    """Derive streams and HAND from cached routing, at a new threshold.

    The same work ``restream`` does, reached from the other direction: there
    the routing is in hand as a ``TerrainResult``, here it has just come back
    off disk. Both exist because the stream threshold does not affect routing —
    filling, direction and accumulation are all computed before any threshold
    is applied — so changing it must never cost a second routing pass.
    """
    import rasterio

    with rasterio.open(paths["accumulation"]) as src:
        acc = src.read(1)
        profile = src.profile.copy()

    streams = _streams_from_acc(acc, threshold_cells, dem_p, warnings)
    if not streams.any():
        raise GeolensError(
            f"No cells exceed the stream threshold of {threshold_cells:,} "
            f"cells. Lower stream_threshold_km2."
        )

    streams_path = out_dir / "streams.tif"
    with rasterio.open(str(streams_path), "w",
                       **gtiff_profile(profile, dtype="uint8", nodata=0)) as dst:
        dst.write(streams.astype("uint8"), 1)

    with rasterio.open(paths["conditioned"]) as src:
        cond = src.read(1)
    hand = _hand_from_flow(cond, streams, paths["flow_dir"])
    hand = _sanitise_hand(np.where(np.isfinite(hand), hand, np.nan),
                          dem_p, warnings)
    hand_path = out_dir / "hand.tif"
    with rasterio.open(str(hand_path), "w",
                       **gtiff_profile(profile, dtype="float32",
                                       nodata=-9999.0)) as dst:
        dst.write(hand.astype("float32"), 1)
    return {"streams": str(streams_path), "hand": str(hand_path)}


def build_terrain(
    dem: str | Path,
    stream_threshold_km2: float | None = None,
    backend: Backend = "auto",
    out_prefix: str = "terrain",
    flow: Any = None,
    vertical_unit: str | None = None,
    reuse_routing: bool = True,
    guard: Guard | None = None,
) -> TerrainResult:
    """Condition a DEM, route flow, extract streams, and compute HAND and slope.

    Parameters
    ----------
    stream_threshold_km2
        Contributing area at which a cell becomes a stream. This single number
        sets drainage density and therefore how much of the landscape ends up
        classified as valley bottom. 1 km² suits 30 m data in most terrain;
        lower it in arid or low-relief settings, raise it in steep wet terrain.
    backend
        ``auto`` picks WhiteboxTools above ~50M cells when installed, else
        pysheds.
    vertical_unit
        ``"m"``, ``"ft"`` or ``"ftUS"`` -- what the DEM's elevation values are
        in. A GeoTIFF almost never declares this, so when it is not given the
        horizontal unit is assumed and the assumption is reported in
        ``warnings`` with the relief converted both ways, so a wrong guess is
        visible rather than silent.
    flow
        A :class:`~geolens.landscape.flow.FlowResult` computed over a LARGER
        extent (usually the whole landscape). When given, flow direction and
        accumulation are read from it and clipped to this DEM instead of being
        recomputed — so a tile inherits the true contributing area from
        upstream rather than restarting it at zero.

        **This is what makes tiling correct.** Accumulation is the only
        quantity with a global dependency; everything downstream of it (HAND,
        valley bottom, roughness, sections) is local and tiles freely. Route
        once over the full grid with :func:`~geolens.landscape.flow.flow_routing`,
        then pass the result here for every tile.
    reuse_routing
        Cache the conditioned DEM, flow direction and accumulation on disk and
        reuse them for any later run on the same DEM. Conditioning is the
        expensive step and it is deterministic, so paying for it once per DEM
        rather than once per run is the difference between an overnight job and
        a coffee break — and it survives closing the notebook, which reusing a
        ``TerrainResult`` in memory does not.

        The key is the DEM's resolved path, size and modification time, plus
        the backend, so editing the DEM or switching backend misses cleanly.
        Pass ``False`` to force a recompute.

    Returns paths to every derived raster — nothing large is held in memory
    across the call.
    """
    import rasterio

    guard = guard or Guard.from_env()
    dem_p = guard.read_path(dem)
    t0 = time.monotonic()
    warnings: list[str] = []

    with rasterio.open(str(dem_p)) as src:
        cells = src.width * src.height
        cell_size = abs(src.res[0])
        cell_y = abs(src.res[1])
        crs_obj = src.crs
        crs = src.crs.to_string() if src.crs else None
        try:
            # Approximate stats read the overviews, so this costs nothing even
            # on a multi-gigabyte DEM. It exists only to restate the relief in
            # metres in the units warning.
            _st = src.stats(approx=True)[0]
            z_range = (float(_st.min), float(_st.max))
        except Exception:                                    # noqa: BLE001
            z_range = None
        if src.crs is None:
            warnings.append(
                "DEM has no CRS. Drainage-area thresholds and HAND are in raw "
                "grid units and cannot be trusted."
            )
        elif src.crs.is_geographic:
            warnings.append(
                "DEM is in a geographic CRS (degrees). Flow routing works, but "
                "drainage area, slope and HAND are all distorted. Reproject to a "
                "projected CRS before using any of these numbers."
            )

    # What a cell and an elevation actually MEAN. Treating a US-survey-foot
    # grid as metres put the stream threshold out by 10.8x on real data and
    # nothing in the output showed it. See geolens.units.
    from ..units import unit_info

    units = unit_info(crs_obj, cell_size, cell_y, z_range,
                      vertical_unit=vertical_unit)
    warnings.extend(units.warnings)
    extent_km2 = cells * units.cell_area_m2 / 1e6

    # None means "fit it to this DEM". A fixed 1 km2 default is a landscape-
    # scale number: on Eagle Creek (40 km2) it kept 13 segments on 2 branches,
    # the smallest draining 2 km2 -- only the main stems -- so every tributary
    # was missing from the network and therefore from the stream types, the
    # valley types and the valley extent. The run even printed the right
    # number (0.08 km2) in a warning and then used 1.0.
    #
    # Capped at the old 1 km2 so a large DEM is never made COARSER than it
    # used to be -- drainage density belongs to the landscape, not to the size
    # of the file -- and floored at 0.01 km2 so a tiny clip does not map every
    # rill. Ruidoso (1,036 km2) stays at 1.0; Eagle Creek (40 km2) gets 0.08.
    if stream_threshold_km2 is None:
        stream_threshold_km2 = float(min(1.0, max(extent_km2 / 500.0, 0.01)))
        note(f"stream threshold fitted to the extent: "
             f"{stream_threshold_km2:.3g} km2 (DEM {extent_km2:,.1f} km2)")
    threshold_cells = units.cells_for_area_km2(stream_threshold_km2)

    # A threshold close to the size of the DEM leaves almost nothing above it.
    # On a few-km2 extent a landscape-scale number produces a handful of
    # segments and no indication that the parameter, rather than the terrain,
    # is the reason.
    if stream_threshold_km2 > extent_km2 / 50.0:
        warnings.append(
            f"stream_threshold_km2={stream_threshold_km2:g} against a DEM of "
            f"only {extent_km2:,.1f} km2. Almost nothing will exceed it, so "
            f"expect a very short network and few segments — the threshold is "
            f"the reason, not the terrain. Something near "
            f"{max(extent_km2 / 500.0, 1e-4):.3g} km2 suits this extent."
        )

    if flow is not None:
        note("using precomputed flow — contributing area preserved from upstream")
        return _terrain_from_flow(
            dem_p, flow, stream_threshold_km2, threshold_cells, cell_size, crs,
            cells, warnings, guard, out_prefix, t0, units,
        )

    chosen = _pick(backend, cells)
    note(
        f"{cells:,} cells @ {units.cell_size_m:g} m  |  backend: {chosen}  |  "
        f"stream threshold {stream_threshold_km2} km² = {threshold_cells:,} cells"
    )

    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)

    cache_dir = _routing_cache_dir(guard, dem_p, chosen)
    miss_reason: list[str] = []
    cached = (_read_routing_manifest(cache_dir, dem_p, miss_reason)
              if reuse_routing else None)

    # Say — in the RESULT, not only in a status line that scrolls past — what
    # the cache did. "I don't see how it picks up the cache from previous
    # runs" is a fair thing to say about a feature whose only evidence is a
    # note() on stderr: from the code it is one branch buried in a long
    # function, and from the outputs it is invisible. A miss also needs a
    # reason, because the usual cause is mundane and diagnosable: the DEM's
    # timestamp changed, which a copy, a re-download or a sync client all do.
    cache_info: dict[str, Any] = {
        "enabled": bool(reuse_routing),
        "directory": str(cache_dir),
        "hit": cached is not None,
    }
    if reuse_routing and cached is None:
        cache_info["reason"] = (miss_reason[0] if miss_reason
                                else "nothing cached for this DEM yet")
        if not cache_dir.exists():
            others = ([d.name for d in cache_dir.parent.glob("*_*")
                       if d.is_dir() and (d / "manifest.json").exists()]
                      if cache_dir.parent.exists() else [])
            if others:
                # Worth saying out loud: the routing code or the backend
                # changed, so nothing cached under the old key can be used
                # and it is now dead weight next to the DEM.
                cache_info["stale_caches"] = sorted(others)

    if cached is not None:
        paths = {k: cached[k] for k in
                 ("conditioned", "flow_dir", "accumulation")}
        if cached.get("threshold_cells") == threshold_cells \
                and cached.get("streams") and cached.get("hand") \
                and Path(cached["streams"]).exists() \
                and Path(cached["hand"]).exists():
            note("reusing the cached routing AND stream network — nothing to do")
            paths["streams"], paths["hand"] = cached["streams"], cached["hand"]
            cache_info["reused"] = "conditioning, routing, streams and HAND"
        else:
            note(f"reusing the cached conditioned DEM and flow routing; "
                 f"re-extracting streams at {threshold_cells:,} cells")
            paths.update(_streams_and_hand(
                dem_p, out_dir, paths, threshold_cells, warnings))
            cache_info["reused"] = ("conditioning and routing; streams and "
                                    "HAND re-extracted at a new threshold")
    else:
        # Where the backend writes.
        #
        # With the cache ON it writes straight into the cache directory, so
        # there is nothing to copy afterwards -- a 7.7 GB conditioned raster
        # is not something to duplicate per run.
        #
        # With the cache OFF it must NOT. It did, and it wrote streams.tif and
        # hand.tif over the cached ones while leaving the manifest describing
        # the threshold they used to hold. A later run at that threshold then
        # got a clean cache hit and was served a stream network built at a
        # different one -- silently, with no file newer than the manifest to
        # give it away. Caught by running the suite from a clean unzip, where
        # the cache starts empty and the order actually bites; in a working
        # tree an older cache happened to paper over it.
        #
        # reuse_routing=False means "do not use the cache", and mutating it is
        # worse than using it.
        target = cache_dir if reuse_routing else out_dir
        target.mkdir(parents=True, exist_ok=True)
        runner = _run_pysheds if chosen == "pysheds" else _run_whitebox
        paths = runner(dem_p, target, threshold_cells, warnings, units)
        if reuse_routing:
            _write_routing_manifest(cache_dir, paths, chosen, dem_p,
                                    threshold_cells)
            cache_info["written"] = True
            note(f"routing cached for reuse in {cache_dir}")

    note("computing slope")
    slope_path = _slope(dem_p, out_dir / "slope.tif")

    elapsed = time.monotonic() - t0
    note(f"terrain complete in {elapsed:.1f}s")

    return TerrainResult(
        dem=str(dem_p),
        conditioned=paths["conditioned"],
        flow_dir=paths["flow_dir"],
        accumulation=paths["accumulation"],
        streams=paths["streams"],
        hand=paths["hand"],
        slope=slope_path,
        backend=chosen,
        # TRUE metres, not CRS units. Everything downstream -- window sizes,
        # segment lengths, gully widths -- does metre arithmetic with this.
        cell_size=units.cell_size_m,
        cell_size_crs=cell_size,
        units=units.as_dict(),
        routing_cache=cache_info,
        crs=crs,
        cells=cells,
        stream_threshold_cells=threshold_cells,
        stream_threshold_km2=stream_threshold_km2,
        seconds=elapsed,
        warnings=warnings,
    )


def _terrain_from_flow(
    dem_p, flow, stream_threshold_km2, threshold_cells, cell_size, crs, cells,
    warnings, guard, out_prefix, t0, units=None,
) -> TerrainResult:
    """Build a TerrainResult from precomputed global flow, clipped to this DEM.

    The clip is by window, so the tile inherits exactly the accumulation the
    full-grid pass computed — including everything that drained in from outside
    the tile. That is the whole point: recomputing accumulation on a clip resets
    upstream area to zero and silently deletes streams.
    """
    import numpy as np
    import rasterio
    from rasterio.windows import from_bounds

    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)

    with rasterio.open(str(dem_p)) as src:
        bounds = src.bounds
        profile = src.profile.copy()
        shape = (src.height, src.width)

    def _clip(path, name, dtype, nodata):
        with rasterio.open(str(path)) as src:
            win = from_bounds(*bounds, transform=src.transform)
            arr = src.read(1, window=win, boundless=True,
                           fill_value=nodata if nodata is not None else 0)
        if arr.shape != shape:
            # Window rounding can differ by a cell; pad or trim to match.
            fixed = np.zeros(shape, dtype=arr.dtype)
            h = min(shape[0], arr.shape[0])
            w = min(shape[1], arr.shape[1])
            fixed[:h, :w] = arr[:h, :w]
            arr = fixed
        pr = gtiff_profile(profile, dtype=dtype, nodata=nodata)
        out = out_dir / name
        with rasterio.open(str(out), "w", **pr) as dst:
            dst.write(arr.astype(dtype), 1)
        return str(out), arr

    fdir_path, _ = _clip(flow.flow_dir, "flow_dir.tif", "uint8", 0)
    # Read accumulation at its own dtype. Forcing float32 here would undo the
    # uint32 exactness the routing pass went to trouble to get: a cell count
    # above 16.7M does not survive the round trip.
    import rasterio as _rio
    with _rio.open(str(flow.accumulation)) as _src:
        _acc_dtype = _src.dtypes[0]
    acc_path, acc = _clip(flow.accumulation, "accumulation.tif", _acc_dtype, None)
    cond_path, cond = _clip(flow.filled, "conditioned.tif", "float32", -9999.0)

    note(f"extracting streams above {threshold_cells:,} contributing cells")
    streams = _streams_from_acc(acc, threshold_cells, dem_p, warnings)
    if not streams.any():
        warnings.append(
            "No cells in this tile exceed the stream threshold even with "
            "upstream area preserved — the tile may be entirely headwater."
        )

    pr = gtiff_profile(profile, dtype="uint8", nodata=0)
    streams_path = out_dir / "streams.tif"
    with rasterio.open(str(streams_path), "w", **pr) as dst:
        dst.write(streams.astype("uint8"), 1)

    note("computing HAND")
    hand = _hand_from_flow(cond, streams, fdir_path)
    pr = gtiff_profile(profile, dtype="float32", nodata=-9999.0)
    hand_path = out_dir / "hand.tif"
    with rasterio.open(str(hand_path), "w", **pr) as dst:
        dst.write(np.nan_to_num(hand, nan=-9999.0).astype("float32"), 1)

    note("computing slope")
    slope_path = _slope(dem_p, out_dir / "slope.tif")

    elapsed = time.monotonic() - t0
    note(f"terrain from precomputed flow in {elapsed:.1f}s")
    return TerrainResult(
        dem=str(dem_p), conditioned=cond_path, flow_dir=fdir_path,
        accumulation=acc_path, streams=str(streams_path), hand=hand_path,
        slope=slope_path, backend="precomputed",
        cell_size=(units.cell_size_m if units is not None else cell_size),
        cell_size_crs=cell_size,
        units=(units.as_dict() if units is not None else {}), crs=crs,
        cells=cells, stream_threshold_cells=threshold_cells,
        stream_threshold_km2=stream_threshold_km2, seconds=elapsed,
        warnings=warnings,
    )


def _hand_from_flow(z, streams, fdir_path, path_cap: int | None = None):
    """Height above nearest drainage, following flow paths to a stream cell.

    ``path_cap`` bounds the walk buffer and exists so the truncation claim can
    be tested: the result must be identical at any cap, because every cell is
    the first entry of its own walk. ``tests/test_hand_streams.py`` runs it at
    a cap of 3 against the default and requires bit-identical output.
    """
    import numpy as np
    import rasterio
    from numba import njit

    from .flow import _CODES, _DR, _DC

    if isinstance(fdir_path, (str, Path)):
        with rasterio.open(str(fdir_path)) as src:
            fdir = src.read(1)
    else:
        fdir = np.asarray(fdir_path)
    # One dtype for both entry points, so numba compiles one specialisation
    # rather than one per backend's pointer dtype.
    fdir = np.ascontiguousarray(fdir, dtype=np.int32)

    @njit(cache=True)
    def _trace(z, fdir, streams, codes, dr, dc, cap):
        H, W = z.shape
        out = np.full((H, W), np.nan, dtype=np.float32)
        # `path` holds only the walk in progress, so it does NOT need to be
        # H*W long. It was, and on a 1.94-billion-cell DEM that is a 15.5 GB
        # int64 allocation on top of the 7.7 GB output -- 23 GB before any
        # other array, requested up front, every call.
        #
        # Truncating a walk at `cap` loses nothing. Every cell is path[0] of
        # its own walk, so it is always inside the cap and always gets filled;
        # a cell beyond the cap is simply resolved later, when the scan reaches
        # it and its walk terminates at a cell this pass has already solved.
        path = np.empty(cap, dtype=np.int64)
        for r0 in range(H):
            for c0 in range(W):
                if not np.isfinite(out[r0, c0]):
                    r, c, n = r0, c0, 0
                    # `steps` always increments; `n` stops at the path cap.
                    # They used to be the same counter, which meant capping the
                    # buffer would silently remove the guard against a cycle in
                    # the flow-direction raster and hang the walk forever.
                    steps = 0
                    limit = H * W
                    # `found` is an ELEVATION, so it cannot double as its own
                    # "nothing found yet" sentinel. This used to test
                    # `found >= 0.0`, which silently means "only trust a base
                    # above sea level" -- and the arid Southwest has plenty of
                    # ground below it (Salton Sea -69 m, Death Valley -86 m).
                    # On such a DEM the resolved-neighbour branch below would
                    # hand back a perfectly good negative base elevation, the
                    # test would reject it, and the whole upstream path would
                    # be dropped to NaN with nothing reported. An explicit flag
                    # costs one byte and cannot collide with the data.
                    have_base = False
                    base = 0.0
                    while True:
                        if streams[r, c]:
                            base = z[r, c]
                            have_base = True
                            break
                        if np.isfinite(out[r, c]):
                            base = z[r, c] - out[r, c]
                            have_base = True
                            break
                        if n < cap:
                            path[n] = r * W + c
                            n += 1
                        steps += 1
                        f = fdir[r, c]
                        if f == 0 or steps >= limit:
                            break
                        moved = False
                        for k in range(8):
                            if f == codes[k]:
                                rr = r + dr[k]
                                cc = c + dc[k]
                                if 0 <= rr < H and 0 <= cc < W:
                                    r, c = rr, cc
                                    moved = True
                                break
                        if not moved:
                            break
                    if have_base:
                        for i in range(n):
                            idx = path[i]
                            rr, cc = idx // W, idx % W
                            d = z[rr, cc] - base
                            out[rr, cc] = d if d > 0 else 0.0
        for r in range(H):
            for c in range(W):
                if streams[r, c]:
                    out[r, c] = 0.0
        return out

    # 1M entries is 8 MB and longer than any real flow path on any DEM that
    # fits in memory; the cap only has to stop the allocation scaling with the
    # grid. Still bounded by H*W so tiny test grids do not over-allocate.
    cap = path_cap or min(z.shape[0] * z.shape[1], 1_000_000)
    cap = max(1, int(cap))
    return _trace(np.ascontiguousarray(z, dtype=np.float32), fdir,
                  np.ascontiguousarray(streams), _CODES, _DR, _DC, cap)


def terrain_from_result(result: dict[str, Any] | TerrainResult) -> TerrainResult:
    """Rebuild a TerrainResult from what ``landscape_pass`` returned.

    ``run_landscape`` returns ``terrain.as_dict()`` -- paths, not the object --
    so there was no way for a later step to say "use what you already
    computed". Every later step took a DEM path and routed flow again.
    """
    if isinstance(result, TerrainResult):
        return result
    d = dict(result)
    if "terrain" in d and isinstance(d["terrain"], dict):
        d = dict(d["terrain"])          # accept the whole landscape_pass result
    fields = set(TerrainResult.__dataclass_fields__)
    missing = {"dem", "conditioned", "flow_dir", "accumulation", "streams",
               "hand", "slope"} - set(d)
    if missing:
        raise GeolensError(
            f"This does not look like a terrain result: missing {sorted(missing)}. "
            f"Pass res['terrain'] from landscape_pass, or the whole result."
        )
    return TerrainResult(**{k: v for k, v in d.items() if k in fields})


def restream(terrain: dict[str, Any] | TerrainResult,
             stream_threshold_km2: float,
             out_prefix: str = "restream",
             guard: Guard | None = None) -> TerrainResult:
    """Re-extract the stream network at a new threshold. No re-routing.

    **The stream threshold does not affect flow routing.** Depression filling,
    flow direction and accumulation are all computed before any threshold is
    applied; the threshold only decides which accumulation values get called a
    stream. So changing it should never cost a second routing pass -- and until
    this existed it always did, because the only way to change the threshold was
    to call ``build_terrain`` again, which starts from the DEM.

    That matters because the right threshold genuinely differs by task. The
    landscape pass wants ~1 km2 so the network is trunk streams. Gully work
    wants it ABOVE the drainage area gullies reach (0.2-1 km2), or the gullies
    are already classified as streams. Headcut detection wants it very low
    (0.005 km2) or the gully heads are not in the network to be found. Three
    thresholds, one routing pass.

    HAND is recomputed, because it is a height above the *nearest stream* and
    that set has just changed. On a large grid that is a small fraction of the
    cost of routing.
    """
    import rasterio

    t0 = time.monotonic()
    guard = guard or Guard.from_env()
    src_terrain = terrain_from_result(terrain)
    warnings: list[str] = []

    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)

    with rasterio.open(str(guard.read_path(src_terrain.accumulation))) as src:
        acc = src.read(1)
        profile = src.profile.copy()

    units = src_terrain.units or {}
    cell_area_m2 = units.get("cell_area_m2") or float(src_terrain.cell_size) ** 2
    threshold_cells = max(1, int(round(stream_threshold_km2 * 1e6 / cell_area_m2)))

    note(f"re-extracting streams above {threshold_cells:,} cells "
         f"(no re-routing; accumulation is unchanged)")
    streams = _streams_from_acc(acc, threshold_cells,
                                Path(guard.read_path(src_terrain.dem)), warnings)
    if not streams.any():
        raise GeolensError(
            f"No cells exceed {threshold_cells:,} contributing cells "
            f"({stream_threshold_km2} km2). The largest contributing area in "
            f"this grid is {float(acc.max()) * cell_area_m2 / 1e6:.4f} km2 — "
            f"lower stream_threshold_km2 below that."
        )

    streams_path = out_dir / "streams.tif"
    with rasterio.open(str(streams_path), "w",
                       **gtiff_profile(profile, dtype="uint8", nodata=0)) as dst:
        dst.write(streams.astype("uint8"), 1)

    if src_terrain.backend not in ("pysheds", "whitebox", "precomputed", ""):
        raise GeolensError(
            f"restream cannot re-trace HAND for backend "
            f"{src_terrain.backend!r}: the flow-direction encoding is only "
            f"known for pysheds and whitebox (both written in the ESRI D8 "
            f"scheme). Re-run build_terrain with the new threshold instead."
        )

    note("recomputing HAND for the new stream set")
    with rasterio.open(str(guard.read_path(src_terrain.conditioned))) as src:
        cond = src.read(1)
    hand = _hand_from_flow(cond, streams,
                           str(guard.read_path(src_terrain.flow_dir)))
    hand = _sanitise_hand(np.where(np.isfinite(hand), hand, np.nan),
                          Path(guard.read_path(src_terrain.dem)), warnings)
    hand_path = out_dir / "hand.tif"
    with rasterio.open(str(hand_path), "w",
                       **gtiff_profile(profile, dtype="float32",
                                       nodata=-9999.0)) as dst:
        dst.write(hand.astype("float32"), 1)

    elapsed = time.monotonic() - t0
    note(f"restream complete in {elapsed:.1f}s")
    return replace(
        src_terrain, streams=str(streams_path), hand=str(hand_path),
        stream_threshold_cells=threshold_cells,
        stream_threshold_km2=stream_threshold_km2,
        seconds=elapsed, warnings=list(src_terrain.warnings) + warnings,
    )


def compare_backends(dem: str | Path | None = None,
                     stream_threshold_km2: float = 0.0005,
                     out_prefix: str = "backend_check",
                     guard: Guard | None = None) -> dict[str, Any]:
    """Run both flow-routing backends on the same DEM and report the difference.

    WhiteboxTools is MIT-licensed, written in Rust, and far more memory- and
    time-efficient than the pure-Python path on large grids -- which is exactly
    what a multi-billion-cell DEM needs. But it has never been verified against
    the pysheds path here, because the WhiteboxTools binary is downloaded on
    first use and that download is blocked in the environment this was built in.

    So rather than claim the backends agree, this measures it. Run it once on a
    machine where the binary can be fetched, on your own DEM if you have a small
    one, and read the numbers.

    What it compares, and why each one:

    ``accumulation_ratio``
        Max contributing area, whitebox over pysheds. Should be ~1.0. A large
        departure means one backend is losing drainage -- usually depression
        handling, since whitebox BREACHES (carves an outlet) where pysheds
        FILLS (floods the basin). Breaching preserves valley-floor elevations
        that filling raises, so small differences here are expected and are a
        point in whitebox's favour, not against it.
    ``stream_iou``
        Intersection over union of the two stream masks. This is the number
        that matters most, because everything downstream is keyed to the
        network. Below ~0.9 the two backends are not interchangeable on this
        terrain and you should pick one and stay with it.
    ``hand_median_abs_diff_m``
        HAND from ``elevation_above_stream`` against HAND traced along the D8
        path. Both are legitimate definitions and they differ slightly by
        construction; a median difference of a metre or two on real relief is
        normal, tens of metres is not.
    """
    import rasterio

    guard = guard or Guard.from_env()
    avail = available_backends()
    missing = [k for k, v in avail.items() if not v]
    if missing:
        return {
            "ran": False,
            "reason": f"backend(s) unavailable: {missing}",
            "notes": backend_notes(),
        }

    if dem is None:
        dem = _synthetic_valley(guard, out_prefix)
    dem = str(guard.read_path(dem))

    results, timings = {}, {}
    for backend in ("pysheds", "whitebox"):
        t = build_terrain(dem, stream_threshold_km2=stream_threshold_km2,
                          backend=backend, out_prefix=f"{out_prefix}_{backend}",
                          guard=guard)
        timings[backend] = t.seconds
        with rasterio.open(t.accumulation) as s:
            acc = s.read(1).astype("float64")
        with rasterio.open(t.streams) as s:
            st = s.read(1) > 0
        with rasterio.open(t.hand) as s:
            hand = s.read(1, masked=True)
        results[backend] = (acc, st, hand)

    (a_acc, a_st, a_h) = results["pysheds"]
    (b_acc, b_st, b_h) = results["whitebox"]

    union = (a_st | b_st).sum()
    iou = float((a_st & b_st).sum() / union) if union else float("nan")

    both = ~(np.ma.getmaskarray(a_h) | np.ma.getmaskarray(b_h))
    dh = (np.abs(np.asarray(a_h)[both] - np.asarray(b_h)[both])
          if both.any() else np.array([np.nan]))

    out = {
        "ran": True,
        "dem": dem,
        "accumulation_ratio": (float(b_acc.max() / a_acc.max())
                               if a_acc.max() else float("nan")),
        "accumulation_max": {"pysheds": float(a_acc.max()),
                             "whitebox": float(b_acc.max())},
        "stream_cells": {"pysheds": int(a_st.sum()), "whitebox": int(b_st.sum())},
        "stream_iou": iou,
        "hand_median_abs_diff_m": float(np.nanmedian(dh)),
        "hand_p95_abs_diff_m": float(np.nanpercentile(dh, 95)),
        "seconds": timings,
        "speedup": (timings["pysheds"] / timings["whitebox"]
                    if timings.get("whitebox") else float("nan")),
        "verdict": [],
    }
    if iou >= 0.90:
        out["verdict"].append(
            f"Stream networks agree (IoU {iou:.3f}); the backends are "
            f"interchangeable on this terrain."
        )
    else:
        out["verdict"].append(
            f"Stream networks DISAGREE (IoU {iou:.3f}). Pick one backend and "
            f"stay with it — results from the two are not comparable."
        )
    if np.isfinite(out["accumulation_ratio"]) and not (
            0.95 <= out["accumulation_ratio"] <= 1.05):
        out["verdict"].append(
            f"Max contributing area differs by "
            f"{abs(1 - out['accumulation_ratio']):.1%}. Expect some difference "
            f"(breaching vs filling), but this is more than that."
        )
    return out


def _synthetic_valley(guard: Guard, out_prefix: str) -> str:
    """A V-shaped watershed draining through one gap in a nodata collar.

    Deliberately collared: a clipped DEM is the normal case and the one that
    broke depression filling, so a backend check that used a clean rectangle
    would be checking the easy case.
    """
    import rasterio
    from rasterio.transform import from_origin

    n, collar, axis = 200, 5, 100
    yy, xx = np.mgrid[0:n, 0:n]
    z = (1800.0 + 0.8 * np.abs(xx - axis) - 0.30 * yy).astype("float32")
    z[60:66, 40:46] = 1700.0                     # a pit, so filling has work
    nod = np.zeros((n, n), bool)
    nod[:collar, :] = nod[-collar:, :] = True
    nod[:, :collar] = nod[:, -collar:] = True
    nod[-collar:, axis - 1:axis + 1] = False     # the pour point
    z[nod] = -9999.0

    out = Path(guard.write_path(f"{out_prefix}_dem.tif"))
    prof = {"driver": "GTiff", "width": n, "height": n, "count": 1,
            "dtype": "float32", "crs": "EPSG:32612", "nodata": -9999.0,
            "transform": from_origin(500000, 4000000, 2.0, 2.0)}
    with rasterio.open(str(out), "w", **prof) as dst:
        dst.write(z, 1)
    return str(out)
