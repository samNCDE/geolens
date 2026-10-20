# geolens — start here

*Version 0.30 (beta). What it does, how to run it (from ArcGIS Pro, Jupyter or
QGIS), and how much to trust each result. About ten minutes to read.*

geolens maps valley and channel form from a lidar DEM, built for the arid
Southwest. It is open source (MIT), runs from a Jupyter notebook on your own
machine, and needs no ArcGIS licence. Everything it makes is an ordinary
GeoPackage, GeoTIFF, CSV or PNG that opens in QGIS or ArcGIS Pro.

---

## What it does

One **landscape pass** takes a DEM through these steps. Each step writes its
own folder under your output folder.

| Step | What it works out | Main output |
|---|---|---|
| 1. Terrain | Fills pits, routes flow, finds the stream network, computes HAND (height above the nearest drainage) and slope | `terrain_dir/`; the flow routing is cached, so a second run on the same DEM skips it |
| 2. Valley bottom | Low ground connected to the network, from HAND plus slope evidence | `valley_dir/valley_bottom.gpkg` |
| 3. Segments and confinement | Cuts the network into ~500 m reaches and compares valley width with channel width (the confinement ratio) | `classified_dir/segments_classified.gpkg` |
| 4. Valley form, alluvial fans and Rosgen valley types | Cross-sections from the valley floor up to the divides give floor width, side slopes and shape; a fan search finds cones below canyon mouths (Type III); with geology, these give Rosgen valley types I–XI | `valley_types_dir/valley_types.gpkg`, `valley_extent_dir/valley_extent.gpkg`, `fans_dir/` |
| 5. Rosgen stream types | Five channel cross-sections per reach give bankfull width and depth, width/depth ratio and entrenchment; with slope and sinuosity, these give a Rosgen letter (A–G) | `stream_types_dir/` — reaches, sections, bank points, **a plot of every section** |
| 6. Stable or unstable form | Each reach's stream type judged against its valley type: **stable**, **unstable**, **check** or **unknown** (Rosgen's valley–stream type associations, NRCS NEH 654 TS3E) | `stability_dir/stream_stability.gpkg` |
| 7. (separate cells) | Gullies, NAIP imagery and NDVI, a riparian band | `gullies_dir/`, `naip_dir/`, `riparian_dir/` |

Every run also logs its settings, timings and output files to `runs/` inside
the output folder. When you are asking "which settings made this layer?",
that folder has the answer.

## How much to trust it

This is the honest state as of v0.28, from Sam's review of the Cherokee Bill
and Ruidoso runs and from checks against Moody et al. (2003).

| Result | Status | How to use it |
|---|---|---|
| Valley bottom and valley extent | **Working well** | Use as mapped. |
| Rosgen valley types | **Working well** | Without geology, types II/V/VI and VIII–XI stay unresolved on purpose; they list their candidates instead of guessing. |
| Alluvial fans (Type III) | **New in v0.29, checked on synthetic fans only** | A reach is Type III when half of it lies on a detected fan. Only cone-shaped regions count as fans. On Ruidoso (10 m) all 26 candidates were strips of trunk-valley floor below tributary mouths, so it found none. Review `fans_dir/fan_candidates.gpkg`, where every candidate has the reason it was kept or turned down. |
| Confinement classes | **Good once calibrated** | The default class breaks (2 and 5) rarely transfer between landscapes. Set them from your own percentiles in notebook step 6. |
| Rosgen stream types | **Screening only** | Bankfull comes from the regional curve's depth (v0.28); check the section plots before relying on a type (see below). |
| Stable / unstable form | **As good as the two types it compares** | `form_confidence` is the lower of the two. A `check` is a reach whose stream type is not one Rosgen associates with its valley type: a real departure, or a wrong type. Look before calling it unstable. |
| Gullies | **Experimental** | In steep mountain terrain most "gullies" are headwater ravines. Treat widths, depths and volumes as unreliable. The polygon layer is now off by default. |

**Why stream types are screening only.** Bankfull is hard to see on a DEM.
The first method (to v0.27) raised the water from the lowest point of each
section until the width jumped. On Ruidoso that picked depths a median
2–2.6× Moody et al.'s (2003) New Mexico field bankfull in drainages under
20 km² — usually the top of a small incised channel, or a low-flow thread in a
wide sandy wash — and those too-deep picks inflated the entrenchment ratio as
well.

**Since v0.28 bankfull is the regional curve's depth** (Moody's, for the
drainage area) above the lowest point, and the DEM supplies the width and
shape at that depth. Where that depth would spill a small channel onto a flat
floor, the depth is lowered until the width is plausible. It is consistent
from section to section and matches Moody's field width/depth better, but it
leans on the curve, so no reach it types is rated above *medium* confidence,
and it has not been checked against surveyed bankfull on this ground. Wide
sandy washes now come out D (braided) and small incised channels B, where the
old method said C. The old picker still runs as a check: purple dashed on
every section plot, `picker_*` columns, `picker_bank_top` points. Where the
two disagree, look. `bankfull_method="picker"` brings the old behaviour back.

