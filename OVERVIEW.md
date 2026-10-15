# geolens — what it is, how to use it, where it goes next

A formatted summary with method descriptions, figures and references is in
[docs/geolens_technical_summary_v0.28.pdf](docs/geolens_technical_summary_v0.28.pdf).

A Python package for extracting channel and valley geometry from a DEM. Open
source throughout — GDAL, rasterio, geopandas, shapely — so no ArcGIS licence is
needed for anything, including reading File Geodatabases.

---

## What it actually does

Two scales, sharing definitions so the coarse pass can be calibrated against the
fine one.

### Reach scale — a DEM and a centerline

| | |
|---|---|
| **Cross-sections** | Perpendicular sections at a chosen spacing and width, written as a shapefile with an `xs_id` you can query in GIS to pull that section's plot |
| **Bank and floodplain delineation** | Finds the bank tops by slope break, then climbs to the floodplain and **stops at valley confinement**, so floodplains do not run up hillsides |
| **Bankfull geometry** | Width, mean and max depth, width/depth ratio, entrenchment ratio, per section |
| **Longitudinal profile** | Thalweg elevation and reach slope along the centerline |
| **Representative section** | The medoid section per reach, so one section stands in for the reach instead of fifty |

Verified against a synthetic compound channel of known geometry: floodplain
width 0.00% error, entrenchment 2.59%.

### Landscape scale — a DEM, optionally a stream network

| | |
|---|---|
| **Flow routing** | D8 direction and accumulation, priority-flood depression filling. Outputs in their own right, and the foundation everything else tiles on |
| **Watershed unit** | Clip to a complete watershed before analysing — from a boundary you have, a pour point, or the USGS WBD. A supplied boundary is checked for inflow, not trusted: a rectangle over the test landscape is missing 3.65x its own area in upstream contribution |
| **HAND** | Height above nearest drainage, traced along flow paths. Stream cells are restricted to valid ground first — without that, flow collecting in a clipped DEM's nodata collar becomes "stream" and HAND reports `elevation + 9999` |
| **Valley bottoms** | VBET-style evidence weighting (0.65 HAND / 0.35 slope), drainage-area scaled |
| **Network segmentation** | The network split into segments with a valley width per segment, from a Euclidean distance transform |
| **Confinement classes** | Confined / partly confined / unconfined from the valley-width to channel-width ratio |
| **Gullies** | Slope standard deviation + negative TPI + drainage, with real cross-sectional geometry measured on each one — delivered as centrelines **and as 2D footprints closed through the measured bank tops** |
| **Headcuts** | TopoToolbox `knickpointfinder`, with the background gradient removed |
| **Alluvial fans** | Apexes from the concave break in the channel long profile, then a cost-limited region grow |
| **Geology** | Surficial and bedrock material for an area, from the USGS national map |
| **Valley form** | Cross-section shape **floor to ridgetop**, against a fixture with known ridges: top width within 0.7% of truth, shape exponent within 0.03, both divides found on 100% of sections. The valley floor is measured as the floor, not the incised channel — that defect made an 1800 m alluvial floor read as 40 m |
| **Valley form (detail)** | Cross-section shape floor-to-divide: Vf (Bull & McFadden), a V-versus-U shape exponent, side slopes, relief, asymmetry |
| **Rosgen valley types** | I-XI, from valley form plus material and detected fans |
| **Riparian band** | NDVI in excess of the local upland background, cut to low HAND. The arid-country stand-in for a flood extent |
| **NAIP imagery** | 1 m four-band imagery streamed by HTTP range request over a DEM's extent, cached to disk. NDVI is built at **1 m regardless of the DEM's cell size**, and `riparian_band` resamples HAND onto it |
| **Observed flood extent** | Copernicus GFM radar flood maps, for validating a floodplain against something that is not another model |

Gully detection finds 6 of 6 on a fixture with known gullies, recovers 91% of
their length, and measures depth to −1.0%. The footprint polygons imply a mean
width of 15.75 m against a known 14 m — the same one-sample bank-top offset seen
at reach scale. **`mask_area_m2` is not the footprint**: the detector's
thresholds select a ribbon near the centreline and imply 2.45 m on the same
gullies, so use `measured_area_m2`. Headcuts score 6 of 6 with no false
positives, each within 3–6 m of its known position.

