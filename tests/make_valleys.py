"""A landscape with RIDGES — the fixture valley form never had.

``make_landscape.py`` builds a valley whose side slopes rise at a constant
gradient and never stop. It has truth for valley-bottom *width*, which is what
it was built for, and it is the only landscape fixture there was. So every test
of valley form was run on terrain with **no ridgetop in it**: the crest
detector found nothing because there was nothing to find, and the failure
looked like a tuning problem for two releases running.

This builds five parallel valleys separated by real divides, each with a
different, known form, so "valley bottom to ridgetop" is a measurable claim:

    valley  floor half-width   depth   shape n   what it is
    ------  ----------------   -----   -------   ------------------------------
      0            40 m         320 m    1.0     narrow V-notch
      1           450 m         260 m    2.0     broad U, glacial-trough-like
      2            90 m         400 m    1.2     confined gorge
      3           900 m         180 m    1.6     wide shallow alluvial valley
      4            30 m         300 m    1.0     steep canyon, 70% walls

Ridge positions are **measured from the surface that was built**, not assumed
to sit midway: neighbouring valleys have different depths, so their flanks
cross wherever they cross. Truth is what the DEM actually contains.

Valley 4 exists because the first four are all kilometres wide and therefore
have moderate flanks (12-16%) however deep they are, which left the steep half
of the Rosgen key — the canyons — untestable.

Writes ``valleys/valleys_dem.tif``, ``valleys/valleys_axes.gpkg`` (the five
valley axes, as a centreline layer) and ``valleys/valleys_truth.csv``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
import geopandas as gpd
from shapely.geometry import LineString

OUT = (Path(sys.argv[1]) if len(sys.argv) > 1
       else Path(__file__).resolve().parent / "valleys")
OUT.mkdir(parents=True, exist_ok=True)

CELL = 20.0
W, H = 1100, 700                     # 22 km x 14 km
X0, Y0 = 500_000.0, 4_600_000.0
EPSG = 32612

BASE = 1800.0
DOWN_VALLEY = 0.008                  # falls to the south, so water moves
CHANNEL_INCISION = 3.0
CHANNEL_HALF = 30.0
NOISE = 0.15

# axis_x_m, floor half-width, depth to ridge, shape exponent, flank reach
#
# The reach is per valley so the side-slope GRADIENT can be set independently
# of the shape: the first four valleys are kilometres wide and therefore have
# moderate flanks (12-16%) however deep they are, which left the steep half of
# the Rosgen key — I and VI, the canyons — untestable. Valley 4 is a real
# canyon: 300 m of relief in 430 m, a 70% wall.
VALLEYS = [
    (2_600.0,   40.0, 320.0, 1.0, 2_600.0),   # narrow V-notch
    (7_800.0,  450.0, 260.0, 2.0, 2_150.0),   # broad U, glacial-trough-like
    (13_000.0,  90.0, 400.0, 1.2, 2_510.0),   # confined gorge
    (18_600.0, 900.0, 180.0, 1.6, 1_700.0),   # wide shallow alluvial
    (21_300.0,  30.0, 300.0, 1.0,   430.0),   # steep V-notch canyon, 70% walls
]
# The distance at which a flank has risen by its full ``depth``. Flanks are
# NOT capped there: capping made every flank flat beyond its reach, the
# elementwise minimum picked the lowest cap, and the whole landscape came out
# as one flat surface with no ridges at all -- the same defect as the fixture
# this replaces, reintroduced by a np.clip.
FLANK_REACH = 2_600.0            # default, used for the phantom valleys

# Valleys outside the grid, so the outermost real valleys have a neighbour to
# form a ridge against instead of running off the edge still climbing.
PHANTOM = (-1_500.0, 23_000.0)


def _flank(d: np.ndarray, floor_hw: float, depth: float, n: float,
           reach: float = FLANK_REACH) -> np.ndarray:
    """Height above the floor at cross-valley distance ``d`` from the axis.

    Rises without limit beyond ``FLANK_REACH``; the ridge is where this flank
    meets the neighbouring valley's, not where it stops climbing.
    """
    t = np.clip((np.abs(d) - floor_hw), 0.0, None) / max(reach - floor_hw, 1e-6)
    return depth * t ** n


def build() -> None:
    xs = np.arange(W) * CELL
    ys = np.arange(H) * CELL
    X, Y = np.meshgrid(xs, ys)

    # Each valley proposes a surface; the land is the lowest proposal, so a
    # ridge forms exactly where two flanks cross.
    surfaces = []
    for ax, hw, depth, n, reach in VALLEYS:
        surfaces.append(_flank(X - ax, hw, depth, n, reach))
    for ax in PHANTOM:
        surfaces.append(_flank(X - ax, 100.0, 300.0, 1.3))
    above_floor = np.minimum.reduce(surfaces)

    z = BASE - DOWN_VALLEY * Y + above_floor

    # Incise a channel along each axis so a stream network can be extracted.
    for ax, hw, _d, _n, _r in VALLEYS:
        d = np.abs(X - ax)
        z -= np.clip(CHANNEL_INCISION * (1.0 - d / CHANNEL_HALF), 0, None)

    rng = np.random.default_rng(11)
    z = (z + rng.normal(0.0, NOISE, z.shape)).astype("float32")

    dem = OUT / "valleys_dem.tif"
    prof = {"driver": "GTiff", "width": W, "height": H, "count": 1,
            "dtype": "float32", "crs": f"EPSG:{EPSG}", "nodata": -9999.0,
            "transform": from_origin(X0, Y0, CELL, CELL), "compress": "lzw"}
    with rasterio.open(str(dem), "w", **prof) as dst:
        dst.write(z, 1)

    # Valley axes as a centreline layer, north to south (raster order).
    lines, ids = [], []
    for i, (ax, *_rest) in enumerate(VALLEYS):
        pts = [(X0 + ax, Y0 - y) for y in ys[::10]]
        lines.append(LineString(pts))
        ids.append(i)
    gpd.GeoDataFrame({"valley_id": ids}, geometry=lines,
                     crs=f"EPSG:{EPSG}").to_file(
        str(OUT / "valleys_axes.gpkg"), driver="GPKG")

    # -- truth, measured off the surface that was actually built --------------
    #
    # Taken along a row in the middle of the grid, away from the edges, with
    # the regional tilt and the channel incision removed so what is left is the
    # cross-valley form alone.
    mid = H // 2
    band = above_floor[mid]
    rows = []
    for i, (ax, hw, depth, n, reach) in enumerate(VALLEYS):
        axis_col = int(round(ax / CELL))
        # The ridge on each side is the highest point BETWEEN this axis and the
        # neighbouring one — searching the whole flank would find whichever
        # distant valley happens to be deepest.
        axes = sorted([v[0] for v in VALLEYS] + list(PHANTOM))
        j = axes.index(ax)
        lcol = max(0, int(round(axes[j - 1] / CELL)))
        rcol = min(W - 1, int(round(axes[j + 1] / CELL)))
        left = band[lcol:axis_col]
        right = band[axis_col:rcol + 1]
        lo = lcol + (int(np.argmax(left)) if left.size else 0)
        hi = axis_col + (int(np.argmax(right)) if right.size else 0)
        rows.append({
            "valley_id": i,
            "axis_x_m": ax,
            "floor_half_width_m": hw,
            "built_depth_m": depth,
            "built_shape_n": n,
            "ridge_left_x_m": lo * CELL,
            "ridge_right_x_m": hi * CELL,
            "ridge_left_height_m": float(band[lo]),
            "ridge_right_height_m": float(band[hi]),
            "ridge_left_distance_m": ax - lo * CELL,
            "ridge_right_distance_m": hi * CELL - ax,
            "valley_top_width_m": (hi - lo) * CELL,
        })
    import csv

    tp = OUT / "valleys_truth.csv"
    with tp.open("w", newline="", encoding="utf-8") as fh:
        wtr = csv.DictWriter(fh, fieldnames=list(rows[0]))
        wtr.writeheader()
        wtr.writerows(rows)

    print(f"wrote {dem}")
    print(f"      {OUT / 'valleys_axes.gpkg'}")
    print(f"      {tp}")
    for r in rows:
        print(f"  valley {r['valley_id']}: ridges at "
              f"{r['ridge_left_distance_m']:.0f} m / "
              f"{r['ridge_right_distance_m']:.0f} m from the axis, "
              f"heights {r['ridge_left_height_m']:.0f} / "
              f"{r['ridge_right_height_m']:.0f} m, "
              f"top width {r['valley_top_width_m']:.0f} m")


if __name__ == "__main__":
    build()
