"""Build geolens_start.ipynb.

Written as a script rather than hand-edited JSON so the notebook can be
regenerated and executed as a test — a notebook that is never run is a notebook
that silently rots.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "geolens_start.ipynb"

cells: list[dict] = []


def md(text: str) -> None:
    cells.append({"cell_type": "markdown", "metadata": {},
                  "id": f"md{len(cells):02d}",
                  "source": text.strip("\n").splitlines(keepends=True)})


def code(text: str) -> None:
    cells.append({"cell_type": "code", "metadata": {}, "execution_count": None,
                  "id": f"code{len(cells):02d}", "outputs": [],
                  "source": text.strip("\n").splitlines(keepends=True)})


md("""
# geolens — start here

Run the cells top to bottom. Only **one** cell needs editing: the paths in
step 1.

What this notebook does:

| Step | What it answers |
|---|---|
| 0 | Is geolens installed and working? |
| 1 | Where is my data? |
| 2 | **Will this DEM fit in memory?** ← run this before anything expensive |
| 3 | Coarsen the DEM if step 2 says it will not fit |
| 4 | What is actually in my data? |
| 5 | Valley bottoms, network, confinement classes |
| 6 | Calibrate the class breaks to this landscape |
| 7 | **Rosgen valley types — valley bottom to ridgetop** |
| 8 | Look at it |
| 9 | Gullies |
| 9 | Riparian corridor from 1 m NAIP — the arid-country floodplain check |
| 10 | Where did everything go? |

**If you only remember one thing:** run step 2 first. An out-of-memory kill gives
no error message, and step 2 tells you what to do instead.
""")

md("""
## 0. Install check

This runs the same diagnostic as `python install.py --check`. It reports what is
installed, where it came from, and what specifically is wrong if anything is.

**If this cell raises "geolens is not installed in the Python this notebook is
running"**, the package is fine — Jupyter is just using the wrong interpreter.
Kernel -> Change Kernel -> **Python (geolens)** and run it again. That is by far
the most common problem, and it has nothing to do with the install.

If something is genuinely missing, close the notebook and run the installer from
the folder containing `pyproject.toml`:

```
install_windows.bat            # Windows: or just double-click it
bash install.sh                # macOS / Linux
```

Then restart the kernel (Kernel -> Restart) and run this cell again.

See `INSTALL.md` if it does not go smoothly.
""")

code(r"""
import sys, subprocess
from pathlib import Path

# ---------------------------------------------------------------- KERNEL CHECK
# This runs FIRST and stops the notebook if geolens is not importable, because
# the alternative is a bare ModuleNotFoundError three cells later that says
# nothing about the cause. The cause is almost always the same: the installer
# built a separate environment and registered a kernel for it, and Jupyter
# started with a different Python -- usually Anaconda base, which has none of
# this installed. Nothing is broken; the notebook is pointed at the wrong
# interpreter.
try:
    import geolens
    _ok = True
except ImportError:
    _ok = False

if not _ok:
    here = Path.cwd()
    root = next((c for c in [here, *here.parents][:5]
                 if (c / "install.py").exists()), None)

    candidates = []
    if root:
        for rel in ("Scripts/python.exe", "bin/python"):
            v = root / ".venv" / rel
            if v.exists():
                candidates.append(str(v))

    kernels = []
    try:
        from jupyter_client.kernelspec import KernelSpecManager
        kernels = sorted(KernelSpecManager().find_kernel_specs())
    except Exception:
        pass

    bar = "=" * 72
    lines = [
        "",
        bar,
        "geolens is not installed in the Python this notebook is running.",
        bar,
        "",
        "  this kernel : " + sys.executable,
    ]
    if candidates:
        lines.append("  geolens is  : " + candidates[0])
    lines += [
        "",
        'FIX: Kernel -> Change Kernel -> "Python (geolens)", then re-run this cell.',
        "",
    ]
    if kernels:
        lines.append("  kernels available here: " + ", ".join(kernels))
        if not any("geolens" in k for k in kernels):
            exe = candidates[0] if candidates else "<geolens python>"
            lines += [
                "",
                "  No geolens kernel is registered. Register it with:",
                '    "' + exe + '" -m ipykernel install --user --name geolens'
                ' --display-name "Python (geolens)"',
            ]
    lines.append(bar)
    raise RuntimeError("\n".join(lines))

# ---------------------------------------------------------------- VERSION CHECK
# A notebook newer than the installed library fails several cells later with
# an error about the code, not about the install -- on Sam's machine a v23
# notebook passed stream_threshold_km2=None into a v21 library and got a
# TypeError from inside build_terrain. Stop here instead, and say the fix.
NOTEBOOK_FOR = "@@GEOLENS_VERSION@@"
def _ver(s):
    return tuple(int("".join(ch for ch in p if ch.isdigit()) or 0)
                 for p in str(s).split(".")[:3])
