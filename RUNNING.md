# Running geolens

> **Easiest path: open `notebooks/geolens_start.ipynb`.** It walks the whole
> workflow with one cell to edit, checks your install, and tells you whether
> your DEM will fit in memory before it tries. Every cell is executed as a test,
> so it does not rot.

## Out of memory? Read this first

Run this before anything expensive — it reads only the DEM header, so it is
instant on a 100 GB file:

```python
from geolens.landscape import plan_run
print(plan_run("dem.tif"))
```

It reports cells, peak RAM and a verdict, and when it will not fit it names the
cell size that would. Since v0.25 the estimate is **measured**, not counted
from the code — the old one was 2.6-3x low and called a 1.8-billion-cell grid
"TIGHT — should fit" on a 117 GB machine, which then paged for hours.
Measured peaks, bytes per DEM cell: routing ~81, gullies ~48, valley bottom
~48, classify ~31, network ~16, valley extent ~12, stream types ~0.

**Route coarse, measure fine.** Routing, HAND, valley bottoms and valley form
do not need lidar cells — 2-3 m is plenty — and routing is the peak. Stream
types only need the fine grid along the channel cross-sections, which
`dem_fine=` reads without loading it:

```python
routing = api.resample_dem("dem_1m.tif", 2.0)["path"]      # 4x fewer cells
api.landscape_pass(routing, dem_fine="dem_1m.tif", regional_curve="arid_sw_nm")
```

Check the cell size in the plan against what you think the DEM is: a file
named `..._2m.tif` on a 2.5 ft grid is a 0.76 m grid, and 7x the cells.

**Coarsen, do not tile.** Memory scales with the square of resolution — 1 m to
10 m is 100x less:

```python
from geolens import api
small = api.resample_dem("dem.tif", 10.0)["path"]
```

Tiling looks like the obvious fix and is usually the wrong one. Flow
accumulation at a cell depends on everything upstream, so cutting the DEM
anywhere — **including on a watershed divide** — resets the contributing area of
any mainstem entering the tile. Streams that depended on that area drop below
the threshold and vanish. Measured: 12 subwatershed tiles recovered **6.4 km of
a 187 km network**. `api.landscape_tiled` exists for when you truly cannot
coarsen, reports the loss, and should be treated as per-tile standalone analyses
rather than one merged network.

A landscape generalization over millions of acres does not need metre cells.
10-30 m is the working resolution.

Three ways in: **plain Python**, **a notebook**, or **the QGIS Python Console**.
Pick one, get `quickstart.py` to pass, then use the recipes.

There is no QGIS plugin yet — the Python Console recipe below is how you run this
inside QGIS today, and it loads results straight onto the map canvas.

---

## 1. Install

**Windows** — double-click `install_windows.bat`
**macOS / Linux** — `bash install.sh`

It builds a separate venv, installs everything into it with pip wheels,
registers it as a Jupyter kernel called **Python (geolens)**, and then checks
itself. Nothing is installed into your Anaconda base or into ArcGIS Pro's
Python. `--conda` builds a conda-forge environment instead (one transaction,
fresh env — it will not install on top of an existing one).

Full detail, and every failure mode worth knowing about, is in **`INSTALL.md`**.

### When something is wrong

```bash
python install.py --check    # what is installed, where it came from, what is broken
python install.py --smoke    # cut cross-sections on a synthetic valley and check
                             # the answer against a known 16 m channel
```

`--check` looks at the things that actually break rather than just whether
imports succeed: whether your GDAL stack came from one place or two, whether
`proj.db` exists, whether a stale `PROJ_LIB` from an ArcGIS install is shadowing
pyproj, whether numpy and numba still agree, and whether the Jupyter kernel you
are running is the environment geolens went into.

### Manual install

Still supported if you prefer it:

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[all]"
python quickstart.py               # synthetic compound channel, full pipeline
python -m pytest tests/ -q
```

`[all]` pulls the MCP server, `pysheds` (flow routing), `numba`, `scikit-image`,
`scipy` and `psutil`. Core deps (geopandas, rasterio, shapely, pyproj, fiona,
numpy, pandas, matplotlib, tqdm) come regardless.

Python 3.9+, 3.11 tested.

### Into QGIS's Python

QGIS ships its own interpreter. Install into *that* one, not your system Python.

**Windows** — open **OSGeo4W Shell** (Start menu, inside the QGIS folder):

```bat
python -m pip install --user -e C:\path\to\geolens[all]
```

**macOS**:

```bash
/Applications/QGIS.app/Contents/MacOS/bin/python3 -m pip install --user -e /path/to/geolens[all]
```

**Linux** — QGIS usually uses system Python, so ordinary `pip install --user`
works.

QGIS already ships GDAL, shapely, numpy and matplotlib. `geopandas`, `rasterio`,
`pysheds` and `scikit-image` may not be there and will be installed by the
command above. Restart QGIS afterwards.

Check it took — in **Plugins → Python Console**:

```python
import geolens, sys
print(geolens.__version__, sys.executable)
```

---

## 2. Run it in Python

Every function lives on one flat surface, `geolens.api`.

```python
import os
os.environ["GEOLENS_OUT"] = r"C:/work/geolens_out"   # where outputs go

from geolens import api
```

**Source data is never written to.** Outputs go only to `GEOLENS_OUT`, and the
tool refuses to run if that folder sits inside a read-only source root.

### Look at data first

```python
api.list_datasets(r"C:/work/RiverData")              # what's in the folder
api.describe_raster(r"C:/work/RiverData/dem.tif")    # CRS, cell size, stats
api.describe_vector(r"C:/work/RiverData/reach.gdb", layer="streams")
api.attribute_summary(r"C:/work/RiverData/sites.gpkg", layer="sites")
api.sample_features(r"C:/work/RiverData/sites.gpkg", layer="sites",
                    n=5, where="bankfull_w > 12")
```

`.gdb` works with **no ArcGIS installed** — GDAL's open-source `OpenFileGDB`
driver reads it.

**Always read the `warnings` key.** A geographic CRS (degrees) makes every
distance meaningless, and the tools say so rather than quietly returning numbers.

### Reach scale — cross-sections and bank/floodplain

```python
d = api.delineate_channel(
    "dem.tif", "centerline.shp",
    spacing=25,        # distance between sections, CRS units
    width=160,         # total section width — must reach the valley walls
    bank_slope=0.15,   # slope that marks a bank face (rise/run)
    valley_slope=0.20, # slope that closes the floodplain at a valley wall
)

print(d["summary"])                      # bankfull width, W/D, entrenchment
api.plot_delineation(d, station=340)     # PNG of one section
api.plot_section_stack(d["_sections"])   # all sections, colored by station
api.export_csv(d, "delineation")

# As a GIS layer: section lines with xs_id, a plot per section (up to
# max_plots), and a QGIS style -- hover a line with Map Tips on to see it.
x = api.export_cross_sections("dem.tif", "centerline.shp", spacing=25,
                              width=160, plot=True, max_plots=200)
```

Tuning, one change at a time:

| Symptom | Change |
|---|---|
| Banks not found on subtle channels | lower `bank_slope` (0.15 → 0.08) |
| Bed roughness read as banks | raise `min_bank_height` |
| Noisy lidar, junk breaks | raise `run_length`, then `smooth_window` |
| Floodplain never closes | widen `width`, or set `max_floodplain_offset` |
| Floodplain closes immediately | raise `valley_slope` above the floodplain's own slope |

### Watershed scale — pick the unit before you analyse it

**Do this before the landscape pass, not after.** A rectangular clip cuts
across drainage divides, so flow paths are truncated at every edge and
accumulation, HAND, confinement and valley bottoms are all wrong there.
Measured on the test fixture: a rectangle over the middle third is missing
**3.65× its own area** in upstream contribution.

A complete watershed has no inflow across its boundary by definition, so
accumulation inside the clip is exact — and it is usually the unit the question
is about anyway, valley bottom to the top of the watershed.

```python
# You already have a boundary — a WBD HUC12, a permit area, last year's shapefile
w = api.watershed("dem.tif", boundary="huc12.shp")