## Install (Windows; you need to be an admin on the machine)

1. Unzip the folder somewhere permanent, e.g. `Documents\Tools\geolens`.
2. Double-click **`install_windows.bat`**. The first time takes 5–15 minutes,
   mostly downloading. It builds its own Python environment, tests itself on
   a synthetic valley with a known channel, and prints the command that
   starts the notebook.
3. **Updating:** unzip the new version anywhere and run its installer. It
   finds the existing install, updates the code in place, and leaves the
   environment alone.

**Or portable, on an external drive.** Unzip onto the drive and
double-click **`setup_portable.bat`** instead. It puts geolens's own Python
in `env` on the drive, about 3 GB, and needs no admin rights. Nothing is
installed on the computer, and temporary files and caches stay on the
drive, so the drive can be passed to a colleague whose computer has ArcGIS
Pro.

**Then, to run a whole project on its own:** drag the project's boundary
onto **`run_project.bat`**. No ArcGIS needed; the results go in a folder
beside the boundary.

[INSTALL.md](INSTALL.md) covers the manual route and what to do when the
install fails.

## Run it

1. Start Jupyter with the command the installer printed, then open
   **`notebooks/geolens_start.ipynb`**.
2. **Edit the one cell in step 1:**
   - `DEM`: your DEM. Lidar at 0.5–2 m is what the stream types need.
   - `OUT_DIR`: must be *outside* your data folders; geolens refuses to write
     inside them.
   - `GEOLOGY = "auto"`: downloads USGS surficial geology.
   - `REGIONAL_CURVE = "arid_sw_nm"`: Moody et al. 2003, eastern Arizona /
     New Mexico. Use `"arid_sw_az"` for central and southern Arizona.
3. Run the cells top to bottom. Step 2 estimates memory before anything
   expensive runs. If the DEM will not fit, step 3 coarsens it for you.
4. **Step 6: calibrate the confinement breaks.** It takes seconds and nothing
   is rerun.

**What to expect.** On a 128 GB workstation:

- **Ruidoso** (1.785 billion cells at 0.76 m): the landscape pass took
  **72 minutes** with the flow routing already cached. Stream types took
  7.5 minutes, about 6 of them drawing 4,280 section plots and 856 reach
  sheets.
- **Cherokee Bill** (234 million cells at 0.61 m): the landscape pass took
  12 minutes.

Always check the printed `WARNING:` lines. They explain what a number
means, for example "this DEM is in US survey feet" or "these sections are
lower bounds".

## Or run it from ArcGIS Pro (toolbox)

The same steps as geoprocessing tools. Install geolens first (above). The
tools run geolens in the environment the installer built, so nothing is
installed into ArcGIS Pro's Python and a DEM too big for memory cannot crash
ArcGIS.

1. **Add the toolbox once:** in the Catalog pane, right-click *Toolboxes ▸
   Add Toolbox* and choose `arcgis_toolbox\geolens.pyt` in the geolens
   folder.
2. **Check it:** run *0 Setup ▸ Find the geolens environment*. It should
   report "geolens 0.37.0 found". If it can't find the environment, give it
   the environment's `python.exe`, which the installer prints. The tool
   remembers it.
   *0 Setup ▸ Check internet services* says which download services this
   computer can reach.