if _ver(geolens.__version__) < _ver(NOTEBOOK_FOR):
    raise RuntimeError(
        "\n" + "=" * 72 + "\n"
        "This notebook is for geolens " + NOTEBOOK_FOR + ", but the library\n"
        "this kernel imports is " + geolens.__version__ + ":\n"
        "  " + str(Path(geolens.__file__).parent) + "\n\n"
        "FIX: from the folder you unzipped this notebook from, run\n"
        "    python update.py\n"
        "then Kernel -> Restart and run this cell again.\n" + "=" * 72)
elif _ver(geolens.__version__) > _ver(NOTEBOOK_FOR):
    print("note: the library (" + geolens.__version__ + ") is newer than this "
          "notebook (" + NOTEBOOK_FOR + "); notebooks/geolens_start.ipynb in "
          "the new release has the current cells.")

# Installed and importable. Now the full diagnostic.
here = Path.cwd()
installer = next((c / "install.py" for c in [here, *here.parents][:5]
                  if (c / "install.py").exists()), None)
if installer:
    subprocess.run([sys.executable, str(installer), "--check"])
else:
    print("geolens " + geolens.__version__ + " ready")
    print("python  " + sys.version.split()[0])
    from geolens.landscape import available_backends, available_ram_gb
    print("backends " + str(available_backends()))
    ram = available_ram_gb()
    print(("RAM available: %.0f GB" % ram) if ram else "RAM: unknown")
""")

md("""
## 1. Point at your data — **edit this cell**

`OUT_DIR` must be **outside** your data folders. geolens never writes to source
data and will refuse to run if the output directory sits inside a read root.
""")

code("""
%matplotlib inline
from pathlib import Path
import os

# ---------------------------------------------------------------- EDIT THESE
DEM      = r"C:/work/RiverData/dem.tif"        # your DEM — routing runs on THIS
DEM_FINE = None                                # optional: r"C:/work/.../dem_1m.tif"
OUT_DIR  = r"C:/work/geolens_out"              # where results go (NOT in your data)
CENTERLINE = None                              # optional: r"C:/work/.../centerline.shp"
NHD      = None                                # optional: r"C:/work/.../nhd.shp"
GEOLOGY  = "auto"                              # "auto" downloads USGS surficial
                                               # geology; a .gpkg path also works;
                                               # None to skip (types stay unresolved)
REGIONAL_CURVE = "arid_sw_nm"                  # bankfull vs drainage area for
                                               # stream types: Moody et al. 2003
                                               # "arid_sw_nm" (E. AZ / NM) or
                                               # "arid_sw_az" (central & S. AZ);
                                               # None = generic US curve
# ----------------------------------------------------------------------------

os.environ["GEOLENS_OUT"] = str(OUT_DIR)
Path(OUT_DIR).mkdir(parents=True, exist_ok=True)

# Demo mode: if the DEM path does not exist, build a synthetic landscape so the
# rest of the notebook still runs. Replace DEM above with your own to use it.
if not Path(DEM).exists():
    import subprocess
    here = Path.cwd()
    maker = None
    for cand in [here / "tests" / "make_landscape.py",
                 here.parent / "tests" / "make_landscape.py"]:
        if cand.exists():
            maker = cand
            break
    if maker:
        print(f"'{DEM}' not found — building a synthetic landscape to demo with.")
        # The placeholder OUT_DIR is a Windows path; on another OS it would be
        # created literally as a nested folder. Use a local one for the demo.
        if not Path(OUT_DIR).is_absolute() or ":" in str(OUT_DIR)[1:2]:
            OUT_DIR = str(Path.cwd() / "geolens_out")
            os.environ["GEOLENS_OUT"] = OUT_DIR
            Path(OUT_DIR).mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, str(maker)], check=True, capture_output=True)
        DEM = str(maker.parent / "landscape" / "landscape_dem.tif")
        NHD = str(maker.parent / "landscape" / "nhd_flowline.shp")
    else:
        raise FileNotFoundError(
            f"{DEM} does not exist, and no synthetic fixture was found. "
            "Edit DEM above to point at a real file."
        )

print("DEM:", DEM)
print("OUT:", OUT_DIR)
""")

md("""
## 2. Will this fit in memory?

**Run this before anything expensive.** It reads only the DEM's header, so it is
instant even on a 100 GB file.

If the verdict is `WILL NOT FIT`, the advice names the cell size that *would*
fit. Take it — see step 3 for why coarsening beats tiling.
""")

code("""
from geolens import api
from geolens.landscape import plan_run

plan = plan_run(DEM)
print(plan)
print()
print("per-stage GB:", plan.per_stage_gb)
if plan.suggested:
    print("suggested   :", plan.suggested)
""")

md("""
## 3. Coarsen if needed

**Coarsening is almost always better than tiling.** Memory scales with the
square of resolution, so 1 m → 10 m is 100× less. And unlike tiling it keeps the
drainage network intact.

Why tiling is worse: flow accumulation at a cell depends on everything upstream.
Cut the DEM anywhere — including on a watershed divide — and a mainstem entering
the tile arrives with its contributing area reset to zero. Streams that depended
on that area fall below the threshold and vanish. Measured on a test landscape,
tiling into 12 subwatersheds recovered **6.4 km of a 187 km network**.

Tile only when you genuinely need the fine resolution and cannot coarsen
(`api.landscape_tiled`), and then treat each tile as a standalone analysis
rather than merging them.