Those fixture numbers are not the whole story, and the gap between them and a
real run is worth stating plainly. `max_drainage_km2` is the only criterion in
the gully detector that means *small* — slope variability, TPI and slope are
every bit as true of a river. It shipped defaulting to `None`. On a
1.94-billion-cell DEM that returned 10,739 features and 3,195 km of "gullies"
against a 1,767 km stream network: the drainage network, computed correctly,
and none of it gullies. It now defaults to 0.5 km² and warns when disabled.
The companion mistake was choosing `slope_std_min` as a percentile — p90 admits
10% of a DEM by construction, and p99 finds nothing at all, because the top of
the roughness distribution is valley walls. `suggest_slope_std_min` finds the
gap between the smooth and incised modes instead, and reproduces the 6-of-6
fixture result without being tuned to it.

Fans score 3 of 3 with no
false positives: apexes within 7 m, area within 5%, radius within 3%, radial
gradient within 2%.

The riparian band recovers 100% of a known corridor on a synthetic arid valley
and reports its area to the cell (0.481 km2 connected, against 0.481 km2 of
truth). A green hilltop is excluded by HAND; a detached irrigated field lands in
`unconnected_km2` and in `suspect_patches` rather than being counted as
riparian.

### The thing that makes it usable at 1–2 m

The memory problem is solved properly, without resampling. Only flow
accumulation has a global dependency — every other operation (slope, TPI,
distance transforms, region grows, cross-sections) is local and tiles freely. So
flow routing runs once over the whole grid in about 30 GB at 2 m, and tiles
inherit the accumulation rather than recomputing it — when each tile is given
the whole-DEM routing through `run_landscape(flow=...)`. The test holds tiled
and untiled to within 10%; `api.landscape_tiled` does not pass the routing
through and warns that most of the network is lost. (The 0.1% figure this
section used to quote is not backed by a test; see NEXT.md.)

`plan_landscape_run(dem)` reads only the DEM header and tells you cells, peak
RAM and whether it will fit — run it first. An out-of-memory kill gives no
error message at all.

---

## How to use it

### Install

Windows: double-click `install_windows.bat`. macOS/Linux: `bash install.sh`.
It builds its own environment and then verifies itself by running the workflow
on a synthetic valley with a known 16 m channel. `INSTALL.md` covers failures.

### Four ways in

**Notebook** — `notebooks/geolens_start.ipynb`. One cell to edit. Start here.
Results draw **inline** as each step finishes (`geolens.view`), so you can see a
valley bottom leaking onto uplands without opening QGIS. Rasters are read
decimated, so displaying a multi-gigabyte DEM costs the same as a small one.

**Python**
```python
from geolens import api

api.plan_landscape_run(dem)                     # will it fit? run this first
api.landscape_pass(dem, nhd=network)            # valleys, segments, confinement
api.export_cross_sections(dem, centerline)      # section lines + a plot each, linked
api.plot_stream_sections(stream_types_dir)      # picture every stream-type section
api.detect_headcuts(dem, lines=gully_lines)     # knickpoints
api.detect_alluvial_fans(dem)                   # fans at canyon mouths
api.fetch_geology(watershed_boundary)           # surficial + bedrock material
api.rosgen_valley_types(segments, geology=geo, fans=fans)   # valley types I-XI
api.restream(res["terrain"], 0.2)               # new stream threshold, NO re-routing
api.confinement_break_sweep(segments)           # compare candidate breaks, writes nothing
api.reclassify_confinement(segments, 3, 9)      # apply breaks, seconds not hours
api.valley_form(dem, segments)                  # Vf, shape, side slopes floor-to-divide
api.compare_backends()                          # do pysheds and whitebox agree here?
api.naip_imagery(dem)                           # stream 1 m NAIP, build NDVI, cache the tiles
api.riparian_band(ndvi, hand=hand)              # riparian corridor from NDVI excess
api.fetch_inundation(dem)                       # observed flood extent, for validation
api.calibration_cycle(segments)                 # fit from your corrections, honestly
api.run_history()                               # what have I already run?

from geolens import view
view.show_landscape(res, dem=dem)               # inline: relief, HAND, valley, classes
view.show_sections(xs, delineation=d)           # inline: sections with bank tops
```

**Command line**
```
geolens plan dem.tif
geolens landscape dem.tif --stream-threshold-km2 1.0
geolens watershed dem.tif --pour-point 429985,4510035 --run
geolens gullies dem.tif --max-drainage-km2 0.5
geolens headcuts dem.tif --lines gullies.gpkg
geolens fans dem.tif
geolens geology aoi.shp --buffer-m 1000
geolens valleytypes segments.gpkg --geology geology.gpkg --fans fans.gpkg
geolens breaks segments.gpkg
geolens reclassify segments.gpkg --confined-max 3 --partly-confined-max 9
geolens valleyform dem.tif segments.gpkg
geolens compare-backends
geolens naip dem.tif --start 2021-01-01
geolens riparian ndvi.tif --hand hand.tif --streams streams.tif
geolens history
```

