---
name: geolens
description: Inspect geospatial datasets and extract channel geometry from a DEM — describe shapefiles/geodatabases/rasters, cut cross-sections and longitudinal profiles along a centerline, and delineate channel, banks and floodplain by slope break. Use when the user asks about GIS data, a DEM, a stream or channel, cross-sections, bankfull geometry, floodplain extent, entrenchment, or points at a .shp/.gdb/.gpkg/.tif file.
---

# Working with geospatial data

This skill covers two jobs that look similar and are not: **examining data** and
**measuring geometry**. Most requests are the first. Do not jump to analysis.

## Before anything else: look, then reason

When the user points at data, the first move is always to describe it — never to
assume the schema, the units, or the CRS. The tools exist so you can look.

1. `list_datasets` on the folder — what is actually there.
2. `describe_vector` / `describe_raster` on the relevant file — CRS, extent,
   fields, cell size, nodata.
3. `attribute_summary` before `sample_features` — know the shape of the
   distribution before staring at five arbitrary rows.
4. `plot_map` when position or context matters. You can look at the PNG.

Report what you found in plain terms. If the user just wanted to think about
their data with you, **stop here** — that is the whole task.

## CRS hygiene — check this every time

Nearly every wrong answer in geospatial work traces back to a CRS problem, and
the tools surface the two that matter:

- **Geographic CRS (degrees).** Cell size, station spacing, section width, slope
  and area are all meaningless in degrees. If `is_geographic` is true, say so and
  recommend reprojecting to a projected CRS before any measurement. Do not
  quietly report numbers you know are distorted.
- **Mismatched CRS.** Centerline and DEM in different systems are reprojected
  automatically, and the result says so in `warnings`. Pass that on — a silent
  reprojection the user did not expect is worth one sentence.

Always read the `warnings` array and relay anything in it. It is there because it
changes how the numbers should be read.

## Measuring channel geometry

Three tools, in increasing order of what they assume:

**`longitudinal_profile`** — elevation along the centerline. Returns reach slope
from a least-squares fit over all samples, which is more robust than
endpoint-to-endpoint on a noisy DEM. Use `spacing` at or near the DEM cell size;
finer buys nothing real.

**`cross_sections`** — perpendicular sections at fixed stations. Offset is signed:
negative left of the direction of travel, positive right. `width` must be wide
enough to capture what you intend to measure; `point_spacing` defaults to the
cell size, which is the finest spacing carrying information.

**`delineate_channel`** — cuts sections and classifies each one by slope break,
walking outward from the thalweg:

```
flat bed  →  steep bank face  →  bank top  →  flat floodplain  →  valley wall (STOP)
```

The valley-wall test is the point: it closes the floodplain where terrain resumes
a sustained climb, so the floodplain does not run up a confining hillslope
forever. Sections that reach the section edge without meeting a wall are flagged
— their floodplain width is a **floor, not a measurement**, and you should say so
rather than reporting it as a width.

### Tuning the delineation

All thresholds are slopes as rise/run. Defaults suit a clear channel on a ~1 m
DEM. When results look wrong, change one thing at a time:

| Symptom | Change |
|---|---|
| Banks not found on subtle channels | lower `bank_slope` (0.15 → 0.08) |
| Bed roughness detected as banks | raise `min_bank_height` |
| Noisy lidar producing junk breaks | raise `run_length`, then `smooth_window` |
| Floodplain never closes | check `width` is wide enough; set `max_floodplain_offset` |
| Floodplain closes immediately | raise `valley_slope` above the floodplain's own slope |

`valley_slope` must exceed `flat_slope` or every floodplain closes at once; the
tool refuses that combination rather than returning nonsense.

**Report the parameters you used alongside the numbers.** A bankfull width
without its `bank_slope` is not reproducible.

### What the derived metrics are and are not

`width_depth_ratio` and `entrenchment_ratio` come back because they are standard
classification inputs. They are **geometry from a DEM** — not a field-verified
bankfull determination. A DEM cannot see a bankfull indicator; it sees a slope
break that is usually near one. Say which you are reporting. Never present a
DEM-derived ratio as a channel classification on its own.

## Reading results without drowning in them

Analysis tools return a **handle**. Pass it to `plot_result` and `export_csv`
rather than asking for the full point arrays — a 30-section extraction is tens of
thousands of coordinates, and none of them belong in a conversation.

- `plot_result` with `kind`: `profile`, `cross_section`, `section_stack`,
  `delineation`. It returns the PNG, so **look at it** before describing it.
- `export_csv` with `kind`: `profile`, `sections`, `delineation` when the user
  wants the numbers in a spreadsheet or another tool.

Plot before concluding. A section stack shows in one image what fifty rows of
JSON will not: whether the centerline is actually following the channel, whether
sections are clipping the DEM edge, whether the reach is uniform.

## Landscape scale

`landscape_pass` runs a whole drainage network from a coarse DEM: HAND, valley
bottom, network segmentation, confinement classes. Use it when the question is
"what kind of valleys are out there, generally", not "what is this reach".

Two things to get right, both of which the tool reports rather than hides:

**Calibrate the confinement breaks.** The defaults (2 / 5 channel widths) follow
convention and rarely transfer. Run the pass, read
`summary.confinement_ratio_percentiles`, and pick breaks from the distribution.
Reporting classes off uncalibrated defaults is the main way to be wrong here.

**Do not promise Rosgen types from terrain.** Segments carry `rosgen_candidates`
(often two alternatives) and `needs_geology`. Several Rosgen types differ by
genesis — colluvial, alluvial, glacial, lacustrine — which a DEM cannot see.
Report confinement classes as measured and Rosgen as candidate, flagged.

**Width accuracy depends on how many cells wide the valley is.** Wide valleys
come out under 1 %; a valley only ~10 cells across carries 25–30 % error. Say
which regime a number is in rather than quoting one accuracy for everything.

## Read-only discipline

Source data is never modified. Outputs go to `GEOLENS_OUT`, which is refused if
it sits inside a read root. This is structural, not a convention — you do not
need to be careful, but you should tell the user where outputs went.

Use `workspace` if you are unsure what is readable or where output lands.

## ArcGIS is not required

Everything here runs on GDAL/OGR, rasterio and shapely. Esri File Geodatabases
are read through GDAL's open-source `OpenFileGDB` driver with **no ArcGIS
install**, on any OS. If the user assumes they need Pro open, they do not.

Pass a `.gdb` path plus a layer name; `list_datasets` reports the layers in every
geodatabase it finds.
