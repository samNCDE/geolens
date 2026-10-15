"""Synthetic LANDSCAPE: a drainage network whose valley width varies by a known
function, so confinement classification can be scored against truth.

Layout (30 m cells, 30 x 20 km by default):

  * regional surface falling west -> east
  * a mainstem valley running W->E along the middle, whose HALF-WIDTH cycles
    narrow (gorge) -> wide (alluvial) -> narrow -> wide, by an exact function
  * two tributaries joining from north and south
  * flat valley bottoms, V-shaped side slopes above them, channel incised within

Truth written to landscape_truth.csv: valley half-width at every mainstem x.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
import geopandas as gpd
from shapely.geometry import LineString

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "landscape"
OUT.mkdir(parents=True, exist_ok=True)

CELL = 30.0
W, H = 1000, 667                     # 30 km x 20 km
X0, Y0 = 400_000.0, 4_500_000.0
EPSG = 32612

REGIONAL_SLOPE = 0.004               # falls to the east
BASE = 2000.0
VALLEY_SIDE_SLOPE = 0.25             # steep confining walls
VALLEY_FLOOR_SLOPE = 0.006           # cross-valley tilt toward the channel
CHANNEL_INCISION = 4.0
CHANNEL_HALF = 45.0
NOISE = 0.25

# Mainstem valley half-width (m) as a function of distance east.
# Four segments: gorge, wide, gorge, wide — each with a known constant width.
SEGMENTS = [
    (0.0,     7_500.0,  150.0),      # confined gorge
    (7_500.0, 15_000.0, 900.0),      # unconfined alluvial
    (15_000.0, 21_000.0, 240.0),     # confined
    (21_000.0, 30_000.0, 1_400.0),   # broad unconfined
]
TAPER = 600.0                        # blend distance between segments


def half_width(x: np.ndarray) -> np.ndarray:
    """Valley half-width at distance ``x`` east, smoothly blended at joins."""
    hw = np.full_like(x, SEGMENTS[0][2], dtype="float64")
    for x0, x1, w in SEGMENTS:
        hw = np.where(x >= x0, w, hw)
    # Smooth the steps so flow routing doesn't see a cliff.
    out = hw.copy()
    for x0, _, _ in SEGMENTS[1:]:
        m = np.abs(x - x0) < TAPER
        if m.any():
            t = (x[m] - (x0 - TAPER)) / (2 * TAPER)
            t = np.clip(t, 0, 1)
            smooth = t * t * (3 - 2 * t)
            before = np.interp(x0 - TAPER - 1, [s[0] for s in SEGMENTS],
                               [s[2] for s in SEGMENTS])
            prev_w = next(w for a, b, w in SEGMENTS if a <= x0 - TAPER - 1 < b)
            next_w = next(w for a, b, w in SEGMENTS if a <= x0 + 1 < b)
            out[m] = prev_w + (next_w - prev_w) * smooth
    return out


xs = np.arange(W) * CELL
ys = np.arange(H) * CELL
XX, YY = np.meshgrid(xs, ys)

y_mid = (H * CELL) / 2.0

# --- mainstem ---------------------------------------------------------------
# Gentle sinuosity so the valley isn't perfectly axis-aligned.
main_cy = y_mid + 250.0 * np.sin(2 * np.pi * xs / 12_000.0)
CY = np.broadcast_to(main_cy, (H, W))
HW = np.broadcast_to(half_width(xs), (H, W))

d_main = np.abs(YY - CY)

# --- tributaries ------------------------------------------------------------
# North trib joins at x=10 km; south trib joins at x=23 km. Each runs diagonally.
def trib_distance(join_x: float, from_north: bool, length: float, hw: float):
    """Distance to a straight tributary valley axis, and its half-width."""
    jy = y_mid + 250.0 * np.sin(2 * np.pi * join_x / 12_000.0)
    end_y = jy + (length if from_north else -length)
    ax, ay = join_x, jy
    bx, by = join_x - length * 0.6, end_y
    vx, vy = bx - ax, by - ay
    L2 = vx * vx + vy * vy
    t = ((XX - ax) * vx + (YY - ay) * vy) / L2
    t = np.clip(t, 0.0, 1.0)
    px, py = ax + t * vx, ay + t * vy
    return np.hypot(XX - px, YY - py), hw


d_n, hw_n = trib_distance(10_000.0, True, 6_000.0, 180.0)
d_s, hw_s = trib_distance(23_000.0, False, 5_000.0, 200.0)

# --- assemble ---------------------------------------------------------------
# Each valley contributes a floor; terrain is the MINIMUM of the three surfaces
# so junctions merge naturally.
def valley_surface(dist, hw, floor_base):
    inside = dist <= hw
    z = np.where(
        inside,
        floor_base + dist * VALLEY_FLOOR_SLOPE,
        floor_base + hw * VALLEY_FLOOR_SLOPE + (dist - hw) * VALLEY_SIDE_SLOPE,
    )
    return z


floor_main = BASE - REGIONAL_SLOPE * XX
z = valley_surface(d_main, HW, floor_main)

# Tributary floors sit slightly above the mainstem at their junction and climb.
for d, hw, join_x, sgn in ((d_n, hw_n, 10_000.0, 1.0), (d_s, hw_s, 23_000.0, -1.0)):
    jz = BASE - REGIONAL_SLOPE * join_x
    along = np.hypot(XX - join_x, YY - y_mid)
    floor_t = jz + along * 0.010          # tributaries steeper than the mainstem
    z = np.minimum(z, valley_surface(d, hw, floor_t))

# Incise the channels.
for d in (d_main, d_n, d_s):
    inner = d <= CHANNEL_HALF
    z = np.where(inner, z - CHANNEL_INCISION * (1 - (d / CHANNEL_HALF) ** 2), z)

rng = np.random.default_rng(11)
z = z + rng.normal(0, NOISE, z.shape)
z = z[::-1, :].astype("float32")

transform = from_origin(X0, Y0 + H * CELL, CELL, CELL)
dem_path = OUT / "landscape_dem.tif"
with rasterio.open(
    dem_path, "w", driver="GTiff", height=H, width=W, count=1, dtype="float32",
    crs=f"EPSG:{EPSG}", transform=transform, nodata=-9999.0, compress="deflate",
) as dst:
    dst.write(z, 1)

# A stand-in "NHD" mainstem line, deliberately offset ~2 cells from the true
# thalweg so snapping/conflation has something real to fix.
nhd_xs = np.arange(0, W * CELL, 200.0)
nhd_cy = y_mid + 250.0 * np.sin(2 * np.pi * nhd_xs / 12_000.0) + 60.0
gpd.GeoDataFrame(
    {"gnis_name": ["Test Mainstem"], "reachcode": ["15010001000001"]},
    geometry=[LineString([(X0 + float(a), Y0 + float(b))
                          for a, b in zip(nhd_xs, nhd_cy)])],
    crs=f"EPSG:{EPSG}",
).to_file(OUT / "nhd_flowline.shp")

# Truth table: valley half-width along the mainstem.
import csv
with (OUT / "landscape_truth.csv").open("w", newline="") as fh:
    w_ = csv.writer(fh)
    w_.writerow(["x_east", "valley_half_width", "valley_width", "segment"])
    hw_all = half_width(xs)
    for x, hw in zip(xs, hw_all):
        seg = next((i for i, (a, b, _) in enumerate(SEGMENTS) if a <= x < b), len(SEGMENTS) - 1)
        w_.writerow([x, hw, 2 * hw, seg])

print(f"DEM: {dem_path}  ({W}x{H} @ {CELL} m = {W*CELL/1000:.0f} x {H*CELL/1000:.0f} km)")
print(f"     {W*H:,} cells | elevation {z.min():.1f} .. {z.max():.1f}")
print(f"NHD stand-in: {OUT/'nhd_flowline.shp'} (offset ~60 m from true thalweg)")
print(f"Truth table:  {OUT/'landscape_truth.csv'}")
print()
print("=== TRUTH: mainstem valley width by segment ===")
for i, (a, b, w) in enumerate(SEGMENTS):
    kind = "confined" if w < 300 else "unconfined"
    print(f"  seg {i}: x {a/1000:5.1f}-{b/1000:5.1f} km | half-width {w:7.1f} m"
          f" | width {2*w:7.1f} m | {kind}")
