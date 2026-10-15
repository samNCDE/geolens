"""Synthetic hillslope with GULLIES and HEADCUTS at exactly known positions.

  * a plane falling west -> east at a known gradient
  * N parallel gullies running downslope, known spacing / width / depth
  * each gully has a HEADCUT: upstream of x_head it is absent, downstream it
    deepens to full depth over a short ramp. The headcut is the step.
  * a trunk channel along the eastern edge collecting them

Truth is written to gully_truth.csv so detection can be scored against it.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "gullies"
OUT.mkdir(parents=True, exist_ok=True)

CELL = 2.0                       # 2 m DEM — gullies need resolution
W, H = 900, 700                  # 1.8 km x 1.4 km
X0, Y0 = 300_000.0, 3_800_000.0
EPSG = 32612

BASE = 1200.0
HILL_SLOPE = 0.045               # 4.5% hillslope, falling east
N_GULLIES = 6
GULLY_SPACING = 200.0            # m between gully axes
GULLY_HALF_WIDTH = 7.0           # m — gullies are ~14 m wide
GULLY_DEPTH = 3.0                # m at full development
HEADCUT_DROP = 2.2               # m of the depth gained across the headcut
HEADCUT_RAMP = 8.0               # m over which the step is gained (abrupt)
TRUNK_Y_FRAC = 0.5
TRUNK_HALF = 25.0
TRUNK_DEPTH = 6.0
NOISE = 0.04

xs = np.arange(W) * CELL
ys = np.arange(H) * CELL
XX, YY = np.meshgrid(xs, ys)

z = BASE - HILL_SLOPE * XX

# --- gullies ------------------------------------------------------------------
gully_ys = [(i + 1) * GULLY_SPACING for i in range(N_GULLIES)]
gully_ys = [y for y in gully_ys if y < H * CELL - 50]

# Each gully starts (has its headcut) at a different x, staggered.
head_xs = [300.0 + 120.0 * i for i in range(len(gully_ys))]

truth_rows = []
for gi, (gy, hx) in enumerate(zip(gully_ys, head_xs)):
    d = np.abs(YY - gy)
    inside = d <= GULLY_HALF_WIDTH
    # Cross-section: parabolic, full depth at axis.
    shape = np.where(inside, 1.0 - (d / GULLY_HALF_WIDTH) ** 2, 0.0)

    # Longitudinal: 0 upstream of the headcut, then a fast ramp to full depth.
    t = np.clip((XX - hx) / HEADCUT_RAMP, 0.0, 1.0)
    step = t * t * (3 - 2 * t)                      # smoothstep over the ramp
    # Shallow pre-headcut swale so flow concentrates and routing finds the head.
    pre = np.where(XX < hx, 0.12, 0.0)
    depth = (GULLY_DEPTH - HEADCUT_DROP) + HEADCUT_DROP * step
    depth = np.where(XX >= hx, depth, pre * GULLY_DEPTH)

    z = z - shape * depth
    truth_rows.append(
        {
            "gully_id": gi,
            "axis_y": gy,
            "headcut_x": hx,
            "headcut_x_world": X0 + hx,
            "headcut_y_world": Y0 + gy,
            "headcut_drop_m": HEADCUT_DROP,
            "ramp_m": HEADCUT_RAMP,
            "gully_depth_m": GULLY_DEPTH,
            "gully_width_m": 2 * GULLY_HALF_WIDTH,
        }
    )

# --- trunk channel along the east edge ---------------------------------------
trunk_x = W * CELL - 120.0
dt = np.abs(XX - trunk_x)
inside_t = dt <= TRUNK_HALF
z = z - np.where(inside_t, TRUNK_DEPTH * (1 - (dt / TRUNK_HALF) ** 2), 0.0)

rng = np.random.default_rng(5)
z = z + rng.normal(0, NOISE, z.shape)
z = z[::-1, :].astype("float32")

transform = from_origin(X0, Y0 + H * CELL, CELL, CELL)
dem_path = OUT / "gully_dem.tif"
with rasterio.open(
    dem_path, "w", driver="GTiff", height=H, width=W, count=1, dtype="float32",
    crs=f"EPSG:{EPSG}", transform=transform, nodata=-9999.0, compress="deflate",
) as dst:
    dst.write(z, 1)

with (OUT / "gully_truth.csv").open("w", newline="") as fh:
    w_ = csv.DictWriter(fh, fieldnames=list(truth_rows[0].keys()))
    w_.writeheader()
    w_.writerows(truth_rows)

print(f"DEM: {dem_path}  ({W}x{H} @ {CELL} m = {W*CELL/1000:.1f} x {H*CELL/1000:.1f} km)")
print(f"     {W*H:,} cells | elevation {z.min():.1f} .. {z.max():.1f}")
print()
print("=== TRUTH ===")
print(f"  hillslope gradient   : {HILL_SLOPE}")
print(f"  gullies              : {len(truth_rows)}  "
      f"({2*GULLY_HALF_WIDTH:.0f} m wide, {GULLY_DEPTH:.1f} m deep, "
      f"{GULLY_SPACING:.0f} m apart)")
print(f"  headcut drop         : {HEADCUT_DROP} m over {HEADCUT_RAMP} m ramp "
      f"(gradient {HEADCUT_DROP/HEADCUT_RAMP:.2f})")
print("  headcut x positions  : " + ", ".join(f"{r['headcut_x']:.0f}" for r in truth_rows))
print(f"  total gully length   : ~{sum((W*CELL - r['headcut_x']) for r in truth_rows)/1000:.2f} km")
print(f"  truth table          : {OUT/'gully_truth.csv'}")