A landscape-scale generalization over millions of acres does not need metre
cells. 10–30 m is the right working resolution.
""")

code("""
# Runs only if step 2 said it will not fit.
if plan.fits is False and plan.suggested.get("resample_to_cell_m"):
    target = plan.suggested["resample_to_cell_m"]
    print(f"Coarsening to {target:g} m ...")
    rs = api.resample_dem(DEM, target)
    DEM = rs["path"]
    print(f"  {rs['cells']:,} cells now ({rs['reduction_factor']}x fewer)")
    print(plan_run(DEM))
else:
    print("No resampling needed.")
""")

md("""
## 4. What is in the data?

Look before analysing. **Always read the `warnings`** — a geographic CRS
(degrees) makes every distance, area and slope meaningless, and this will say so
rather than quietly returning bad numbers.
""")

code("""
import json

info = api.describe_raster(DEM)
print(f"CRS      EPSG:{info['crs'].get('epsg')}  ({info['crs'].get('name')})")
_mpu = info["crs"].get("metres_per_unit") or 1.0
_unit = info["crs"].get("linear_units") or "m"
print(f"grid     {info['grid']['width']:,} x {info['grid']['height']:,} "
      f"@ {info['grid']['cell_size_x']:g} {_unit}"
      + ("" if _mpu == 1.0 else f" = {info['grid']['cell_size_x'] * _mpu:.3g} m"))
st = info["bands"][0].get("stats") or {}
print(f"elev     {st.get('min', float('nan')):.1f} .. {st.get('max', float('nan')):.1f}")
for w in info["warnings"]:
    print("WARNING:", w)
""")

md("""
## 5. Landscape pass

Conditions the DEM → routes flow → computes HAND → extracts the valley bottom →
segments the network → classifies confinement.

`stream_threshold_km2` is the single most important knob: it is the drainage
area at which a cell becomes a stream, and it sets how much of the landscape
ends up as valley bottom. 1 km² suits 30 m data in most terrain. Lower it in
arid or low-relief country; raise it in steep wet country.
""")

code("""
res = api.landscape_pass(
    DEM,
    nhd=NHD,                      # optional; joins NHD identity to segments
    dem_fine=DEM_FINE,            # channel measured here; routing stays on DEM
    geology=GEOLOGY,              # "auto" downloads it; needed for a definite type
    regional_curve=REGIONAL_CURVE,  # the channel stream types measure against
    stream_threshold_km2=None,    # fitted to the DEM; set a number to override
    segment_length_m=500,
)

c = res["classified"]
print(f"segments   {c['segment_count']:,}")
print(f"network    {res['network']['total_length_km']:,.1f} km")
print(f"valley     {res['valley']['valley_area_km2']:,.1f} km² "
      f"({res['valley']['valley_fraction']:.1%} of the landscape)")
print(f"took       {res['seconds']:.1f} s")
print()
print("classes with DEFAULT breaks:", c["summary"]["confinement_counts"])
for w in res["warnings"]:
    print("WARNING:", w)
""")

md("""
## 6. Calibrate the breaks — **do not skip this**

The default confinement breaks (2 and 5 channel widths) come from the
literature and **rarely transfer between landscapes**. Reporting classes off
uncalibrated defaults is the main way to be wrong with this tool.

Read the percentiles of your own `confinement_ratio` and set breaks that match
how *your* terrain actually divides.
""")

code("""
# The breaks are two thresholds on confinement_ratio, which step 5 already
# computed and stored. Changing them does NOT re-run anything: compare several
# candidates, then apply one. Both read a table; neither touches the DEM.
sweep = api.confinement_break_sweep(c["segments"])
print(f"{sweep['n_segments']:,} segments, {sweep['total_length_km']:.1f} km\\n")
print("confinement_ratio percentiles in YOUR landscape:")
for k, v in sweep["confinement_ratio_percentiles"].items():
    print(f"   p{k:<3} {v:10.2f}")

print("\\nhow the landscape divides under each candidate:")
for row in sweep["candidates"]:
    frac = row["fraction_km"]
    print(f"  confined<={row['confined_max']:7.2f} partly<={row['partly_confined_max']:7.2f}  "
          + "  ".join(f"{k} {v:.0%}" for k, v in sorted(frac.items())))
""")

md("""
### Apply the breaks you chose

Seconds, not hours, because it relabels a column rather than recomputing the
DEM. Re-run this cell as often as you like — that is what calibration is
supposed to be.
""")

code("""
pct = sweep["confinement_ratio_percentiles"]
confined_max        = pct[25]        # a reasonable starting point
partly_confined_max = pct[50]

rc = api.reclassify_confinement(c["segments"], confined_max, partly_confined_max)
print(f"confined <= {confined_max:.2f}, partly <= {partly_confined_max:.2f}")
print("classes:", rc["summary"]["confinement_counts"])
print("km by class:", {k: round(v, 1)
                       for k, v in rc["summary"]["confinement_length_km"].items()})

# Everything downstream should use the reclassified segments.
SEGMENTS = rc["segments"]
print("\\nuse this for valley types and valley form:\\n  ", SEGMENTS)
""")

md("""
## 7. Rosgen valley types — the valley as an area