# Or delineate one from a point: a gage, a confluence, a culvert, a project site
w = api.watershed("dem.tif", pour_point="429985,4510035")
w = api.watershed("dem.tif", pour_point="-105.67,33.33", pour_crs="EPSG:4326")

print(w["area_km2"], w["dem"])          # the clipped DEM to analyse
print(w["inflow"])                      # is this really a watershed?
```

A supplied boundary is **checked, not trusted**. `inflow` reports whether
anything drains into the polygon across its edge; if it does, the polygon is a
study area rather than a watershed, and geolens says so rather than letting you
find out from a map that looks slightly wrong. A pour point is complete by
construction, and the point is snapped to the largest drainage within
`snap_radius_m` — one cell off the channel gives a watershed of a few hundred
square metres, which reads as a broken delineation rather than a misplaced
point.

Clip and analyse in one call:

```python
r = api.watershed_pass("dem.tif", pour_point="429985,4510035",
                       stream_threshold_km2=1.0, segment_length_m=500)
print(r["watershed"]["area_km2"], r["classified"]["segments"])
```

From the shell: `geolens watershed dem.tif --pour-point 429985,4510035 --run`.

`derive_basins` / `split_by_basins` cut on approximate divides from a coarsened
DEM. They are better than a grid and still not exact — use them only when the
whole extent genuinely has to be covered.

### Conditioning is cached — the second run is the fast one

Filling depressions, resolving flats and routing D8 is the expensive part, and
it is deterministic: the same DEM through the same backend gives the same
filled surface every time. geolens caches the conditioned DEM, flow direction
and accumulation on disk and reuses them for any later run on that DEM —
including after you close the notebook, which reusing a result in memory does
not survive.

On the test fixture: 2.01 s cold, 0.09 s warm, bit-for-bit identical. On a
billion-cell DEM the saving is hours per session, and changing
`stream_threshold_km2` costs a stream re-extraction rather than a re-route.

**The cache lives beside the DEM**, in a `.geolens_cache` folder next to the
data it belongs to — not in your output folder. That matters: it used to be
under the output directory, so it was thrown away by the most ordinary things
anyone does, a new results folder or a new session with a different
`OUT_DIR`. The point is to pay for conditioning once per DEM, and keying it to
the output folder meant paying once per run *configuration* instead.

If the DEM sits on a read-only or network mount, set `GEOLENS_CACHE` to
somewhere fast and local; failing that it falls back to a per-user cache
folder. Delete the `.geolens_cache` folder whenever you want the space back.

**A cache hit or miss is reported in the result**, so you never have to guess
whether it worked:

```python
t = api.build_terrain("dem.tif")
print(t.routing_cache)
# {'enabled': True, 'hit': True, 'directory': '.../.geolens_cache/pysheds_a1b2...',
#  'reused': 'conditioning, routing, streams and HAND'}
```

A miss says *why* — `nothing cached for this DEM yet`, `the DEM's contents have
changed since it was cached`, `the cache is incomplete — one of its rasters is
gone`. Pass `reuse_routing=False` to force a recompute.

**The modification time is not part of the key.** It was, and that was the
difference between a cache that works and one that looks like it was never
written. A cloud sync client touches files; so does copying the DEM to another
folder, re-downloading it, or restoring it from a backup. Every one of those
changed the key and silently threw away hours of conditioning — and if your
DEM lives under `Documents` on a OneDrive-synced machine, that is most of the
time.

What is checked instead is the DEM's *contents*. Size, then the timestamp; a
timestamp that has moved is not a miss, it is a prompt to look at the bytes —
8 MB sampled first, which can only rule a change in, then every byte if that
is inconclusive. A 7.7 GB DEM hashes in about 8 seconds against the hours of
conditioning it is protecting, and the verified timestamp is written back, so
a file the sync client keeps touching pays it once rather than on every run.

That last part matters more than it sounds: 8 MB sampled out of 7.7 GB is
0.1% of it, and burning a channel into one corner changes bytes the sample
never reads. The sampled hash is only ever allowed to say *no*.

### Landscape scale — valley bottoms and confinement

```python
r = api.landscape_pass(
    "dem_30m.tif",
    nhd="nhd_flowlines.shp",       # optional, joins NHD identity
    stream_threshold_km2=1.0,      # drainage area that makes a stream
    segment_length_m=500,
)

