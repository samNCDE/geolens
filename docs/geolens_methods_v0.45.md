---
title: "geolens v0.45.0 — methods"
subtitle: "Valley and channel geomorphology from a DEM, for whole project areas: what it does, how it works, and how far to trust it"
date: "October 2026"
---

| | |
|---|---|
| **Version** | 0.45.0 (beta, 23 October 2026). This replaces the v0.40.0 technical summary (21 October 2026); what changed since v0.28 is in Section 1.2. A step-by-step guide to running it is the companion *How to use geolens*. |
| **Licence** | MIT; built on open-source libraries (GDAL, rasterio, geopandas, shapely, pysheds, scikit-image). No ArcGIS licence is needed for anything; the ArcGIS Pro toolbox is one way in. |
| **Built for** | Arid and semi-arid Southwest watersheds, from one lidar DEM to a whole BLM field office. State-plane grids in US survey feet are handled. |
| **Ways in** | ArcGIS Pro toolbox (which can start a long run in a window of its own), Jupyter notebook, QGIS plugin, Python API (`geolens.api`), command line (`geolens …`), a drag-and-drop batch file for whole projects, and an MCP server so Claude can run it. |

# 1 Summary

geolens takes a digital elevation model and returns a reach-by-reach description of the valleys and channels on it, as GIS layers. One call conditions the DEM and routes flow, traces a stream network and the height above it (HAND), maps valley bottoms, cuts the network into reaches and measures each one's confinement from its valley floor, measures each valley from floor to divide, searches for alluvial fans, and assigns Rosgen's valley types — the 2022 fluvial landscape type as the valley type, with the 1996 type I–XI beside it — and, from cross-sections on the finest DEM available, Rosgen stream types A–G, then judges whether each reach's stream type is a stable or an unstable form for its valley.

Since v0.33 the unit of work is a **project**: a boundary (a field office, a ranch, a district) rather than a DEM. Level A runs the whole area at 10 m, widened to every HUC12 watershed draining into it; level B runs one HUC12 at a time on 1 m lidar where 3DEP has it. The project also fetches, per HUC12, the data a landscape plan's hydrologic section needs — fire history, SSURGO soils and ecological sites, PRISM climate, NOAA Atlas 14 storm depths, Rangeland Analysis Platform cover and its trends — and writes one summary row per HUC12. The last step (v0.40) gives every reach its stream power and the stream it is on, dissolves the major streams with the drainage area at their mouths, and ranks the HUC12s by weighted percentile ranks of what was measured, with the weights written beside the table to be argued with. Since v0.43 the valleys can be mapped a second time on the lidar itself, resampled to 3 m, HUC12 by HUC12, and compared with the 10 m pass (Section 8.5); since v0.44 that runs over a whole field office, several HUC12s at once, for days if need be.

The design rule throughout is that a classification says what it rests on. Every valley and stream type carries a confidence and a basis, candidates are listed when the data cannot separate them, measurements the grid cannot resolve are reported as unknown rather than filled with a guess, and every layer says what resolution it was measured at. Section 11 sets out what has been verified and what has not.

## 1.1 The first field office

The Kingman Field Office (BLM, northwestern Arizona: 41,000 km² with its upstream HUC12s, 309 HUC12s inside the boundary, 446 in all) was the proving ground in October 2026. The first run (v0.35) took 3 h 26 min end to end on a desktop: 86,783 reaches at 10 m, 39,109 of them inside the boundary (22,700 km of stream), three representative pilot HUC12s on 1 m lidar at about 3 minutes each. Its review drove versions 0.37.1 to 0.40 (Sections 3–5 and 9). The whole-office run (v0.42.1, finished 23 October) typed 39,484 reaches (22,883 km) at 10 m and ran level B on the 266 HUC12s with lidar that could be measured (43,635 reaches, 25,312 km; four failed, six had no lidar), with the confinement breaks calibrated to its lidar (Section 4). Its figures are quoted below where they are the newer. On 23 October the office-wide 3 m pass showed that the 1 m mosaics both it and level B were built on had holes where lidar projects overlap or meet at a UTM zone line (Section 8.6): the stream-type and form figures of that run are therefore provisional until level B is measured again on whole mosaics.

## 1.2 What changed since v0.28

| Version | Change |
|---|---|
| 0.29–0.31 | ArcGIS Pro toolbox and QGIS plugin; section plots as geodatabase attachments; fetchers for 3DEP DEMs, NAIP imagery, SSURGO soils and Sentinel-1 radar statistics. |
| 0.32 | SSURGO soils as the valley floor's material, finer than the USGS geologic map. |
| 0.33–0.36 | Projects of any size by HUC12 (Section 8); a portable release on one drive; the feet/metres audit; a drag-and-drop batch file for a boundary. |
| 0.34 | A representative pilot: HUC12s that together stand for the whole area. The 10 m DEM's accuracy measured against ICESat-2 (Section 8.4). |
| 0.35 | Rosgen's 2022 fluvial landscape types (Section 5.4). |
| 0.37 | The watershed data package and the HUC12 summary (Section 8.3). What the first field office taught the classifiers (Section 5.5). |
| 0.38 | Confinement from the measured valley floor, calibrated to the project's own lidar; valley polygons that are the valley bottom, not the catchment; the 2022 type as the valley type (Sections 4, 5). Braided needs more than one channel (Section 6.6). |
| 0.39 | Fans from the soils map's landform field (Section 5.3). |
| 0.40 | Stream power, major streams, the HUC12 sensitivity table (Section 9). The network tracer keeps the stream whole through junctions and a reach's drainage area is its own (Section 3.2). |
| 0.41 | Reconnaissance runs on a coarser level A grid (20–30 m) within a laptop's or a cloud runner's memory; the major streams named from NHDPlus HR (Section 9.2). |
| 0.42 | Every reach drawn downstream (Section 3.2); soils that arrive after the pass re-type the valleys; a share of nothing in the HUC12 summary is 0, not blank (Section 8.3). |
| 0.43 | Valleys on the lidar at 3 m for a pilot, compared with 10 m (Section 8.5); fire perimeters repaired one by one; the HUC12 at each major stream's mouth; a new project folder can start from an earlier one's measurements, which are only read. |
| 0.44 | The lidar valley pass over a whole project: the 10 m drainage carried into each tile, several HUC12s at once, built to run unattended for days (Section 8.5). |
| 0.45 | Whole 1 m mosaics: a lidar tile is skipped only where its cells already have data (Section 8.6). Long runs started from the ArcGIS toolbox in a window of their own, a check on a run's progress, and a project's results added to the map, named and coloured (Section 13). |

# 2 Inputs, workflow and outputs

## 2.1 One DEM

