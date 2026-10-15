"""Does a TIN capture channel geometry better than a raster, and at what?

The question this answers: a 2.4 m raster smears the bank top. Would a TIN do
better? The honest answer has two halves that point opposite ways, and the only
way to separate them is to hold the DETECTOR fixed and vary only how the
surface is sampled.

So: one analytic channel of exactly known geometry, one bank-top finder, and
five ways of sampling the same ground.

    1. raster            regular grid, bilinear -- what geolens does now
    2. tin_from_raster   Delaunay over the SAME grid samples
    3. points_irregular  the same NUMBER of samples, scattered
    4. tin_breakline     scattered samples PLUS the bank-top breakline
    5. raster_fine       a finer regular grid

1 vs 2 asks whether triangulating a raster adds information. 1 vs 3 asks
whether irregular sampling helps. 3 vs 4 asks what a breakline is worth. 1 vs 5
asks what simply using finer data is worth.

The truth is analytic:

    flat bed to +/-6.0 m, bank face at 90% for 1.8 m, bank top at +/-8.0 m

so bank-top position error is measured in metres against 8.0, not inferred.

Run:  python tests/experiments/resolution_vs_tin.py
"""
from __future__ import annotations

import numpy as np

# -- the channel, lifted verbatim from tests/make_compound.py -----------------
CHANNEL_HALF = 6.0
BANK_HEIGHT = 1.8
BANK_SLOPE = 0.90
FLOODPLAIN_HALF = 55.0
FLOODPLAIN_SLOPE = 0.005
VALLEY_SLOPE = 0.35
BANK_TOP = CHANNEL_HALF + BANK_HEIGHT / BANK_SLOPE          # 8.0 m
NOISE = 0.02                       # metres, the DEM's own vertical noise

HALF_WIDTH = 80.0                  # how far out we sample either side


def cross(d):
    """Elevation above the invert as a function of |offset|."""
    d = np.asarray(d, dtype="float64")
    z = np.zeros_like(d)
    m = (d > CHANNEL_HALF) & (d <= BANK_TOP)
    z = np.where(m, (d - CHANNEL_HALF) * BANK_SLOPE, z)
    m = (d > BANK_TOP) & (d <= FLOODPLAIN_HALF)
    z = np.where(m, BANK_HEIGHT + (d - BANK_TOP) * FLOODPLAIN_SLOPE, z)
    m = d > FLOODPLAIN_HALF
    fp_top = BANK_HEIGHT + (FLOODPLAIN_HALF - BANK_TOP) * FLOODPLAIN_SLOPE
    z = np.where(m, fp_top + (d - FLOODPLAIN_HALF) * VALLEY_SLOPE, z)
    return z


def surface(x, y):
    """A straight channel running along y. Downstream fall keeps it realistic."""
    return cross(np.abs(x)) - 0.004 * np.asarray(y, dtype="float64")


# -- one bank-top finder, used on every sampling strategy ---------------------

def bank_top(offsets, z, bank_slope=0.15, flat_slope=0.05):
    """Offset of the right-hand bank top, by slope break.

    Walk outward from the thalweg: find where the ground first steepens past
    ``bank_slope``, then where it flattens back below ``flat_slope``. That
    second point is the bank top. This is the same rule geolens' delineation
    uses, reimplemented in 20 lines so that it is IDENTICAL across all five
    sampling strategies — otherwise the comparison measures the detector, not
    the sampling.
    """
    offsets = np.asarray(offsets, dtype="float64")
    z = np.asarray(z, dtype="float64")
    keep = offsets >= 0
    o, zz = offsets[keep], z[keep]
    order = np.argsort(o)
    o, zz = o[order], zz[order]
    if o.size < 4:
        return np.nan

    slope = np.gradient(zz, o)
    on_bank = False
    for i in range(1, o.size):
        if not on_bank and slope[i] >= bank_slope:
            on_bank = True
        elif on_bank and slope[i] < flat_slope:
            return float(o[i])
    return np.nan


# -- the five ways of sampling the same ground --------------------------------

def _noise(rng, n):
    return rng.normal(0.0, NOISE, n)


def sample_raster(cell, rng, y0=0.0, sub=0.25):
    """Regular grid, bilinear-interpolated along the section."""
    from scipy.interpolate import RegularGridInterpolator

    xs = np.arange(-HALF_WIDTH - 2 * cell, HALF_WIDTH + 2 * cell, cell)
    ys = np.arange(y0 - 4 * cell, y0 + 4 * cell, cell)
    zz = surface(xs[None, :], ys[:, None]) + rng.normal(0, NOISE, (ys.size, xs.size))
    f = RegularGridInterpolator((ys, xs), zz, bounds_error=False,
                                fill_value=None)
    o = np.arange(-HALF_WIDTH, HALF_WIDTH + sub, sub)
    return o, f(np.column_stack([np.full(o.size, y0), o]))


def sample_tin_from_raster(cell, rng, y0=0.0, sub=0.25):
    """Delaunay over the SAME grid samples, then linear interpolation."""
    from scipy.interpolate import LinearNDInterpolator

    xs = np.arange(-HALF_WIDTH - 2 * cell, HALF_WIDTH + 2 * cell, cell)
    ys = np.arange(y0 - 4 * cell, y0 + 4 * cell, cell)
    XX, YY = np.meshgrid(xs, ys)
    ZZ = surface(XX, YY) + rng.normal(0, NOISE, XX.shape)
    f = LinearNDInterpolator(np.column_stack([XX.ravel(), YY.ravel()]),
                             ZZ.ravel())
    o = np.arange(-HALF_WIDTH, HALF_WIDTH + sub, sub)
    return o, f(o, np.full(o.size, y0))