print(r["classified"]["summary"]["confinement_ratio_percentiles"])
print(r["classified"]["segments"])      # GeoPackage of classified segments
print(r["valley"]["valley_polygons"])   # valley bottom polygons
print(r["terrain"]["hand"])             # HAND raster
```

**Then calibrate and re-run.** The default confinement breaks (2 / 5 channel
widths) follow convention and rarely transfer between landscapes. Read the
percentiles, pick breaks that match how *your* terrain actually divides:

```python
r = api.landscape_pass("dem_30m.tif", confined_max=14, partly_confined_max=47)
```

Sizing: 3 M acres at 30 m is 13.5 M cells, roughly 2 minutes in one pass. At 10 m
that is 121 M cells — tile by HUC8.

### If your CRS is in feet, read this

Every parameter is in **metres** and is converted for you. That was true in
principle and repeatedly false in practice: nine separate places took a metre
parameter and used it as a CRS unit, or wrote a CRS unit into a column named
for metres. On EPSG:6529 (US survey feet) that meant

* segments asked for at 500 m were cut at 500 ft = **152 m**, and everything
  downstream inherits the segment length;
* `length_m` on the network held feet, so `total_length_km` was kilofeet;
* the HAND thresholds that bound the valley bottom were applied as feet, so
  the valley came out a third of the size asked for;
* gully sections were cut 3.28× too narrow, the minimum bank height was 3.28×
  too permissive, and width, depth, volume and footprint area were feet and
  square feet in columns named for metres and square metres.

None of it was visible to the test suite, because every fixture was metric and
a metres-per-unit of 1.0 makes a missing conversion a no-op.

`tests/make_terrain.py` now grows one landscape-evolution surface — stream
power incision plus hillslope diffusion, so it has real dendritic drainage,
confluences and wandering divides — and writes it **twice**: once in EPSG:6529
and once in UTM metres, the same ground in two unit systems.
`tests/test_units_parity.py` runs the whole pass on both and requires the
physical answers to agree. A missing conversion is wrong by 3.28 or 10.76,
which that cannot miss.

If you have run an earlier version on a foot grid, the numbers in those
outputs are wrong by those factors. Re-run.

### Rosgen valley types — the valley as an area, floor to ridgetop

Rosgen is **two** classifications and geolens produces both:

| Output | What it is | Geometry |
|---|---|---|
| `r["valley_extent"]` | **the valley — every cell that drains to the reach** | polygon |
| `r["valley_types"]["segments"]` | the valley type, per reach | line |
| `r["stream_types"]["segments"]` | the **stream** type A–G, per reach | line |
| `r["valley"]["valley_polygons"]` | the valley **bottom** — the flat that floods | polygon |

**Open `valley_extent` and style it by `valley_type`.** That is the layer the
valley types are about. A reach's valley is the ground that drains to it, so
its boundary is the drainage divide — which is what floor-to-ridgetop means —
and it comes off the flow-direction raster conditioning already paid for. The
valleys tile with no overlap and no gaps, because every cell flows to exactly
one reach.

Two earlier constructions are worth knowing about because their output is
still in circulation. Up to v18 the valley type was written **only** on the
stream centreline: a classification of a valley drawn as a line. A later
attempt walked each cross-section out to its measured divide and stitched the
divide points into a ribbon; on a fixture of straight parallel troughs that
looked right, and on real terrain it was 2,186 triangular shards covering 55%
of the DEM. Every summary statistic said "tiles perfectly, 1.00× overlap",
which is also true of shards. It was thrown away.

#### What is measured, and where it lives

`valley_form` cuts a section every 250 m along each reach (half the segment
length), perpendicular to the stream line at that point, out to the search
distance on both sides (a third of the DEM's shorter side, 500 m to 6 km —
6 km on Ruidoso). Each is sampled every metre (or every cell). The **floor**
is the ground within 2 m of the lowest point near the stream line (the
channel itself stepped over); walking outward on a lightly smoothed profile,
the **divide** on each side is the first crest at least 5 m above the floor
that is not exceeded over the next tenth of the search (600 m on Ruidoso).

**The lines on the map run divide to divide** (since v0.28.1), the part that
was measured, and `valley_form_floors.gpkg` shows each floor. Before, each
line was the whole cut — 12 km on every Ruidoso section — which is why that
layer looked like a mesh of identical lines crossing neighbouring valleys. A
side whose divide was not found still runs to the end of the search
(`divide_found_left/right` = False). Two known weaknesses: the section is
perpendicular to the stream line over about ±1 m, which on a 0.76 m
stair-stepped network is a multiple of 45° a third of the time (median 10°
off the channel's 50 m direction, 11% more than 30°); and the 600 m
look-ahead can step past a low divide when the next ridge is higher and close.

Two tables. Per **section** (`valley_form_sections.gpkg`):

| Field | Meaning |
|---|---|
| `valley_top_width_m` | divide to divide — **this is valley width** |
| `valley_floor_width_m` | the flat bottom, channel excluded |
| `vf` | Bull & McFadden ratio — low is a V, high is a broad floor |
| `shape_n` | fitted `z ~ \|x\|ⁿ`: n≈1 a V-notch, n≈2 a glacial U |
| `side_slope_left` / `side_slope_right` | mean gradient floor to divide |
| `valley_relief_m` | floor to the lower of the two divides |
| `divide_offset_left_m` / `..._right_m` | how far out each divide was found |
| `divide_found_left` / `..._right` | whether a divide was actually reached |
| `vf_is_lower_bound` | true when it was not — the value can only be larger |

Per **reach** (`valley_form_segments.csv`) the same quantities aggregated,
plus `n_complete`, `n_unbounded`, `asymmetry` and `quality`.

Note `valley_width_m` on the **classified** layer is a different quantity —
that is the VBET valley *bottom* width. Divide-to-divide is
`valley_top_width_m`.

```python
r = api.landscape_pass("dem.tif", geology="auto")
print(r["valley_types"]["type_counts"])   # {'II': 1219, 'I': 613, 'V': 274, ...}
print(r["valley_extent"])                 # the polygons — open THIS one
```

**Geology is not optional for a definite answer.** Types II/V/VI, VIII/IX and
X/XI differ by depositional history, not by shape, so without it those come
back as candidate sets however good the DEM is. `geology="auto"` downloads
USGS surficial geology over the DEM's footprint; a failure warns and the pass
continues. Until v19 nothing called the fetcher at all, so every run was
geology-blind.

**How far out it looks.** `valley_max_search_m` defaults to a third of the
DEM's shorter side, capped at 6 km. Derived too conservatively it silently
turns every measurement into a lower bound — an early default of *one sixth*
reached both divides on 20% of sections where the divides sat 3.4 km out, and
a third reached 100%. A warning fires below 50%, and it means what it says:
unreached sections are **lower bounds**, so the valley can only be wider and
the walls gentler than reported. The usual cause is a clip whose divides are
outside the DEM — which is the argument for running on a whole watershed
(`api.watershed`) rather than a rectangle.

### Rosgen stream types — A through G

The channel classification, on the stream network where it belongs. Four
discriminators:

| | |
|---|---|
| entrenchment ratio | flood-prone width / bankfull width, flood-prone width measured at **twice the maximum bankfull depth** |
| width/depth ratio | bankfull width / mean bankfull depth |
| sinuosity | channel length / straight-line distance |
| slope | channel gradient over the reach |

```python
r = api.landscape_pass("dem_10m.tif", dem_fine="dem_1m.tif", geology="auto")
print(r["stream_types"]["type_counts"])
```

**Nearly every reach gets a letter, and `stream_basis` says what it rests
on.** A reach whose measurements sit exactly between two types keeps both in
`stream_candidates` and no letter.

| `stream_basis` | what the letter rests on | confidence |
|---|---|---|
| `measured` | banks found on the DEM in two or more sections: entrenchment and width/depth from those sections | medium–high |
| `partial` | banks in one section only: its entrenchment joins the regional ones, width/depth is not used | low |
| `regional` | no usable banks — the channel is under three samples wide on this grid, or none was found. Entrenchment is the flood-prone width — several channels wide, so it *is* visible — read at twice the regional depth and divided by the regional width. Width/depth unknown | low |
| `slope_sinuosity` | no section at all; a letter only where slope and sinuosity separate the types on their own (above 10% only A exists) | low |

**Use a local regional curve.** `regional_curve="arid_sw_nm"` (Moody et al.
2003, Eastern Arizona / New Mexico) or `"arid_sw_az"` (central & southern
Arizona); the notebook sets `REGIONAL_CURVE = "arid_sw_nm"`. It sizes the bank
search, bounds what is plausible, and is the channel the regional basis
measures against. Without it the width is the classifier's generic 2.7·A^0.35
(Bieger et al. 2015) with Bieger's depth, 0.30·A^0.213. A custom curve is
`{"width": (a, b), "depth": (c, d)}`, A in km², metres out. Maximum depth is
taken as mean depth / 0.58 (Moody et al. 2003, riffles).

**DEM bankfull is the top of bank; Southwest field bankfull usually is not.**
Moody et al. (2003): "bankfull stage is rarely identified with the top of a bank
in this region" — it is the lower depositional break, a point bar or inset
bench. The detector takes the first stage where the water leaves the channel
the DEM can see, which on a 0.6 m grid is usually the higher surface. On Eagle
Creek the main-stem picks run about 2x the NM curve's width. Treat measured
widths as upper bounds, and compare with field bankfull where you have it.

**So pass `dem_fine`.** Routing, HAND and the valley catchments run on the
coarse DEM; only the channel cross-sections read the fine one. A
cross-section is a line of point samples, so the fine grid only has to exist
where the measurement happens — nothing is resampled, and you do not pay 1.94
billion cells to route flow at 0.73 m when the divide is three kilometres
away.

**Entrenchment and width/depth pick the letter. Slope and sinuosity rank, and
slope sets the suffix.** That is how Rosgen's key is built: each letter has a
*typical* slope and sinuosity, and a reach outside them keeps its letter — a B
at 1.5% is a **Bc**, a C at 3% a **Cb**, an A over 10% an **Aa+**. The first
version treated slope as a hard cut, and on Eagle Creek (0.61 m, the first run
that resolved the channel) 6 of 13 reaches came back "no type fits" —
including one at ER 1.51, W/D 29, 1.5% slope, a textbook Bc.

| Field | |
|---|---|
| `stream_type` | letter plus slope class as Rosgen writes it: `B`, `Bc`, `Aa+`, `Cc-` |
| `stream_letter` | the letter alone |
| `slope_class` | the suffix, `a+`, `a`, `b`, `c`, `c-`, when the letter carries one at that slope |
| `stream_candidates` | the letter and every type close behind it — what a field visit would decide between |
| `stream_basis` | `measured`, `partial`, `regional` or `slope_sinuosity` — see above |
| `n_sections_measured`, `n_sections_regional` | sections behind each basis |
| `stream_type_full` | letter, substrate number, slope class — `B1c`, `A2a+` |
| `bankfull_ratio_median` | measured bankfull ÷ the width the drainage area supports |
| `n_sections_implausible` | sections left out because that ratio was over 3 |

**Bankfull is the regional curve's depth; the DEM gives the shape (v0.28
default, `bankfull_method="regional_stage"`).** At each section the water is
raised from the thalweg to the maximum bankfull depth Moody et al. (2003) give
for its drainage area (mean depth / 0.58), and the width, area, mean depth and
flood-prone width (twice that depth) are read off the DEM at that stage. If
the water at that depth has left a small channel and spread over a flat floor
— wider than 3× the regional width — the stage is lowered until the width is
plausible, but not below 0.1 m (`stage_capped` on the section). Nothing
channel-shaped at that scale: `no bank found`, and the reach falls back to the
regional entrenchment.

Why: the v0.24–0.27 bank picker (raise the water until the width jumps) was
checked against Moody's New Mexico field data on Sam's Ruidoso run — 1,875
usable picks, median depth **2.1–2.6×** the field depth in drainages under
20 km², only one in four or five within a factor of 1.5. At 0.5–1 m lidar the
first break it can see is usually the top of a small incised channel, and in
wide sandy washes a low-flow thread. Its too-deep picks also inflated
entrenchment (the flood-prone stage is twice the depth). Replayed on the same
Ruidoso sections:

| | picker (v0.27) | regional stage (v0.28) |
|---|---|---|
| sections with a usable bankfull | 1,875 | 3,336 |
| median width ÷ regional width | 1.71 | 1.33 |
| median width/depth (Moody's NM curve gives ≈ 21–24) | 14.5 | 26.8 |
| median entrenchment | 2.9 | 1.6 |
| reaches C / E (all slope classes) | 336 / 44 | 55 / 0 |
| reaches B / D / F (all slope classes) | 377 / 15 / 0 | 536 / 137 / 27 |

That is a large change and it is not validated against field bankfull on
Sam's ground — it trades an inconsistent pick for a consistent,
curve-dependent one. Wide sandy washes now read as D (braided), small incised
channels as B. **The picker still runs** and its answer is on every section
(`picker_*` columns, purple dashed on the plots, `picker_bank_top` points in
`stream_type_banks.gpkg`), so the two can be compared reach by reach.
`bankfull_method="hybrid"` uses the picker where its depth is within 2× of the
regional one; `"picker"` is the old behaviour. Because the depth comes from
the curve, a regional-stage reach is at most **medium** confidence
(`bankfull_source` says which it rests on), and the curve matters:
set `regional_curve` for your region.

**The letter is the nearest type.** Each type gets a misfit — how far each
measurement falls outside its range, entrenchment and width/depth in Rosgen's
own tolerance (±0.2 and ±2.0), slope and sinuosity at lower weight — and the
least misfit takes the letter when it clearly beats the next. A letter and
slope class that are not in Rosgen's chart are never returned: above 4% only
A and B exist, above 10% only A, and an entrenched low width/depth channel
below 4% is a G, not an "Ab". DA (anastomosed) is multi-thread, which a
single centreline cannot show, so it appears in `stream_candidates` but never
as the letter. `no_type_fits` marks a measured reach outside every box by
more than Rosgen's tolerance; it still gets its nearest letter.

**Roads confuse it.** A road bench beside a small channel reads as a
floodplain — wide flood-prone width, high entrenchment ratio. Check
`stream_type_banks.gpkg` on the hillshade where a steep reach comes back B or C.

**D is never asserted without a measured width/depth.** Braiding is many
threads and the network carries one centreline, so D is only offered when
width/depth was actually measured above 40. `braided_possible` carries it.

**The number is a prior, not a pebble count.** With `geology=`, the substrate
number comes from the surficial map — good enough to separate a bedrock reach
from an alluvial one, not good enough to tell gravel from cobble.
`substrate_basis` says what the number rests on.

#### Seeing a section: plots linked to the map

Every section line in `stream_type_sections.gpkg` has a picture, and the map
knows where it is:

| File | What |
|---|---|
| `section_plots/xs_<xs_id>.png` | the whole section and the channel close up, **looking downstream**, compass directions at both ends; bank tops, bankfull and flood-prone stages, regional channel width, thalweg, where the mapped line crosses, the other stages the detector considered |
| `reach_plots/reach_<seg_id>.png` | every section of a reach, upstream first, under its type, basis, confidence, ER, W/D, alternatives |
| `stream_type_plots.html` | every reach sheet on one page, filterable by id, type, basis, confidence, `no_type_fits` |
| `plot_file`, `plot_path` columns | the PNG, relative to the folder and absolute, on both GeoPackages |
| `stream_type_sections.qml`, `stream_types.qml` | QGIS styles, picked up automatically |

**QGIS:** add `stream_type_sections.gpkg`. Turn on *View ▸ Show Map Tips*, make
the layer active and rest the pointer on a line: its plot pops up. Or take the
*Run Feature Action* tool (or right-click ▸ Actions ▸ Open section plot) and
click a line: the PNG opens full size. `stream_types.gpkg` does the same with
the reach sheets. Lines are coloured by `status` — blue measured and used,
amber/orange/purple a bank pick rejected (one-sided / implausible / too
narrow), grey no bank the DEM can show. The links try `plot_path`, then the
layer's own folder + `plot_file`, so the folder can be moved or copied
whole (QGIS 3.16+; checked in 3.34 by `tests/qgis/check_plot_links.py`).

**ArcGIS Pro:** *Configure Pop-ups ▸ add Image*, source `{plot_path}`. Local
paths show in Pro; they will not survive sharing to ArcGIS Online.

**Earlier runs** — no DEM needed, any folder with `stream_type_profiles.csv`:

```python
api.plot_stream_sections(r"C:\...\outputs\v25\stream_types_dir")
```

Cost: ~70 ms a section and ~0.2 s a reach sheet per process, 16 KB and 40 KB
each. Above 800 sections it renders on up to 8 processes. Cherokee Bill (515
sections) took 61 s in one process and 34 s in two. `section_plots=False`
on `landscape_pass` skips it; `api.plot_stream_sections(folder,
reach_ids=[...])` renders a few reaches, `skip_existing=True` resumes.

### Alluvial fans — Rosgen Type III, detected rather than inferred

The landscape pass searches for fans before it types valleys (`fans=True`,
the default). A reach with at least half its length on a fan is Type III
(medium confidence). Without the search, every steep-but-unconfined reach
stays an unresolved III/VII.

```python
f = api.detect_alluvial_fans(r"C:/data/dem.tif")          # any DEM, on its own
print(f["fan_count"], f["rejected_count"], f["candidates"])
api.retype_valleys_with_fans(r"C:/runs/v29", f["fans"])    # re-type a run's valleys
```

How it works:

1. **Apex.** A concave break in a channel's long profile: the gradient
   drops at least 1.6x within 100 m, from a canyon at 7% or steeper.
2. **Grow.** From the apex, cells are added while they stay inside the fan
   slope band (2-15%), keep descending, and stand above local base level.
3. **Cone test (v0.29).** A region is a fan only if it subtends at least 60
   degrees at its apex (`2 * area / r90**2`) and stops short of the 3 km
   radius cap. The fixture's fans subtend about 200 degrees. On Ruidoso
   (10 m), all 26 candidates were strips of trunk-valley floor below
   tributary mouths, at 3-44 degrees or reaching the cap, so none were kept.
   Every candidate is in `fan_candidates.gpkg` with `accepted` and `reason`.

Checked on synthetic fans only (3 of 3, apexes within 7 m, area within 5%),
not against mapped fans. Before v0.29 it had three bugs that fired on real
DEMs:
- It compared elevations in feet against metres on foot-unit DEMs, so a 5%
  fan read 16%.
- It fed the slope raster's nodata into the slope-floor estimate.
- It took apex candidates from the pass's own stream threshold.

The search is pure Python per cell. Above 60 million cells it runs on an
averaged copy of the DEM, with cells of at least 5 m. Fans are hundreds of
metres across, so nothing that matters is lost.

### Stable or unstable form — stream type against valley type

The reason for both classifications. At the end of `landscape_pass` (or
`api.stream_stability(valley_types_gpkg, stream_types_gpkg)` on any earlier
run) each reach's stream letter is judged against its valley type with
Rosgen's valley type / stream type associations — NRCS (2007) NEH 654
Technical Supplement 3E, Table TS3E-3, after Rosgen (1996), transcribed in
`geolens.landscape.stability.VALLEY_STREAM_TYPES` with the table's wording
beside each row:

| Valley type | Stream types (stable) | Only out of balance |
|---|---|---|
| I | A (Aa+) | |
| II | B | |
| III | A, B, G, D | |
| IV | F | |
| V | C, D, G | |
| VI | B, C, F | G (fault disequilibrium) |
| VII | A, G | |
| VIII | C, E | D, F, G |
| IX | C, D | |
| X | E, C, DA | G, F (base level changed) |
| XI | DA, C, E | |

`form_status`: **stable** (in the first column), **unstable** (in the second),
**check** (in neither: a departure, or one of the two types is wrong — do not
call it unstable until you have looked), **unknown**. An unresolved valley
type is judged under every candidate and reported only if they agree.
`form_confidence` is the lower of the two classifications' confidence;
`form_note` says why in words. `stream_stability.gpkg` comes with a QGIS style
coloured by status, and hovering over a reach shows its section sheet.

**Your own table (v0.30.1).** `docs/valley_stream_associations.csv` is this
table as a CSV: one row per valley type, with columns `valley_type`, `stable`,
`unstable` and `wording`, and letters separated by spaces or commas. Extra
columns such as notes are ignored. To use your own:

1. Edit a copy of the CSV. Excel is fine.
2. Pass it to `api.stream_stability(..., association_table="mine.csv")`, or
   give it to toolbox step *4. Stable or unstable form*.

The table is checked first. An unknown letter, a valley type listed twice, or
a letter that is both stable and unstable stops the step with the row number.
The run keeps a copy as `association_table.csv`, so re-running step 2 or 3
re-judges with the same table. `form_source` and the step's messages say which
table was used. `association_table="builtin"` (the toolbox's *Go back to the
built-in table*) drops the run's copy. `api.association_table(path,
write=True)` writes the built-in table anywhere.

Two things to know. The newer descriptive valley-type names Sam uses are not
in this table yet (the crosswalk is waiting on his copy of Rosgen's list); a
custom table can carry them as soon as the valley types use them. And with the
v0.28 bankfull method most stream types are medium confidence at best, so read
`check` and `unstable` as "go and look", not as a finding.

### Erosion features — gullies

**Bound the drainage area.** `max_drainage_km2` is the only criterion in the
detector that means *small* — slope variability, TPI and slope are every bit as
true of a river. It defaults to 0.5 km². Use the **same** number for
`stream_threshold_km2`, so area above the ceiling is a stream and area below it
can be a gully; the two then partition cleanly and `exclude_streams=True` only
trims the banks of mapped channels.

With the ceiling off, a real 1.94-billion-cell run returned 10,739 features and
3,195 km of "gullies" against a 1,767 km stream network — the drainage network,
mapped correctly, and none of it gullies.

**Do not pick `slope_std_min` as a percentile.** p90 admits 10 % of the DEM by
construction whatever is in it. "Use p99 then" is arbitrary in the same way and
measurably worse — on both fixtures here p97 and above return *zero* gullies,
because the top of the distribution is valley walls, not small incised things.
`slope_std` over gullied terrain is bimodal in the log; the threshold belongs in
the gap, and `suggest_slope_std_min` finds it.

```python
from geolens.landscape import (build_terrain, terrain_roughness,
                               suggest_slope_std_min, detect_gullies)