**From Claude** — there is an MCP server (`geolens.mcp_server`), so Claude can
run the analysis and read the results directly.

### Where results go

`GEOLENS_OUT` sets the output directory; it must sit outside your data folders
and the package refuses to run otherwise.

Every api call now records itself under `<output>/runs/` — the full result, the
arguments that produced it, how long it took, and the files it wrote, plus a
`manifest.jsonl` indexing all of it. `geolens history` reads it back. This
exists because the expensive part is not re-running the code, it is
reconstructing which threshold produced the layer you are looking at.

**Every cross-section has a picture, and the map knows where it is.** Section
layers (`stream_type_sections.gpkg`, the reach-scale `cross_sections.gpkg`)
carry `plot_file`/`plot_path` and come with a QGIS style: turn on *View ▸ Show
Map Tips* and rest the pointer on a section to see its plot, or click it with
*Run Feature Action* to open it. `stream_types.gpkg` links each reach to a
sheet of all its sections. In ArcGIS Pro, add an Image to the pop-up with
source `{plot_path}`.

---

## What to trust, and how much

Being specific about this matters more than the feature list.

| Component | Status |
|---|---|
| Bank/floodplain delineation | **Verified** against known geometry. Floodplain width 0.00% error |
| Flow routing | **Verified** with pysheds. Tiled runs match untiled within the test's 10% only when given whole-DEM routing |
| Gully detection | **Verified** — 6/6, 91% of length, depth −1.0%. Width from one sample only |
| Headcuts | **Verified** — 6/6, no false positives, 3–6 m position error. Drop reads ~23% high |
| Valley bottoms / confinement | **Working, uncalibrated.** The class breaks are defaults, not fitted to your landscape |
| Geology fetch | **Logic tested, live call untested.** Outbound HTTPS was blocked where it was built — run `geolens geology --selftest` once before trusting it |
| Alluvial fans | **Verified** — 3/3, no false positives. Apex 7 m, area −5%, radius +3%, gradient −2% |
| Rosgen valley types | **Logic verified, thresholds uncalibrated.** The crosswalk is tested exhaustively; the gradient breaks are an encoding of Rosgen's descriptive classes, not field-validated values |
| Valley form (Vf, shape) | **Verified against valleys of known geometry** — Vf, side slope and relief to within 10-30%, shape exponent to 0.02 — and now exercised end to end on a fixture. Never run on a field-surveyed valley |
| Flow routing (whitebox backend) | **Unverified.** The binary could not be downloaded where this was built — run `geolens compare-backends` once before trusting it |
| Riparian band | **Verified on a synthetic scene** — corridor recovered exactly, hilltop excluded, detached field flagged. Never validated against a field-mapped riparian boundary |
| NAIP streaming | **Logic tested, live call untested.** Outbound HTTPS was blocked where it was built — run `geolens naip --selftest` once before trusting it |
| Observed flood extent | **Logic tested, live call untested**, and **the wrong tool in arid country** — see below. Recall only, never precision |

Two limits worth stating plainly:

- **Confinement breaks are uncalibrated.** Run real data, read
  `summary.confinement_ratio_percentiles`, set the breaks from that
  distribution. Until then the classes are arbitrary.
- **Geology cannot resolve narrow valleys.** The national map is built from
  source maps at 1:50,000 to 1:1,000,000. A confined valley 80 m wide comes
  back as its hillslope's bedrock, because its alluvial floor was never
  mappable at that scale. `mapped_fraction` and the warnings are there to catch
  it; a valley-type call on a small stream should be treated as low confidence.
- **The riparian band cannot tell alfalfa from cottonwood.** Nothing in NDVI
  can. A field detached from the corridor is separated by connectivity and
  flagged; a field touching it merges into one patch and inflates the area, and
  in the arid West fields sit on the floodplain by definition, so that is the
  common case rather than the edge case. A test asserts the inflation rather
  than pretending otherwise. Separating contiguous agriculture needs a
  land-cover layer.
- **Radar flood mapping does not work on ephemeral washes.** A monsoon flood
  lasts hours against a 6-12 day revisit, and a dry sandy bed is dark at C-band
  and reads exactly like open water. `fetch_inundation` sets `arid_signature`
  when it sees the pattern. In the Southwest the riparian band is the
  substitute: phreatophytes rooted in the alluvial aquifer map the floodplain
  far more stably than flow does.
