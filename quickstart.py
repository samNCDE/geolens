"""Generate synthetic test data and run the full pipeline. Verifies the install.

    python quickstart.py
"""
import os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "quickstart_out"
os.environ.setdefault("GEOLENS_OUT", str(OUT))

print("1/4  generating synthetic compound channel ...")
subprocess.run([sys.executable, str(ROOT / "tests" / "make_compound.py")], check=True)

from geolens import api  # noqa: E402

DEM = str(ROOT / "tests" / "compound" / "compound_dem.tif")
CL = str(ROOT / "tests" / "compound" / "centerline.shp")

print("\n2/4  describing the DEM ...")
r = api.describe_raster(DEM)
print(f"     {r['grid']['width']}x{r['grid']['height']} @ {r['grid']['cell_size_x']} m"
      f" | EPSG:{r['crs']['epsg']}")

print("\n3/4  delineating channel and floodplain ...")
d = api.delineate_channel(DEM, CL, spacing=25, width=160)
s = d["summary"]
print(f"     resolved {d['resolved']}/{d['section_count']} sections")
for k in ("bankfull_width", "width_depth_ratio", "entrenchment_ratio"):
    v = s[k]
    if v:
        print(f"     {k:20s} median {v['median']:8.2f}  (n={v['n']})")

print("\n4/4  rendering and exporting ...")
for p in (
    api.plot_delineation(d)["path"],
    api.plot_section_stack(d["_sections"])["path"],
    api.plot_map(dem=DEM, centerline=CL)["path"],
    api.export_csv(d, "delineation")["path"],
):
    print(f"     {p}")

print(f"\nDone. Outputs in {OUT}")