3. **A whole project area** (a field office, a ranch): *1 Whole routine ▸
   Run a project area (by HUC12)*. Give it the boundary, or a map-service
   URL and a where clause, plus an empty project folder. It maps valleys and
   valley types over the whole area at 10 m. Then it does stream types and
   stable/unstable form one HUC12 at a time where there is 1 m lidar,
   starting with a pilot: a few HUC12s that together represent the area
   (by valley types and soils), or, for stream projects, the HUC12s with the
   most unconfined valley. It also fetches, per HUC12, fire history,
   ecological sites, PRISM climate, Atlas 14 storm depths and RAP
   vegetation cover, and writes one summary row per HUC12 (`huc12_summary`).
   `RESULTS.md` in the folder says what ran at what resolution. Run it again
   to carry on.

   **Or run the whole routine** on one DEM (*1 Whole routine*), or **one step
   at a time** (*2 Steps*):

   | Tool | What it does |
   |---|---|
   | 1. Terrain, valley bottom and confinement | The preprocessing: routing, HAND, valley bottom, reaches. Run it once. |
   | 1b. Calibrate confinement breaks | Run it with the breaks blank to see this landscape's percentiles and five candidate splits. Run it again with the pair you choose: it re-types the valleys without re-measuring anything. |
   | 2. Valley form, alluvial fans and valley types | Reads the run folder from step 1. |
   | 3. Stream types | Reads the run folder. Also re-judges stable/unstable form. Give it a new *Output name* to keep the old stream types. |
   | 4. Stable or unstable form | Valley types against stream types. |
   | 5. Gullies (experimental) | Needs lidar. Checks memory first. |
   | 6. Riparian band from NAIP imagery | Downloads 1 m NAIP over the run, then maps NDVI above the upland background, near a channel. Needs internet. |

   *3 More tools* has:
   - the alluvial fan search on any DEM (it can re-type a run's valleys with
     the fans it finds)
   - clipping a DEM to one watershed
   - headcuts (knickpoints)
   - section plots for an earlier run
   - cross-sections along a centerline

   *0 Setup* has the memory check, and *Coarsen a DEM* for a DEM that won't
   fit (below).

   *0 Get data* has:
   - **Fetch a DEM (USGS 3DEP):** point it at a polygon. It uses 1 m lidar
     where a project covers the area and the 1/3 arc-second DEM (about 10 m)
     where none does, and says how much of each. Tiles go to `dem_tiles` and
     the mosaic to `dem_mosaic.tif`, in the folder you choose. The output is
     in the lidar's UTM zone unless you pick a coordinate system; on a foot
     coordinate system the elevations are converted to feet as well.
   - **NAIP imagery and NDVI:** an NDVI tile and an imagery tile for each
     scene, plus an NDVI mosaic and a true-colour mosaic, all in the folder
     you choose. Newest imagery goes on top. A folder that already has the
     tiles is reused. Step 6 does the same into a NAIP folder of your
     choice before mapping the riparian band.
   - **SSURGO soils:** USDA soil map units over the area, each with the
     material it formed in (alluvial, colluvial, residual...). SSURGO is
     mapped finely enough to show a narrow valley's own alluvium, which the
     national geologic map often misses. Give the layer to step 2 as the
     geology layer, or pick *Download USDA SSURGO soils* there. New and not
     yet tried against USDA's servers.
   - **Sentinel-1 radar statistics (OPERA RTC):** 30 m radar over the area
     for a period (default the last year): how strong and how steady the
     backscatter is. It is for telling cottonwoods from hay fields, which
     NDVI cannot. Give it polygons, such as the riparian band, to get the
     numbers per polygon. It needs your own free NASA Earthdata login,
     saved once with *0 Setup ▸ Save Earthdata login*. New and not yet
     tried against NASA's servers.

   **Clip to one watershed:** the whole routine and step 1 can clip the DEM
   to one complete watershed first. Give it a pour point layer (its first
   point, snapped to the largest drainage nearby), a boundary polygon, or a
   HUC12 code.

   **A DEM too big for memory:**
   1. Run *Coarsen a DEM*. With the cell size left at 0 it uses the size the
      memory check suggests.
   2. Run the routine on the coarse copy, with the full lidar as the *fine
      DEM*. Valleys and routing don't need lidar; the channel sections do,
      and they read the fine DEM.
   
   There is deliberately no tool for running the routine in tiles: splitting
   flow routing across tiles cut 97% of the stream network on the test
   landscape.
4. **Always set the output folder.** For steps 2–4, the *run folder* is the
   output folder of step 1 or of the whole routine. Redoing a step replaces
   that step's layers in the run folder; nothing earlier is rerun.
5. Progress shows in the geoprocessing pane and *Cancel* stops the run.
   geolens's warnings appear as tool warnings.

The layers are added to the map already coloured.

**Plots are geodatabase attachments.** By default each tool copies its layers
into `geolens.gdb` in the run folder and attaches every feature's plot. Click
a section, reach or stability reach with the *Explore* tool and its plot is in
the pop-up, with nothing to set up. The attachments go with the layer if you
share or publish it. The GeoPackages stay alongside for QGIS. Untick *Store
layers in a file geodatabase* to get GeoPackage layers with an image pop-up
instead. The toolbox has been tested against a stand-in for arcpy, not yet
in ArcGIS Pro itself. If the colours or pop-ups don't appear, the layers are
still correct: set the pop-up by hand (below) and tell Sam what happened.

The notebook can do everything the toolbox does, step by step, with the
numbers printed as it goes. A QGIS plugin with the same tools is in
`qgis_plugin` (*Plugins ▸ Manage and Install Plugins ▸ Install from ZIP*,
`qgis_plugin\geolens_qgis_plugin.zip`). From v0.29 new tools go into the
ArcGIS toolbox first.

## Look at the results