| Input | Required? | Used for |
|---|---|---|
| DEM | yes | Flow routing, HAND, valley bottoms, valley form, confinement. A 2–10 m grid is enough for routing and valleys. |
| DEM_FINE | no | Channel cross-sections for stream types. Point samples along each section, so a multi-gigabyte 1 m lidar DEM is never loaded whole. |
| Geology | no | Material per reach: SSURGO soils (`"ssurgo"`, the default in a project) or the USGS Cooperative National Geologic Map (`"auto"`). Separates valley types that shape cannot. |
| Regional curve | no (`"auto"`) | Bankfull width, depth, area and discharge from drainage area (Section 6.4). `"auto"` picks the Moody et al. (2003) province from the location. |
| Stream network (NHD) | no | Joins NHD identity to the derived reaches. |
| Fans, centerline | no | A fan layer to type against; a surveyed centerline for reach-scale sections. |

The landscape pass is six steps — terrain, valley bottom and confinement, valley form, fans, valley types, stream types — and the stable/unstable judgement. Routing runs once per DEM and is cached beside it, so later runs with other thresholds skip the expensive part. Every result goes under the output folder, one sub-folder per step, with a `runs/` record of every call.

| Folder / file | What it holds |
|---|---|
| `network_dir/segments.gpkg` | The stream network cut into reaches (500 m by default). |
| `classified_dir/segments_classified.gpkg`, `valley_alloc.tif` | Per reach: drainage area, gradient, valley width, confinement; the valley bottom allocated by reach. |
| `valley_dir/valley_bottom.gpkg` | The valley bottom (the low-lying core in `valley_core.gpkg`). |
| `valley_form_dir/` | The floor-to-divide sections, floors and per-reach measurements. |
| `reclassified_dir/segments_classified.gpkg` | Confinement from the measured valley floor (Section 4). |
| `fans_dir/fans.gpkg`, `fan_candidates.gpkg` | Accepted fans and every candidate, for review. |
| `valley_types_dir/valley_types.gpkg` | The valley type (2022 and 1996), candidates, confidence and basis per reach. |
| `valley_extent_dir/valley_extent.gpkg` | The valley bottom split by reach, carrying the types. |
| `stream_types_dir/stream_types.gpkg`, `stream_type_sections.gpkg` | Stream type per reach and every channel section, with its plot. |
| `stability_dir/stream_stability.gpkg` | Stable / unstable / check per reach, by the 2022 card and the 1996 table. |

## 2.2 A project

A project folder holds `project.json` (every step and HUC12, so a run carries on where it stopped), `huc12.gpkg`, `A_landscape/` and `B_huc12/<code>/`, `data/` (the watershed data), `merged/` (the layers for the boundary: valley types, stream types, sections, stability, `huc12_status`, `huc12_summary`, `major_streams`, `huc12_sensitivity`) and `RESULTS.md`, which says what ran at what resolution, how far to trust it, and every warning. Section 8 has the levels.

# 3 Terrain, streams and valley bottoms

## 3.1 Conditioning and flow routing

