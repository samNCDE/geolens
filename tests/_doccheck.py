"""Execute the exact snippets RUNNING.md tells the user to run."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ["GEOLENS_OUT"] = str(ROOT / "docout")
os.environ["GEOLENS_NO_PROGRESS"] = "1"

ok, fail = [], []


def check(label, fn):
    try:
        fn()
        ok.append(label)
        print(f"  PASS  {label}")
    except Exception as exc:
        fail.append((label, f"{type(exc).__name__}: {exc}"))
        print(f"  FAIL  {label}\n        {type(exc).__name__}: {exc}")


print("building fixtures")
for s in ("make_compound.py", "make_landscape.py", "make_gullies.py",
          "make_valleys.py"):
    subprocess.run([sys.executable, str(ROOT / s)], check=True,
                   capture_output=True)

CMP = ROOT / "compound"
LND = ROOT / "landscape"
GUL = ROOT / "gullies"

print("\n-- section 2: inspection --")
from geolens import api

check("api.list_datasets", lambda: api.list_datasets(str(CMP)))
check("api.describe_raster", lambda: api.describe_raster(str(CMP / "compound_dem.tif")))
check("api.describe_vector", lambda: api.describe_vector(str(CMP / "centerline.shp")))
check("api.attribute_summary", lambda: api.attribute_summary(str(CMP / "centerline.shp")))
check("api.sample_features", lambda: api.sample_features(str(CMP / "centerline.shp"), n=2))

print("\n-- section 2: reach scale --")
d = {}


def _reach():
    d["r"] = api.delineate_channel(
        str(CMP / "compound_dem.tif"), str(CMP / "centerline.shp"),
        spacing=25, width=160, bank_slope=0.15, valley_slope=0.20,
    )
    assert d["r"]["summary"]


check("api.delineate_channel", _reach)
check("api.plot_delineation", lambda: api.plot_delineation(d["r"], station=340))
check("api.plot_section_stack", lambda: api.plot_section_stack(d["r"]["_sections"]))
check("api.export_csv", lambda: api.export_csv(d["r"], "delineation"))

print("\n-- section 2: landscape scale --")
L = {}


def _land():
    L["r"] = api.landscape_pass(
        str(LND / "landscape_dem.tif"), nhd=str(LND / "nhd_flowline.shp"),
        stream_threshold_km2=1.0, segment_length_m=500,
    )
    s = L["r"]["classified"]["summary"]
    assert s["confinement_ratio_percentiles"]
    for k in ("segments",):
        assert Path(L["r"]["classified"][k]).exists()
    assert Path(L["r"]["valley"]["valley_polygons"]).exists()
    assert Path(L["r"]["terrain"]["hand"]).exists()


check("api.landscape_pass", _land)
check("landscape_pass with calibrated breaks",
      lambda: api.landscape_pass(str(LND / "landscape_dem.tif"),
                                 confined_max=14, partly_confined_max=47))

print("\n-- section 2: gullies --")


def _gully():
    """Mirrors the RUNNING.md block exactly. Keep the two in step."""
    from geolens.landscape import (build_terrain, terrain_roughness,
                                   suggest_slope_std_min, detect_gullies)

    MAX_KM2 = 0.5

    t = build_terrain(str(GUL / "gully_dem.tif"), stream_threshold_km2=0.2)
    rg = terrain_roughness(t, window_m=40)

    sug = suggest_slope_std_min(rg.slope_std)
    print(f"        {sug['note']}")
    assert sug["bimodal"], "the gully fixture should read as bimodal"

    g = detect_gullies(t, roughness=rg, slope_std_min=sug["slope_std_min"],
                       tpi_max=-0.3, max_drainage_km2=MAX_KM2,
                       min_length_m=100)
    print(f"        {g.gully_count} gullies, {g.total_length_km:.2f} km, "
          f"density {g.gully_density_km_per_km2:.2f} km/km2")
    assert g.gully_count > 0


check("terrain_roughness + detect_gullies", _gully)

print("\n-- section 6: CLI --")


def cli(args):
    r = subprocess.run([sys.executable, "-m", "geolens.cli"] + args,
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])


check("geolens backends", lambda: cli(["backends"]))
check("geolens workspace", lambda: cli(["workspace"]))
check("geolens list", lambda: cli(["list", str(CMP)]))
check("geolens delineate", lambda: cli([
    "delineate", str(CMP / "compound_dem.tif"), str(CMP / "centerline.shp"),
    "--width", "160", "--summary"]))
check("geolens landscape", lambda: cli([
    "landscape", str(LND / "landscape_dem.tif"), "--segment-length", "500"]))

print("\n-- section 5: MCP server --")


def _mcp():
    import asyncio
    from geolens.mcp_server import mcp
    tools = asyncio.run(mcp.list_tools())
    assert len(tools) >= 13, f"only {len(tools)} tools"


# The MCP server is an optional extra (pip install geolens[mcp]). A clean
# install without it is a correct install, so this has to read as SKIP rather
# than FAIL -- otherwise every fresh environment looks broken, and a check that
# cries wolf on a good install is worse than no check.
try:
    import mcp as _mcp_mod          # noqa: F401
except ImportError:
    print("  SKIP  MCP tools register  (optional extra not installed: "
          "pip install geolens[mcp])")
else:
    check("MCP tools register", _mcp)

print(f"\n{'=' * 58}")
print(f"RUNNING.md check: {len(ok)} passed, {len(fail)} failed")
for label, err in fail:
    print(f"  FAILED: {label}\n          {err}")
sys.exit(1 if fail else 0)