**Open `valley_extent.gpkg` and style it by `valley_type`.** That is the
valley: every cell that drains to the reach, so its boundary is the drainage
divide — floor to ridgetop — and the valleys tile with no overlap, because
every cell flows to exactly one reach. The segments layer is the same
classification attached to the stream line down the middle, which is useful
for joins and is not a picture of a valley.

**This is the valley, not the channel and not the valley floor.** Sections are
cut from the valley axis out to the drainage divide on each side, and the
classification is made on what those sections measure: how steep the side
slopes are, whether the cross-section is a V or a U, how much relief there is
from floor to divide, and how broad the floor is relative to that relief.

Measured against a fixture with known ridges: valley top width within 0.7% of
truth, shape exponent within 0.03, both divides found on 100% of sections.

`max_search_m` has to be wide enough to REACH the divide. It is the single
parameter that decides whether this works: too small and every section returns
a lower bound instead of a measurement, and the run looks fine while measuring
nothing. Check `n_measured` against `n_sections` below.

Supply `geology` if you have it. Geometry alone cannot separate an alluvial
valley (VIII) from outwash (IX) from a lacustrine basin (X) — they are the same
shape. Without it those come back unresolved with the candidates listed, which
is the honest answer rather than a guess.
""")

code("""
# The landscape pass in step 5 ALREADY did this -- valley form and Rosgen
# valley types are part of it now, not a separate opt-in step. They were
# opt-in, nothing opted in, and three rounds of work went into something no
# run ever produced.
vform  = res.get("valley_form")
vtypes = res.get("valley_types")

if vform is None:
    print("The landscape pass was run with valley_types=False.")
else:
    got = vform["n_measured"]; tot = vform["n_sections"]
    print(f"{got} of {tot} sections reached BOTH divides "
          f"({100 * got / max(tot, 1):.0f}%)")
    if got < 0.5 * tot:
        print("!! Most sections never reached a divide, so what follows is a")
        print("   LOWER BOUND, not a measurement. Re-run step 5 with a larger")
        print("   valley_max_search_m, or clip to terrain whose divides are")
        print("   inside the DEM.")
    print(f"median Vf {vform['median_vf']:.2f}, side slope "
          f"{vform['median_side_slope']:.3f}, shape n {vform['median_shape_n']:.2f}")
    print()
    geo = res.get("geology") or {}
    print(f"geology:      {geo.get('status', 'unknown')}"
          + (f"  ->  {geo['path']}" if geo.get("path") else ""))
    print(f"              used by valley types: {vtypes.get('geology_used')}")
    fans = res.get("fans")
    if fans:
        # Type III (alluvial fans) is detected, not inferred. Only cone-shaped
        # regions count; every candidate, kept or not, is in fan_candidates.gpkg
        # with the reason -- open it to judge the ones that were turned down.
        print(f"alluvial fans: {fans['fan_count']} ({fans['total_area_km2']:.2f} km2); "
              f"{fans.get('rejected_count', 0)} candidate(s) failed the cone test")
        print(f"               {fans['fans']}")
        print(f"               {fans.get('candidates')}")
    print("valley types:", vtypes["type_counts"])
    print("unresolved:  ", vtypes["unresolved"])
    print("confidence:  ", vtypes["confidence_counts"])
    print()
    print("segments (line):", vtypes["segments"])
    print("VALLEY EXTENT (polygons):", res.get("valley_extent"))
    print()
    print("Style valley_extent.gpkg by `valley_type` -- that is the valley as")
    print("an area, ridgetop to ridgetop. The segments layer is the same")
    print("classification attached to the stream line down the middle.")
    for w in vform["warnings"] + vtypes["warnings"]:
        print("WARNING:", w)
""")

code("""
# What each segment got, and why.
import geopandas as gpd

# `view` is imported properly in step 8; this step runs first, so it is
# imported here too rather than depending on cell order.
from geolens import view as _view

if res.get("valley_extent"):
    _view.show_vector(res["valley_extent"], column="valley_type",
                      title="Rosgen valley types - valley extent, ridge to ridge")

if vtypes:
    vt = gpd.read_file(vtypes["segments"])
    cols = [c for c in ("segment_id", "valley_type", "valley_type_desc",
                        "valley_type_confidence", "valley_type_why")
            if c in vt.columns]
    display(vt[cols].head(12))
""")

code("""
# HAVE GEOLOGY? Re-run just the classification with it -- no re-routing, no
# re-measuring. Rosgen II/V/VI, VIII/IX and X/XI differ by depositional
# history, not by shape, so geology is what separates them. Without it those
# come back unresolved with their candidates listed, which is the honest
# answer rather than a guess.
GEOLOGY = None            # e.g. r"C:\\data\\geology.shp"

if GEOLOGY and vform:
    better = api.rosgen_valley_types(
        res["classified"]["segments"],
        valley_form=vform["segment_table"],
        geology=GEOLOGY,
    )
    print("with geology:", better["type_counts"])
    print("unresolved:  ", better["unresolved"],
          f"(was {vtypes['unresolved']})")
    print("layer:", better["segments"])