MAX_KM2 = 0.5           # the size ceiling: above this it is a stream, not a gully

# Ideally stream_threshold_km2 == MAX_KM2, so the two sets are disjoint and
# exclude_streams=True only trims the banks of mapped channels. On a small clip
# a threshold that large can leave no network at all, as here -- so keep the
# ceiling and lower the threshold, and leave exclude_streams off. Setting it
# with a threshold BELOW the ceiling deletes your largest gullies; geolens
# warns when you do.
t  = build_terrain("lidar_dem.tif", stream_threshold_km2=0.2)
rg = terrain_roughness(t, window_m=40)         # 3-5x expected gully width

sug = suggest_slope_std_min(rg.slope_std)      # decimated read, not the whole grid
print(sug["note"])                             # threshold, percentile, separation
if not sug["bimodal"]:
    print("no clear gap in this landscape - check the result hard")

g = detect_gullies(t, roughness=rg,
                   slope_std_min=sug["slope_std_min"],
                   tpi_max=-0.3,               # must be negative (incised)
                   max_drainage_km2=MAX_KM2,
                   min_length_m=100)
print(g.gully_count, g.total_length_km, g.gully_density_km_per_km2)
```

`geolens.api.detect_gullies` does the same thing in one call and **lands in the
run log**, which the library function does not — it reuses a `landscape_pass`
result rather than re-routing, scales the windows to the cell size, and picks
`slope_std_min` for you when you leave it `None`:

```python
import geolens.api as api