- **Valley types need geology to mean much.** On the fan fixture, without
  geology 94% of segments come back unresolved; with it, 19%. That is the
  design working — the types geometry alone cannot separate (II/V/VI, VIII/IX,
  X/XI) are most of the classification, and guessing them would be worse than
  saying so. Type VI is never asserted at all, because structural control is
  not visible in a DEM or a geologic map.

---

## A note on TIN vs raster

Measured, not argued — `tests/experiments/resolution_vs_tin.py`, one analytic
channel with the bank top at ±8.0 m, one detector, five ways of sampling it:

| sampling | 0.5 m | 1 m | 2.4 m | 5 m |
|---|---|---|---|---|
| raster | +0.25 | +0.25 | +1.25 | +2.25 |
| TIN from that raster | +0.25 | +0.25 | +1.25 | +2.25 |
| scattered points, same density | +0.50 | +0.75 | +1.75 | +3.00 |
| TIN + breakline at the true bank top | +0.25 | +0.25 | +0.25 | +0.25 |
| TIN + breakline at an estimated one | +0.50 | +0.50 | +1.50 | +2.50 |

A TIN built from a raster is **identical** to the raster — same samples, no new
information. Scattered points at the same density are **worse**, because a grid
guarantees a sample near the break. A breakline makes the measurement
resolution-independent, which is the real reason hydraulic modellers use TINs —
but the breakline cannot be bootstrapped from the raster's own estimate, so that
gain needs surveyed or digitised lines, not a second pass.

Practically: use the finest raster you have and don't resample away from it.

---

## A note on what this does and does not rebuild

Flow routing, depression filling and HAND are solved problems. **WhiteboxTools
(MIT, Rust, 567 tools) does all of them**, including `elevation_above_stream`,
which is HAND — and it is already an optional backend here. The hand-rolled
implementations remain as a no-binary fallback, but they are where nearly every
serious bug in this package has been found, and that is not a coincidence:
rebuilding a solved thing costs you a decade of other people's bug reports.

Riverscapes' VBET and ConfinementTool are the obvious things to reuse for valley
bottoms and confinement, and **they are GPL-3.0**, which an MIT package cannot
import and redistribute. (`pip install vbet` gets a gambling bot; `pip install
fct` gets a radio-astronomy viewer. Check before typing.) So the valley-bottom
evidence layer stays here — it is ~100 lines of published VBET weighting on top
of HAND and slope, and that thin layer is worth owning.

Run `geolens compare-backends` once on a machine that can fetch the binary. If
the stream networks agree, use whitebox for anything large: it is the right
engine for a billion-cell DEM and this package is not.

---

## Where it goes next

**1. Run `geolens compare-backends`, and the three live-call selftests.**
Four checks, one machine with internet, maybe ten minutes — and they move five
components from "logic tested" to "verified".

**2. Run the three live-call selftests, once, on a machine with internet.**
Nothing else on this list matters if the fetchers are broken, and none of them
has ever made a real request — outbound HTTPS was blocked where they were built.

```
geolens geology --selftest
geolens naip --selftest
geolens inundation --selftest
```

Each hits a known area and reports what came back. Five minutes, and it moves
three components from "logic tested" to "verified".

**2. Calibrate on your own data.** The highest-value step that needs no new
code. The confinement breaks and the channel-width relation are both defaults.
`api.calibration_cycle()` runs the whole loop: write a worksheet, fill it in in
GIS, ingest it, refit, and get back `adopt` or `keep_current` with a permutation
null behind the decision, so it can tell you the feature does not separate the
classes at all rather than fitting noise.

**3. Field-check the valley types.** The crosswalk is built and its logic is
tested exhaustively, but the gradient breaks (0.5%, 2%, 4%, 10%) are an
encoding of Rosgen's descriptive classes rather than values he published. Type
a dozen valleys in the field, compare, and move the breaks. `basis` on every
segment says what decided it, so disagreements are diagnosable rather than
mysterious.

**4. Ground-truth the riparian band.** It is verified against a synthetic scene
and has never seen a field-mapped riparian boundary. The NAIP cache exists for
exactly this: the tiles are kept under `<output>/naip/` with their flight year
in the filename, so the imagery you classified against is still there when you
go back to check it.

**5. Deployment.** Notebooks are the cheapest distributable path and are
testable. A QGIS Processing plugin is the nicer distribution but should only be
written where a QGIS install is available to run it against.

**Deliberately not built:** 3DEP and SSURGO fetchers, because `py3dep` and
`pygeohydro` already do exactly that by geometry. Knickpoint detection from
scratch, because TopoToolbox implements the published method and the
hand-rolled version was wrong. Read `NEXT.md` for the full survey of what
exists already and should not be rebuilt.