else:
    print("No geology supplied -- set GEOLOGY above to sharpen VIII/IX/X.")
""")

md("""
## 7b. Rosgen **stream** types — A through G

Rosgen is two classifications. Step 7 is the valley; this is the channel, and
it is keyed on four different things: entrenchment ratio, width/depth ratio,
sinuosity and slope.

**Bankfull width and depth need a DEM that resolves the channel.** At 10 m a
4 m channel is under half a cell and the measurement is the cell size, not the
stream — so set `DEM_FINE` in step 1 to your native lidar and routing still
runs on the coarse `DEM`. Nothing is resampled: a cross-section is a line of
point samples, so the fine grid only has to exist where the measuring happens.

**Every reach gets a letter; `stream_basis` says what it rests on.**
`measured` — banks found on the DEM (medium or high confidence). `regional` —
the channel is too small for this grid to show its banks, so entrenchment is
read from the flood-prone width against the regional channel and width/depth is
unknown (low). `slope_sinuosity` — neither. `stream_candidates` lists the types
close behind the letter.
""")

code("""
st = res.get("stream_types")
if st is None:
    print("The landscape pass was run with stream_types=False.")
else:
    print(f"channel measured on {st['cell_size_m']:.2f} m cells; "
          f"{st['n_measured']} of {st['n_segments']} reaches gave a bankfull "
          f"channel")
    if not st["resolvable"]:
        print("!! Most channels are narrower than three cells here. They are")
        print("   typed on the regional basis (low confidence). DEM_FINE in")
        print("   step 1 pointing at native lidar measures more of them.")
    print()
    print("typed on:    ", st.get("basis_counts"))
    print("stream types:", st["type_counts"])
    print("confidence:  ", st["confidence_counts"])
    print("unresolved:  ", st["unresolved"])
    print("layer:", st["segments"])
    for w in st["warnings"]:
        print("WARNING:", w)
""")

code("""
# Where the four discriminators landed, and what each letter rests on.
# Width/depth is NaN on the regional basis by design -- it needs banks.
import pandas as pd

st = res.get("stream_types")
if st:
    import geopandas as gpd
    sg = gpd.read_file(st["segments"], layer="stream_types")
    cols = ["bankfull_width_m", "width_depth_ratio", "entrenchment_ratio",
            "sinuosity", "slope"]
    print(sg[cols].describe().round(3).to_string())
    print()
    print(pd.crosstab(sg["stream_type"].fillna("-"), sg["stream_basis"]))
""")

md("""
### Every section, linked to the map

Each section line in `stream_type_sections.gpkg` has a picture:
`stream_types_dir/section_plots/xs_<xs_id>.png` — the whole section on the
left, the channel close up on the right, **looking downstream**, compass
directions at both ends. Bank tops, the bankfull and flood-prone stages, the
regional channel width, the thalweg, and the other stages the bank detector
considered. Every reach has a sheet of all its sections under the type they
produced (`reach_plots/`), and `stream_type_plots.html` puts every sheet on one
filterable page.

**In QGIS** — add `stream_type_sections.gpkg` (its style comes with it):

* **hover:** turn on *View ▸ Show Map Tips*, make the layer active, rest the
  pointer on a section — its plot pops up;
* **click:** choose the *Run Feature Action* tool (or right-click ▸ Actions)
  and click a section — the plot opens full size;
* `stream_types.gpkg` does the same with the reach sheets.

**In ArcGIS Pro** — *Configure Pop-ups ▸ add Image*, source `{plot_path}`.

Section lines are coloured by what happened: blue = bankfull used, orange /
amber / purple = a bank pick that was rejected (and why), grey = no bank the
DEM can show. Earlier output folders: `api.plot_stream_sections(folder)`.
""")

code("""
from IPython.display import Image, display

st = res.get("stream_types")
p = (st or {}).get("plots") or {}
if not p:
    print("No section plots in this run (section_plots=False?). To render "
          "them: api.plot_stream_sections(<stream_types_dir>)")
else:
    print(f"{p['section_plots']} section plots, {p['reach_sheets']} reach "
          f"sheets in {p['seconds']:.0f} s")
    print("browse:", p["index"])
    # One reach sheet inline: the best-measured reach.
    import geopandas as gpd
    sg = gpd.read_file(st["segments"], layer="stream_types")
    best = sg.sort_values("n_sections_measured", ascending=False).iloc[0]
    print("reach", best["seg_id"] if "seg_id" in sg else best.iloc[0], "->",
          best["plot_path"])
    display(Image(filename=best["plot_path"], width=900))
""")

md("""
### Stable or unstable form — the point of both classifications

Each reach's stream type is judged against its valley type using Rosgen's
valley type / stream type associations (NRCS NEH 654 TS3E, Table TS3E-3, after
Rosgen 1996):

* **stable**: a stream type Rosgen associates with that valley type (a B in a
  colluvial valley II, a C or E in a wide alluvial valley VIII);
* **unstable**: a type he lists for that valley only when it is out of
  balance (D, F or G in VIII, an incised or braided channel; G or F in X; G in
  VI);