g = api.detect_gullies(res["terrain"],        # the result, NOT the DEM path
                       max_drainage_km2=0.5,
                       stream_threshold_km2=0.5,   # same number -> disjoint
                       exclude_streams=True)
print(g["threshold_choice"]["note"])
print(g["gully_count"], g["total_length_km"], g["_run_record"])
```

From the shell: `geolens gullies dem.tif --max-drainage-km2 0.5`.

On a synthetic fixture with 6 known gullies totalling 7.20 km this finds
**exactly 6 gullies, 6.53 km (91 % of length)**, matching them one-to-one — at
a threshold `suggest_slope_std_min` chose on its own (0.0459, p80.5), not one
tuned until the answer came out right:

| Detected | Truth | |
|---|---|---|
| 1383 m | 1500 m | −7.8 % |
| 1263 m | 1380 m | −8.5 % |
| 1146 m | 1260 m | −9.0 % |
| 1022 m | 1140 m | −10.4 % |
| 926 m | 1020 m | −9.2 % |
| 783 m | 900 m | −13.0 % |

#### Footprints — `gully_polygons.gpkg` (opt-in since v0.27: `polygons=True`)

**Off by default.** In steep mountain terrain the footprint climbs the walls of
the V-shaped hollow the gully sits in: on Cherokee Bill (0.61 m lidar) the
median footprint was 31 m wide and 11 m deep for gullies draining under
0.5 km², and Sam judged the polygons "not great". The width, depth and volume
columns on the gully lines come from the same footprints — treat them with the
same caution there. What follows is how the footprint works where it does.

Each gully's footprint is measured **from the ground**: a cross-profile every
two cells along the whole centreline, depth below the **lower** of the two
shoulders (water leaves at the lower rim), and the cells where the incision is
more than 5% of that section's depth. On the fixture of known gullies:

| | measured | built |
|---|---|---|
| width | **14.00 m** | 14 m |
| mean depth | 1.95 m | 2.00 m (parabolic) |
| max depth | 2.92 m | 3.0 m |
| volume | 27.3 m³ per metre | 28.0 m³ per metre |

The old footprint joined the bank tops of a few cross-sections into a ring,
and it failed on real terrain. On Eagle Creek (0.61 m) that meant a median of
2 usable sections per gully, footprints for only 55 of 117 gullies, a median
of 56% of each gully's own centreline inside its polygon (5% at worst),
square-ended slabs beside the line, and a figure-of-eight spanning two
parallel incisions. The new one covers 110 of 117, with a median of 96% of
each centreline inside. The old section numbers stay in the table as
`section_width_m` / `section_depth_m` for comparison.

`volume_m3` is now summed from measured depths rather than width × depth ×
length × a shape factor.

**Filter on `footprint_quality`** (`good`, `partial`, `doubtful`). A detected
line that is mostly not inside an incision, or a footprint barely deeper than
`min_depth_m`, is `doubtful`. On Eagle Creek that separated the real gullies
from a road cut and two lines wandering across a hummocky slope.

**Roads: pass a roads layer.** Detection admits roads — they are incised,
steep-sided and linear — and shape alone didn't separate the Eagle Creek one:
the detector follows a road's inside ditch, and a ditch is a small V (bed
flatness 0.33, inside the gully range). A map of the roads does:

```python
g = api.detect_gullies(res["terrain"], exclude="roads.gpkg",
                       exclude_buffer_m=10)