def sample_points(cell, rng, y0=0.0, sub=0.25, breakline=False):
    """Scattered points at the same average density as the grid.

    With ``breakline``, the bank-top lines are inserted as vertices — which is
    what a surveyed or photogrammetric breakline does in a real TIN.
    """
    from scipy.interpolate import LinearNDInterpolator

    area = (2 * HALF_WIDTH + 4 * cell) * (8 * cell)
    n = max(50, int(area / (cell * cell)))
    px = rng.uniform(-HALF_WIDTH - 2 * cell, HALF_WIDTH + 2 * cell, n)
    py = rng.uniform(y0 - 4 * cell, y0 + 4 * cell, n)

    if breakline:
        # Dense vertices exactly on the two bank tops, and on the toe of the
        # bank. A breakline is a LINE the triangulation is forced to honour.
        bl_y = np.arange(y0 - 4 * cell, y0 + 4 * cell, max(cell / 4, 0.25))
        for bx in (-BANK_TOP, BANK_TOP, -CHANNEL_HALF, CHANNEL_HALF):
            px = np.concatenate([px, np.full(bl_y.size, bx)])
            py = np.concatenate([py, bl_y])

    pz = surface(px, py) + _noise(rng, px.size)
    f = LinearNDInterpolator(np.column_stack([px, py]), pz)
    o = np.arange(-HALF_WIDTH, HALF_WIDTH + sub, sub)
    return o, f(o, np.full(o.size, y0))


# -- run ----------------------------------------------------------------------

def sample_points_estimated_breakline(cell, rng, y0=0.0, sub=0.25):
    """Breakline at the bank top the RASTER pass estimated, not the true one.

    The `tin_breakline` case above is circular: it places the breakline exactly
    where the answer is. In practice you do not know where the bank top is --
    that is what you are measuring -- so the only honest two-pass version is to
    detect on the raster first and use THAT position.

    The question this settles is whether a breakline at a wrong position
    CREATES a false break there, in which case the second pass just returns the
    first pass's answer with a confident-looking TIN wrapped around it.
    """
    from scipy.interpolate import LinearNDInterpolator

    o_r, z_r = sample_raster(cell, rng, y0=y0)
    est = bank_top(o_r, z_r)
    if not np.isfinite(est):
        est = BANK_TOP

    area = (2 * HALF_WIDTH + 4 * cell) * (8 * cell)
    n = max(50, int(area / (cell * cell)))
    px = rng.uniform(-HALF_WIDTH - 2 * cell, HALF_WIDTH + 2 * cell, n)
    py = rng.uniform(y0 - 4 * cell, y0 + 4 * cell, n)
    bl_y = np.arange(y0 - 4 * cell, y0 + 4 * cell, max(cell / 4, 0.25))
    for bx in (-est, est):
        px = np.concatenate([px, np.full(bl_y.size, bx)])
        py = np.concatenate([py, bl_y])

    pz = surface(px, py) + _noise(rng, px.size)
    f = LinearNDInterpolator(np.column_stack([px, py]), pz)
    o = np.arange(-HALF_WIDTH, HALF_WIDTH + sub, sub)
    return o, f(o, np.full(o.size, y0))


STRATEGIES = {
    "raster": lambda c, r: sample_raster(c, r),
    "tin_from_raster": lambda c, r: sample_tin_from_raster(c, r),
    "points_irregular": lambda c, r: sample_points(c, r, breakline=False),
    "tin_breakline": lambda c, r: sample_points(c, r, breakline=True),
    "tin_breakline_est": sample_points_estimated_breakline,
}


def run(cells=(0.5, 1.0, 2.4, 5.0), trials=25):
    print(f"TRUTH: bank top at +/-{BANK_TOP:g} m, bank face {BANK_SLOPE:.0%}, "
          f"{BANK_HEIGHT:g} m high, DEM noise {NOISE:g} m\n")
    header = f"{'cell (m)':>9} {'strategy':>18} {'bank top':>10} {'error':>9} {'|err| p90':>10} {'miss':>6}"
    print(header)
    print("-" * len(header))

    results = {}
    for cell in cells:
        for name, fn in STRATEGIES.items():
            errs = []
            misses = 0
            for t in range(trials):
                rng = np.random.default_rng(1000 + t)
                o, z = fn(cell, rng)
                bt = bank_top(o, z)
                if not np.isfinite(bt):
                    misses += 1
                    continue
                errs.append(bt - BANK_TOP)
            if errs:
                e = np.asarray(errs)
                results[(cell, name)] = (float(np.median(e)),
                                         float(np.percentile(np.abs(e), 90)))
                print(f"{cell:9g} {name:>18} {BANK_TOP + np.median(e):10.2f} "
                      f"{np.median(e):+9.2f} {np.percentile(np.abs(e), 90):10.2f} "
                      f"{misses:6d}")
            else:
                print(f"{cell:9g} {name:>18} {'--':>10} {'--':>9} {'--':>10} "
                      f"{misses:6d}")
        print()
    return results


if __name__ == "__main__":
    res = run()
    print("READ IT LIKE THIS")
    print("  raster vs tin_from_raster   -> what triangulating a raster buys")
    print("  raster vs points_irregular  -> what irregular sampling buys")
    print("  points_irregular vs tin_breakline -> what a TRUE breakline buys")
    print("  tin_breakline vs tin_breakline_est -> whether a two-pass estimate")
    print("                                        recovers any of that, or")
    print("                                        just echoes pass one")
    print("  across cell sizes           -> what finer data buys")
