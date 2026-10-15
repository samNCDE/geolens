"""Generate a synthetic valley DEM + meandering centerline for testing.

Creates a V-shaped valley falling at a known slope with a sinuous channel
incised along it, so extracted geometry can be checked against known truth.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
import geopandas as gpd
from shapely.geometry import LineString

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "fixture"
OUT.mkdir(parents=True, exist_ok=True)

# Grid: 1 m cells, 600 x 400 m, EPSG:32612 (UTM 12N — Arizona).
CELL = 1.0
W, H = 600, 400
X0, Y0 = 500_000.0, 3_900_000.0
EPSG = 32612

VALLEY_SLOPE = 0.004      # 0.4% fall downstream (+x)
BASE_ELEV = 1500.0
CHANNEL_DEPTH = 2.5
CHANNEL_HALF_WIDTH = 8.0
VALLEY_SIDE_SLOPE = 0.06
MEANDER_AMP = 45.0
MEANDER_WAVELEN = 250.0

xs = np.arange(W) * CELL
ys = np.arange(H) * CELL

# Channel centerline: y as a function of x (sinuous).
cy = H / 2.0 + MEANDER_AMP * np.sin(2 * np.pi * xs / MEANDER_WAVELEN)

XX, YY = np.meshgrid(xs, ys)
CY = np.broadcast_to(cy, (H, W))

dist = np.abs(YY - CY)

# Valley: rises away from the channel, falls downstream.
z = BASE_ELEV - VALLEY_SLOPE * XX + VALLEY_SIDE_SLOPE * dist

# Incise a trapezoidal channel.
inner = dist <= CHANNEL_HALF_WIDTH
z = np.where(inner, z - CHANNEL_DEPTH * (1 - (dist / CHANNEL_HALF_WIDTH) ** 2), z)

# Light roughness so stats aren't degenerate.
rng = np.random.default_rng(42)
z += rng.normal(0, 0.03, z.shape)

# Row 0 is the TOP (max y) in a north-up raster.
z = z[::-1, :].astype("float32")

transform = from_origin(X0, Y0 + H * CELL, CELL, CELL)
dem_path = OUT / "valley_dem.tif"
with rasterio.open(
    dem_path, "w", driver="GTiff", height=H, width=W, count=1,
    dtype="float32", crs=f"EPSG:{EPSG}", transform=transform,
    nodata=-9999.0, compress="deflate",
) as dst:
    dst.write(z, 1)

# Centerline as real-world coordinates.
line_xs = np.arange(0, W, 5.0)
line_cy = H / 2.0 + MEANDER_AMP * np.sin(2 * np.pi * line_xs / MEANDER_WAVELEN)
coords = [(X0 + float(x), Y0 + float(y)) for x, y in zip(line_xs, line_cy)]
line = LineString(coords)

gpd.GeoDataFrame(
    {"name": ["Test Creek"], "reach": ["R1"]},
    geometry=[line],
    crs=f"EPSG:{EPSG}",
).to_file(OUT / "centerline.shp")

# A second vector layer to exercise inspection: sample cross-section markers.
pts = [line.interpolate(d) for d in np.arange(0, line.length, 50.0)]
gpd.GeoDataFrame(
    {
        "site_id": [f"XS-{i:03d}" for i in range(len(pts))],
        "surveyed": [bool(i % 3) for i in range(len(pts))],
        "bankfull_w": rng.uniform(9.0, 18.0, len(pts)).round(2),
        "notes": ["riffle" if i % 2 else "pool" for i in range(len(pts))],
    },
    geometry=pts,
    crs=f"EPSG:{EPSG}",
).to_file(OUT / "survey_sites.gpkg", driver="GPKG", layer="sites")

print(f"DEM:        {dem_path}")
print(f"  elevation {z.min():.2f} .. {z.max():.2f}")
print(f"Centerline: {OUT / 'centerline.shp'}  length={line.length:.1f} m")
print(f"Sites:      {OUT / 'survey_sites.gpkg'}  n={len(pts)}")
print(f"TRUTH: valley slope={VALLEY_SLOPE}, channel depth={CHANNEL_DEPTH} m, "
      f"half-width={CHANNEL_HALF_WIDTH} m")