* **check**: not a stream type of that valley at all. Either the channel has
  departed from its stable form, or one of the two classifications is wrong;
  the section sheets and the valley layer tell you which;
* **unknown**: a type is missing.

`form_confidence` is the lower of the two classifications' confidence, so a
`stable` resting on two low-confidence calls is a hypothesis to check in the
field, not a finding. Open `stability_dir/stream_stability.gpkg` in QGIS: it
is coloured by status, and hovering over a reach shows its section sheet.
""")

code("""
import geopandas as gpd, pandas as pd

stab = res.get("stability")
if not stab:
    print("No stability layer: it needs both valley types and stream types.")
else:
    print("reaches by form:", stab["counts"])
    print("confidence:     ", stab["by_confidence"])
    g = gpd.read_file(stab["stability"])
    print()
    print(pd.crosstab(g["valley_type"].fillna("?"), g["stream_letter"].fillna("?")))
    print()
    print(g.loc[g["form_status"] != "stable",
                ["seg_id", "valley_type", "stream_type", "form_status",
                 "form_confidence", "form_note"]].head(15).to_string())

# For an earlier run's folders:
#   api.stream_stability(r"...\\valley_types_dir\\valley_types.gpkg",
#                        r"...\\stream_types_dir\\stream_types.gpkg")
""")

md("""
## 8. Look at it — inline

Numbers hide problems a picture shows immediately: a centerline that is not
following the channel, sections clipping the DEM edge, a valley bottom leaking
onto uplands.

These draw in the notebook. **Rasters are read decimated**, so a multi-gigabyte
DEM costs no more to display than a small one — the full array is never loaded.
""")

code(r"""
from geolens import view

# Everything at once: relief, HAND, valley bottom, confinement classes.
view.show_landscape(res, dem=DEM)
""")

md("""
Each layer on its own, when you want a closer look. `view.show` also works out
what it has been handed — a raster path, a vector path, or a whole result dict.
""")

code(r"""
view.show_raster(res["terrain"]["hand"], title="HAND", cmap="Blues", dem=DEM)
""")

code(r"""
view.show_vector(res["classified"]["segments"], column="confinement", dem=DEM,
                 title="Confinement class")
""")

md("""
### Cross-sections

Small multiples, not a stack. Overlaying fifty sections makes a solid band that
answers nothing; the useful question is whether an individual section looks like
a channel. Bank tops are the orange dashed lines, the bankfull surface the blue
dotted one, and the shading is the bankfull area.

Each panel is titled with its `xs_id`, the same number as in
`centerline_sections_dir/centerline_sections.gpkg` (and the `.shp` beside it):
find it in the attribute table to see where it is, or hover over any line in
QGIS with Map Tips on to see its plot.
""")

code(r"""
if CENTERLINE:
    # Written as a GIS layer (xs_id on every line) with a plot per section,
    # so a panel below can be found on the map and any section on the map
    # can be seen: hover in QGIS with Map Tips on. Spacing and width are in
    # the DEM's units (feet on a foot DEM).
    xr = api.export_cross_sections(DEM, CENTERLINE, spacing=25, width=200,
                                   plot=True, max_plots=200,
                                   name="centerline_sections")
    print(xr["section_count"], "sections ->", xr["lines"])
    print(xr["plotted"], "plots in", xr["plot_dir"])
    for w in xr["warnings"]:
        print("note:", w)
    # Panel titles are the xs_id in the layer's attribute table.
    view.show_sections(xr["_sections"], delineation=xr["_delineation"], n=6)
else:
    print("No CENTERLINE set in step 1 - skipping cross-sections.")
""")

code("""
import pandas as pd

seg_csv = c.get("segments_csv")
if seg_csv:
    seg = pd.read_csv(seg_csv)
    print(seg.groupby("confinement")["length_m"].agg(["count", "sum"]))
""")

md("""
### Also in QGIS or ArcGIS Pro

Everything above is already on disk. Open the output folder and add
`segments_classified.gpkg`, `valley_bottom.gpkg` and `hand.tif`, then style the
segments categorized on `confinement`.
""")

md("""
## 9. Gullies

**The one thing that makes this work: an upper bound on drainage area.**
Slope variability, TPI and slope are every bit as true of a creek or a river as
of a gully — `max_drainage_km2` is the only criterion that means *small*. It
defaults to 0.5 km². With it off, a real 1.94-billion-cell run returned
10,739 features and 3,195 km of "gullies" against a 1,767 km stream network:
the drainage network, mapped correctly, and none of it gullies.

Set `stream_threshold_km2` to the **same** number, so area above the ceiling is
a stream and area below it can be a gully. The two then partition cleanly and
`exclude_streams=True` only trims the banks of mapped channels.

**Do not pick the roughness threshold as a percentile.** p90 admits 10% of
the DEM by construction whatever is in it — that is what produced the run
above. The obvious correction, "use p99 then", is measurably worse: on both
test fixtures p97 and above return **zero** gullies, because the top
percentiles of `slope_std` are steep valley walls and mountain flanks, not
small incised things.

