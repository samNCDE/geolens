# ArcGIS Pro test checklist (v0.30 beta)

About 30 minutes, on the Eagle Creek DEM (`DEM\EagleCreekDEM.tif`: 108
million cells at 0.61 m, 40 km²). It runs every tool once. Use a new output
folder, for example `outputs\v30_test`.

For each step, note what happened: did it run, did the layers appear, and
did the pop-up show the plot. If a tool fails, copy the red message from the
geoprocessing pane. That message plus the run folder's `runs\` subfolder is
all that is needed to fix it.

| # | Tool | Settings | Expect |
|---|---|---|---|
| 1 | 0 Setup ▸ Find the geolens environment | (none) | "geolens 0.30.1 found" |
| 2 | 0 Setup ▸ Check memory before a run | DEM = Eagle Creek | "FITS comfortably", about 10 GB |
| 3 | 2 Steps ▸ 1. Terrain, valley bottom and confinement | DEM; output folder `v30_test` | A few minutes. *Valley bottom* and *Reaches (confinement)* added, coloured. |
| 4 | 2 Steps ▸ 1b. Calibrate confinement breaks | Run folder `v30_test`, breaks blank | A percentile table and five candidate splits in the messages. No layers. |
| 5 | Same tool again | Pick a pair from step 4 | *Reaches (calibrated confinement)* added |
| 6 | 2 Steps ▸ 2. Valley form, alluvial fans and valley types | Run folder; geology Download | *Valley types*, *Valley extent* and *Fan candidates* added. Probably no fans, since Eagle Creek is a mountain valley. |
| 7 | 2 Steps ▸ 3. Stream types | Run folder (the fine DEM is left blank: Eagle Creek is already lidar) | *Stream types*, *Stream-type sections* and *Stable / unstable form* added. **Click a section with Explore: the pop-up should show its plot** (a geodatabase attachment). |
| 7b | 2 Steps ▸ 4. Stable or unstable form | The two layers from step 7; output folder `v30_test`; association table = a copy of `docs\valley_stream_associations.csv` with one letter moved (e.g. G from *unstable* to *stable* for VIII) | "judged with: custom table …", and the changed reaches recoloured. |
| 8 | 2 Steps ▸ 6. Riparian band from NAIP imagery | Run folder; NAIP folder `v30_test\naip_test` | Needs internet. NDVI tiles, an imagery tile and two mosaics in that folder, and *Riparian band* added. |
| 9 | 0 Get data ▸ Fetch a DEM (USGS 3DEP) | Area: draw or pick a small polygon (1–2 km²) | `dem_tiles` and `dem_mosaic.tif`. The messages say how much is 1 m lidar. |
| 10 | 3 More tools ▸ Clip a DEM to one watershed | DEM; pour point = a point on a channel | *Watershed boundary* and the clipped DEM |
| 11 | 1 Whole routine ▸ Run the whole routine | Eagle Creek; new folder `v30_whole` | Every layer from steps 3–7 at once |

Things most likely to need a fix (they could not be tested without ArcGIS
Pro):

- **The colours** (step 3 onward). If layers appear in a single default
  colour, the styling call failed; the data is still right.
- **The pop-up plots** (step 7). If the pop-up shows no image, select the
  feature and look in *Edit ▸ Attributes ▸ Attachments*. Say whether the PNG
  is there.
- **Cancel.** Start step 3 and press Cancel: the tool should stop within a
  few seconds.
- **The live services** (steps 6, 8, 9): The National Map and the Planetary
  Computer through NCDE's network.
