"""A test DEM that is actually terrain.

``make_valleys.py`` builds five straight parallel troughs with planar flanks.
That is the right fixture for "does a 900 m floor measure 900 m", and it is
the wrong one for anything that has to hold together across a landscape: it
has no confluences, no sinuosity, no divides that wander, and the drainage
network extracted from its planar sides is nonsense -- 124 branches on five
valleys. Judging a valley map on it is judging it on a corrugated roof.

So this grows terrain instead of drawing it, with the standard pairing:

* **stream-power incision** ``dz/dt = -K A^m S^n`` (Howard 1994), which is
  what makes channels cut down and branch, and
* **hillslope diffusion** ``dz/dt = D del^2 z``, which is what rounds the
  ridges between them and gives divides somewhere to be.

Run to near steady state from fractal noise, that produces a dendritic network
with realistic junction angles, valleys whose width varies down-stream, and
divides that are ridgelines rather than straight lines. The solver is the
implicit one from Braun & Willett (2013): ordering nodes by elevation means a
receiver is always already updated, so the timestep is not limited by the
Courant condition and a few dozen iterations reach steady state.

The CRS is **EPSG:6529** (NAD83(2011) New Mexico Central, US survey feet),
deliberately: it is the grid this package is actually used on, and a fixture
in metres cannot catch a unit bug on the valley path.

    python tests/make_terrain.py            # tests/terrain/terrain_dem.tif
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent / "terrain"
FT_US = 1200.0 / 3937.0                       # metres per US survey foot

NY, NX = 700, 900                             # 7 x 9 km at 10 m
CELL_M = 10.0
CELL = CELL_M / FT_US                         # 32.808... ftUS
RELIEF_M = 600.0                              # ridge to outlet
SEED = 20260922


def _fbm(ny, nx, rng, octaves=7, persistence=0.55):
    """Fractal noise. Real topography is self-affine; white noise is not."""
    out = np.zeros((ny, nx))
    amp, total = 1.0, 0.0
    for o in range(octaves):
        n = 2 ** (o + 2)
        coarse = rng.standard_normal((min(n, ny), min(n, nx)))
        yi = np.linspace(0, coarse.shape[0] - 1, ny)
        xi = np.linspace(0, coarse.shape[1] - 1, nx)
        y0 = np.clip(yi.astype(int), 0, coarse.shape[0] - 2)
        x0 = np.clip(xi.astype(int), 0, coarse.shape[1] - 2)
        fy, fx = (yi - y0)[:, None], (xi - x0)[None, :]
        c = coarse
        lay = ((c[y0][:, x0] * (1 - fy) + c[y0 + 1][:, x0] * fy) * (1 - fx)
               + (c[y0][:, x0 + 1] * (1 - fy) + c[y0 + 1][:, x0 + 1] * fy) * fx)
        out += amp * lay
        total += amp
        amp *= persistence
    return out / total


def _fill(z, eps=1e-4):
    """Priority-flood depression fill, so every cell has a downhill receiver."""
    import heapq

    ny, nx = z.shape
    filled = np.full_like(z, np.inf)
    seen = np.zeros(z.shape, bool)
    heap = []
    for i, j in ([(0, j) for j in range(nx)] + [(ny - 1, j) for j in range(nx)]
                 + [(i, 0) for i in range(ny)] + [(i, nx - 1) for i in range(ny)]):
        if not seen[i, j]:
            seen[i, j] = True
            filled[i, j] = z[i, j]
            heapq.heappush(heap, (z[i, j], i, j))
    while heap:
        zv, i, j = heapq.heappop(heap)
        for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1),
                       (-1, -1), (-1, 1), (1, -1), (1, 1)):
            a, b = i + di, j + dj
            if 0 <= a < ny and 0 <= b < nx and not seen[a, b]:
                seen[a, b] = True
                filled[a, b] = max(z[a, b], zv + eps)
                heapq.heappush(heap, (filled[a, b], a, b))
    return filled


def _receivers(z, dx):
    """Steepest-descent neighbour of every cell, and the distance to it."""
    ny, nx = z.shape
    flat = z.ravel()
    idx = np.arange(flat.size)
    best_r = idx.copy()
    best_s = np.zeros(flat.size)
    best_l = np.full(flat.size, dx)
    ii, jj = np.divmod(idx, nx)
    for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1),
                   (-1, -1), (-1, 1), (1, -1), (1, 1)):
        a, b = ii + di, jj + dj
        ok = (a >= 0) & (a < ny) & (b >= 0) & (b < nx)
        nidx = np.where(ok, a * nx + b, idx)
        length = dx * (2 ** 0.5 if di and dj else 1.0)
        slope = np.where(ok, (flat - flat[nidx]) / length, -np.inf)
        take = slope > best_s
        best_r = np.where(take, nidx, best_r)
        best_l = np.where(take, length, best_l)
        best_s = np.where(take, slope, best_s)
    return best_r, best_l


try:
    from numba import njit
except ImportError:                                   # pragma: no cover
    def njit(*a, **k):
        def deco(f):
            return f
        return deco if not a else a[0]


@njit(cache=True)
def _accumulate(order, recv, area):
    """Drainage area, walking high to low so a receiver is always after."""
    for k in range(order.size):
        i = order[k]
        if recv[i] != i:
            area[recv[i]] += area[i]
    return area


@njit(cache=True)
def _incise(order, recv, z, zn, f):
    """Implicit stream power, n = 1 (Braun & Willett 2013).

    Exact for any timestep, because walking low to high means the receiver's
    new elevation is already known when its donors are solved. That is what
    makes 80 steps enough to reach a steady state instead of 80,000.
    """
    for k in range(order.size - 1, -1, -1):
        i = order[k]
        r = recv[i]
        if r != i:
            zn[i] = (z[i] + f[i] * zn[r]) / (1.0 + f[i])
    return zn


def build(ny=NY, nx=NX, cell_m=CELL_M, relief_m=RELIEF_M, seed=SEED,
          steps=60, k=4.5e-5, m=0.45, dt=6000.0, diff=0.012, progress=True):
    """Grow terrain. Stream power cuts the channels, diffusion rounds the ridges.

    ``diff`` is applied in SUB-STEPS. Explicit diffusion is only stable while
    ``D dt / dx^2 <= 1/4``; the first version of this ran at 0.72 and the
    surface did what an unstable explicit scheme does -- it did not wobble, it
    went to 1.6e31 metres of relief in eighty steps. The sub-step count is
    derived from the stability limit rather than guessed, so changing ``dt``,
    ``diff`` or the cell size cannot quietly reintroduce it.
    """
    rng = np.random.default_rng(seed)
    yy = np.linspace(1.0, 0.0, ny)[:, None]
    z = relief_m * (0.62 * yy + 0.38 * (_fbm(ny, nx, rng) * 0.5 + 0.5))
    z[-1, :] = 0.0                                  # the outlet edge

    # Sub-stepping for the diffusion term.
    courant = diff * dt / (cell_m ** 2)
    n_sub = max(1, int(np.ceil(courant / 0.2)))
    dt_sub = dt / n_sub
    if progress:
        print(f"  diffusion D dt/dx^2 = {courant:.2f} -> {n_sub} sub-steps "
              f"of {courant / n_sub:.3f} (stable below 0.25)")

    t0 = time.time()
    for it in range(steps):
        zf = _fill(z)
        recv, ell = _receivers(zf, cell_m)
        flat = zf.ravel()
        order = np.argsort(flat)[::-1].astype(np.int64)   # high -> low
        area = _accumulate(order, recv.astype(np.int64),
                           np.full(flat.size, cell_m * cell_m))

        f = k * dt * area ** m / ell
        z = _incise(order, recv.astype(np.int64), flat, flat.copy(),
                    f).reshape(ny, nx)

        for _ in range(n_sub):
            lap = (-4.0 * z + np.roll(z, 1, 0) + np.roll(z, -1, 0)
                   + np.roll(z, 1, 1) + np.roll(z, -1, 1)) / (cell_m ** 2)
            lap[0], lap[-1], lap[:, 0], lap[:, -1] = 0, 0, 0, 0
            z = z + diff * dt_sub * lap
        z[-1, :] = 0.0

        if progress and (it + 1) % 10 == 0:
            print(f"  step {it+1}/{steps}  relief {z.max()-z.min():7.1f} m "
                  f"  {time.time()-t0:5.1f}s", flush=True)
        if not np.isfinite(z).all() or z.max() - z.min() > 50 * relief_m:
            raise RuntimeError(
                f"the surface ran away at step {it+1} (relief "
                f"{z.max()-z.min():.3g} m). Something is unstable — check "
                f"the diffusion sub-stepping."
            )
    return z


def write(z, path: Path, metric: bool = False):
    """Write the surface, in US survey feet or in metres.

    **Both, from one array, on purpose.** Every unit bug this package has had
    -- and there have been several, including two found in the gully
    measurement the day this was written -- is invisible on a metric fixture,
    because a metres-per-unit of 1.0 makes every missing conversion a no-op.
    A pair of DEMs that describe the SAME GROUND in different units turns each
    of those into a failing assertion: the physical answer cannot depend on
    which file you opened.

    EPSG:6529 is NAD83(2011) New Mexico Central in US survey feet, which is
    the grid this package is actually used on. Elevations follow the
    horizontal unit, as a real DEM on that grid does.
    """
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_origin

    if metric:
        crs, cell, zz = CRS.from_epsg(32613), CELL_M, z
        origin = (400_000.0, 3_900_000.0)
    else:
        crs, cell, zz = CRS.from_epsg(6529), CELL, z / FT_US
        origin = (1_600_000.0, 1_400_000.0)

    prof = dict(driver="GTiff", height=z.shape[0], width=z.shape[1], count=1,
                dtype="float32", crs=crs, nodata=-9999.0,
                transform=from_origin(origin[0], origin[1], cell, cell),
                compress="deflate", predictor=3, tiled=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(str(path), "w", **prof) as dst:
        dst.write(zz.astype("float32"), 1)
    return path


def build_pair(small: bool = False, steps: int | None = None) -> dict:
    """The fixture: one surface, written twice, in feet and in metres."""
    if small:
        ny, nx, steps = 300, 380, steps or 45
    else:
        ny, nx, steps = NY, NX, steps or 60
    z = build(ny=ny, nx=nx, steps=steps, progress=not small)
    tag = "_small" if small else ""
    return {
        "ftus": write(z, OUT / f"terrain{tag}_dem.tif", metric=False),
        "metric": write(z, OUT / f"terrain{tag}_dem_m.tif", metric=True),
        "relief_m": float(z.max() - z.min()),
        "shape": z.shape,
    }


if __name__ == "__main__":
    small = "--small" in sys.argv
    rest = [a for a in sys.argv[1:] if not a.startswith("-")]
    steps = int(rest[0]) if rest else None
    print(f"growing {'small ' if small else ''}terrain at {CELL_M} m "
          f"({CELL:.4f} ftUS)")
    r = build_pair(small=small, steps=steps)
    print(f"wrote {r['ftus']}")
    print(f"wrote {r['metric']}")
    print(f"{r['shape'][0]}x{r['shape'][1]}, relief {r['relief_m']:.0f} m "
          f"({r['relief_m'] / FT_US:.0f} ftUS)")