Depressions are filled with the priority-flood algorithm (Barnes et al., 2014), flow directions are D8 (O'Callaghan and Mark, 1984) and flow accumulation gives drainage area; pysheds does the routing. Routing dominates the run time on a large DEM, so its outputs are cached in a `.geolens_cache` folder beside the DEM, keyed on the DEM's path and size and checked by a content hash when the file date changes. The planner reads only the DEM header and reports cells and peak memory before anything starts; a project coarsens its level A DEM when memory says so, and the cell size travels with every result.

## 3.2 Stream network and drainage area

The channel-initiation threshold is 1 km² in a project (fitted to the DEM's extent otherwise). The stream raster is thinned to a one-cell skeleton (Zhang and Suen, 1984) and traced into paths between junctions; paths under 300 m with a free end are spurs of the thinning and are dropped; the paths are cut into reaches of 500 m, a short tail merged into the reach before it. Height above the nearest drainage (HAND; Rennó et al., 2008; Nobre et al., 2011) is traced along the flow paths to the stream cell each cell drains to.

Two faults in this layer were found by tracing streams on the first field office (Section 9.2) and fixed in v0.40. **Short paths between two junctions were dropped as if they were spurs.** An 8-connected skeleton makes a small triangle of junction cells wherever a diagonal step meets an orthogonal one, and a wide wash's skeleton grows side loops; the two-cell stubs between those junctions, and the stretches of main stem between tributaries closer than 300 m, were all deleted, so the network broke at every one — on the 30 m test landscape that hid 30% of its length, and on Kingman 1,323 of 39,109 reaches had a gap of up to 400 m below them and the largest wash came out in nine pieces. Such a path is now joined onto the neighbouring path carrying the most flow at the junction they share (the river goes on through the junction); the short side of a loop is dropped, the other side keeping the stream whole; only a path with a free end is a spur. **A tributary's mouth reach could inherit the river's drainage area.** Every traced path ends on the junction cell, the segment-id raster gives that cell to one of the paths meeting there, and when it was the tributary's the per-reach maximum of accumulation took the river's: 264 mouth reaches on Kingman carried 1,000–3,800 km² above a reach of 10, with a bankfull width from the curve, and a confinement ratio, to match. Junction cells (three or more skeleton neighbours) now count for no reach unless a reach has nothing else. Layers traced before v0.40 are repaired, not re-run, by the synthesis step (Section 9.2). Since v0.42 every branch is drawn downstream when traced (the filled DEM never rises along a stream; the accumulation decides on a flat), and a path that climbs over a divide is split at the top.

## 3.3 Valley bottoms

Valley bottoms follow the Valley Bottom Extraction Tool approach (VBET; Gilbert et al., 2016): a HAND evidence surface (full credit at 2 m, none at 12 m, both scaled up with drainage area so one parameter set bounds a headwater floor and a river's) and a slope evidence surface (full at 2%, none at 25%) are combined as E = E_HAND^0.65 × E_slope^0.35, cut at 0.65, and gaps to 250 m closed. Slope evidence is down-weighted with distance from the channel, which stops flat uplands — mesas, terraces, ridge tops — reading as valley floor. VBET itself is GPL-3.0 and cannot be bundled in an MIT package, so this evidence layer is implemented here. Each reach's share of the bottom is the valley-bottom cells nearest to it (`valley_alloc.tif`); that allocation, polygonised by reach and carrying the types, is the valley extent layer since v0.38. Until then the valley extent was each reach's catchment — every cell draining to it — which is a watershed (the Basin tool's answer), not a valley; the catchments remain available for whoever wants them.

# 4 Confinement

A reach's confinement ratio is its valley floor width over its channel width (cf. Fryirs et al., 2016). The channel width comes from the regional curve. Until v0.38 the floor width was the allocated valley-bottom area over the reach's length — the HAND area — and the published breaks of 2 and 5 made the classes. The first field office showed what that costs: 89% of stream length "unconfined" in country with real canyons, and against the 1 m lidar of the pilot HUC12s the HAND ratio tracked the channels' measured entrenchment at ρ = 0.38, while the valley floor width the cross-sections measure (Section 5.1) tracked it at 0.77. So from v0.38:

- **confinement is measured from the sections' valley floor** where they measured one (`confinement_basis` = `sections`, `valley_floor_width_m`), with the HAND figures kept as `valley_width_hand_m` and `confinement_ratio_hand` and used where no floor was measured;
- **a project calibrates the breaks to its own lidar.** After level B, the 10 m ratio is fitted to the entrenchment classes of the reaches the lidar measured — entrenchment ratio under 1.4 confined, 1.4–2.2 partly confined, over 2.2 unconfined — by a coordinate descent over every cut, length-weighted. When the fitted breaks agree at least five points better than the published ones they are applied to every reach, the valleys are re-typed and the form judged again, and `RESULTS.md` shows the agreement table before and after. On three pilots the fit was unstable from one HUC12 to the next; on a whole field office of lidar it is what the breaks should come from.

A reach with no valley bottom at all has an unknown ratio, not zero (8.5% of one early run had been classed confined by a zero).

# 5 Valley form, fans and valley types

## 5.1 The valley measured

Rosgen's valley types are defined by the whole valley — V-notched canyons, gentle side slopes, U-shaped troughs, broad alluvial floors — so geolens measures the valley as a landform. For each reach, long sections are cut across the valley and walked outward and upward from the floor to the divide on each side. Each gives the valley floor width, the valley-floor width to height ratio V_f = 2V_fw / [(E_ld − E_sc) + (E_rd − E_sc)] (Bull and McFadden, 1977), the exponent n of z ~ |x|^n fitted above the floor (n ≈ 1 a V, n ≈ 2 a U), side slopes from floor edge to divide, relief and asymmetry. The floor width is what the confinement of Section 4 now rests on.

## 5.2 Material

Each reach's material comes from the soils or geology within a 200 m buffer, the dominant unit's parent material read into a small vocabulary (alluvial, colluvial, residual, bedrock, eolian, lacustrine, glacial till, …) with its cover recorded. SSURGO (Soil Survey Staff) is the project default since v0.35: the USGS Cooperative National Geologic Map (1:50,000 to 1:1,000,000) calls most valleys their hillslope's bedrock unit, while the soil map units follow the valley floors. The material step uses a spatial index (56 s for 39,109 reaches against 7,380 polygons). SSURGO over the boundary also carries the hydrologic group, erodibility (K), landform and ecological site that the summary and the sensitivity table use.

## 5.3 Alluvial fans

Two tests, with every candidate kept for review. **The cone test** (v0.29) finds a concave break in the channel long profile and grows a cost-limited region below it, accepting cone-shaped regions. Its caps were built on a 49 km² DEM; on a Basin-and-Range field office they excluded the bajadas (5 to 15 km long at 1 to 4%), so the search radius now grows with the DEM (8% of its short side, 3 to 12 km) and the slope floor it raises itself stops at 3%. **The landform test** (v0.39) reads NRCS's `landform` field: coalesced fans are not cones, and the soils map names them outright — at Kingman "fan terraces", "alluvial fans", "fan remnants" and "fan piedmonts" cover a fifth of the field office. A reach whose buffer is mostly on such a unit is on a fan: on a relict surface the wash has cut into (terraces, remnants, piedmonts, bajadas, ballenas) or on a fan being built (alluvial fans, aprons, skirts, inset fans), which is the active-or-inactive call the DEM cannot make. `fan_source` (`dem`, `soils`, `dem+soils`) and `fan_activity` say which. Re-typed, 43% of Kingman's stream length is on fan surfaces (0.1% by the cone test alone).

## 5.4 The two classifications

**Rosgen 1996, types I–XI.** A crosswalk maps the measurements, the material and the fans to types I–XI. Several pairs differ by depositional history rather than shape (II/V/VI, VIII/IX, X/XI); without a material they come back unresolved with their candidates listed, and type VI (structurally controlled) is never asserted, because structure is not visible in a DEM or a map. Kept beside the 2022 type as `valley_type_1996`.

**Rosgen 2022, fluvial landscape types** (the valley type since v0.38). The newer classification names a valley by whether it confines its river (`U` unconfined, `C` confined), what built it (`AL` alluvial, `BR` bedrock, `EO` eolian, `RE` residual, `CO` colluvial, `GL` glacial, `LA` lacustrine, `MA` marine, `PE` periglacial) and its landform: `U-AL-AD` is an unconfined alluvial valley with terraces, `U-AL-IF` an inactive fan, `C-CO-VS` a steep V-shaped colluvial valley. Each type has a slope range, the stream types natural to it and those seen under disequilibrium (Rosgen, 2022, transcribed in `geolens.landscape.landscapes`). What decides the type: confinement, the channel gradient (a stand-in for the valley slope the card means), the material, whether the reach is on a fan and what kind, and the valley's cross-section shape. What a DEM and a soils map cannot tell apart stays open with the alternatives listed: sand dunes from loess, an inner gorge from a narrow alluvial valley, and partly confined reaches between the U and C types (they lean to the unconfined type, the floor being wider than the channel needs, with the confined one as the alternative). Glacial and periglacial types need a glacial material; residual and colluvial valleys have no unconfined type on the card.

## 5.5 What the first field office taught the classifiers

Both classifiers carry a revision number; a project typed under an older one is re-typed when it is run again, from its saved valley form and fans, in minutes.

- **A U-shaped valley is a glacial trough only with glacial till.** At 10 m over a desert piedmont a wash between two concave bajada flanks fits a parabola as well as a trough does: 1,500 km of Kingman was "V" on alluvium at 35° N. A mapped material that is not till now argues against a trough and the fluvial types decide, with the U-shape noted in the basis.
- **An unconfined floor is stream-built.** The soils are read into a floor narrower than the map unit, so a broad gentle valley "built of colluvium" is the hillslope's unit over alluvial fill: the 1996 call is VIII at low confidence (IX and X open), and on the card the floor's material is alluvial (`U-AL-*`, low confidence, the map's word in the basis); confined valleys keep the map's material, which there names the walls. Unresolved landscape types fell from 22% of stream length to 5%, the 1996 ones from 6% to 2%.
- **Confinement from the floor, calibrated** (Section 4), **valley polygons that are the valley bottom** (Section 3.3), **fans from the soils map** (Section 5.3), and **braided needing more than one channel** (Section 6.6) came from the same review.

# 6 Rosgen stream types

Stream types A–G describe the channel, and Rosgen's Level II key (Rosgen, 1994, 1996) discriminates them with four measurements: the entrenchment ratio (flood-prone width over bankfull width, the flood-prone width taken at twice the maximum bankfull depth), the width/depth ratio, sinuosity and slope. Everything turns on finding bankfull on a DEM.

## 6.1 Channel cross-sections

Five sections are cut across each reach, perpendicular to the channel and twenty channel widths long (at least 60 m), sampled on the fine DEM at its cell size but no finer than 0.5 m. Offsets and elevations are converted to metres, each with its own factor, before anything is measured; a test feeds the same profiles through in feet and in metres and requires the same answer.

## 6.2 Bankfull at the regional depth

At each section the water is raised from the thalweg to the maximum bankfull depth the regional curve gives for the reach's drainage area (the curve's mean depth / 0.58, the riffle relation Moody et al. (2003) report across their Arizona, New Mexico and Navajo Nation sites, R² 0.90), and the width, area, mean depth and flood-prone width are read off the DEM at that stage. Where that depth would spill a small channel onto a flat floor — a width over three times the regional one — the stage is lowered until the width is plausible, but not below 0.1 m, about the vertical noise of 1 m lidar. A section with nothing channel-shaped at that scale falls back to the regional entrenchment. Because the depth comes from the curve, no reach typed this way is rated above medium confidence.

This replaced a stage–width bank picker (Riley's 1972 bench index, after Williams 1978 and Navratil et al. 2006) in v0.28, after the picker was checked against Moody et al.'s New Mexico field data on a 1.785-billion-cell run: its depths ran 2.1–2.6× the field depth in drainages under 20 km², because the first break it can see is usually the top of a small incised channel or a low-flow thread. The picker still runs on every section as a check (`picker_*` columns, purple on the plots), and `bankfull_method="picker"` or `"hybrid"` restores it.

## 6.3 From sections to a reach

| `stream_basis` | Evidence | Confidence |
|---|---|---|
| measured | Usable banks in two or more sections; entrenchment and width/depth are the medians. | high, medium, or low if `no_type_fits` |
| partial | Banks in one section; its entrenchment pooled with the regional ones; width/depth not used. | low |
| regional | No usable banks; entrenchment from the flood-prone width against the regional channel. | low |
| slope_sinuosity | No section; a letter only where slope and sinuosity separate the types on their own. | low |

## 6.4 Regional curves

| `regional_curve` | Width W, m | Mean depth D, m | Area, m² | Discharge Q, m³/s | Source |
|---|---|---|---|---|---|
| `arid_sw_az` | 3.554 A^0.317 | 0.193 A^0.219 | 0.665 A^0.540 | 1.605 A^0.471 | Moody et al. (2003), central and southern Arizona |
| `arid_sw_nm` | 2.311 A^0.281 | 0.113 A^0.237 | 0.273 A^0.512 | 0.242 A^0.612 | Moody et al. (2003), eastern Arizona / New Mexico |
| `bieger_us` | 2.70 A^0.352 | 0.30 A^0.213 | — | — | Bieger et al. (2015), all-US, generic |

A is drainage area in km²; the Moody relations are converted from feet, square miles and cubic feet per second. `"auto"` picks the province from the report's own map (its dashed division near 109.6° W), uses the nearest province with a warning within a degree outside its study region, and Bieger beyond that. The discharge relation is what the stream power of Section 9.1 rests on; the generic curve has none.

## 6.5 The key: the nearest Rosgen type

Rosgen's key is delineative on entrenchment and width/depth; slope and sinuosity are "typical" values, and a reach outside the typical slope keeps its letter with a slope suffix (Bc, Cb, Aa+). Rosgen (1994) allows a measured entrenchment ratio ±0.2 either side of a boundary and a width/depth ratio ±2.0, so every type is scored by its misfit — how far each measurement lies outside the type's range: misfit = ΔER/0.2 + Δ(W/D)/2.0 + 0.5·log₂(slope distance) + 0.25·Δsinuosity/0.1. The type with the least misfit takes the letter when it beats the next by 0.05 (0.25 when entrenchment is unmeasured); otherwise the near-tied types are candidates. A letter is excluded at a slope where it does not exist (a 13% reach is never a C), an entrenched low width/depth channel is an A above 4% and a G below it, DA (anastomosed) can be a candidate but never the letter, and `no_type_fits` flags a measured reach beyond Rosgen's tolerance of the nearest type at its slope. With a material, the substrate number (1 bedrock to 6 silt/clay) is added as a prior, giving `stream_type_full` (e.g. B4c). The key was tested exhaustively: no label outside Rosgen's chart over 13,600 input combinations.

## 6.6 Braided needs more than one channel

Width/depth over 40 alone made D (braided) 48% of the pilots' stream length on the first field office: wide sandy washes at the regional stage. Since v0.38.1 each section also counts the threads — runs of ground below the bankfull stage parted by bars at least a quarter of the maximum depth high, across the flood-prone width — and a reach is D only when its median section has at least two and its width/depth is measured over 40. Thirty of the 32 D reaches of one pilot had one channel.

# 7 Stable or unstable form

The point of classifying both the valley and the channel is to ask whether each reach's stream type is a stable form for its valley. Two judgements are written. **By the 2022 card** (`form_status`): `stable` is one of the type's natural stream types, `unstable` one it lists only under disequilibrium, `check` one it does not list (or lists both ways, like D in `U-AL-AD`); the match is on the stream type with its slope class, as the card writes it. **By the 1996 table** (`form_status_1996`): NRCS (2007) NEH 654 Technical Supplement 3E, Table TS3E-3, after Rosgen (1996), which lists the stream types associated with each valley type and, for three of them, the types that appear only out of balance; a custom association table can replace it. An unresolved valley type is judged under every candidate; the confidence is the lower of the two classifications'. A `check` is a real departure, or one of the two classifications is wrong, and the section plots are there to tell which.

# 8 Projects of any size, by HUC12

## 8.1 Two levels

At 1 m a field office would be tens of billions of cells, so a project runs in two levels.

| Level | Area | DEM | Results |
|---|---|---|---|
| A | the boundary's HUC12s plus the HUC12s draining into them (from the WBD's `tohuc`), so no stream is cut off at the boundary | 3DEP 1/3 arc-second, 10 m; coarser only if memory says so | routing, valley bottom, confinement, valley form, fans, valley types, valley extent |
| B | one HUC12 at a time | 3DEP 1 m lidar, where the tile index covers at least 80% of the HUC12; the share that has data is recorded (`lidar_data`, v0.45) | stream types, section plots, stable / unstable form for the level A reaches inside it |

Level B runs on a pilot, on every HUC12 with lidar, on a list, or not at all. The **representative pilot** (v0.34) sorts the HUC12s inside the boundary into groups of similar watersheds — by stream length in each valley type and area in each soil hydrologic group, plus the surface soil's erodibility, each weighted by area inside the boundary — and runs each group's most typical HUC12 with lidar; `huc12_status` says which pilot stands for each HUC12, and `RESULTS.md` how much of the area each stands for. Each HUC12's 1 m mosaic is deleted once its channels are measured when every HUC12 runs, unless it is kept (`keep_lidar_dem`, about 300 MB a HUC12), in which case the lidar valley pass of Section 8.5 uses it instead of downloading it again. After level B the confinement breaks are calibrated (Section 4). The run is resumable at every step, the boundary can be a polygon layer or a map-service URL with a where clause, and a project can be dropped onto `run_project.bat` and left.

## 8.2 Resolution travels with the results

Merged valley types carry `dem_cell_m`, and `valley_resolved` is false for valleys under three cells wide. Stream types carry `fine_dem_cell_m` and the HUC12's lidar share. Drainage areas on rivers entering from outside the HUC8s the project touches (the Colorado) are local only, and `RESULTS.md` says so.

## 8.3 The watershed data package and the HUC12 summary

A vegetation management plan's hydrologic section is written from what the subwatersheds are made of and how they have been behaving, so the project also fetches, per HUC12 inside the boundary, each source its own resumable step:

| Step | Source | Per HUC12 |
|---|---|---|
| fire | NIFC InterAgency Fire Perimeter History | share burned (all years, since 2000, last 10 years), fires, last fire year |
| ecosites | SSURGO components' ecological sites, on the project's soils | dominant ecological site and its share |
| prism | PRISM 1991–2020 normals, 800 m (Daly et al., 2008) | annual precipitation, the July–September share, mean temperature |
| atlas14 | NOAA Atlas 14 (Bonnin et al., 2011), at each HUC12's centre | 1-hour and 24-hour depths at 2, 10, 25 and 100 years |
| rap | Rangeland Analysis Platform cover v3, 30 m, 1986 on (Allred et al., 2021) | annual and perennial forb and grass, shrub, tree, litter and bare ground: the last five years' mean and the trend per decade |

`huc12_summary` then puts them beside what the project measured: stream km and shares by confinement, the main valley types (2022 and 1996) and the share in alluvial landscape types; soil shares by material and hydrologic group (of the mapped part, with how much that is), erodibility K, the dominant ecological site; slope; km stable, unstable and check where level B ran. Nothing in the summary is weighted into a score; the sensitivity table of Section 9.3 is one proposal for that call, with its weights in the open. A share of something a HUC12 has none of is 0; blank means nothing was measured there (no reaches, or no level B). Fire perimeters are checked one at a time and repaired where their rings are invalid (GEOS `make_valid`); one that cannot be repaired is dropped and counted, so one bad polygon no longer fails the whole step.

## 8.4 How far to trust the 10 m DEM

Where 3DEP has no lidar, the 10 m DEM was measured against 6,156 ICESat-2 ATL08 ground points (Neuenschwander and Pitts, 2019) in northern Arizona: NMAD 0.23 m and LE90 0.66 m on slopes under 10%, 0.99 / 2.43 m from 10 to 20%, 2.66 / 6.64 m over 20% (the steep figures pessimistic, ICESat-2 smoothing steep ground over 100 m). Good enough for watershed-scale work, not for channel design; where there is no lidar the 10 m DEM comes from older photogrammetry or contours and does not show recent incision or headcuts.

## 8.5 Valleys on the lidar at a finer cell (v0.43–v0.45)

Level A measures the valleys on the 10 m DEM, and a field office shows what that costs: on the first Kingman run 11% of the stream length was in valleys narrower than three 10 m cells (`valley_resolved` false), the 10 m confined class held 0.1% of the length while the lidar sections of level B found 13% of the measured reaches entrenched, and the valley polygons are 10 m stair-steps at 1:8,000. A whole field office at 1 m is some 23 billion cells, against the 70 GB the 10 m pass needed for 0.4 billion, so the finer pass runs HUC12 by HUC12.

**The pass.** Each HUC12's 1 m lidar mosaic (with a 250 m buffer) is resampled to `fine_cell_m` (3 m by default; 11–22 million cells for a HUC12 of 100–200 km²) and level A's valley pass is run on it unchanged: the same 1 km² stream threshold, 500 m reaches, the project's confinement breaks (calibrated ones where Section 4 applied them), the valley form, the fans and the valley types on the project's soils. A tile cut to one HUC12 routes only the drainage inside it, so a main stem entering from upstream starts as a wash. **The 10 m drainage is carried in:** a tile reach whose midpoint lies within 40 m of a 10 m reach running the same way (within 35°) that drains at least 1.5 times as much, and 5 km² more, takes the 10 m reach's drainage area; its channel width scales as A^0.35^ (the width exponent of the generic hydraulic geometry), its confinement ratio by the inverse, its class is set again with the project's breaks, and the tile's valleys are typed again. A HUC12 that runs out of memory runs again at twice the cell.

**The comparison.** Each finer reach is matched to the nearest 10 m reach inside the HUC12, and per HUC12 the pass reports the stream km on each grid, the share of the finer length matched, the agreement in valley type and in confinement class by length, the confined and unresolved shares on each grid, the median ratio of valley widths and the valley area (`A_fine/comparison_3m.csv`). `RESULTS.md` gives one length-weighted summary over all the HUC12s and the rows of a pilot. The **pilot** is three headwater HUC12s whose channels level B measured — the most confined, the one most on fans and piedmonts, and the most alluvial valley — chosen headwater so that no drainage enters them from outside; over a whole project they run first, so their comparison comes early. Merged, the finer layers are `valley_types_3m` and `valley_extent_3m`, each feature carrying its HUC12 and the cell it was measured on.

**Built to run unattended (v0.44).** Several HUC12s run at once in separate processes (by default as many as 60% of the free memory allows at about 170 bytes a cell, half the cores, at most six); each HUC12 is saved to `project.json` as it finishes and its working rasters deleted; a failed HUC12 is retried once at the end and never stops the run; the free space is checked before every HUC12 and the pass stops cleanly, resumable, below 30 GB; the computer is asked not to sleep while it works; and one run at a time can work in a project folder (`run.lock`, taken over from a run that died). A run stopped by anything — a reboot, a power cut — carries on from the last finished HUC12 when started again. The first Kingman HUC12s took two to five minutes each at 3 m, download included, six at a time, on the incomplete mosaics of Section 8.6; whole ones take longer.

The office-wide comparison is not reported here: the first run of it is what exposed the incomplete mosaics of Section 8.6, and it is being run again.

## 8.6 Whole 1 m mosaics (v0.45)

3DEP's 1 m DEM comes as 10 km tiles, one set per lidar project and in that project's UTM zone. Where projects overlap, the most recently published should win cell by cell. Until v0.45 the mosaic skipped a tile outright when newer tiles' footprints covered it — but a tile's footprint is its 10 km square, whatever the project flew. Two cases broke it. Where projects overlap, an older project's tile was skipped under a newer one whose data ends partway across the square. And a project that crosses a UTM zone line delivers the same squares in both zones, its data in one of them: Kingman straddles 114° W, and the western half of one project lay in its zone 11 tiles, skipped under the same project's empty zone 12 tiles. On the first six HUC12s of the office-wide 3 m pass the mosaics filled 14–70% of their grids and the finer networks held 12–82% of the 10 m stream length; 46 of the first 50 level B mosaics of the whole-office run had skipped a tile, and two of the four HUC12s where level B failed ("no channel sections could be sampled") had 6–7% of their grid filled.

Since v0.45 a tile is skipped only when every cell it could fill — inside the area and inside its own footprint — already has data, and the 1/3 arc-second fill of a "best" DEM fills whatever the lidar left empty, wherever that is. The download records `area_coverage`, the share of the area that has data, and the "no 1 m lidar" warnings are measured, not read from footprints. A mosaic made before the fix that skipped a tile is recognised from its `dem_sources.json`: the lidar valley pass redoes its HUC12 by itself (and any HUC12 typed with other confinement breaks than the current ones), and level B does when asked (`redo_lidar_holes`), which over a field office takes most of a day. Each HUC12's records carry `lidar_data`, the share of it with lidar data, and `RESULTS.md` lists the HUC12s with lidar over less than 90% of their area.

# 9 Stream power, major streams and the sensitivity table (v0.40)

## 9.1 Stream power

Every reach gets a bankfull discharge from the regional curve (Q = a·A^b, Section 6.4), the total stream power Ω = ρgQS in W/m (Bagnold, 1966; S the 10 m reach gradient) and the unit stream power ω = Ω / W in W/m² over the curve's bankfull width. Both are indices from a curve and a coarse slope: they rank reaches by how much work the channel can do (the literature's thresholds, such as Brookes's (1988) 35 W/m² above which channelized rivers adjusted back, are a guide to reading them; Knighton (1999) on their downstream pattern), not design values. Outside the arid Southwest the generic curve has no discharge relation and they stay empty, with `power_basis` saying so.

## 9.2 Which stream a reach is on

A reach's `drainage_km2` is the area draining to its downstream end; the contributing area of a *stream* is that at its mouth. From every outlet the main stem is followed upstream, taking the larger drainage at each junction, and each tributary numbered in turn: `stream_id` (1 is the biggest outlet's stream), `dist_to_mouth_km`, `flows_into` and `stream_km2_at_mouth`. The flow direction of each branch is its fall along the filled DEM (half the reaches are drawn uphill), a flat branch going by its drainage. Where the network has gaps — every layer traced before v0.40 (Section 3.2) — a reach whose downstream end meets no reach at a node joins the reach whose line passes within two cells, else the nearest reach within 400 m, in every case one carrying at least 95% of its own drainage. A mouth reach carrying the river's drainage is flagged `drainage_inherited` and `drainage_km2_used` takes the reach above; a parallel thread of one wash belongs to the river's stream; two reaches ending at one confluence with nothing traced below, which receive each other, are cut at the bigger one, the outlet. Among the tributaries joining a reach, the one with the most drainage goes on as the stem, but among those within 10% of it the one with the longest network above, and a lone candidate carrying the river's whole drainage while the others together account for most of it is a side branch at a real confluence and goes last. On the first Kingman run: 39,109 reaches, 534 outlets, 5,848 streams, 1,323 gaps bridged, 264 inherited mouths; before the three rules the largest wash (3,798 km²) was nine streams, after them the three biggest main stems (2,600–3,800 km² at the mouth) are whole, 60–111 km each.

`major_streams` is one line per stream draining at least 25 km² at the mouth with the drainage at the mouth, length, reach count, length-weighted gradient, mean and maximum stream power, the dominant landscape type and its share, the shares on fans and unconfined, the HUC12 its mouth is in (`huc12_mouth`, v0.43) and the stream it flows into. Since v0.41 each is named from the NHDPlus HR flowlines it follows, where they carry a name. On the whole-office Kingman run: 681 major streams, 229 of them named (21 of the 30 largest); the largest are the Big Sandy River (3,798 km² at the mouth), Sacramento Wash (3,648 km²) and the Santa Maria River (3,035 km²). Rivers whose drainage mostly lies outside the project (the Colorado) carry their local drainage only, and a river can break where a reservoir interrupts the 10 m network (the Bill Williams below Alamo Lake).

## 9.3 The HUC12 sensitivity table

Each component below is a percentile rank among the project's HUC12s (1 = the most), the score is the weighted mean of the ranks a HUC12 has, and the class cuts the scored HUC12s into thirds (`insufficient data` when under half the weight is measured, as for stream form where level B has not run). `sensitivity_drivers` names the components with rank ≥ 0.67 that put a HUC12 where it is.

| Component | Column | Weight |
|---|---|---|
| soil erodibility (K) | `soil_k_mean` | 0.15 |
| runoff-prone soils (hydrologic group D) | `hsg_D_share` | 0.10 |
| mean slope | `slope_mean_pct` | 0.10 |
| stream length in alluvial landscape types | `alluvial_landscape_share` | 0.15 |
| unstable stream form (level B) | `form_unstable_share` | 0.15 |
| unit stream power, length-weighted per HUC12 | `unit_power_mean_w_m2` | 0.10 |
| bare ground trend, % per decade | `rap_bgr_per_decade` | 0.10 |
| bare ground now, % | `rap_bgr_recent` | 0.05 |
| burned in the last 10 years | `fire_burned_share_10yr` | 0.05 |
| 100-year 24-hour storm depth | `atlas14_24h_100yr_mm` | 0.05 |

A component the summary lacks or that is the same everywhere drops out and the rest are renormalised; `RESULTS.md` says which. The weights are a first proposal, not a finding: `merged/sensitivity_weights.json` has the set used, and any JSON of summary column → weight (any numeric column of the summary) re-ranks on the next run without re-running anything else. The ranks are relative to the project's HUC12s, so a score is not comparable between field offices. On the first Kingman run (without slope, cover, fire or storm depth, which the second run brings) the HUC12s at the top of the table are there on erodibility, stream power and alluvial valley.

# 10 Other tools

| Tool | Method | Status |
|---|---|---|
| Gullies | Slope variability, negative TPI and a drainage-area ceiling; width, depth and volume on cross-sections; roughness in strips so any DEM fits; an optional roads layer sets road cuts aside. | 6 of 6 on a known-gully fixture; on steep mountain terrain most detections are headwater ravines. Experimental. |
| Headcuts | TopoToolbox knickpointfinder (Schwanghart and Scherler, 2014, 2017) with the background gradient removed. | 6 of 6, no false positives. |
| NAIP imagery and riparian band | 1 m four-band NAIP from Microsoft Planetary Computer, cached with flight year; NDVI in excess of the local upland background, cut to HAND ≤ 15 m; rectangular patches flagged as possible agriculture. | Working on live services; usefulness of the band under review. |
| Sentinel-1 radar statistics | OPERA RTC-S1 (NASA JPL, via ASF): per-pass VV/VH tiles, per-track and combined medians, VH/VV, VH's coefficient of variation, and the statistics per polygon, to tell riparian woodland from irrigated fields. Each person saves their own Earthdata login. | Tested offline only; the experiment is in `docs/SAR_FEASIBILITY.md`. |
| Fetchers | 3DEP DEMs (1 m lidar where it exists), SSURGO soils through Soil Data Access, the WBD's HUC12s, BLM boundaries from map services. | Working on live services on the Kingman runs. |
| Reach scale | Sections along a surveyed centerline; bank tops by slope break, floodplain stopped at valley confinement. | Floodplain width 0.00% error on a known compound channel. |

# 11 Verification

**Fixtures.** The suite (about 750 tests) runs on synthetic landscapes with exact answers: a landscape-evolution surface with real dendritic drainage, written once in US survey feet and once in UTM metres so the whole pass must agree in both (a missing conversion is wrong by 3.28 or 10.76, which that cannot miss); valleys of known geometry for the form measurements; known-answer channel profiles sampled at 0.25, 0.6 and 1.2 m with noise and with the expected width deliberately wrong; a drawn network for the stream tracing of Section 9.2 (a trunk drawn uphill, a tributary at a node, one onto a line, one across a gap, one whose mouth inherited the river's drainage, a parallel thread). The ArcGIS toolbox is tested against a stand-in for arcpy running real geolens; the QGIS plugin in a headless QGIS.

**The lidar audit (v0.38).** On the three pilot HUC12s of the first Kingman run, 45 km of reaches the lidar measured as entrenched had been called unconfined at 10 m (35 km of 45); the floor-width measure and the calibration of Section 4 came from that comparison.

**Kingman.** The first run's review found the U-shape, the unconfined-floor material, the fan caps, the braided calls and the 1996 types standing as the valley type (Section 5.5), and the tracing of Section 9.2 found the network layer's two faults (Section 3.2). The whole-office run (v0.42.1) calibrated the 10 m confinement-ratio breaks on 31,148 lidar-measured reaches to 0.99 and 16.17, raising the agreement with the lidar's entrenchment classes from 55% to 65%. By length its valley types were 45% inactive fans (U-AL-IF), 29% unconfined alluvial valleys with terraces (U-AL-AD), 7% C-AL-AD, 4% active fans (U-AL-AF) and 3% C-CO-VS, with 10% unresolved; on 42% of the length the soils map names a fan or piedmont landform. Its review found what v0.43 fixed (the fire step's invalid perimeters, the empty `huc12_mouth`, blank shares) and what is still open: the 10 m confined class is nearly empty (0.1% of the length) where the lidar puts 13% of the measured reaches below an entrenchment ratio of 1.4 — the reason for Section 8.5 — and straight flow lines across playas (Red Lake) come out as Cc-. Its stream-type and form statistics (17% without a stream type, 44% `check`) are provisional until level B is measured again on whole mosaics (Section 8.6).

# 12 What to trust

| Component | Status |
|---|---|
| Flow routing | Verified (pysheds). |
| Valley bottoms, confinement | The floor-width measure tracks lidar entrenchment at ρ 0.77 on the pilots; the breaks are calibrated per project where level B has run, published breaks otherwise. |
| Valley form, valley types | Measurements verified on valleys of known geometry; the type thresholds encode Rosgen's descriptions and the first field office's review, not field validation. Without a material, types that differ only by history stay unresolved. |
| Alluvial fans | The cone test verified on synthetic fans; the landform test rests on the soils map. Every candidate is kept for review; coalesced fans are still not cones (a piedmont-surface test is next). |
| Bankfull, stream types | The regional-stage method verified on known geometry; the old picker ran 2–2.6× the field depths. Not compared with surveyed bankfull on these areas: screening only. Look at the plots. |
| Stable / unstable | As good as the two types it compares. |
| Stream power, major streams | Checked on the first Kingman run's reaches; indices for comparing reaches. |
| Sensitivity table | A ranking with proposed weights; the components are what the summary measured. |
| Watershed data | Live services, tested on the Kingman runs; a failed step is a warning the next run retries. |
| Valleys on the lidar (3 m) | The pass is level A's, tested on synthetic ground; the drainage carried in tested on a river and a tributary crossing it. The office-wide comparison with 10 m is pending (Section 8.5). |
| 1 m mosaics | Whole since v0.45, tested on overlapping projects and a zone line; records made before it are recognised and redone (Section 8.6). |
| Units | The landscape pass, both classifications, gullies and riparian compared on the same ground in feet and in metres. |

Limitations to keep in mind: bankfull leans on the regional curve, so a channel genuinely deeper or shallower than it (an incised arroyo, an E channel in a meadow) is measured at the wrong stage; a road bench beside a small channel looks like a floodplain; a channel under three samples wide is typed on the regional basis, which on a 10 m DEM is every channel; the soils and geologic maps cannot resolve narrow valleys; NDVI cannot tell alfalfa from cottonwood; a 10 m DEM without lidar does not show recent incision.

# 13 Running it

Install once with `install_windows.bat`, which builds a permanent conda environment and checks itself on a synthetic valley; or unzip the release onto an external drive and run `setup_portable.bat`, which builds geolens's own Python beside it with nothing on the computer. In ArcGIS Pro add `arcgis_toolbox\geolens.pyt` and run *0 Setup ▸ Find the geolens environment*; *1 Whole routine ▸ Run a project area (by HUC12)* takes a boundary and a folder. A run of hours or days is started with *Run in its own window* ticked: the call is written into the project folder (`geolens_run.json`) beside a launcher (`run_geolens.bat`), which runs in a console window of its own, so ArcGIS Pro stays free; it tries again by itself, a minute apart, up to five times, and after a reboot it is double-clicked to carry on. *Check on a project run* reads a project folder and says whether a run is working in it, how far it has got and roughly how long is left; *Add a project's results to the map* adds the merged layers, named and coloured. From a shell:

```
geolens project boundary.gpkg
geolens project <map service layer URL> --where "ADM_UNIT_CD='AZC01000'" --out S:\project\geolens --level-b all
geolens project <...> --out S:\project\geolens_b --copy-from S:\project\geolens --fine-valleys all --fine-cell 3 --keep-lidar-dem
geolens status S:\project\geolens_b
```

or in Python `api.run_project(boundary, level_b="all")`; `api.landscape_pass(dem, dem_fine=..., geology="ssurgo", regional_curve="auto")` for one DEM. `RUNNING.md` has every parameter, `START_HERE.md` and *How to use geolens* the user's guides, and `NEXT.md` the working log.

# References

Allred, B.W., et al. (2021). Improving Landsat predictions of rangeland fractional cover with multitask learning and uncertainty. Methods in Ecology and Evolution 12, 841–849.

Bagnold, R.A. (1966). An approach to the sediment transport problem from general physics. U.S. Geological Survey Professional Paper 422-I.

Barnes, R., Lehman, C., Mulla, D. (2014). Priority-flood: An optimal depression-filling and watershed-labeling algorithm for digital elevation models. Computers & Geosciences 62, 117–127.

Bieger, K., Rathjens, H., Allen, P.M., Arnold, J.G. (2015). Development and evaluation of bankfull hydraulic geometry relationships for the physiographic regions of the United States. Journal of the American Water Resources Association 51(3), 842–858.

Bonnin, G.M., Martin, D., Lin, B., Parzybok, T., Yekta, M., Riley, D. (2011). NOAA Atlas 14, Volume 1, Version 5: Precipitation-Frequency Atlas of the United States, Semiarid Southwest. NOAA National Weather Service, Silver Spring, MD.

Brookes, A. (1988). Channelized Rivers: Perspectives for Environmental Management. Wiley, Chichester.

Bull, W.B., McFadden, L.D. (1977). Tectonic geomorphology north and south of the Garlock fault, California. In: Doehring, D.O. (ed.), Geomorphology in Arid Regions. Proceedings, 8th Annual Geomorphology Symposium, SUNY Binghamton, 115–138.

Daly, C., Halbleib, M., Smith, J.I., Gibson, W.P., Doggett, M.K., Taylor, G.H., Curtis, J., Pasteris, P.P. (2008). Physiographically sensitive mapping of climatological temperature and precipitation across the conterminous United States. International Journal of Climatology 28, 2031–2064.

Fryirs, K.A., Wheaton, J.M., Brierley, G.J. (2016). An approach for measuring confinement and assessing the influence of valley setting on river forms and processes. Earth Surface Processes and Landforms 41(5), 701–710.

Gilbert, J.T., Macfarlane, W.W., Wheaton, J.M. (2016). The Valley Bottom Extraction Tool (V-BET): A GIS tool for delineating valley bottoms across entire drainage networks. Computers & Geosciences 97, 1–14.

Knighton, A.D. (1999). Downstream variation in stream power. Geomorphology 29, 293–306.

Moody, T., Wirtanen, M., Yard, S.N. (2003). Regional Relationships for Bankfull Stage in Natural Channels of the Arid Southwest. Natural Channel Design, Inc., Flagstaff, AZ.

Navratil, O., Albert, M.B., Hérouin, E., Gresillon, J.M. (2006). Determination of bankfull discharge magnitude and frequency: comparison of methods on 16 gravel-bed river reaches. Earth Surface Processes and Landforms 31, 1345–1363.

Neuenschwander, A., Pitts, K. (2019). The ATL08 land and vegetation product for the ICESat-2 Mission. Remote Sensing of Environment 221, 247–259.

Nobre, A.D., Cuartas, L.A., Hodnett, M., Rennó, C.D., Rodrigues, G., Silveira, A., Waterloo, M., Saleska, S. (2011). Height Above the Nearest Drainage – a hydrologically relevant new terrain model. Journal of Hydrology 404, 13–29.

NRCS (2007). Rosgen Stream Classification Technique — Supplemental Materials. National Engineering Handbook Part 654, Technical Supplement 3E. USDA Natural Resources Conservation Service, Washington, DC.

O'Callaghan, J.F., Mark, D.M. (1984). The extraction of drainage networks from digital elevation data. Computer Vision, Graphics, and Image Processing 28, 323–344.

Rennó, C.D., Nobre, A.D., Cuartas, L.A., Soares, J.V., Hodnett, M.G., Tomasella, J., Waterloo, M.J. (2008). HAND, a new terrain descriptor using SRTM-DEM: Mapping terra-firme rainforest environments in Amazonia. Remote Sensing of Environment 112, 3469–3481.

Riley, S.J. (1972). A comparison of morphometric measures of bankfull. Journal of Hydrology 17, 23–31.

Rosgen, D.L. (1994). A classification of natural rivers. Catena 22, 169–199.

Rosgen, D.L. (1996). Applied River Morphology. Wildland Hydrology, Pagosa Springs, CO.

Rosgen, D.L. (2022). Fluvial landscape types (field card). Wildland Hydrology.

Schwanghart, W., Scherler, D. (2014). TopoToolbox 2 – MATLAB-based software for topographic analysis and modeling in Earth surface sciences. Earth Surface Dynamics 2, 1–7.

Schwanghart, W., Scherler, D. (2017). Bumps in river profiles: uncertainty assessment and smoothing using quantile regression techniques. Earth Surface Dynamics 5, 821–839.

Soil Survey Staff. Soil Survey Geographic (SSURGO) Database. USDA Natural Resources Conservation Service, through Soil Data Access.

U.S. Geological Survey (2025). Cooperative National Geologic Map: Earth's surface geology. U.S. Geological Survey data release.

Williams, G.P. (1978). Bank-full discharge of rivers. Water Resources Research 14(6), 1141–1154.

Zhang, T.Y., Suen, C.Y. (1984). A fast parallel algorithm for thinning digital patterns. Communications of the ACM 27(3), 236–239.

Data services: USGS 3DEP and The National Map; the Watershed Boundary Dataset; BLM administrative boundaries; NAIP imagery from the USDA Farm Service Agency via Microsoft Planetary Computer; OPERA RTC-S1 (NASA JPL, distributed by ASF; contains modified Copernicus Sentinel data); NIFC InterAgency Fire Perimeter History; PRISM Climate Group, Oregon State University; NOAA Hydrometeorological Design Studies Center; Rangeland Analysis Platform.