```

Lines lying mostly within the buffer are set aside into
`gullies_excluded.gpkg`, not deleted — a gully running beside a road is still
a gully, so look at them. TIGER roads and OpenStreetMap both work; any line or
polygon layer does. From the shell: `geolens gullies dem.tif --exclude
roads.gpkg`.

Each reads ~9 % short because the *head* of a gully is not yet incised — the
detector finds the incised part, which is the right answer. Expect detected
lengths to start just downstream of the true head.

**Width and depth are real measurements, not proxies.** Cross-sections are cut
along each detected centreline and the reach-scale bank detection is run on
them, because the detection mask hugs the walls and is not the gully section.
On the same fixture (14 m wide, 3.0 m deep):

| | Truth | Measured | |
|---|---|---|---|
| Mean depth | 3.0 m | 2.97 m | **−1.0 %** |
| Width | 14.0 m | 16.0 m | +14 % (one sample at 2 m cells) |

Per-gully output: `length_m`, `width_m`, `mean_depth_m`, `max_depth_m`,
`volume_m3`, `drainage_km2_max`. Reach totals on the result:
`mean_width_m`, `mean_depth_m`, `eroded_volume_m3`, `gully_density_km_per_km2`.

If width comes back NaN, `section_width_m` is too narrow to reach undisturbed
ground — raise it to 3-4x your expected gully width.

`exclude_streams=True` subtracts the stream network, but only turn it on if the
threshold is set well above your gullies — otherwise it deletes them. The result
warns you when that happens.

> **`detect_headcuts` is broken — do not use its numbers.** It reported 380 m
> drops on a DEM with 85 m of relief. Use
> `topotoolbox.StreamObject.knickpointfinder` instead. See `NEXT.md`.

### Imagery — NAIP NDVI, kept on disk

```python
n = api.naip_imagery(DEM)          # start="2021-01-01" pins a flight year
print(n["rgbn"])      # the imagery: red, green, blue, NIR — keep this
print(n["ndvi"])      # derived
print(n["sources"])   # naip_sources.json — what was searched, used, failed
```

Everything lands in `naip_dir/` in the output folder:

| File | |
|---|---|
| `naip_rgbn.tif` | all four bands, mosaicked newest-first, band names set — **the reference copy** |
| `ndvi.tif` | (NIR − red) / (NIR + red) at 1 m |
| `rgb.tif` | true colour |
| `naip_sources.json` | scene ids, flight dates, how each was fetched, and the full error for any that failed |

The per-scene clips are cached **beside the DEM** in `.geolens_cache/naip/`,
like the routing cache, so a new output folder or a new release doesn't
re-fetch them.

**Two reasons it failed before, both fixed in v21.** First, the 1 m grid was
built in the DEM's CRS units — on EPSG:6529 that is 1 ft, 10.76× the cells, and
Eagle Creek (40 km²) hit the 400-million-cell guard and raised before a single
request was made. The notebook then suggested checking the network, which was
never reached. Second, GDAL reads remote imagery through its own libcurl, which
trusts a different certificate store from Python. Behind a proxy that inspects
TLS — normal on a work Windows machine — Python can reach the catalog while
GDAL cannot read a tile. When streaming fails the tile is now downloaded with
Python's HTTPS instead, kept under `.geolens_cache/naip/tiles/`, and read
locally. Slower, a few hundred MB per tile, but it works and the tile stays on
disk.

If it still fails, open `naip_sources.json`: it says which stage failed —
the catalog search, or a specific scene — with the whole error, not the first
110 characters of it.

**Not yet tested against the live service from here** — the sandbox this was
written in cannot reach the Planetary Computer.

### Radar — Sentinel-1 statistics (v0.31)

What it is for: telling riparian woodland from irrigated fields, which NDVI
cannot (both are green in June). [docs/SAR_FEASIBILITY.md](docs/SAR_FEASIBILITY.md)
has the reasoning and the experiment.

**Once per person:** a free NASA Earthdata login
(<https://urs.earthdata.nasa.gov>), saved with the toolbox's *0 Setup ▸ Save
Earthdata login*, or in Python:

```python
import getpass
from geolens import sar
sar.save_login("your_username", getpass.getpass())   # into ~/_netrc, not geolens
```

It goes in your own netrc file (`%USERPROFILE%\_netrc` on Windows), where
NASA's tools, curl and GDAL look. It is never written into geolens, its run
records or its logs. Do not put a login in shared code: each person uses
their own.

```python
s = api.sentinel1_stats(DEM, start="2024-01-01", end="2024-12-31",
                        polygons="riparian_dir/riparian.gpkg")
print(s["stats"])         # sar_stats.tif
print(s["zonal"])         # sar_zonal.gpkg: the same numbers per polygon
```

The data is NASA's OPERA RTC-S1: Sentinel-1, terrain-corrected to
gamma-naught, 30 m, from 2016. Passes are found in NASA's catalog (CMR, no
login) and read through GDAL with the login. Behind a proxy GDAL cannot get
through, the files are downloaded whole with Python's HTTPS instead, as for
NAIP. In the output folder:

| File | |
|---|---|
| `sar_tiles/T<track>/<date>_T<track>.tif` | VV and VH (linear) per pass on the area's 30 m UTM grid; shadow, layover and fill are NaN. Reused on a rerun. |
| `sar_stats_T<track>.tif` | one track's statistics |
| `sar_stats.tif` | all tracks, each weighted by its passes |
| `sar_zonal.gpkg` | `sar_vh_db`, `sar_vv_db`, `sar_vh_vv_db`, `sar_vh_cv`, `sar_cells` per polygon (every cell a polygon touches) |
| `sar_sources.json` | every burst used, by track and date |

The bands are `vh_db` (median VH, dB), `vv_db` (median VV, dB), `vh_vv_db`
(their difference), `vh_cv` (VH's standard deviation over its mean through
the period) and `passes`. Each track is summarised alone, because each
looks at the ground from its own angle. A pixel needs `min_passes` (8)
valid passes, and a track with fewer is left out with a warning. The area
is limited to 20 million 30 m cells (about 18,000 km²).

**Tested offline only.** The catalog and NASA's files are stood in for in
`tests/test_sar.py`; the sandbox this was written in cannot reach NASA. The
first real run is the test.

### Soils — SSURGO as the valley floor's material (v0.32)

The national geologic map behind `geology="auto"` is compiled at 1:50,000 to
1:1,000,000, so a valley 80 m wide often comes back as its hillslopes'
bedrock (see `geology.py`). SSURGO is mapped at about 1:24,000 and records
each soil's parent material, so a valley floor's own alluvium is usually
there. Use it in place of the geology:

```python
api.landscape_pass(DEM, geology="ssurgo")          # or, for an earlier run:
api.valley_types_for_run(RUN, geology="ssurgo")
s = api.fetch_soils(DEM, buffer_m=200)             # or just the layer
```

`soils_dir/soils.gpkg` (layer `soils`) has every map unit polygon in the
area, clipped to it, with:

| Field | |
|---|---|
| `material_class` | the dominant soil's parent material in geology's vocabulary: `alluvial`, `mass_movement` (colluvium, slope alluvium, debris flows), `residual` (residuum), `eolian`, `lacustrine`, ... or empty when the material says nothing about how the ground was laid down (volcanic ash, fill) |
| `pmkind`, `pmgroupname`, `landform` | what SSURGO says, for checking the class |
| `compname`, `comppct_r` | the dominant component and its share of the map unit |
| `flodfreqdcd`, `drclassdcd`, `hydgrpdcd`, `brockdepmin` | flooding frequency, drainage class, hydrologic group, depth to bedrock (cm) |
| `kwfact` | whole-soil erodibility (K) of the dominant soil's surface horizon (v0.33) |

"Slope alluvium" is classed as mass movement, not alluvial: it is hillslope
wash, and calling it alluvium would put a valley floor on every footslope.
The data comes from USDA-NRCS Soil Data Access (public, no key) in tiles of
0.1°, so a large area is many small queries. A tile the service cannot
answer is split in four, up to three times. `soils_sources.json` records
what was asked and the area by material.

**Tested offline only,** against a stand-in for the service
(`tests/test_soils.py`). The SQL follows NRCS's documented tables
(`mupolygon`, `mapunit`, `muaggatt`, `component`, `copmgrp`, `copm`,
`cogeomordesc`); the first real run is the test.

### Projects of any size, by HUC12 (v0.33)

A real project is a boundary: a BLM field office, a ranch, a district. At
1 m the Kingman Field Office (about 23,000 km²) would be some 23 billion
cells, far beyond one run. A project therefore runs in two levels:

```python
r = api.run_project(
    "https://gis.blm.gov/arcgis/rest/services/admin_boundaries/BLM_Natl_AdminUnit/MapServer/3",
    where="ADM_UNIT_CD='AZC01000'",         # or a polygon layer path
    level_b="pilot", pilot_count=3)          # run with GEOLENS_OUT = the project folder
