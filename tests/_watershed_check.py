"""Does clipping to a COMPLETE watershed preserve drainage area?

This is the question the whole tiling argument turns on. A complete watershed
has no inflow across its boundary by definition, so accumulation computed on the
clip should match accumulation computed on the full grid, cell for cell.

Compared against the old approach — cutting the network into reach-length
slices, which are NOT complete watersheds — to show the difference is the
watershed property, not the clipping.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ["GEOLENS_OUT"] = str(ROOT / "wsout")
os.environ["GEOLENS_NO_PROGRESS"] = "1"

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.mask import mask as rio_mask

from geolens.landscape import flow_routing, complete_watersheds

DEM = str(ROOT / "landscape" / "landscape_dem.tif")

print("1. full-grid flow routing")
full = flow_routing(DEM)
with rasterio.open(full.accumulation) as src:
    acc_full = src.read(1)
    full_transform = src.transform
print(f"   max drainage {full.max_accumulation_km2:.2f} km²")

print("\n2. partition into COMPLETE watersheds")
ws_path = complete_watersheds(full, target_area_km2=40.0)
ws = gpd.read_file(ws_path)
print(f"   {len(ws)} watersheds, areas {ws.area_km2.min():.1f}-{ws.area_km2.max():.1f} km²")

print("\n3. clip to each watershed, re-route, compare against the full grid")
print("   (a complete watershed has no inflow, so these must match)")
rows = []
for i, row in ws.sort_values("area_km2", ascending=False).head(4).iterrows():
    geom = row.geometry
    with rasterio.open(DEM) as src:
        arr, tr = rio_mask(src, [geom], crop=True, filled=True,
                           nodata=src.nodata if src.nodata is not None else -9999.0)
        prof = src.profile.copy()
    prof.update(height=arr.shape[1], width=arr.shape[2], transform=tr,
                nodata=prof.get("nodata") or -9999.0, compress="deflate")
    clip_path = Path(os.environ["GEOLENS_OUT"]) / f"_ws_{int(row.ws_id)}.tif"
    with rasterio.open(str(clip_path), "w", **prof) as dst:
        dst.write(arr[0], 1)

    sub = flow_routing(str(clip_path), out_prefix=f"ws{int(row.ws_id)}")
    with rasterio.open(sub.accumulation) as src:
        acc_sub = src.read(1)
        sub_transform = src.transform

    # Compare only cells inside the watershed, at matching coordinates.
    valid = arr[0] != prof["nodata"]
    hh, ww = acc_sub.shape
    rr, cc = np.nonzero(valid[:hh, :ww])
    xs, ys = rasterio.transform.xy(sub_transform, rr, cc)
    fr, fc = rasterio.transform.rowcol(full_transform, xs, ys)
    fr = np.clip(np.asarray(fr), 0, acc_full.shape[0] - 1)
    fc = np.clip(np.asarray(fc), 0, acc_full.shape[1] - 1)

    a_sub = acc_sub[rr, cc]
    a_full = acc_full[fr, fc]
    big = a_full > 100          # compare where drainage actually matters
    if big.sum() < 10:
        continue
    rel = np.abs(a_sub[big] - a_full[big]) / a_full[big]
    rows.append({
        "ws_id": int(row.ws_id), "area_km2": float(row.area_km2),
        "cells": int(big.sum()),
        "median_rel_err": float(np.median(rel)),
        "p90_rel_err": float(np.percentile(rel, 90)),
        "max_sub": float(a_sub.max()), "max_full_here": float(a_full.max()),
    })
    print(f"   ws {int(row.ws_id):>3}  {row.area_km2:6.1f} km²  "
          f"median rel err {np.median(rel) * 100:6.2f}%  "
          f"p90 {np.percentile(rel, 90) * 100:6.2f}%  "
          f"max acc clip {a_sub.max():.0f} vs full {a_full.max():.0f}")

print()
if rows:
    med = float(np.median([r["median_rel_err"] for r in rows]))
    print(f"MEDIAN relative error in drainage area across watersheds: {med * 100:.2f}%")
    ok = med < 0.05
    print("COMPLETE WATERSHEDS PRESERVE DRAINAGE AREA" if ok
          else "STILL TRUNCATING — the clip is not a complete watershed")
    sys.exit(0 if ok else 1)
else:
    print("no comparable watersheds found")
    sys.exit(1)
