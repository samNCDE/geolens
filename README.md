# geolens

**Valley and channel form from a lidar DEM, for the arid Southwest.** geolens
maps valley bottoms and confinement, classifies Rosgen valley types and stream
types, and judges whether each reach's stream type is a **stable or unstable
form for its valley**. Every channel section it measures comes with a plot you
can click on the map.

*Beta (v0.37). Open source (MIT). Runs in ArcGIS Pro, in a Jupyter notebook,
or in QGIS. Windows first; the Python package also runs on macOS and Linux.*

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
| Project runs (by HUC12) | **New; tested offline only.** The first real run is the Kingman Field Office. |

The reasons, with the numbers behind them, are in [START_HERE.md](START_HERE.md).

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
   environment**.

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
  however large.
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
| `docs/geolens_technical_summary_v0.28.pdf` | Methods and references. |
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
