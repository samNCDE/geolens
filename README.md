# geolens

**Valley and channel form from a lidar DEM, for the arid Southwest.** geolens
maps valley bottoms and confinement, classifies Rosgen valley types and stream
types, and judges whether each reach's stream type is a **stable or unstable
form for its valley**. Every channel section it measures comes with a plot you
can click on the map.

*Beta (v0.45). Open source (MIT). Runs in ArcGIS Pro, in a Jupyter notebook,
or in QGIS. Windows first; the Python package also runs on macOS and Linux.*

**New to it? Start with [Run it from ArcGIS Pro](#run-it-from-arcgis-pro)
below, or the 3-page overview `docs/geolens_overview_v0.45.pdf`.**

---

## What it does

| Step | Result |
|---|---|
| 1. Terrain | Pit filling, flow routing, streams, HAND (height above the nearest drainage), slope. The routing is cached next to the DEM, so a second run skips it. |
| 2. Valley bottom and confinement | The valley floor, cut into ~500 m reaches, each with a confinement ratio (valley width / channel width). |
| 3. Valley form, alluvial fans, valley types | Sections from floor to divide (width, side slopes, shape), a fan search, and USGS surficial geology give **Rosgen valley types I–XI** and the valley as an area. |
| 4. Stream types | Five channel sections per reach on the lidar: bankfull at the regional curve's depth (Moody et al. 2003 for AZ/NM), width/depth, entrenchment, slope and sinuosity give a **Rosgen stream type A–G**, with a plot of every section. |
| 5. Stable or unstable form | Each reach's stream type against its valley type (NRCS NEH 654 TS3E, after Rosgen 1996): **stable**, **unstable**, **check** or **unknown**. |
| Projects of any size | Give a boundary (a BLM field office, a ranch): the whole area at 10 m for valleys and valley types, widened to the HUC12 watersheds draining into it, then HUC12 by HUC12 on 1 m lidar for stream types and stable/unstable form. Resumable; every layer says what resolution it was measured at. |
| Stream power, major streams, sensitivity | Every reach gets its bankfull discharge and stream power from the regional curve and the stream it is on; the major streams are one line each with the drainage at the mouth; and a table ranks the HUC12s by weighted percentile ranks of what was measured, with the weights written beside it to be argued with. |
| More | Gullies and headcuts, riparian band from NAIP NDVI, cross-sections along any centerline, watershed clipping, fetching 3DEP DEMs, NAIP imagery and SSURGO soils (finer than the geologic map for valley floors), and Sentinel-1 radar statistics (to tell riparian woodland from irrigated fields). |

## How much to trust it

| Result | Status |
|---|---|
| Valley bottom, valley extent, valley types | **Working well** on the Ruidoso and Cherokee Bill reviews. Without geology, types that differ only by history stay unresolved instead of guessed. |
| Stream types | **Screening only.** Bankfull leans on the regional curve and has not been checked against surveyed bankfull. Check the section plots. |
| Stable / unstable | **As good as the two types it compares.** A *check* is a stream type Rosgen does not list for that valley: a real departure, or a wrong type. |
| Alluvial fans | **New; checked on synthetic fans only.** Only cone-shaped regions count; every candidate is kept for review. |
| Gullies, headcuts | **Experimental.** |
| Sentinel-1 radar statistics | **New; tested offline only.** For an experiment, not yet a classification. |
| SSURGO soils as geology | **New; tested offline only.** Compare its valley types with the USGS geology's before relying on it. |
| Project runs (by HUC12) | **Run on a whole BLM field office** (41,000 km², 446 HUC12s), level B on every HUC12 with lidar. Since v0.45 the 1 m lidar mosaics are whole where lidar projects overlap or meet at a UTM zone line (earlier runs had holes there). |
| Stream power, major streams, sensitivity table | **New; checked on the first Kingman run's reaches.** Stream power is an index from a curve and a 10 m slope, for comparing reaches. The sensitivity weights are a first proposal. |

The reasons, with the numbers behind them, are in [START_HERE.md](START_HERE.md).

## Run it from ArcGIS Pro

### Set up once

1. Unzip the release into a folder of its own, for example
   `Documents\Tools\geolens` or a folder on a data drive. Each new release
   goes in a new folder: a run that is working uses the files of the
   release it was started from.
2. Double-click **`install_windows.bat`** (or **`setup_portable.bat`** for a
   portable drive; see [Install](#install-windows)).
3. In ArcGIS Pro, in the **Catalog** pane, right-click **Toolboxes ▸ Add
   Toolbox** and choose **`arcgis_toolbox\geolens.pyt`** in the release
   folder.
4. Run **0 Setup ▸ Find the geolens environment**. It should report
   "geolens 0.45.1 found". If not, give it the `python.exe` the installer
   printed; it remembers it.
5. Optional: **0 Setup ▸ Check internet services** says which download
   services this computer can reach.

### Run a project area

1. Open **1 Whole routine ▸ Run a project area (by HUC12)**.
2. Give it a **boundary** layer (a ranch, an allotment, a district), or a
   **map service layer URL** and a **where clause** (for a BLM field office:
   `https://gis.blm.gov/arcgis/rest/services/admin_boundaries/BLM_Natl_AdminUnit/MapServer/3`
   with `ADM_UNIT_CD='XXX00000'`), and a new, empty **project folder**.
3. Choose **Level B**: *Every HUC12 with lidar* for the full product, or a
   pilot for a first look. Leave *Valley material* on SSURGO soils and
   *Watershed data* on All.
4. For anything that takes hours (a field office: about a day), tick
   **Run in its own window**. Under *Options* you can also map the valleys
   again on the lidar at 3 m (*Valleys again on the lidar*), keep each
   HUC12's 1 m DEM, or start from an earlier project folder without
   changing it.
5. Click **Run**. With *Run in its own window* the tool finishes in a few
   seconds and a black console window titled *geolens project … - leave
   open* does the work (the launcher, below).

### The launcher: a run in its own window

*Run in its own window* writes the run's settings into the project folder
(`geolens_run.json`) with a launcher beside them, **`run_geolens.bat`**, and
starts the launcher in a console window of its own.

- **Leave the console window open.** Closing it stops the run; everything
  finished so far is kept.
- **ArcGIS Pro is free** while it works, and can be closed: the run does not
  depend on it.
- **After a reboot, a crash or a power cut, double-click `run_geolens.bat`**
  in the project folder. The run carries on from the last finished step or
  HUC12; nothing done is redone.
- If geolens stops with an error, the launcher waits a minute and carries
  on, up to five times. It will not start while another run is working in
  the same folder.
- It runs the code in the release folder the toolbox came from, so do not
  unzip a new release over that folder while a run uses it.
- For a run of a day or more: pause Windows Update, keep the computer
  plugged in. geolens asks Windows not to sleep while it works.
- The same run from a command window:
  `python -m geolens.job <project folder>\geolens_run.json`.

### While it runs, and when it has finished

- **1 Whole routine ▸ Check on a project run** (any time, on the project
  folder): whether a run is working there now, the steps done, the HUC12s
  done, failed and still to go, roughly how many hours are left, and the
  free space. It only reads the folder. From a command window:
  `geolens status <project folder>`.
- **1 Whole routine ▸ Add a project's results to the map** (when it has
  finished): the valley types, the valley extent polygons coloured like
  them, stream types, stable / unstable form, major streams and the HUC12
  summary, sensitivity and status, named and coloured. Run it again to
  re-colour; nothing is added twice.
- Read **`RESULTS.md`** in the project folder first: what ran at what
  resolution, how far to trust it, and every warning.

Step by step, with every option and what to do when something goes
wrong: [`docs/geolens_how_to_use.pdf`](docs/geolens_how_to_use.pdf).

## Install (Windows)

You need admin rights on the machine, and either Miniforge/Anaconda or Python
3.11+.

1. Download the release zip and unzip it somewhere permanent, e.g.
   `Documents\Tools\geolens`.
2. Double-click **`install_windows.bat`**. It builds its own Python
   environment (5–15 minutes the first time), tests itself on a synthetic
   valley, and prints what to do next. Running a newer release's installer
   later updates the code in place and leaves the environment alone.
3. In ArcGIS Pro: **Catalog ▸ Toolboxes ▸ Add Toolbox ▸
   `arcgis_toolbox\geolens.pyt`**, then run **0 Setup ▸ Find the geolens
   environment** ([Run it from ArcGIS Pro](#run-it-from-arcgis-pro)).

Nothing is installed into ArcGIS Pro's own Python. The tools run geolens in
the environment the installer built, so a DEM too big for memory cannot crash
ArcGIS. Manual installs and troubleshooting are in [INSTALL.md](INSTALL.md).

**Portable, on an external drive (nothing on the computer).** Unzip the
release onto the drive and double-click **`setup_portable.bat`**. It builds
geolens's own Python in `env` beside it (about 3 GB, internet needed once),
with no installer, registry or user-profile changes. The toolbox then uses
that `env`, and temporary files, caches and the Earthdata login stay on the
drive. The drive letter can change, so the drive can be handed to someone
else. Their computer needs only ArcGIS Pro.

**A whole project, no ArcGIS needed.** Drag a project boundary (shapefile,
GeoPackage, GeoJSON or KML; a stream line or points are buffered) onto
**`run_project.bat`** and leave it. It runs everything below for the
boundary on its own, fetches the watershed data a plan needs per HUC12
(fire history, SSURGO ecological sites, PRISM climate, NOAA Atlas 14 storm
depths, Rangeland Analysis Platform cover and its trends since 1986), and
writes it all to `<boundary name>_geolens` beside the boundary: the layers,
one summary table per HUC12 of what it is made of and how it is behaving,
and `RESULTS.md` saying what ran and how far to trust it. Run it again to
carry on where it stopped. The same from a shell:

```
geolens project boundary.gpkg
```

**From a laptop, on GitHub's machines.** The repo's *Reconnaissance run*
workflow (Actions) runs a project at 30 m on a free runner and hands the
results back as a download; `--cell-a 30` does the same coarse run on a
laptop.

## Use it

**ArcGIS Pro toolbox** (recommended):

- **0 Get data**: fetch a USGS 3DEP DEM (1 m lidar where it exists), NAIP
  imagery and NDVI, SSURGO soils, and Sentinel-1 radar statistics. Tiles and
  mosaics go to a folder you choose.
- **0 Setup**: find the environment, check which download services this
  computer can reach, check memory before a run, coarsen a DEM that won't
  fit, and save your own NASA Earthdata login (for radar).
- **1 Whole routine**: every step in one tool, optionally clipped to one
  watershed first; or **Run a project area (by HUC12)** for any boundary,
  however large, in its own window for long runs; **Check on a project
  run**; and **Add a project's results to the map**.
- **2 Steps**: the same steps one at a time. Step 1 does the routing once;
  later steps read the run folder, so any step can be redone alone.
- **3 More tools**: alluvial fans, watershed clipping, headcuts, section
  plots, and cross-sections along a centerline.

Layers are added to the map coloured. By default they are stored in the
run's `geolens.gdb` with each plot as a **geodatabase attachment**, so
clicking a section or reach with *Explore* shows its plot with no pop-up
setup. The plots stay attached when the layer is shared.

**Jupyter notebook:** `notebooks/geolens_start.ipynb`. Edit one cell, run top
to bottom. It is also where the confinement breaks are calibrated.

**QGIS:** `qgis_plugin/geolens_qgis_plugin.zip` (*Plugins ▸ Install from
ZIP*) has the main tools. Hover over a section with Map Tips on to see its
plot.

**Python:** `from geolens import api` has the same functions the tools call.
See [RUNNING.md](RUNNING.md).

## Requirements and limits

- **Lidar at 0.5–2 m** for stream types and gullies. Valleys and routing are
  fine on a coarser copy (*Coarsen a DEM*); give the lidar as the *fine DEM*.
- **Memory:** a landscape pass needs roughly 40–90 bytes a cell. Run *Check
  memory* first. On 128 GB, a 1.8-billion-cell Ruidoso DEM ran in 72 minutes
  with the routing cached.
- **Units:** DEMs in US survey feet are handled. Parameters are in metres and
  converted.
- **Internet** is needed for geology, soils, NAIP, DEM and radar downloads.
  Everything else runs offline. Radar also needs a free NASA Earthdata
  login, which each person saves for themselves (*0 Setup ▸ Save Earthdata
  login*). Never put a login in shared code.
- **Not validated against field data.** Treat stream types and stability as
  screening, and look at the plots.

## Documentation

| File | For |
|---|---|
| [START_HERE.md](START_HERE.md) (`.html`) | Colleagues: what it does, install, run, read the results, what to trust. |
| [RUNNING.md](RUNNING.md) | Every step and parameter, the toolbox internals, tuning tables. |
| [OVERVIEW.md](OVERVIEW.md) | How each step works and why. |
| [`docs/geolens_overview_v0.45.pdf`](docs/geolens_overview_v0.45.pdf) | How the latest version works, in three pages. |
| [`docs/geolens_how_to_use.pdf`](docs/geolens_how_to_use.pdf) | Step by step: install, the ArcGIS Pro tools and the launcher, reading the results, troubleshooting. |
| [`docs/geolens_methods_v0.45.pdf`](docs/geolens_methods_v0.45.pdf) | Methods and references (replaces the v0.40 technical summary). |
| [docs/ARCGIS_TEST_CHECKLIST.md](docs/ARCGIS_TEST_CHECKLIST.md) | A 30-minute first run of every toolbox tool, and what to report. |
| [NEXT.md](NEXT.md) | Working log: what changed in each version, known weaknesses, what is next. |
| [docs/SAR_FEASIBILITY.md](docs/SAR_FEASIBILITY.md) | Whether Sentinel-1 radar separates riparian woodland from irrigated fields, and the experiment that decides it. |

## Development

```
python -m pytest tests            # the suite (fixtures are synthetic landscapes)
python tests/qgis/check_plugin.py # the QGIS plugin in a headless QGIS
```

The ArcGIS toolbox is tested against a stand-in for arcpy
(`tests/arcgis/fake_arcpy`) running real geolens; ArcGIS Pro itself is tested
by running the tools. `arcgis_toolbox/geolens_runner.py` and the QGIS
plugin's `runner.py` are the same file, and a test keeps them identical.

## Credits and licence

MIT licence (see [LICENSE](LICENSE)). Methods and data:

- Regional curves: Moody, T., Wirtanen, M. and Yard, S.N. (2003), Regional
  Relationships for Bankfull Stage in Natural Channels of the Arid Southwest.
- Valley and stream types: Rosgen (1994, 1996).
- Valley–stream associations: NRCS National Engineering Handbook 654,
  Technical Supplement 3E.
- Data services: USGS 3DEP and The National Map, USGS surficial geology,
  NAIP on the Microsoft Planetary Computer, USDA-NRCS SSURGO through Soil
  Data Access, and OPERA RTC-S1 (NASA JPL, distributed by ASF; contains
  modified Copernicus Sentinel data).
