"""Synthetic mountain front with ALLUVIAL FANS of known geometry.

  * a steep mountain range on the west, drained by N canyons
  * a range-front escarpment at FRONT_X
  * a basin floor on the east, gently sloping
  * at each canyon mouth a CONE: apex AT the mouth, at the mouth's own
    elevation, spreading east at a known radius and radial slope

Truth goes to fan_truth.csv.

THE GEOMETRY HAS TO BE RIGHT, AND IT WAS WRONG THREE TIMES
----------------------------------------------------------
Each time the fixture was wrong, the detector looked broken and the fixture
looked fine. Worth reading before changing anything here.

1. **Apex at basin elevation.** The cone dropped below the basin floor
   immediately and no fan was deposited at all. The detector correctly found
   nothing and was blamed for it.

2. **No drainage in the range.** The canyons were carved but the range surface
   sheeted east past them, so each canyon collected only its own 80 m width --
   about 0.2 km2 at the mouth, below any sensible stream threshold. The whole
   range held NINE stream cells. There was no confined reach above any apex, so
   an apex detector driven by confinement had nothing to find. Fixed with
   RANGE_CROSS_SLOPE, which tilts the range surface toward the nearest canyon.

3. **The fan dammed its own canyon.** The apex was placed at
   ``basin_elevation + radius x slope``, which put it 87 m ABOVE the canyon
   floor feeding it -- a wall across the mouth. Flow could not cross the apex,
   priority-flood filling then flooded the canyon, and routing escaped around
   the fan's lateral margin. Every apex detector landed on that margin channel,
   500-800 m off in y, and the error read like a tuning problem. It was a dam.

   The fix is the constraint below: **a fan apex sits at the elevation of the
   canyon mouth that feeds it**, and the basin it buries is lower still. That is
   what makes a fan a fan -- sediment leaves confinement and spreads. If the
   apex is not graded to its canyon, nothing downstream of it means anything.

The elevation budget, which is what keeps that true
---------------------------------------------------
    range ridge at the front          RIDGE_Z
    canyon floor at the mouth         RIDGE_Z - CANYON_DEPTH   == the apex
    fan toe, FAN_RADIUS east          apex - FAN_RADIUS * FAN_SLOPE
    basin surface under the toe       equal to the toe, so the fan feathers out

The basin is placed relative to the mouth rather than independently, so the cone
can only ever be a positive deposit that starts level with its own canyon.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "fans"
OUT.mkdir(parents=True, exist_ok=True)

CELL = 10.0
W, H = 700, 700                   # 7 x 7 km
X0, Y0 = 200_000.0, 3_700_000.0
EPSG = 32612

FRONT_X = 2500.0                  # mountain front at x = 2.5 km
RIDGE_Z = 990.0                   # range surface at the front, between canyons
MOUNTAIN_SLOPE = 0.30             # steep range west of the front
BASIN_SLOPE = 0.004               # basin floor falls gently east
CANYON_HALF = 40.0
CANYON_DEPTH = 60.0                # depth below the range surface AT THE MOUTH
# The canyon's LONG PROFILE, which is not the hillslope gradient. Using
# MOUNTAIN_SLOPE for both makes the canyon floor fall at 30%, and a 30% channel
# entering a 5.5% fan is a transition so violent that any detector finds it --
# the fixture would flatter the method rather than test it. Real steep mountain
# canyons run about 5-12%. The canyon consequently deepens upstream, which is
# what canyons incising into a rising range actually do.
CANYON_SLOPE = 0.10
# The range surface must DRAIN INTO the canyons rather than sheet east past
# them; see failure 2 above.
RANGE_CROSS_SLOPE = 0.12

N_FANS = 3
FAN_SPACING = 1800.0
FAN_RADIUS = 700.0                # each fan reaches 700 m onto the basin
FAN_SLOPE = 0.055                 # 5.5% radial slope — classic fan gradient
NOISE = 0.15

# --- the elevation budget ----------------------------------------------------
APEX_Z = RIDGE_Z - CANYON_DEPTH                 # canyon floor at the mouth
TOE_Z = APEX_Z - FAN_RADIUS * FAN_SLOPE         # fan surface at its toe
BASIN_Z_AT_TOE_X = TOE_Z                        # so the cone feathers out there

xs = np.arange(W) * CELL
ys = np.arange(H) * CELL
XX, YY = np.meshgrid(xs, ys)

# --- base surface ------------------------------------------------------------
range_surface = RIDGE_Z + (FRONT_X - XX) * MOUNTAIN_SLOPE
basin_surface = BASIN_Z_AT_TOE_X - BASIN_SLOPE * (XX - FRONT_X - FAN_RADIUS)
z = np.where(XX < FRONT_X, range_surface, basin_surface)

fan_ys = [(i + 1) * FAN_SPACING for i in range(N_FANS)]
fan_ys = [y for y in fan_ys if y < H * CELL - FAN_RADIUS]

# Tilt the range surface toward the nearest canyon so flow concentrates.
if fan_ys:
    dist_to_canyon = np.min(
        np.abs(YY[..., None] - np.array(fan_ys)[None, None, :]), axis=2
    )
    z = np.where(XX < FRONT_X, z + RANGE_CROSS_SLOPE * dist_to_canyon, z)

truth = []
for fi, fy in enumerate(fan_ys):
    # --- canyon through the range, draining east to the mouth ---------------
    # The floor is cut to APEX_Z at the front and rises upstream with the range,
    # so the mouth sits exactly at the elevation the fan apex will take.
    d_can = np.abs(YY - fy)
    in_can = (d_can <= CANYON_HALF) & (XX <= FRONT_X)
    canyon_floor = APEX_Z + (FRONT_X - XX) * CANYON_SLOPE
    wall = (d_can / CANYON_HALF) ** 2          # parabolic cross-section
    canyon_z = canyon_floor + wall * (range_surface - canyon_floor)
    z = np.where(in_can & (canyon_z < z), canyon_z, z)

    # --- the fan: a half-cone with its apex AT the mouth, at mouth elevation -
    apex_x, apex_y = FRONT_X, fy
    r = np.hypot(XX - apex_x, YY - apex_y)
    on_fan = (r <= FAN_RADIUS) & (XX >= apex_x)      # spreads east only
    fan_surface = APEX_Z - r * FAN_SLOPE
    z = np.where(on_fan & (fan_surface > z), fan_surface, z)

    truth.append({
        "fan_id": fi,
        "apex_x_world": X0 + apex_x,
        "apex_y_world": Y0 + apex_y,
        "apex_elev": APEX_Z,
        "toe_elev": TOE_Z,
        "radius_m": FAN_RADIUS,
        "fan_slope": FAN_SLOPE,
        "area_m2": np.pi * FAN_RADIUS ** 2 / 2.0,     # half cone
        "area_km2": np.pi * FAN_RADIUS ** 2 / 2.0 / 1e6,
    })

rng = np.random.default_rng(3)
z = z + rng.normal(0, NOISE, z.shape)

# --- sanity checks: assert the geometry is what the docstring claims ---------
# Run on every build, because every previous fixture bug would have been caught
# by one line here and instead cost a session each.
mid_row = int(fan_ys[len(fan_ys) // 2] / CELL)
col_in = int((FRONT_X - 60.0) / CELL)      # 60 m inside the canyon
col_out = int((FRONT_X + 60.0) / CELL)     # 60 m onto the fan
step = float(z[mid_row, col_out] - z[mid_row, col_in])
assert step < 5.0, (
    f"FIXTURE BROKEN: {step:+.1f} m step across the canyon mouth. The fan is "
    f"damming its own canyon and flow cannot cross the apex. See failure 3 in "
    f"the module docstring."
)
apex_err = float(z[mid_row, int(FRONT_X / CELL)]) - APEX_Z
assert abs(apex_err) < 3 * NOISE + 2.0, (
    f"FIXTURE BROKEN: apex is {apex_err:+.1f} m off its canyon mouth elevation."
)

z = z[::-1, :].astype("float32")           # row 0 = north, for a north-up raster

transform = from_origin(X0, Y0 + H * CELL, CELL, CELL)
dem_path = OUT / "fan_dem.tif"
with rasterio.open(
    dem_path, "w", driver="GTiff", height=H, width=W, count=1, dtype="float32",
    crs=f"EPSG:{EPSG}", transform=transform, nodata=-9999.0, compress="deflate",
) as dst:
    dst.write(z, 1)

with (OUT / "fan_truth.csv").open("w", newline="") as fh:
    w_ = csv.DictWriter(fh, fieldnames=list(truth[0].keys()))
    w_.writeheader()
    w_.writerows(truth)

print(f"DEM: {dem_path}  ({W}x{H} @ {CELL} m = {W*CELL/1000:.1f} x {H*CELL/1000:.1f} km)")
print(f"     elevation {z.min():.1f} .. {z.max():.1f}")
print()
print("=== TRUTH ===")
print(f"  mountain front at x = {FRONT_X:.0f} m   (range slope {MOUNTAIN_SLOPE})")
print(f"  canyon mouth / apex  {APEX_Z:.1f} m")
print(f"  fan toe              {TOE_Z:.1f} m  ({FAN_RADIUS:.0f} m east)")
print(f"  basin floor slope    {BASIN_SLOPE}")
print(f"  fans                 {len(truth)}")
print(f"  fan radius           {FAN_RADIUS:.0f} m")
print(f"  fan radial slope     {FAN_SLOPE}  (between range {MOUNTAIN_SLOPE} "
      f"and basin {BASIN_SLOPE})")
print(f"  fan area (each)      {truth[0]['area_km2']:.3f} km²")
print(f"  apex y positions     " + ", ".join(f"{t['apex_y_world'] - Y0:.0f}" for t in truth))
print(f"  step across mouth    {step:+.1f} m  (must be near zero)")
print(f"  truth table          {OUT / 'fan_truth.csv'}")
