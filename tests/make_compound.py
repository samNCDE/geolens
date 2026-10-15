"""Synthetic COMPOUND channel: flat bed → steep banks → flat floodplain → valley walls.

This is the geometry channel/floodplain delineation must resolve, with every
breakpoint known exactly so detected values can be scored against truth.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
import geopandas as gpd
from shapely.geometry import LineString

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "compound"
OUT.mkdir(parents=True, exist_ok=True)

CELL = 0.5
W, H = 1200, 800          # 600 m x 400 m at 0.5 m cells
X0, Y0 = 500_000.0, 3_900_000.0
EPSG = 32612

# --- TRUTH -------------------------------------------------------------------
CHANNEL_HALF = 6.0        # flat bed out to 6 m either side  -> bankfull W = 12 m
BANK_HEIGHT = 1.8         # bank rises 1.8 m
BANK_SLOPE = 0.90         # steep bank face (90%)
FLOODPLAIN_HALF = 55.0    # floodplain out to 55 m either side
FLOODPLAIN_SLOPE = 0.005  # nearly flat
VALLEY_SLOPE = 0.35       # confining valley walls (35%)
VALLEY_FALL = 0.004       # downstream gradient
BASE = 1500.0
MEANDER_AMP = 30.0
MEANDER_WAVELEN = 300.0
NOISE = 0.02

BANK_TOP = CHANNEL_HALF + BANK_HEIGHT / BANK_SLOPE   # 6 + 2.0 = 8.0 m


def cross(d: np.ndarray) -> np.ndarray:
    """Elevation above channel invert as a function of |offset|."""
    z = np.zeros_like(d)
    # bed: flat
    # bank face
    m = (d > CHANNEL_HALF) & (d <= BANK_TOP)
    z = np.where(m, (d - CHANNEL_HALF) * BANK_SLOPE, z)
    # floodplain
    m = (d > BANK_TOP) & (d <= FLOODPLAIN_HALF)
    z = np.where(m, BANK_HEIGHT + (d - BANK_TOP) * FLOODPLAIN_SLOPE, z)
    # valley wall
    m = d > FLOODPLAIN_HALF
    fp_top = BANK_HEIGHT + (FLOODPLAIN_HALF - BANK_TOP) * FLOODPLAIN_SLOPE
    z = np.where(m, fp_top + (d - FLOODPLAIN_HALF) * VALLEY_SLOPE, z)
    return z


xs = np.arange(W) * CELL
ys = np.arange(H) * CELL
cy = (H * CELL) / 2.0 + MEANDER_AMP * np.sin(2 * np.pi * xs / MEANDER_WAVELEN)

XX, YY = np.meshgrid(xs, ys)
CY = np.broadcast_to(cy, (H, W))
dist = np.abs(YY - CY)

z = BASE - VALLEY_FALL * XX + cross(dist)
rng = np.random.default_rng(7)
z += rng.normal(0, NOISE, z.shape)
z = z[::-1, :].astype("float32")

transform = from_origin(X0, Y0 + H * CELL, CELL, CELL)
dem_path = OUT / "compound_dem.tif"
with rasterio.open(
    dem_path, "w", driver="GTiff", height=H, width=W, count=1, dtype="float32",
    crs=f"EPSG:{EPSG}", transform=transform, nodata=-9999.0, compress="deflate",
) as dst:
    dst.write(z, 1)

line_xs = np.arange(0, W * CELL, 5.0)
line_cy = (H * CELL) / 2.0 + MEANDER_AMP * np.sin(2 * np.pi * line_xs / MEANDER_WAVELEN)
line = LineString([(X0 + float(a), Y0 + float(b)) for a, b in zip(line_xs, line_cy)])
gpd.GeoDataFrame({"name": ["Compound Creek"]}, geometry=[line],
                 crs=f"EPSG:{EPSG}").to_file(OUT / "centerline.shp")

print(f"DEM: {dem_path}  ({W}x{H} @ {CELL} m)")
print(f"Centerline length: {line.length:.1f} m")
print()
print("=== TRUTH ===")
print(f"  bankfull half-width  : {CHANNEL_HALF} m   (bankfull WIDTH = {2*CHANNEL_HALF} m)")
print(f"  bank top at offset   : ±{BANK_TOP} m")
print(f"  bank height          : {BANK_HEIGHT} m")
print(f"  bank face slope      : {BANK_SLOPE}")
print(f"  floodplain edge at   : ±{FLOODPLAIN_HALF} m  (total valley W = {2*FLOODPLAIN_HALF} m)")
print(f"  floodplain slope     : {FLOODPLAIN_SLOPE}")
print(f"  valley wall slope    : {VALLEY_SLOPE}")
print(f"  ENTRENCHMENT RATIO   : {2*FLOODPLAIN_HALF/(2*CHANNEL_HALF):.2f}")
print(f"  W/D (mean depth={BANK_HEIGHT}) : {2*CHANNEL_HALF/BANK_HEIGHT:.2f}")