**QGIS.** Drag the GeoPackages in.

- `stream_type_sections.gpkg` comes styled: sections are coloured by what
  happened to their bank pick.
  - Blue: the pick was used.
  - Orange, amber or purple: the pick was rejected, with the reason in
    `status`.
  - Grey: the DEM shows no bank.
- **Turn on *View ▸ Show Map Tips* and hover over a section to see its
  plot.**
- Or pick the *Run Feature Action* tool and click a section to open the plot
  full size.
- `stream_types.gpkg` works the same way and shows a sheet of each reach's
  five sections.

**ArcGIS Pro.** Layers the toolbox adds come with this pop-up already. For a layer you
add yourself, set it up once and save it as a layer file:

1. Add `stream_type_sections.gpkg`. In the Add Data dialog, pick the layer
   `main.stream_type_sections`.
2. In the Contents pane, right-click the layer ▸ **Configure Pop-ups**.
3. At the top of the pane, click **Image**. Edit the new Image element and set
   **Source URL** to `{plot_path}`. To open the full-size plot on click, set
   **Link URL** to `{plot_path}` as well. A title such as
   `xs {xs_id} - {status}` helps.
4. Drag the Image element above the Fields element and close the pane.
5. Click a section with the **Explore** tool. The pop-up shows its plot;
   resize the pop-up window to enlarge it.
6. Right-click the layer ▸ **Sharing ▸ Save As Layer File** to keep the
   setup. Do the same for `stream_types.gpkg` to get the reach sheets.

The paths in `plot_path` are full local paths. Esri documents that pop-up
images can come from local paths, but not UNC or relative paths, so the
output folder must be on a local drive. The images will not survive
publishing to ArcGIS Online.

**Browser.** Open `stream_types_dir/stream_type_plots.html` to see every
reach sheet on one page. You can filter it by reach id, type or
`no_type_fits`.

**Reading a section plot.**

- Everything is drawn *looking downstream*, with compass directions at both
  ends so it matches the map.
- The left panel is the whole section; the right panel is the channel close
  up.
- Blue fill: bankfull. Green dashed line: the flood-prone stage (twice the
  bankfull depth). Purple dashed with hollow circles: where the bank picker
  would have put bankfull.
- Black bar: the channel width the regional curve expects.
- Faint dotted lines: other levels the picker considered.
- **If the purple line sits well above the blue fill, the ground has a
  deeper channel than the curve expects** (incised, or a terrace): look at
  the section before trusting the type.

**Earlier output folders.** To add plots to an output folder made before
v0.26, run `api.plot_stream_sections(r"...\stream_types_dir")`. It needs no
DEM and takes about a minute per 500 sections.

## What is in the output folder

```
terrain_dir/          slope.tif (flow routing is cached in .geolens_cache next to the DEM)
valley_dir/           valley_bottom.gpkg, valley_core.gpkg, evidence.tif
network_dir/          segments.gpkg (the reaches)
classified_dir/       segments_classified.gpkg: confinement ratio and class per reach
reclassified_dir/     the same, with your calibrated breaks
valley_form_dir/      valley_form_sections.gpkg: sections divide to divide; valley_form_floors.gpkg
valley_extent_dir/    valley_extent.gpkg: each reach's valley, floor to ridgetop
valley_types_dir/     valley_types.gpkg: Rosgen valley type per reach
fans_dir/             fans.gpkg (accepted fans), fan_candidates.gpkg (every candidate, with the reason)
stability_dir/        stream_stability.gpkg: valley type, stream type, stable/unstable
stream_types_dir/     stream_types.gpkg (reaches), stream_type_sections.gpkg,
                      stream_type_banks.gpkg (bank and flood-prone points),
                      stream_type_profiles.csv (every section's elevations),
                      section_plots/, reach_plots/, stream_type_plots.html
gullies_dir/          gullies.gpkg (lines)
runs/                 what was run, with which settings, how long it took
```

## If something goes wrong

- **Out of memory:** read the top of [RUNNING.md](RUNNING.md). In short:
  trust the step 2 estimate, and let step 3 coarsen the DEM. Stream types
  still measure channels on the full-resolution DEM if you set `DEM_FINE`.
- **A number looks wrong:** send the `runs/` folder (small JSON files) and the
  layer involved. Each run record holds the settings and warnings that
  explain most surprises.

## More detail

- [OVERVIEW.md](OVERVIEW.md): what each step does and what to trust, in more
  depth.
- [RUNNING.md](RUNNING.md): every step and parameter, with tuning tables.
- `docs/geolens_technical_summary_v0.28.pdf`: the methods and references,
  including the bankfull method and the stable/unstable table.
- [NEXT.md](NEXT.md): the working log — what changed in each version and what
  is known to be weak.