```

| Level | Area | DEM | Results |
|---|---|---|---|
| A | the boundary's HUC12s plus the HUC12s draining into them (from the WBD's `tohuc`) | 3DEP 1/3 arc-second, 10 m; coarser only if memory says so | routing, valley bottom, confinement, valley form, valley types, fans |
| B | one HUC12 at a time | 3DEP 1 m lidar, where it covers at least `lidar_min_fraction` (0.8) of the HUC12 | stream types, section plots, stable / unstable form for the level A reaches inside it |

Level B order:

- `"pilot"`: the HUC12s with lidar and the most unconfined or partly
  confined valley, where the channel is free to change form;
- `"all"`, or `"listed"` with `huc12s="150301030201,..."`;
- `"none"` for valleys only.

`level_b_hours` stops level B cleanly at a time limit.

The project folder holds:

- `project.json`: every step and HUC12, so running the project again
  carries on where it stopped;
- `huc12.gpkg`: each HUC12's role and lidar coverage;
- `A_landscape/` and `B_huc12/<code>/`: the runs;
- `merged/`: valley types, stream types, sections, stability and
  `huc12_status`, for the boundary;
- `RESULTS.md`: what ran at what resolution, and every warning.

**Resolution travels with the results.** Merged valley types carry
`dem_cell_m`, and `valley_resolved` is false for valleys under three cells
wide. Stream types carry `fine_dem_cell_m` and the HUC12's `lidar_frac`.
`huc12_status` says which HUC12s have stream types (`done`), which have no
lidar (`no lidar`: valleys only), and which are still `queued`.

**Fallbacks.**

- DEM: 1 m lidar where 3DEP has it, otherwise 1/3 arc-second. A 1 arc-second
  or Copernicus fallback for areas outside 3DEP is on the step list.
- Geology: USGS, SSURGO (`geology="ssurgo"`) or none.
- Regional curve: `"auto"` picks the Moody et al. (2003) province from the
  report's own map (Figure 2):
  - west of its dashed division near 109.6° W: central and southern
    Arizona;
  - east of it: eastern Arizona / New Mexico.

  The report's study region runs about 114.0–104.0° W, 31.6–38.1° N. Within
  a degree outside it, the nearest province is used with a warning; beyond
  that, Bieger et al. (2015). `"auto"` is now the toolbox default in every
  tool that types streams. Other regions' curves are on the step list.
- The project also fetches SSURGO over the boundary (`soils=True`). It
  carries composition, hydrologic group and erodibility for the
  scope-of-work questions on soils.

Drainage areas on rivers entering from outside the HUC8s the project
touches (the Colorado) are local only, and `RESULTS.md` says so.

### Portable: everything on one drive (v0.33)

`setup_portable.bat` builds `env` (Python and every library) inside the
release folder with micromamba, a single downloaded program. Downloads,
package caches and temporary files go to the drive; nothing is installed
on the computer. When `env` exists:

- the toolbox uses it and nothing else, never an environment on the
  computer;
- every run's temporary files, caches (routing fallback, matplotlib, numba,
  PROJ) and the Earthdata login go in `home` and `tmp` beside it;
- the toolbox always runs the geolens in its own `src`. The code and the
  toolbox can never be different versions, and a new release runs from
  wherever it is unzipped.

`start_notebook_portable.bat` opens the notebook the same way. Running
`setup_portable.bat` again updates the packages in `env` in place (v0.33.1).

### Can this computer reach the services? (v0.33)

`api.check_services()` (toolbox: *0 Setup ▸ Check internet services*) makes
one small request to each service geolens downloads from: 3DEP, the WBD,
the national geologic map, SSURGO, NAIP, NASA CMR. It also says whether an
Earthdata login is saved. Run it before a long project.

---

## 3. Run it in a notebook

```bash
pip install jupyterlab
jupyter lab
```

Everything above works unchanged. Progress bars use `tqdm.auto`, so you get real
notebook widgets rather than console spam.

Display the PNGs inline:

```python
from IPython.display import Image, display
out = api.plot_delineation(d, station=340)
display(Image(out["path"]))
```

---

## 4. Run it from ArcGIS Pro — the toolbox

`arcgis_toolbox/geolens.pyt` is a Python toolbox. Add it in the Catalog pane
(*Toolboxes ▸ Add Toolbox*). [START_HERE.md](START_HERE.md) has the
walk-through.

| Toolset | Tool | geolens call |
|---|---|---|
| 0 Get data | Fetch a DEM (USGS 3DEP) | `fetch_dem` (1 m lidar, 1/3 arc-second fill; tiles + mosaic) |
| 0 Get data | NAIP imagery and NDVI | `naip_tiles` (NDVI and imagery tiles, NDVI and true-colour mosaics) |
| 0 Get data | Sentinel-1 radar statistics (OPERA RTC) | `sentinel1_stats` (tiles per pass, per-track and combined statistics, per polygon) |
| 0 Get data | SSURGO soils | `fetch_soils` (map units with `material_class`, usable as the geology layer) |
| 0 Setup | Check internet services | `check_services` |
| 1 Whole routine | Run a project area (by HUC12) | `run_project` (level A at 10 m, level B on lidar by HUC12, merged layers, RESULTS.md; resumable) |
| 0 Setup | Save Earthdata login | writes your `_netrc` in the toolbox's own Python (the password never goes through geolens) |
| 1, 2 Steps | Geology: *Download USDA SSURGO soils* | `geology="ssurgo"` |
| 0 Setup | Find the geolens environment | probes and remembers the environment's `python.exe` |
| 0 Setup | Check memory before a run | `plan_landscape_run` |
| 1 Whole routine, 2 Steps | step 1 and the routine: *Clip to one watershed first* | `watershed`, then the pass on the clipped DEM |
| 1 Whole routine | Run the whole routine | `landscape_pass`, then `detect_gullies` if ticked |
| 2 Steps | 1. Terrain, valley bottom and confinement | `landscape_pass(valley_types=False, stream_types=False)` |
| 2 Steps | 1b. Calibrate confinement breaks | `calibrate_confinement_for_run` (sweep; with breaks: `reclassify_confinement` + valley types from the existing valley-form table) |
| 2 Steps | 2. Valley form, alluvial fans and valley types | `valley_types_for_run` |
| 2 Steps | 3. Stream types | `stream_types_for_run` |
| 2 Steps | 4. Stable or unstable form | `stream_stability` |
| 2 Steps | 5. Gullies (experimental) | `detect_gullies` |
| 2 Steps | 6. Riparian band from NAIP imagery | `riparian_for_run` (`naip_imagery` + `riparian_band`, HAND from the run) |
| 0 Setup | Coarsen a DEM | `resample_dem` (true metres; 0 = the memory check's suggestion) |
| 3 More tools | Clip a DEM to one watershed | `watershed` (pour point layer, boundary or HUC12) |
| 3 More tools | Headcuts (knickpoints) | `detect_headcuts` (needs topotoolbox) |
| 3 More tools | Alluvial fans | `detect_alluvial_fans`, then `retype_valleys_with_fans` |
| 3 More tools | Section plots for an earlier run | `plot_stream_sections` |
| 3 More tools | Cross-sections along a centerline | `export_cross_sections` |

How the toolbox works:

- **It shares the QGIS plugin's runner.** `geolens_runner.py` is the same
  file as `qgis_plugin/geolens_qgis/runner.py`, and a test keeps them
  identical. Each call is a subprocess in the geolens environment.
- **Clean environment.** ArcGIS Pro's `PYTHONHOME`, `GDAL_DATA`, `PROJ_LIB`
  and conda variables are removed, and ArcGIS entries are dropped from
  `PATH`.
- **Progress and cancelling.** Progress goes to `arcpy.SetProgressor`. With
  `arcpy.env.autoCancelling = False`, the tool polls `arcpy.env.isCancelled`
  and ends the process tree on Cancel.
- **Finding the environment.** The tools look first at
  `~/.geolens/python.txt`, which the installer writes, and then at the
  usual conda locations.
- **Inputs.** Layers come in as files. A shapefile or a one-layer GeoPackage
  is used as it is. Anything else (a file geodatabase, a selection) is
  copied to `<output>/_inputs/<name>.gpkg`, because geolens reads files.
- **Outputs.** Each result is a derived output, so ArcGIS adds it to the
  map. `postExecute` then names the layers, colours them and adds the
  pop-ups:
  - categories come from the QGIS `.qml` beside each GeoPackage, so both
    programs show one legend;
  - valley types have a fixed palette;
  - a layer with a `plot_path` field gets an image pop-up through the
    layer's CIM (`CIMImageMediaInfo`, Source and Link URL `{plot_path}`).
- **Testing.** `tests/test_arcgis_toolbox.py` runs every tool against a
  stand-in arcpy (`tests/arcgis/fake_arcpy`) and real geolens. It checks the
  toolbox's own logic, not ArcGIS Pro's behaviour.

**Plots as attachments.** With *Store layers in a file geodatabase* (the
default), each layer is copied into `<run>/geolens.gdb`. For layers with a
`plot_path` field, missing paths are set to null, then `EnableAttachments`
and `AddAttachments` run with the feature class as its own match table.
ArcGIS's default pop-up then shows the plot. Those layers get no custom
pop-up, because a custom one could hide the attachments element. A layer
from a renamed step folder is stored as `<prefix>__<layer>`
(`stream_types_v2__stream_types`).

The run folder is the unit. Step 1 writes a record in `runs/`; the later
steps read the run's terrain, reaches, geology and DEM paths from that
record, so they need nothing but the folder.

## 4b. Run it inside QGIS

### The plugin (recommended)

`qgis_plugin/geolens_qgis_plugin.zip` adds a **geolens** group to the
Processing Toolbox (QGIS 3.22+): check memory, landscape pass, section plots,
stable/unstable form, cross-sections. Install it with *Plugins ▸ Manage and
Install Plugins ▸ Install from ZIP*. [START_HERE.md](START_HERE.md) has the
steps.

How it works:

- Every tool runs `geolens.api` **in the geolens environment as a
  subprocess**, not in QGIS's Python. QGIS's Python lacks numba, pysheds and
  geopandas, and an out-of-memory kill in a subprocess can't take the QGIS
  project with it.
- **Finding the environment.** The plugin tries these in order:
  - the path saved by *Plugins ▸ geolens ▸ Set up*
  - `GEOLENS_PYTHON`
  - `~/.geolens/python.txt` (written by `install.py` after every successful
    install or update)
  - the usual conda locations for an env named `geolens`
- **Clean environment.** The child gets QGIS's `PYTHONHOME`, `PYTHONPATH`,
  `GDAL_DATA`, `PROJ_LIB` and `PROJ_DATA` removed, and QGIS/OSGeo4W removed
  from `PATH`. Otherwise it would load QGIS's GDAL and PROJ database, not
  its own.
- **Progress.** `GEOLENS_PROGRESS=json` makes geolens's progress bars and
  status notes machine-readable lines on stderr. The plugin turns them into
  the dialog's progress bar and log, calling QGIS only from the Processing
  task's own thread.
- **Cancel** ends the process tree, including the section-plot workers.
- **Styles.** Layers load with the `.qml` written beside each GeoPackage, so
  the map tips and colours come with them.
- **Testing.** `tests/test_qgis_runner.py` covers the runner without QGIS.
  `tests/qgis/check_plugin.py` runs the tools in a headless QGIS.

### The Python console

**Plugins → Python Console** (Ctrl+Alt+P), then:

```python
import os
os.environ["GEOLENS_OUT"] = "/tmp/geolens_out"     # Windows: C:/work/geolens_out
from geolens import api

