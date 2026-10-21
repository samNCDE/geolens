# ArcGIS Pro test checklist (v0.40 beta)

About 30 minutes, on the Eagle Creek DEM (`DEM\EagleCreekDEM.tif`: 108
million cells at 0.61 m, 40 km²). It runs every tool once. Use a new output
folder, for example `outputs\v30_test`.

For each step, note what happened: did it run, did the layers appear, and
did the pop-up show the plot. If a tool fails, copy the red message from the
geoprocessing pane. That message plus the run folder's `runs\` subfolder is
all that is needed to fix it.

| # | Tool | Settings | Expect |
|---|---|---|---|
| 1 | 0 Setup ▸ Find the geolens environment | (none) | "geolens 0.40.0 found" |
| 2 | 0 Setup ▸ Check memory before a run | DEM = Eagle Creek | "FITS comfortably", about 10 GB |
| 3 | 2 Steps ▸ 1. Terrain, valley bottom and confinement | DEM; output folder `v30_test` | A few minutes. *Valley bottom* and *Reaches (confinement)* added, coloured. |
| 4 | 2 Steps ▸ 1b. Calibrate confinement breaks | Run folder `v30_test`, breaks blank | A percentile table and five candidate splits in the messages. No layers. |
| 5 | Same tool again | Pick a pair from step 4 | *Reaches (calibrated confinement)* added |
| 6 | 2 Steps ▸ 2. Valley form, alluvial fans and valley types | Run folder; geology Download | *Valley types*, *Valley extent* and *Fan candidates* added. Probably no fans, since Eagle Creek is a mountain valley. |
| 7 | 2 Steps ▸ 3. Stream types | Run folder (the fine DEM is left blank: Eagle Creek is already lidar) | *Stream types*, *Stream-type sections* and *Stable / unstable form* added. **Click a section with Explore: the pop-up should show its plot** (a geodatabase attachment). |
| 7b | 2 Steps ▸ 4. Stable or unstable form | The two layers from step 7; output folder `v30_test`; association table = a copy of `docs\valley_stream_associations.csv` with one letter moved (e.g. G from *unstable* to *stable* for VIII) | "judged with: custom table …", and the changed reaches recoloured. |
| 8 | 2 Steps ▸ 6. Riparian band from NAIP imagery | Run folder; NAIP folder `v30_test\naip_test` | Needs internet. NDVI tiles, an imagery tile and two mosaics in that folder, and *Riparian band* added. |
| 8b | 0 Setup ▸ Save Earthdata login | Your Earthdata username and password | "Saved the Earthdata login for … in C:\Users\…\_netrc". The password is not shown in the messages. |
| 8c | 0 Get data ▸ Sentinel-1 radar statistics (OPERA RTC) | Area: Eagle Creek DEM; folder `v30_test\sar`; polygons: *Riparian band* from step 8; dates blank (the last year) | Needs internet. Tiles in `sar\sar_tiles`, *Radar statistics* (5 bands) and *Radar statistics per polygon* added. Note how long it took, and what the messages say about streaming or downloading. Symbolise the polygons by `sar_vh_cv`. |
| 8d | 0 Get data ▸ SSURGO soils | Area: Eagle Creek DEM; folder `v30_test\soils` | Needs internet. *Soils (SSURGO)* added, coloured by material: alluvium (yellow) should follow the valley floors. |
| 8e | 2 Steps ▸ 2. Valley form, alluvial fans and valley types | Run folder `v30_test`; geology *Download USDA SSURGO soils* | First export step 6's *Valley types* (this run replaces them). Then compare: which reaches changed type, and which version looks right? |
| 9 | 0 Get data ▸ Fetch a DEM (USGS 3DEP) | Area: draw or pick a small polygon (1–2 km²) | `dem_tiles` and `dem_mosaic.tif`. The messages say how much is 1 m lidar. |
| 10 | 3 More tools ▸ Clip a DEM to one watershed | DEM; pour point = a point on a channel | *Watershed boundary* and the clipped DEM |
| 11 | 1 Whole routine ▸ Run the whole routine | Eagle Creek; new folder `v30_whole` | Every layer from steps 3–7 at once |
| 12 | 1 Whole routine ▸ Run a project area (by HUC12) | A small boundary (one ranch, 1–3 HUC12s); new folder `v30_project`; Level B *None: valleys only*; Watershed data *All* | Needs internet, 20–40 minutes. *Valley types*, *HUC12 status*, *HUC12 summary*, *Soils (SSURGO)*, *Soils with ecological sites* and, if anything burned, *Fire perimeters* added, and *Major streams* (one line per stream over 25 km², symbolise by `drainage_km2`) and *HUC12 sensitivity* (coloured by class; with 1–3 HUC12s it says "fewer than 3" and skips the ranking). `RESULTS.md` has a "Watershed data by HUC12" table with every step done and a "Stream power, major streams and the sensitivity table" section; `data\` holds `fire_huc12.csv` … `rap_huc12.csv`. Open *HUC12 summary*'s table: `prism_ppt_mm`, `atlas14_24h_100yr_mm`, `rap_bgr_recent` filled; *Valley types* now has `stream_id`, `stream_power_w_m`, `unit_stream_power_w_m2`. |

Things most likely to need a fix (they could not be tested without ArcGIS
Pro):

- **The colours** (step 3 onward). If layers appear in a single default
  colour, the styling call failed; the data is still right.
- **The pop-up plots** (step 7). If the pop-up shows no image, select the
  feature and look in *Edit ▸ Attributes ▸ Attachments*. Say whether the PNG
  is there.
- **Cancel.** Start step 3 and press Cancel: the tool should stop within a
  few seconds.
- **The live services** (steps 6, 8, 8c, 8d, 9): The National Map, the
  Planetary Computer, USDA Soil Data Access and NASA Earthdata (CMR and
  ASF) through NCDE's network. For step 8c, a "refused the saved login" message means the
  username or password is wrong, or the account must sign in once at
  search.asf.alaska.edu.
