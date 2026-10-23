---
title: "geolens v0.45 — how it works"
subtitle: "Valley and channel geomorphology for a whole project area, in three pages"
date: "23 October 2026"
---

**What it does.** geolens takes a project boundary — a BLM field office, a ranch, a district — and returns, reach by reach, what the valleys and channels are and whether each channel is a stable form for its valley, as GIS layers and one summary row per HUC12 watershed. It runs from an ArcGIS Pro toolbox, a command window or a notebook, downloads everything it needs from public services (USGS 3DEP, the Watershed Boundary Dataset, SSURGO, NIFC, PRISM, NOAA Atlas 14, RAP), and needs no ArcGIS licence for the work itself. Every result says what it rests on: its confidence, its basis and the resolution it was measured at.

# 1 A project run

![The steps of a project run. Each is recorded in `project.json` as it finishes, so a run that stops carries on from the last one done.](geolens_project_flow.svg){ width=92% }

1. **HUC12s.** The boundary is widened to every HUC12 it touches plus those draining into them, so no stream is cut off at the boundary line.
2. **Level A, the whole area at 10 m** (3DEP 1/3 arc-second). Flow is routed, a stream network traced (1 km² to start a stream) and cut into 500 m reaches, and valley bottoms are mapped from height above the nearest drainage and slope. Each reach's valley is then measured by cross-sections from floor to divide, alluvial fans are found (from the DEM and the soils map's landforms), and each reach gets a confinement class and a valley type.
3. **Level B, one HUC12 at a time on 1 m lidar.** Five channel cross-sections per reach on the lidar give bankfull width, depth and entrenchment, hence a Rosgen stream type (A–G) and a section plot; the stream type is then judged against the valley type: **stable**, **unstable** or **check**. Level B's lidar also **calibrates the 10 m confinement classes** to what the lidar measured.
4. **Valleys on the lidar at 3 m (optional).** Level A's valley pass again on each HUC12's lidar resampled to 3 m, six HUC12s at a time, with the drainage from upstream carried in from the 10 m network, and each HUC12 compared with the 10 m result.
5. **The watershed data per HUC12**: fire history, ecological sites, PRISM climate, Atlas 14 storm depths, and RAP vegetation cover with its trends.
6. **Merged, summarised, ranked.** Layers for the boundary; `huc12_summary` (one row per HUC12: valley and soil makeup, slope, stream form, the watershed data); every reach's stream power and the stream it is on; `major_streams` (one line per stream draining 25 km² or more, named from the NHD); `huc12_sensitivity` (the HUC12s ranked by weighted percentile ranks, the weights written beside it); and `RESULTS.md`, which says what ran at what resolution, how far to trust it, and every warning.

# 2 The methods in brief

| Result | How it is made |
|---|---|
| Valley bottom and extent | A VBET-style evidence surface (height above the nearest drainage and slope, both scaled with drainage area); each reach's share of the bottom, as a polygon carrying its types. |
| Confinement | Valley floor width (from the cross-sections) over channel width (from the regional curve); the class breaks fitted to the lidar's entrenchment on the project's own reaches. |
| Valley type | Rosgen's 2022 fluvial landscape type (e.g. `U-AL-AD`, `U-AL-IF`, `C-CO-VS`) from confinement, gradient, the floor's material (SSURGO), fans and valley shape; the 1996 type I–XI beside it. What the data cannot separate stays open, with the candidates listed. |
| Stream type | Bankfull at the regional curve's depth (Moody et al., 2003, in the arid Southwest) on lidar sections; width/depth, entrenchment, slope and sinuosity through Rosgen's key, the nearest type taken within Rosgen's tolerances. |
| Stable / unstable form | The stream type against the stream types Rosgen associates with the valley type (the 2022 card, and NRCS NEH 654 TS3E for the 1996 types). A *check* means the pairing is not one Rosgen lists: look at the section plots. |
| Stream power, sensitivity | Bankfull discharge from the regional curve, stream power from it and the reach gradient; the sensitivity table ranks the HUC12s on erodibility, runoff-prone soils, slope, alluvial valleys, unstable form, stream power, bare ground and its trend, recent fire and storm depth. |

# 3 What is new in v0.45

**Whole 1 m lidar mosaics.** The lidar download used to skip an older or overlapping tile whenever a newer tile's outline covered it — but a tile's outline is its 10 km square whatever the project flew. Where lidar projects overlap, or where a project meets a UTM zone line and files the same squares in both zones (Kingman, at 114° W), the mosaics had holes: 14–70% filled on the first HUC12s checked, and level B's sections fell on empty ground ("unknown" reaches, and four HUC12s that failed). Now a tile is skipped only when its cells already have data, every download records the share of the area that has lidar (`lidar_data`), and runs made before the fix are measured again where their mosaics skipped a tile. On the first eight HUC12s re-measured for Kingman, the reaches without a stable/unstable call fell from 294 to 9.

**Long runs from the toolbox.** *Run a project area* can now start a run **in its own window**: ArcGIS Pro stays free, the run is not tied to it, and `run_geolens.bat` in the project folder carries it on after a reboot or a crash. *Check on a project run* says whether a run is working and how long is left, and *Add a project's results to the map* adds a finished project's layers, named and coloured. The project tool also takes the 3 m pass's settings and can start from an earlier project's measurements without changing them.

# 4 Running it

1. Install once (`install_windows.bat`, or `setup_portable.bat` for a portable drive) and add `arcgis_toolbox\geolens.pyt` to ArcGIS Pro.
2. *1 Whole routine ▸ Run a project area (by HUC12)*: a boundary layer (or a map service and a where clause), a new project folder, *Every HUC12 with lidar*, and **Run in its own window** for anything that takes hours.
3. *Check on a project run* while it works; *Add a project's results to the map* when it has finished; read `RESULTS.md` first.

From a command window: `geolens project boundary.gpkg --out S:\project\run1 --level-b all`, then `geolens status S:\project\run1`. A field office takes about a day: two hours for level A, about five minutes a HUC12 for level B, a few hours for the 3 m pass.

# 5 How far to trust it

| Result | Use it as |
|---|---|
| Valley bottoms, valley types | Mapped results with their confidence; 10 m resolves valleys three or more cells wide, the 3 m pass narrower ones where there is lidar. |
| Confinement | Calibrated to the project's own lidar where level B ran. |
| Stream types, stable / unstable form | Screening: bankfull leans on the regional curve and has not been checked against surveyed bankfull on these areas. Look at the section plots before relying on a reach. |
| Sensitivity ranking | A proposal to argue with: the weights are in a file, and changing them re-ranks the HUC12s without re-running anything. |

The full methods, with references, are in *geolens methods v0.45*; the step-by-step guide is *How to use geolens*.