`slope_std` over gullied terrain is bimodal in the log — a smooth-ground mode
and an incised mode with a clear gap between them — and the threshold belongs
in the gap. `suggest_slope_std_min()` finds it with Otsu's method and reports
which percentile it landed on and how well separated the two modes are, so you
can see whether to believe it. It is advisory: `slope_std_min` stays an
ordinary argument you can override.

**Gullies need lidar.** They are typically 2-15 m wide, so on a 30 m DEM they
are smaller than one cell and anything "found" is slope noise. The tool warns
you when the cell size is too coarse — believe it. Point `GULLY_DEM` at a lidar
tile for real work.
""")

code("""
import geolens.api as api

GULLY_DEM = DEM          # point at a finer lidar tile for real gully work

# The size ceiling, and the single most important number in this cell. Above
# it a channel is a stream; below it, it can be a gully. `max_drainage_km2` is
# the ONLY criterion that means small -- slope variability, TPI and slope are
# every bit as true of a river. Raise it for arroyos that collect real area.
GULLY_MAX_KM2 = 0.5

# api.detect_gullies, not geolens.landscape.detect_gullies. Same computation,
# but this one lands in the run log, so "which threshold produced these
# gullies" stays answerable a month from now. It also:
#   * REUSES the routing from step 5 -- pass the result, not the DEM path, and
#     nothing is re-routed. The stream threshold never affected routing anyway.
#   * scales the roughness window, section width and minimum length to the cell
#     size, which used to be done by hand in this cell.
#   * picks slope_std_min from the shape of the distribution when you leave it
#     None. Do NOT substitute a percentile: p90 admits 10% of the DEM by
#     construction, and p99 finds nothing at all.
source = res["terrain"] if str(GULLY_DEM) == str(DEM) else str(GULLY_DEM)

g = api.detect_gullies(
    source,
    slope_std_min=None,              # None -> chosen from the distribution
    tpi_max=-0.3,                    # must be negative (incised)
    max_drainage_km2=GULLY_MAX_KM2,
    stream_threshold_km2=GULLY_MAX_KM2,   # same number -> the sets are disjoint
    exclude_streams=True,            # safe, because the two match
)

c = g["threshold_choice"]
if c:
    print("threshold chosen from the data:", c["note"])
    if not c["bimodal"]:
        print("!! roughness is not clearly bimodal here -- check this hard.")
print()

# For comparison, so the choice is never a black box. Look for a big jump
# between two neighbouring percentiles: that gap is smooth ground on one side
# and incised ground on the other, and the suggestion sits in it.
from geolens.raster import percentiles
pcts = percentiles(g["slope_std_raster"], [50, 75, 90, 95, 99])
print("slope_std percentiles:",
      {k: round(v, 4) for k, v in pcts.items() if k.startswith("p")})
print()

print(f"{g['gully_count']} gullies, {g['total_length_km']:.2f} km, "
      f"density {g['gully_density_km_per_km2']:.2f} km/km2")
print(f"mean width {g['mean_width_m']:.1f} m, mean depth {g['mean_depth_m']:.2f} m")
print(f"gullied area {g['gullied_area_km2']:.4f} km2 (from measured bank tops)")
print()
print("lines:    ", g["gully_lines"])
print("polygons: ", g["gully_polygons"])
print("run log:  ", g.get("_run_record"))
for w in g["warnings"]:
    print("WARNING:", w)
""")

code("""
# The 2D footprint, built from the measured bank tops rather than by buffering
# the line. Same gully_id, so it joins back to the lines and the sections.
#
# Note `mask_area_m2` on each gully is NOT the footprint -- it is the cells the
# detector's thresholds admitted, which is a ribbon near the centreline. On a
# fixture of known 14 m gullies it implies 2.45 m. Use `measured_area_m2`.
#
# Polygons are OFF by default since v0.27 (they climb the walls of steep
# hollows). Add polygons=True to api.detect_gullies above to get them.
if g["gully_polygons"]:
    view.show_vector(g["gully_polygons"], column="mean_depth_m",
                     title="Gully footprints, coloured by depth")
""")

md("""
## 10. Riparian corridor — the arid-country floodplain check

In ephemeral and intermittent country you cannot validate a floodplain against a
flood. A monsoon event lasts hours against a satellite revisit of 6–12 days, and
a dry sandy bed is dark at C-band radar and reads exactly like open water, so a
SAR flood map either drops the wash or calls it flooded while it is bone dry.

**Phreatophytes are the stable signal instead.** Cottonwood, willow, mesquite
and tamarisk are rooted in the alluvial aquifer, so they mark the floodplain
whether or not water is running today. This step measures NDVI *in excess of the
local upland background* — a relative measure, so it survives a drought year or
a change of sensor — and cuts it to low HAND, because a green north-facing slope
is not floodplain.

**Two honest limits.** Nothing in NDVI separates alfalfa from cottonwood: a
field detached from the corridor is caught by connectivity and lands in
`suspect_patches`, but a field *touching* the corridor merges into it and
inflates the area. And this has never been checked against a field-mapped
riparian boundary — it is verified on synthetic ground only.