r = api.landscape_pass("/data/dem_30m.tif", stream_threshold_km2=1.0)

# Load the results straight onto the map canvas
iface.addRasterLayer(r["terrain"]["hand"], "HAND")
iface.addVectorLayer(r["valley"]["valley_polygons"], "Valley bottom", "ogr")
iface.addVectorLayer(r["classified"]["segments"], "Segments (classified)", "ogr")
```

Then style `Segments (classified)` by the `confinement` field — right-click →
Properties → Symbology → Categorized → Value: `confinement` → Classify.

Useful fields on that layer: `valley_width_m`, `confinement_ratio`,
`confinement`, `gradient_class`, `rosgen_candidates`, `needs_geology`,
`drainage_km2`, `hand_mean_m`.

Reach scale works the same way:

```python
d = api.delineate_channel("/data/lidar.tif", "/data/centerline.shp",
                          spacing=25, width=160)
csv = api.export_csv(d, "delineation")
iface.addVectorLayer(csv["path"], "Delineation table", "ogr")
```

Long runs block the QGIS UI — the console is single-threaded. For a
multi-million-acre pass, run it in a terminal and load the outputs afterwards.

---

## 5. Run it from Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "geolens": {
      "command": "python",
      "args": ["-m", "geolens.mcp_server"],
      "env": {
        "GEOLENS_OUT": "C:/work/geolens_out",
        "GEOLENS_ROOTS": "C:/work/RiverData"
      }
    }
  }
}
```

Use the **full path to the venv's python** if you installed into one. Restart
Claude Desktop, then ask it things directly. Copy `skill/SKILL.md` into your
skills folder so it knows the conventions.

---

## 6. Command line

```bash
geolens backends                 # which flow-routing backends are installed
geolens workspace                # where outputs go, what's readable
geolens list ./data
geolens describe ./data/reach.gdb --layer streams
geolens delineate dem.tif centerline.shp --width 160 --plot --csv --summary
geolens landscape dem.tif --nhd nhd.shp --segment-length 500
```

---

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| `PathNotAllowed` | Path is outside `GEOLENS_ROOTS`, or doesn't exist. Run `geolens workspace`. |
| `ReadOnlyViolation` | `GEOLENS_OUT` sits inside a source folder. Point it somewhere else. |
| "No flow-routing backend" | `pip install pysheds` (or `whitebox`). Check with `geolens backends`. |
| `AttributeError: np.in1d` | Old geolens — the NumPy 2 shim is in `landscape/terrain.py`. |
| Valley bottom covers >60% of the map | Lower `hand_zero`, or raise `threshold`. |
| HAND looks wrong, or has huge values | Check the run's warnings. HAND cannot exceed the DEM's relief; if it does, flow ran into nodata. geolens drops stream cells that sit on nodata and says how many — a large number there means the DEM is not a single connected area with a real outlet. |
| HAND is empty over part of the DEM | Those cells drain off the edge without passing a stream, so HAND is genuinely undefined for them. Lower `stream_threshold_km2` so the network reaches further upslope, or clip to a catchment with its outlet inside the DEM. |
| Everything classifies "unconfined" | Breaks aren't calibrated. Read `confinement_ratio_percentiles`, set breaks from it. |
| No gullies found | The warning names the tightest test — start there. If it is `slope_std`, use `suggest_slope_std_min` rather than guessing a percentile. |
| Far too many "gullies", longer than the stream network | `max_drainage_km2` is off or too high. It is the only criterion that means *small*. |
| QGIS: `ModuleNotFoundError: geolens` | Installed into the wrong Python. Use OSGeo4W Shell / QGIS's `bin/python3`. |
| Process killed, no error | Out of memory. Tile the DEM, or coarsen it. |
| MCP server won't start | Use the absolute path to the venv python in the config. |

---

## Test checklist for tomorrow

Work down this list; each step is independently useful.

- [ ] `pip install -e ".[all]"` completes
- [ ] `python quickstart.py` prints four output paths
- [ ] `python -m pytest tests/ -q` → 10 passed
- [ ] `api.describe_raster()` on **one of your real DEMs** — check CRS and cell size look right, read the warnings
- [ ] `api.list_datasets()` on a real project folder — does it find your `.gdb` layers?
- [ ] `api.delineate_channel()` on **one short lidar reach with a centerline you trust**; plot it and eyeball whether the bank tops land where you'd put them
- [ ] Tune `bank_slope` / `min_bank_height` until they do; write down what worked
- [ ] `api.landscape_pass()` on **one HUC12** (not the whole 3 M acres) at 30 m
- [ ] Read `confinement_ratio_percentiles`; pick breaks; re-run
- [ ] Load the outputs in QGIS and style by `confinement` — does the map match what you know of that watershed?
- [ ] `terrain_roughness` + `detect_gullies` on a lidar tile where you know gullies exist
- [ ] Note which defaults were wrong for your terrain — that list is the next session's work

Start small. One reach, one HUC12. The parameters that matter are the ones that
turn out wrong on your data, and you find those fastest on a tile you know well.