The NAIP cells below stream 1 m imagery by HTTP range request, so only the
windows overlapping your DEM are transferred, and the tiles are cached under
`<output>/naip/` with their flight year in the filename so you can go back to
the same imagery when you truth the result.
""")

code("""
# Needs internet. If you already have an NDVI raster, set NDVI = r"path\\to\\ndvi.tif"
# and skip the fetch.
NDVI = None

if NDVI is None:
    try:
        # NDVI is built at 1 m -- NAIP's resolution, not the DEM's. A 20 m
        # riparian corridor is 2 cells on a 10 m DEM and 20 cells at 1 m.
        # riparian_band resamples HAND and streams onto it, so this is fine.
        n = api.naip_imagery(DEM)            # add start="2021-01-01" to pin a vintage
        NDVI = n["ndvi"]
        print(f"NAIP years {n['years']}, {n['n_used']} of {n['n_items']} scenes "
              f"at {n['cell_size_m']:g} m, {n['coverage']:.0%} covered")
        print("imagery (R,G,B,NIR):", n.get("rgbn"))   # kept for later reference
        print("NDVI:               ", n["ndvi"])
        print("provenance:         ", n.get("sources"))
        print("scene cache:        ", n["cache_dir"])
        if n.get("tiles"):
            print(f"downloaded {len(n['tiles'])} whole tile(s) — streaming was "
                  f"blocked, see naip_sources.json")
        for w in n.get("warnings", []):
            print("WARNING:", w)
    except Exception as e:
        # Print the whole error. A truncated one cannot tell a certificate
        # problem from an outage from a request that was never made.
        print("NAIP fetch failed:", type(e).__name__)
        print(e)
        print()
        print("naip_sources.json in the naip_dir output folder records which")
        print("stage failed. `geolens naip --selftest` checks the service on")
        print("its own. Or point NDVI at your own raster and re-run.")
""")

code("""
if NDVI:
    rip = api.riparian_band(
        NDVI,
        hand=res["terrain"]["hand"],
        streams=res["terrain"]["streams"],
        # Must be several times your valley width. Too small and the corridor
        # raises its own background, stops looking anomalous, and you get an
        # empty result -- the warnings say so when that happens.
        background_window_m=2000.0,
        min_excess=0.12,        # NDVI above local background to count as riparian
        max_hand_m=15.0,        # above this it is hillslope, however green
    )
    print(f"{rip['n_patches']} patches, {rip['riparian_km2']:.3f} km2")
    print(f"  connected to the network: {rip['connected_km2']:.3f} km2")
    print(f"  hydrologically separate:  {rip['unconnected_km2']:.3f} km2")
    print(f"  local background NDVI:    {rip['background_ndvi']:.3f}")
    for sp in rip["suspect_patches"]:
        print("  SUSPECT:", sp)
    for w in rip["warnings"]:
        print("WARNING:", w)
""")

code("""
if NDVI:
    view.show_raster(NDVI, title="NAIP NDVI (1 m)", cmap="RdYlGn")
    view.show_mask(rip["band_raster"], dem=DEM, title="Riparian band over relief")
""")

md("""
## 10. Where everything went
""")

code("""
print("output directory:", api.workspace()["output_directory"])
print()
for label, path in [
    ("HAND raster",       res["terrain"]["hand"]),
    ("valley polygons",   res["valley"]["valley_polygons"]),
    ("segments (GPKG)",   res["classified"]["segments"]),
    ("segments (CSV)",    res["classified"]["table"]),
]:
    print(f"  {label:20s} {path}")
""")

md("""
## What to do next

1. **Write down the parameters that worked** — `res["classified"]["parameters"]`
   has the full set. A confinement class without its breaks is not reproducible.
2. **Check the map against a watershed you know.** If the classes disagree with
   your own reading of that ground, the breaks are wrong, not the ground.
3. **Record corrections** where the tool is wrong, and fit the breaks to them:

```python
from geolens.learn import dataset_key
api.record_correction(
    dataset=dataset_key(DEM), feature_id="123", field="confinement",
    human_value="partly confined", model_value="unconfined",
    author="your name", params=res["classified"]["parameters"],
)
api.calibrate_confinement(res["classified"]["table"])   # fits breaks to them
api.suggest_labels(res["classified"]["table"], n=20)    # what to look at next
```

The fit reports a **separability** verdict. If it says `not separable`, no
threshold on that feature can reproduce your labels — the feature is wrong for
the question, and tuning breaks will not fix it.
""")

# Stamp the version this notebook was built with into the version check in
# step 0. Read from the package, so the two cannot drift.
import re as _re

_init = (Path(__file__).resolve().parent.parent / "src" / "geolens"
         / "__init__.py").read_text(encoding="utf-8")
_ver = _re.search(r'^__version__ = "([^"]+)"', _init, _re.M).group(1)
for _c in cells:
    _c["source"] = [s.replace("@@GEOLENS_VERSION@@", _ver)
                    for s in _c["source"]]

nb = {
    "cells": cells,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python",
                       "name": "python3"},
        "language_info": {"name": "python", "version": "3.10"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

OUT.write_text(json.dumps(nb, indent=1))
print(f"wrote {OUT}  ({len(cells)} cells)")
