# Where this left off — pick up here

**Start with [OVERVIEW.md](OVERVIEW.md)** — what the tool does, how to run it,
and what to trust. This file is the working log behind it.

## Pick up here (state at the end of the 21 Oct 2026 session)

**State.** v0.42.1 beta. The full test suite passes on Linux with numba
0.61.2. **Work only on drive S:** (Sam's instruction; C: is nearly full).
The release is unzipped at `S:\geolens\gl33` (the folder name stayed; the
changed files of each release are committed into it, never while a run
uses it). The zips and git bundles (`geolens_v0.37.1.bundle`, holding
v0.30.0 to v0.37.1) are in `S:\geolens\Claude outputs`.

**Pick up here (21 Oct evening Arizona).**
- `S:\kingman\geolens_v38` is running v0.38.1 (started 1:25 pm, level B
  on every HUC12 with lidar, data steps, lidar calibration; about 8 to 9 am
  on 22 Oct). Check-ins are scheduled into the session every two hours.
  Do not start another run on that folder; do not change gl33 until it
  ends (a module imported later would be a different version).
- v0.39.0 (fans from the soils map) and v0.40.0 (stream power, major
  streams, the sensitivity table; the network tracer and the drainage
  per reach fixed, below) are released to `S:\geolens\Claude outputs`,
  not gl33. After the run ends: put v0.40 into gl33 and run the project
  tool once more on `geolens_v38`. TYPING 3 re-types everything in
  minutes, the summary follows, and the new last step (stream power,
  `major_streams`, `huc12_sensitivity`) runs on the finished layers. The
  level A pass itself is not redone, so that run keeps the old tracer's
  gaps (bridged in the synthesis) and the inherited drainage (corrected
  there); the next whole run gets both right at the source.
- The draft tables for Sam are in the scratchpad (`synth1/merged`: the
  major streams, the sensitivity table on the first run's data, without
  slope, RAP, fire or Atlas 14, which the v0.38.1 run brings). The
  weights are a proposal for him to change.
- Done since: the methods write-up (v0.40, `docs/`), the reconnaissance
  option, the GitHub workflow and the NHD names on the major streams
  (v0.41; the workflow and the NHD fetch untested live); every reach
  drawn downstream (v0.42). Next: the piedmont-surface fan test;
  the step list (StreamStats, USNVC/LANDFIRE, hydrography, the portable
  drive test).
- **The morning procedure (prepared 21 Oct, 5:25 pm):** when the v38 run
  has finished (expected about 6:30-7 am Arizona): (1) stage project.json,
  RESULTS.md and the merged layers, `review_kingman.py <staged root>`,
  report; (2) put v0.42.0 into gl33: the 32 files that changed since
  v0.38.1 are staged under `/mnt/user-data/outputs/gl41/` (in this
  container) with the commit call written out in the scratchpad's
  `gl33_commit.json` (device_commit_files, 32 entries into
  `S:\geolens\gl33\...`); without the container, unzip
  `geolens_v0.42.0-beta.zip` over gl33; (3) run the project tool once more
  on `geolens_v38` (same settings; it re-types under TYPING 3, retries the
  fire step, fetches the NHD flowlines, and adds stream power,
  `major_streams` with names and `huc12_sensitivity`: 15-30 min); (4) if
  before 8 am, start the whole-office run into `S:\kingman\geolens_v40`.
- **Sam, 21 Oct, 4:10 pm:** if the v0.38.1 run finishes before 8 am
  Arizona on 22 Oct, put v0.40.0 into gl33 and run the latest version on
  the whole field office into a new folder (`S:\kingman\geolens_v40`,
  level B on every HUC12 with lidar, all data steps; no more pilots). If
  it finishes after 8 am, report and hold; his fallback is Friday 23 Oct,
  4:30 pm Arizona, to run over the weekend (he will cancel a daytime run
  that slows his computer). The check-in prompts carry this.
- A fresh session without this container: unzip the newest
  `geolens_v0.4x.x-beta.zip` from Claude outputs (or clone the bundle) as
  the dev tree; `python -m pytest tests/ -q` is the gate.

**Sam's review (21 Oct):** the 1996 types had stayed the valley type; the
valley extent was the catchment ("like the Basin tool"); confinement at
10 m was wrong against the lidar. v0.38.0 answers all three (its entry
below) and the whole project is run again overnight with it.

**The first Kingman run (19 Oct, 3:57 pm to 6:23 pm Arizona, v0.35.0):** it
ran to the end on its own in 3 h 26 min: boundary, 446 HUC12s, the 10 m
DEM (1.2 GB, 70% of its grid), SSURGO (7,380 polygons), level A at 10 m
(1 h 52 min: routing 15 min, valley form 38 min, fans 4 min, valley types
15 min, valley extent 7 min; 86,783 reaches, 39,109 inside the boundary,
22,686 km), the representative pilot (3 groups: 39 / 32 / 29% of the
area), and level B on the three pilots on 1 m lidar at **3 minutes each**
(download included), so all 273 HUC12s with lidar would take about 14
hours. What the review found is in RUNNING.md ("What the first field
office taught the classifiers") and fixed in v0.37.1; the fan search and
the braided (D) calls are still open. Results: `S:\kingman\geolens`.

**Step list:** the Claude Doc "geolens step list" holds the work queue, for
Sam to review, approve and edit. Read it before choosing what to do next.

**Standing instructions (Sam, 19 Oct; until he says otherwise):**
- Keep working immediately after every session reset, without waiting for
  his approval. Claude schedules its own check-ins into the session.
- After the Kingman run: review, revise, re-release, report back.
- The end goal: one tool (geolens plus the watershed data package from his
  no-lidar work) that anyone can copy onto a drive, point at a project
  boundary, and leave to run on its own.

**Tonight (19 Oct, 5 pm Arizona):** the first real project run, the Kingman
BLM Field Office (`ADM_UNIT_CD='AZC01000'`), into `S:\kingman`. A
scheduled task starts it in this session. It runs on the existing
environment unless `setup_portable.bat` succeeds first; nothing goes on C:.
**Started at 3:57 pm on 19 Oct** (Sam said to go when ready), in a second
ArcGIS Pro window so his open Ruidoso project was not touched: project
`S:\kingman\kingman.aprx`, results `S:\kingman\geolens`, v0.35.0 from
`S:\geolens\gl33`. Watch `project.json` there; **never start a second run
on that folder while one is going** (re-running the tool after it stops
carries on from `project.json`). By 4:25 pm: boundary, 446 HUC12s (309 in
the field office, 41,053 km²), the 10 m DEM (plan: 70 GB peak, tight) and
SSURGO (7,380 polygons; 15,617 km² alluvial, 5,035 colluvial) were done; the
10 m level A pass was running.
**Scope (Sam, 19 Oct):** a landscape-scale vegetation management project
looking at geomorphology, not stream restoration. So the core product is
level A and soils over the whole field office, summarized by subwatershed,
soil type and channel type, and the level B pilot is *representative*
(v0.34.0), not the most unconfined valley. If BLM sends planned treatment
areas, centre the pilot and the summaries on them.

**Not yet done:**
- **GitHub: Sam pushes it.** The repo `github.com/samNCDE/geolens` exists
  (empty on 19 Oct) and is now meant to be the home of the code. Claude's
  session had no GitHub access to it, so Sam double-clicks
  `S:\geolens\push_to_github.bat`. It clones the newest bundle in Claude
  outputs into `S:\geolens\repo`, or fast-forwards that copy, and pushes
  `main`. It never forces. It needs git: Git for Windows, or the portable
  Git unpacked into `S:\geolens\PortableGit`. GitHub asks him to sign in
  once, in the browser.
  - **Before the next release, start from GitHub, not from a bundle:**
    fetch `main` from the repo and commit on top of it, so anything Sam
    changed there is kept, and the push stays a fast-forward.
  - A session with push access to the repo can push directly instead.
- **Untested in ArcGIS Pro.** The toolbox is tested against a stand-in for
  arcpy only. Run every tool once on a small area; the 30-minute pass is in
  `docs/ARCGIS_TEST_CHECKLIST.md`. Watch especially:
  - the geodatabase attachments and pop-ups;
  - Fetch a DEM, NAIP, SSURGO and Sentinel-1 (live services);
  - 1b. Calibrate.
- Fetchers are untested against the live services (they are tested offline
  with stubs). Sentinel-1 is the newest, and the most likely to need a fix
  on first contact with NASA's servers (login redirect, URL patterns).

**Next, in order:**
1. Sam's first real runs in ArcGIS Pro, and fixing what they turn up.
2. SAR: the experiment in [docs/SAR_FEASIBILITY.md](docs/SAR_FEASIBILITY.md)
   with the new *Sentinel-1 radar statistics* tool. Sam has an Earthdata
   login; he saves it with *Save Earthdata login* (never in code). If
   riparian and field polygons separate on `sar_vh_cv`, build the
   "field-like" flag on the riparian band.
3. SSURGO against USGS geology for valley types: run step 2 both ways on
   Ruidoso and Eagle Creek and compare. If SSURGO's are better, make it
   the toolbox default.
4. Rosgen's new valley-type names (waiting on Sam's table).
5. Validation against surveyed bankfull.

**Found this session:** a fresh install pulled numba 0.68, which cannot
compile pysheds' depression filling. The landscape pass died at its first
step with "KeyError: int64 generator". numba is now capped below 0.62 in
pyproject.toml and install.py. Existing environments are unaffected.
From v0.30.1 an environment upgraded past the cap is caught in three
places: *Find the geolens environment* warns, `install.py --check` reports
it as a problem, and the routing error names the fix.

## v0.42.1 (beta): soils that arrive after the pass re-type the valleys

The night the v0.42 whole-office run started (21 Oct, 10:10 pm), USDA
Soil Data Access would not answer, so the run fell back to USGS geology
for the valley materials and carried on (the NHD fetch, new in v0.41,
worked live: 29,728 flowlines, 17,358 named, in 23 minutes). A project
whose pass was typed without SSURGO now re-types its valleys from the
saved valley form the next time the soils are fetched (`pass_a.geology`
set to the soils, `typing` reset, the USGS warning withdrawn), in
minutes, instead of the two-hour pass being redone; test in
test_project. The run itself: re-run the tool on `geolens_v42` with
v0.42.1 in gl42 once SSURGO answers.

Also that night: the second ArcGIS Pro window ran the gl42 toolbox (a
fresh folder `S:\geolens\gl42` written file by file; the toolbox runs
the geolens beside it, so gl33 stayed v0.38.1 for the v38 run), and the
link to the computer dropped at 11 pm for the rest of the night.

## v0.42.0 (beta): every reach drawn downstream

Step 9 of the step list ("42% are digitised uphill; profiles and headcut
direction need the right way round"). `extract_network` now turns every
traced branch to run downstream: the filled DEM never rises along a
stream, so a branch whose last cell stands higher than its first is
reversed; a flat one (under 0.5 m either way) goes by the accumulation
along it, medians of the two halves (single cells mislead: the skeleton
wanders on and off the channel cells, and the junction cell at an end
carries whichever path's water). A path that climbs more than 10 m over
an interior high point crosses a divide (two headwaters whose stream
cells touch) and is split there. On the 30 m test landscape (476
branches): 3 still rise, by under 0.5 m, and no reach falls more than
10 m the wrong way. `drop_m` is now the fall; `flow_oriented` is on the
layer. A layer traced earlier (the Kingman runs) is turned round by the
synthesis step instead, lines and fall columns together, `flow_reversed`
recording which; the merged valley types come out downstream either way.

## v0.41.0 (beta): reconnaissance runs; the major streams named from the NHD

**NHD names.** A project fetches the NHDPlus HR network flowlines over its
HUC12s (`wsdata.nhd_flowlines`: GNIS-named, or draining at least the
major-streams threshold; simplified to 20 m; step `nhd`, resumable, a
warning if the service fails) and `synthesis.name_streams` gives each
major stream the name covering at least half its length within 150 m,
the mouth zone left out so a tributary does not take the river's name;
`nhd_totdasqkm` at the mouth and `drainage_ratio` are the cross-check
Sam asked for (rivers entering from outside the HUC8s show under 0.5).
`major_streams` now carries `mouth_x`, `mouth_y`. The service is
reachable from Sam's machine, not from here (the proxy refuses the host),
so the first live fetch is the re-run on `geolens_v38`.


Sam's item 4 ("github cloud integration so i can use this from my laptop
for recon using cloud computing"). `run_project(level_a_cell_m=30)`
(`--cell-a`, the toolbox's *Level A cell size*) fetches the 1/3
arc-second DEM straight onto a coarser grid, with the warning in
RESULTS.md; and `.github/workflows/recon.yml` is a `workflow_dispatch`
job (boundary URL + where or a committed file, cell size, level B none or
a short pilot, the data steps) that installs geolens from the repo, runs
`geolens project`, and uploads `merged/`, RESULTS.md, project.json and
the data CSVs as an artifact. Untested on GitHub (no access from here;
Sam's push puts the repo there): the first dispatch is its test. Things
most likely to need a fix: pip-installing the landscape extras on
ubuntu-latest (rasterio/geopandas wheels, numba < 0.62), the runner's
network reaching 3DEP, the WBD, BLM and SSURGO, and the 6-hour limit on a
big boundary (level B off, or a 1-HUC12 pilot with `--hours 2`).

## v0.40.0 (beta): stream power, major streams, the sensitivity table

Sam's second item for the session ("lets do it in that order"): stream
power and the per-HUC12 sensitivity synthesis as a draft with the weights
for him to decide, and "contributing drainage area to each stream, or at
least major streams". `geolens.synthesis`, the last step of a project
run (RUNNING.md, "Stream power, major streams and the sensitivity table"):

- `stream_power`: Q from the curve's discharge relation (Moody et al.
  2003 publish one per province; the generic US curve has none), Ω = ρgQS
  and ω = Ω / W per reach.
- `trace_streams`: `stream_id` from every outlet up the larger drainage
  at each junction, `dist_to_mouth_km`, `flows_into`,
  `stream_km2_at_mouth`. Written against the first Kingman run's merged
  reaches, which taught it three things about the network layer:
  1. **The tracer dropped connecting paths.** `extract_network` dropped
     every traced path under 300 m as a spur; a short path *between two
     junctions* is the stream itself, and a wide wash's skeleton (side
     loops everywhere) is full of them. 1,323 of 39,109 reaches had a gap
     of up to 400 m below them and the main stems came out in pieces of
     1-17 reaches. Fixed at the source (a short path between two
     junctions is joined onto the neighbouring path carrying the most
     flow, the short side of a loop dropped, and only a path with a free
     end is a spur; the 8-connected skeleton's staircase triangles made
     3,620 two-cell stubs on the 30 m fixture alone, which had cut that
     network at every one and hidden 30% of its length) and bridged in
     the synthesis for layers already traced:
     a downstream end with no reach at its node joins the reach whose
     line passes within two cells, else the nearest within 400 m, in
     every case one carrying at least 95% of its own drainage.
  2. **Tributary mouths inherited the river's drainage.** Every path ends
     on the junction cell; the id raster gave that cell to one of the
     paths meeting there; when it was the tributary's, `acc_max` took
     the river's accumulation: 264 mouth reaches carrying 1,000-3,800 km²
     above a reach of 10 (and a bankfull width from the curve to match,
     so their confinement ratio was wrong too). `classify_segments` now
     leaves junction cells (three or more skeleton neighbours) out of
     the per-segment maximum; the synthesis flags `drainage_inherited`
     on existing layers and uses the reach above (`drainage_km2_used`)
     for the stream power and the stream's drainage.
  3. **Two reaches ending at one confluence with nothing below receive
     each other** (each lies on the other's line), and a ring does the
     same; cycles are cut at their biggest reach, which is the outlet.
     Parallel threads of one wash belong to the river's stream.
  Before the three: 14,552 streams, the 3,798 km² wash in 9 pieces.
  After: 5,848 streams, 534 outlets, the three biggest main stems whole
  (60-111 km). Flow direction is the fall along each branch on the filled
  DEM; half the reaches are drawn uphill (`flow_reversed`).
- `major_streams`: one line per stream draining ≥ 25 km² at the mouth
  (332 on Kingman's first run, 90 over 100 km²), with drainage at the
  mouth, length, gradient, stream power, dominant landscape type, fan and
  unconfined shares, the HUC12 at the mouth. No names yet (NHD next).
- `huc12_sensitivity`: percentile ranks of ten components of the HUC12
  summary (K, HSG D, slope, alluvial share, unstable form, unit stream
  power, bare ground and its trend, fire, the 100-year storm), weighted
  (0.15 / 0.10 / 0.10 / 0.15 / 0.15 / 0.10 / 0.10 / 0.05 / 0.05 / 0.05),
  cut into thirds, with `sensitivity_drivers` naming the components that
  put each HUC12 where it is, `weight_measured`, and `insufficient data`
  under half the weight. Weights from a JSON file re-rank on the next
  run. On the first run's data (no slope, RAP, fire or Atlas 14 yet):
  Iron Spring Wash, Upper Sycamore Creek, Crow Canyon, Hibernia Canyon,
  Rock Creek top the table on K, stream power and alluvial valley.
- Wired into `run_project` (`major_streams_km2`, `sensitivity_weights`),
  the CLI (`--major-km2`, `--weights`), the toolbox (two parameters, two
  outputs, styled), RESULTS.md (a section with the weights, the largest
  streams and the most sensitive HUC12s). 12 tests on a drawn network.

## v0.39.0 (beta): fans from the soils map

While the v0.38.1 run went (21 Oct afternoon), from Sam's "what should we
use the session for": the SSURGO `landform` field per map unit names fan
surfaces outright ("fan terraces" 6,889 km2 of Kingman, "alluvial fans"
765, "fan remnants", "fan piedmonts"), which is what the cone test cannot
see on coalesced fans. `fan_from_landform` reads the words (relict:
terraces, remnants, piedmonts, bajadas, ballenas; building: alluvial fans,
aprons, skirts, inset fans, cones); `attach_material_landform` carries the
dominant landform beside the material, with a spatial index (56 s for
39,109 reaches against 7,380 polygons; the scan per reach took about 15
min); a reach on a fan surface is Type III and `U-AL-IF` / `U-AL-AF` at
medium confidence (the DEM-only fan stays AF low with IF open), its
material alluvial if unresolved. Columns `landform`, `fan_source`,
`fan_activity`. `TYPING` 3, so the v0.38.1 run is re-typed on its next
run. Tested on the first Kingman run's reaches: fans 0.1% -> 43% of stream
length, U-AL-IF 59%, U-AL-AF 4%.

Sam, same afternoon: GitHub cloud runs from the laptop for recon (the
wishlist item; a `workflow_dispatch` action taking a boundary, level A at
20-30 m within the runner's memory, results as an artifact) and a
**major-streams layer** with contributing drainage area at each stream's
mouth (`drainage_km2` is on every reach already; dissolve by NHD name or
branch above a threshold; NHDPlus HR `TotDASqKm` as the cross-check for
rivers entering from outside the HUC8s). Both on the step list.

## v0.38.0 (beta): the valley, measured; the 2022 type as the valley type

Sam, 21 Oct, reviewing the Kingman results: the output still used the old
(1996) valley types; how were the valleys generated, was it like the Basin
tool's output ("there are better ways than that"); be self-critical and
improve it; then an autonomous rerun overnight at 5 pm on the whole
boundary, and a review.

**How the valleys were made (v0.33 to v0.37):** VBET-style evidence (HAND
weighted 0.65, slope 0.35, drainage-scaled thresholds), the valley bottom
allocated to the nearest reach, `valley_width_m` = allocated area / reach
length, confinement = that over a generic channel width with the published
breaks 2 / 5, valley form measured on cross-sections floor-to-divide, and
"valley extent" polygons = each reach's catchment (every cell draining to
it). The catchment IS the Basin tool's answer: a watershed, not a valley.

**Against the lidar pilots (443 reaches measured on 1 m sections):** the
HAND-based ratio tracked the lidar entrenchment ratio at Spearman 0.38;
the sections' valley floor width at 0.77 and the side slope at -0.74. The
lidar called 45 km of the pilots entrenched; the 10 m pass called 35 of
those km unconfined and 0.6 km confined. Fitting new breaks on the HAND
ratio gained 2 points (53% -> 55% agreement) and did not transfer between
HUC12s; on the floor ratio 54% -> 66% (70% on the raw floor width), with
breaks 5.7 / 22.9 giving 10% confined, 37% partly, 52% unconfined over the
field office instead of 1 / 7 / 92. Three pilots are too few for a stable
fit (held out one at a time: 0.37 to 0.84); the whole-office lidar run is.

What changed:

- **The 2022 landscape type is `valley_type`** (`valley_type_1996` beside
  it, `landscape_*` kept as aliases); `form_status` is judged on the card,
  `form_status_1996` on the 1996 table; the toolbox colours the 2022 codes;
  a detected fan is `U-AL-AF` with `U-AL-IF` open rather than blank.
- **Confinement from the measured valley floor** (`confinement_from_form`,
  run inside step 5 after the sections; `confinement_basis`,
  `valley_floor_width_m`, the HAND figures kept as `*_hand`). The layer
  every later step reads is `reclassified_dir/segments_classified.gpkg`.
- **Breaks calibrated to the lidar in the project run**
  (`fit_confinement_breaks`: coordinate descent over every cut,
  length-weighted, from the published breaks; applied when 5 points
  better; the agreement table in RESULTS.md; `calibrate_confinement`).
- **Valley polygons = the valley bottom split by reach**
  (`valley_polygons` from `classified_dir/valley_alloc.tif`, which
  `classify_segments` now writes); `valley_catchments` stays as an API.
- `keep_lidar_dem`: with level B "all" each HUC12's 1 m mosaic is deleted
  once measured (about 300 MB each; 273 HUC12s would be 80 GB on S:).
- (v0.38.1, the same afternoon) **D needs more than one channel**: threads
  counted on the lidar section at the bankfull stage across the
  flood-prone width (`_threads`; the bankfull width itself is the first
  wetted run out from the thalweg, so it always holds one). On pilot
  150302020405, 30 of 32 D reaches were single-thread: they become C or F
  by entrenchment. `threads_at_bankfull`, `single_thread` in the layer.
  **Fan caps**: the radius cap is 8% of the DEM's short side (3 to 12 km)
  and the auto-raised slope floor stops at 3%. Bajadas still fail the
  cone test (they are not cones): a piedmont-surface test is next.

**Tonight (21 Oct, 5 pm Arizona):** a scheduled task starts the whole
project again with v0.38 into `S:\kingman\geolens_v38` (the first run
stays), level B on every HUC12 with lidar (about 14 h), data steps,
calibration at the end, summary. Sam approves one ArcGIS Pro access prompt
at 5 pm. Then the review.

## v0.37.1 (beta): what Kingman taught the classifiers

The review of the first whole-landscape run (above), the same night:

- 1,530 km (6.7%) of Kingman's stream length was Rosgen valley type V,
  a glacial trough, on alluvium and colluvium at 35° N: the form classifier
  took a parabolic cross-section as proof of a trough, and at 10 m a wash
  between two concave bajada flanks fits one. V now needs glacial till; a
  mapped material that is not till hands the call to the fluvial types
  (the U-shape is noted in `basis`); no material keeps V at low
  confidence with II open. Re-typed, V is 0.5%.
- 22% of stream length had no 2022 landscape type ("the card has no
  unconfined type built of mass_movement material", "no material") and
  6% no 1996 type ("VIII|IX|X"): the soils map unit over a narrow floor is
  the hillslope's. An unconfined floor is now taken as stream-built:
  VIII at low confidence (IX, X open) and `U-AL-*` at low confidence with
  the map's word in the basis; confined valleys keep the map's material.
  Gentle, not-broad side slopes: IV when confined, VIII when unconfined.
  Partly confined reaches lean to the card's unconfined type. Re-typed,
  the unresolved landscape types are 5% and the 1996 ones 2%.
- `project.TYPING` (now 2) is recorded with `pass_a`; a project typed
  under an older revision is re-typed on the next run from its saved
  valley form and fans (minutes), the valley extent polygons are rebuilt,
  and the level B form of every HUC12 already done is judged again.
- `huc12_summary`: soil shares are of the HUC12's mapped part, with
  `soil_mapped_share` saying how much that is (the soils are fetched over
  the boundary, not the whole HUC12).

Open, from the same review (on the step list):

- **Fans.** The search was built on a 49 km² DEM: its 3,000 m radius cap
  and the slope floor it raises itself (to 4.9% at Kingman) exclude the
  bajadas, which are 5 to 15 km long at 1 to 4%. 134 fans accepted, 1,190
  candidates rejected, Type III under-called. The cap should scale with
  the DEM and the floor should not rise above the regional fan slope.
- **Braided (D).** 48% of the pilots' stream length is D or Db, typed on
  width-to-depth alone (step 7 of the list). D is natural to `U-AL-AD` on
  the 2022 card (`landscape_form` says check) and unstable for VIII on
  the 1996 table (`form_status` says unstable: 42% of pilot length).
  Sam's call which the map carries.
- Confinement at 10 m: 89% of stream length is unconfined, 2% confined,
  in country with real canyons. The valley bottom at 10 m over-widens
  narrow canyons; the channel width is the generic relation. Level B on
  lidar is where confinement is right; a 10 m confinement audit against
  a lidar HUC12 would say how far to trust the rest.

## v0.37.0 (beta): the watershed data package inside the project run

Sam: "integrate what we've learned and built in that chat into geolens, I
think the two together will be what I'm looking for" — one tool. The data
sources from his no-lidar watershed package (`data_prep.py`, Black Mesa
Unit 407) that a vegetation management plan's hydrologic section needs are
now steps of a project run, in `src/geolens/wsdata.py`:

- `fire` (NIFC perimeter history, through the patient `query_layer`),
  `ecosites` (SSURGO `coecoclass` on the project's soils), `prism`
  (1991-2020 normals, direct zips: the web service has no normals),
  `atlas14` (PFDS at each HUC12's centre, the `cgi_readH5.py` URL from
  NOAA's FAQ, answers cached in `data/atlas14.json`) and `rap` (cover v3
  1986 on, windows from the remote rasters at the coarsest resolution a
  HUC12 mean needs, GDAL's cache capped at 512 MB: the 100 GB lesson).
- Each is its own resumable step (`data_<step>` in `project.json`); a
  failure is a warning the next run retries. `data="all"` is the default;
  toolbox *Watershed data per HUC12*; CLI `--data`.
- `merged/huc12_summary.gpkg` + `.csv`: one row per HUC12 inside the
  boundary — streams by confinement, valley and landscape types, soils by
  material and hydrologic group, K, slope, stream form km (1996 and 2022),
  and every data step's columns. No score: the analyst's table.
- `RESULTS.md` gets a "Watershed data by HUC12" table.
- `check_services` tries NIFC, PRISM, Atlas 14 and RAP too.
- The toolbox adds derived outputs *HUC12 summary*, *Soils with ecological
  sites* and *Fire perimeters*.

Tested offline with stand-ins for every service (`tests/test_wsdata.py`,
14 tests) and inside the project run (stubbed data steps; a failed step
is a warning and is retried). Live: RAP read from Claude's sandbox at
13 s a year over a Kingman-sized window; NIFC, PRISM and Atlas 14 are
blocked from the sandbox (proxy 403), so their first live contact is
Sam's machine. The `services` check will say.

**Kingman:** this release was built while the v0.35 run was going, so it
went to `Claude outputs` only. Once the run has stopped, put v0.37 in
gl33 and run the project tool again on `S:\kingman\geolens`: it resumes
from `project.json` (boundary, HUC12s, DEM, soils and level A are kept),
runs the five data steps, and writes the summary. A v0.35 `pass_a` record
has no `slope` entry; the summary finds `A_landscape/run/**/slope.tif`.

## v0.36.0 (beta): pick a boundary, let it run

The end goal Sam set: copy geolens onto a drive, pick a project boundary,
and leave it to run.

- `run_project.bat`: drag a boundary onto it (or paste a path, or a map
  service URL and a where clause). It finds the drive's own `env`, then the
  installed environment, runs this release's `src`, and pauses at the end
  with where the results are.
- `geolens project <boundary>`: the same from a shell, with the toolbox's
  options; the folder defaults to `<boundary name>_geolens` beside it.
- A boundary of lines or points (a wash, a few springs) is buffered by
  250 m into a polygon, as in Sam's data package.
- Level A that runs out of memory is run again at twice the cell size (up
  to twice), with a warning, instead of stopping the night's run. Kingman's
  plan at 10 m was "tight": a 70 GB peak.
- Checked on 19 Oct: pysheds routing works under numba 0.67.0 with NumPy
  2.4 (Sam's environment); the *Find the geolens environment* warning about
  numba above 0.61 is conservative for 0.67. 0.68 still fails.

## v0.35.0 (beta): Rosgen's 2022 fluvial landscape types

Sam sent Rosgen's 2022 field card (confined and unconfined fluvial
landscape types) and asked for it in the Kingman build.

- `landscape/landscapes.py`: the card's 25 types (11 unconfined, 14
  confined), each with its slope rule, natural stream types and
  disequilibrium ones; `classify_landscape_type` from confinement, channel
  gradient, material, fans and valley shape; `landscape_form` judges a
  reach's stream type (with its slope class) against the card, under each
  candidate when the type is unresolved.
- Valley-types layers carry `landscape_type`, `landscape_name`,
  `landscape_alternatives`, `landscape_confidence`, `landscape_basis`;
  stable / unstable layers carry `landscape_form` and `landscape_note` beside
  the 1996 `form_status`. Sam picks which drives the map after Kingman.
- Projects fetch SSURGO before level A and, with `geology="ssurgo"` (now
  the default for projects, and the toolbox's *Valley material*), type the
  valleys by it. If SSURGO fails, USGS geology, with a warning.
- Open: active vs inactive fans, sand vs loess, inner gorges, partly
  confined reaches; glacial and periglacial classes. RUNNING.md, "Rosgen's
  2022 fluvial landscape types".

## v0.34.1 (beta): lessons from the no-lidar watershed work

Sam's notes from another project (no lidar; a watershed data package built
in a separate chat) are in the geolens project's knowledge and in
`S:\geolens\notes`. They stay out of this repo because they name a client
and contacts. Taken in now, because they bear on tonight's run:

- **Slow map services** (`query_layer`): 500 features a page instead of
  1,000, a 120 s timeout, and a page that fails, or comes back as an ArcGIS
  `{"error": ...}` reply with HTTP 200, is asked for again at half the size,
  down to 50. Paging follows `exceededTransferLimit`, so a server whose own
  page limit is below ours is read to the end.
- **How far to trust the 10 m DEM:** `RESULTS.md` carries the ICESat-2
  accuracy of 10 m 3DEP without lidar by slope class (LE90 0.66 m on 0 to
  10%, 2.43 m on 10 to 20%, 6.64 m over 20%), and says that 10 m 3DEP
  without lidar predates recent incision and headcuts.
- RUNNING.md: "Which DEM, and how far to trust it": the fallback order,
  licences, and why not radar topography.

Next, on the step list for Sam's approval: the rest of the watershed data
package (RAP vegetation trend, fire history, PRISM and Atlas 14, ecological
sites, NHDPlus HR, wetlands, gauges, the GLO-30 fallback, an ICESat-2 check,
roads and crossings, stock tanks), ideally ported from Sam's tested
`data_prep.py`.

## v0.34.0 (beta): a pilot that represents the whole area

Sam, on Kingman: "a landscape scale veg management project looking at
geomorphology". A pilot in the most unconfined valleys suits stream work,
not a landscape plan, whose 1 m results must scale up to the whole area.

- `run_project(pilot_by=...)`, and a choice in the toolbox's *Level B*
  list:
  - **`"representative"`** (now the default; landscape projects): every
    HUC12 inside the boundary gets a profile, its stream length by valley
    type (level A) and its area by soil hydrologic group plus surface
    erodibility K (SSURGO, sampled on a grid of about 300 points per
    HUC12). The shares in each block sum to one, so neither block outweighs
    the other. PAM k-medoids, weighted by the area inside the boundary,
    sorts them into `pilot_count` groups. Each group's most typical HUC12
    that has lidar and is mostly inside the boundary runs.
  - **`"unconfined"`** (stream projects): the old rule.
- `huc12_status` gets `pilot_group`: the pilot whose level B results stand
  for each HUC12, the ones without lidar included. `RESULTS.md` lists the
  pilots and the share of the area each stands for. `project.json` keeps
  the plan.
- Without valley types, the profile uses confinement; without either, the
  HUC12s' area alone.
- Tests: the medoids, the representative pick (a typical canyon and a
  typical basin out of six HUC12s plus a sliver), a pilot without lidar,
  the fallbacks, soil groups (dual groups count as D), the toolbox choice,
  and the project run.

## v0.33.1 (beta): Windows line endings for the batch files

- `setup_portable.bat` and `start_notebook_portable.bat` were written with
  LF-only line endings. cmd.exe can miss a `goto` label in such a file, and
  the portable setup jumps to one whenever a step fails. Both now have CRLF,
  like `install_windows.bat`.
- `.gitattributes` keeps it that way: batch files are stored with CRLF and
  never converted; shell scripts stay LF, even in a Windows checkout.
- `tests/test_line_endings.py` checks the bytes of every `.bat` and `.sh`.
- `setup_portable.bat` run a second time now updates the existing `env` in
  place (`micromamba install`), as its message said, instead of rebuilding
  it from scratch.
- `push_to_github.bat` (beside the release, not in it) puts the release on
  GitHub; see "GitHub" above.

## v0.33.0 (beta): projects of any size, portable drive, audit fixes

- **Run a project area (by HUC12)** (`project.py`, `api.run_project`,
  toolbox *1 Whole routine*):
  - **Level A:** 10 m over the boundary's HUC12s plus those draining into
    them, for valley types everywhere.
  - **Level B:** HUC12 by HUC12 on 3DEP 1 m lidar, for stream types,
    plots and stability. Pilot, all, listed or none.
  - **Merged layers:** carry the cell size they were measured on;
    `valley_resolved` flags valleys under three cells wide.
  - **Records:** `huc12_status` and `RESULTS.md`. The run resumes from
    `project.json`.
  - The boundary can be a layer, or an ArcGIS REST layer with a where
    clause (`query_layer`, paged, safe against servers that ignore paging).
  - The regional curve is picked by location, with Bieger et al. (2015)
    outside the arid Southwest.
- **Portable drive** (`setup_portable.bat`, `start_notebook_portable.bat`):
  - geolens's own Python is built in `env` with micromamba; nothing is
    installed on the computer.
  - The runner uses only that env, and puts TMP, caches and the
    Earthdata login on the drive.
  - The toolbox always runs the geolens in its own `src` (`GEOLENS_SRC`), so
    toolbox and code cannot be different versions, and a release runs
    wherever it is unzipped, without re-pointing an environment.
- **Check internet services** (`services.py`, toolbox *0 Setup*).
- **Regional curves from the report itself.** Sam supplied Moody et al.
  (2003), `Arid_SW_Report_Regional_Curves.pdf`:
  - `"auto"` now uses its Figure 2 map: the division near 109.6° W and the
    study region about 114.0–104.0° W, 31.6–38.1° N. Within a degree
    outside, the nearest province is used with a warning; beyond that,
    Bieger.
  - `"auto"` is the toolbox default in every stream-typing tool.
  - Both provinces now also carry the report's bankfull cross-sectional
    area and discharge relations, converted to metric.
  - The report's "local calibration curve": the regional slope with a local
    offset fitted to five or more surveyed sites. It is on the step list.
- **Projects fetch SSURGO** over the boundary. Soils now carry `kwfact`
  (surface-horizon erodibility), for the Kingman scope of work.
- **Audit of Sam's real runs** (v28.1: Ruidoso 10 m with 1 m channels, Eagle
  Creek, Cherokee Bill):
  - *Fixed:* reaches with no valley bottom (73 of 858 at Ruidoso, 8.5%)
    were classed "confined" (ratio 0) while the warning said unknown. They
    are now unknown.
  - *Fixed:* the notebook set the confinement breaks at each landscape's
    own 25th and 50th percentiles, so every landscape came out a quarter
    "confined". At Eagle Creek that made "confined" mean up to 20× the
    channel width. It now uses the published 2 and 5.
  - *Found, for the step list:*
    - The USGS geology called every reach bedrock on all three sites, so
      no reach was alluvial and valley types collapsed to I/II. Half of
      Ruidoso's reaches are "check".
    - 15% of Ruidoso reaches are typed D (braided) on W/D > 40 alone.
    - 42% of segments are digitised uphill (z_end > z_start).

## v0.32.0 (beta): SSURGO soils, as a finer geology for valley floors

- **SSURGO soils** (`soils.py`, `api.fetch_soils`, toolbox *0 Get data ▸
  SSURGO soils*, and `geology="ssurgo"` in the pass, step 2 and the
  toolbox's geology choice). Map unit polygons come from USDA Soil Data
  Access, in 0.1° tiles; a tile the service cannot answer is split. Each
  polygon gets the dominant component's parent material as
  `material_class`, in geology.py's vocabulary, so valley typing and the
  stream-type substrate use it unchanged.
- **Why.** geology.py documents the national map's weakness: at 1:50,000 to
  1:1,000,000, a narrow valley comes back as its hillslopes' bedrock.
  SSURGO is about 1:24,000 and usually maps the valley floor's own
  alluvium. Sam asked for an SSURGO fetch early on ("download ... soil data
  from SSURGO ... by pointing at a shapefile").
- **Choices made:**
  - "slope alluvium" and "valley side alluvium" are mass movement, not
    alluvial: they are hillslope wash.
  - Volcanic ash and fill get no class.
  - The parent material is the representative group's uppermost one.
- **Tested offline only,** against a stand-in for SDA. No connection from
  the sandbox.

## v0.31.0 (beta): Sentinel-1 radar statistics; styling that survives a reload

- **Sentinel-1 radar statistics** (`sar.py`, `api.sentinel1_stats`, toolbox
  *0 Get data ▸ Sentinel-1 radar statistics (OPERA RTC)*). NASA's OPERA
  RTC-S1 is found in CMR (collection `C2777436413-ASF`, no login). Each
  burst's VV, VH and mask are read onto the area's 30 m UTM grid. The tool
  writes:
  - a tile per pass, reused on a rerun;
  - per-track statistics (median VH and VV in dB, VH/VV, CV of VH, passes),
    then combined;
  - optionally, the statistics per polygon.

  Reading streams through GDAL with the login and a cookie jar, and falls
  back to whole-file downloads with Python's HTTPS when GDAL cannot get
  through, as NAIP does. Tested offline only.
- **Save Earthdata login** (*0 Setup*). Sam asked to hardcode his login.
  It is not hardcoded: geolens goes to GitHub and to coworkers, so the
  password would be in every copy and in git history. Each person saves
  their own into `%USERPROFILE%\_netrc`. The toolbox writes the file in
  ArcGIS's own Python (`geolens_runner.save_earthdata_login`), so the
  password never passes through the geolens subprocess, its run records or
  its messages. A test checks that. `geolens.sar.save_login` does the same
  for notebooks, and a test keeps the two writers in step.
- **Styling no longer depends on memory between execute and postExecute.**
  The .pyt reloads `geolens_arcgis` when Pro imports it, which would have
  wiped two dictionaries (which QML styled which gdb layer, and which layers
  had attachments) before postExecute read them. The colours from the QML
  and the attachment pop-ups would then both have been lost silently. Both
  are now read from disk: `_styles/<name>.qml` beside `geolens.gdb`, and
  ArcGIS's `<name>__ATTACH` table. The toolbox tests now reload the module
  between execute and postExecute, as Pro may.
- **Every `ga.run(...)` call is checked against the API.** A test checks
  that the function exists and takes the keyword arguments the tool passes,
  so a typo cannot wait for a run in Pro to show.
- **START_HERE.html** rendered its nested lists as plain text with dashes.
  It is now regenerated with them indented the way Python-Markdown needs.

## v0.30.1 (beta): editable association table, numba guard, SAR note

- **Editable valley–stream association table.** It is
  `docs/valley_stream_associations.csv` (the built-in TS3E-3 table; a test
  keeps the two identical). Pass an edited copy to `api.stream_stability(...,
  association_table=)` or to toolbox step 4 (*Association table*). It is
  checked first, with row numbers in the errors. The run keeps a copy
  (`association_table.csv`), so steps 2 and 3 re-judge with it. Passing
  `"builtin"`, or the toolbox's *Go back to the built-in table*, drops the
  copy. `form_source` names the table used. Not yet in the whole routine:
  run step 4 with the table after it.

- **numba guard.** The environment probe reports numba's version. *Find the
  geolens environment* (ArcGIS) and *Setup* (QGIS) warn at 0.62 or newer.
  `install.py --check` lists it as a problem. A routing failure raised
  inside numba ≥0.62 becomes "pysheds needs numba below 0.62 … pip install
  "numba>=0.57,<0.62"". It stays quiet on out-of-memory errors and on
  numba versions the pin allows.
- The doctor's fix for a numpy/numba ABI mismatch said `pip install -U
  numba`, which would now install 0.68 and break routing. It now names the
  pinned range.
- **`qgis_plugin/build_plugin_zip.py`** rebuilds the plugin zip. A test fails
  while the zip and the plugin folder disagree. The v0.30.0 zip matched its
  source; nothing before this checked that.
- **docs/SAR_FEASIBILITY.md**: see "Next" above.

**New this session:** *1b. Calibrate confinement breaks*, for the toolbox and
as `api.calibrate_confinement_for_run`. Later steps use the calibrated
reaches (`reclassified_dir`) when they exist.

## v0.30.0 (beta): data fetchers, NAIP tiles, plots as attachments; GitHub-ready

**New:**
- **Fetch a DEM** (`dem_fetch.py`, `api.fetch_dem`):
  - searches The National Map for 3DEP 1 m tiles and fills gaps from the
    1/3 arc-second tiles (named by corner, so no search is needed);
  - streams windows via /vsicurl/, falling back to a whole-tile download;
  - writes a newest-first mosaic;
  - on a foot CRS, converts z to feet so geolens reads it right.
- **NAIP tiles** (`api.naip_tiles`):
  - per-scene NDVI and RGBN tiles and NDVI and true-colour mosaics, in the
    folder the user chooses;
  - skips older scenes where newer ones cover the area, reuses tiles on
    disk, and pages the STAC search;
  - has no 400M-cell limit.
- **Mosaic writer** (`mosaic.py`), shared by both fetchers.
- **Plots as file geodatabase attachments** (default): the toolbox copies
  its layers into `run/geolens.gdb`, sets missing plot paths to null, then
  runs EnableAttachments and AddAttachments. ArcGIS adds an attachments
  element to the default pop-up for such layers (Esri: "If the layer has
  attachments, an attachment element is added"). A renamed rerun of a step
  gets prefixed names, so reruns do not overwrite each other in the gdb.

**Tested:** offline, with the services stubbed. The live services are
untested here; run the tools once on a small area first.

**Repo:** the README is now a front page, and the old developer README is
`docs/python_api.md`.

**Next, in order:**
1. SAR (Sentinel-1 RTC on the Planetary Computer), streamed and used the way
   NAIP NDVI is. Sam wants to return to this.
2. Rosgen's new valley-type names (waiting on Sam's table).
3. Validation against surveyed bankfull.
4. SSURGO soils fetch.
5. Confinement calibration as a toolbox tool (notebook-only today).

## v0.29.1: the rest of the toolbox

Sam said yes to adding what was left out.

**New tools:**
- **6. Riparian band from NAIP imagery.** Uses `riparian_for_run`, which
  takes the run's DEM for the extent and its HAND and streams for the
  channel limit.
- **Clip a DEM to one watershed.** Also an option on the whole routine and
  on step 1. They clip first, then run the pass on the clipped DEM, so the
  run record and the later steps work as usual.
- **Headcuts.**
- **Coarsen a DEM.**

**Supporting changes:**
- `watershed(pour_point=)` accepts a point layer, read in its own CRS.
- The step tools also read watershed-pass records.

**Fixed:** `resample_dem` compared `target_cell_m` with the raster's
resolution in CRS units. On a US-survey-foot DEM, "10 m" gave 10 ft cells,
and a 20 m request on the 33.6 ft Ruidoso DEM was refused. Notebook step 3
used it.

**No tiled tool, on purpose.** `landscape_tiled` documents a 97% network
loss across tile boundaries. For a DEM too big for memory, coarsen it and
give the lidar as the fine DEM.

## v0.29.0: ArcGIS Pro toolbox; alluvial fans in the pass

Sam: "i'd rather do [ArcGIS Pro toolboxes] from now on", with the steps as
separate tools or the whole routine. He also said "we've left some things
out as well, like the alluvial fan search".

**ArcGIS Pro toolbox** (`arcgis_toolbox/geolens.pyt`):
- 11 tools: setup, whole routine, steps 1-5, and three more.
- It uses the QGIS plugin's runner, so each tool is a subprocess in the
  geolens env.
- Layers are styled in `postExecute`: categories from the QML sidecars, and
  image pop-ups through the CIM.
- New API behind the step tools: `valley_types_for_run`,
  `stream_types_for_run`, `retype_valleys_with_fans`. The pipeline's
  valley-types block became `pipeline.valley_types_step`.
- Tested against a stand-in arcpy only. The first run in Pro is the real
  test, and the CIM pop-up property names (`sourceURL`, `linkURL`) are the
  least certain part.

**Fans.** `detect_alluvial_fans` existed and nothing called it, so no reach
was ever Type III.
- It now runs inside the pass (`fans=True`), and above 60M cells on an
  averaged copy.
- Fixed:
  - elevations in feet were compared against metres (a 5% fan read 16%);
  - slope nodata (-9999) went into the smoothing and the slope floor;
  - the slope floor raised the band above itself on mountain DEMs (0.144 →
    0.216 on Ruidoso);
  - apex candidates came from the pass's stream threshold;
  - the per-cell Python loop in `_mainstem_parent` is now vectorised.
- New cone test (60 degrees at the apex, and not reaching the radius cap):
  on Ruidoso 10 m all 26 candidates were trunk-valley strips.
- Type III now needs half the reach on a fan, at medium confidence.

**Gullies "19 hours".** Sam's gully cell finished in 229 s at 6:20 pm. The
notebook display had lost the kernel at "measuring gullies 76%". The run
log in `runs/` proved it. It had also run on the 10 m DEM.

**Not in the toolbox yet:**
- NAIP/NDVI and the riparian band
- watershed delineation (`watershed`, `watershed_pass`)
- headcuts
- tiled landscape runs
- inundation checks

## v0.28.2: QGIS plugin

`qgis_plugin/geolens_qgis` (zip: `qgis_plugin/geolens_qgis_plugin.zip`) is a
Processing provider with five tools:

- check memory
- landscape pass
- section plots
- stable/unstable form
- cross-sections

How it runs:

- Each tool runs `geolens.api` in the geolens env as a subprocess, with
  QGIS's PYTHONHOME, PROJ and GDAL variables stripped.
- `GEOLENS_PROGRESS=json` (in safety.py) streams progress and notes. Callbacks
  are drained on the Processing thread.
- Cancel kills the process tree (`taskkill /T`).
- Layers load with their QML.
- A warning appears when the output folder is QGIS's temporary folder.

Supporting changes:

- `install.py` writes `~/.geolens/python.txt` so the plugin finds the env
  wherever conda or the venv put it.
- `update.py` now also copies `docs/`, `qgis_plugin/` and START_HERE.

Tests: `tests/test_qgis_runner.py` (no QGIS needed) and
`tests/qgis/check_plugin.py` (headless QGIS 3.x, runs the landscape pass on
the fixture and checks the styled layers).

Not done:

- no icon
- not tested on Windows QGIS (OSGeo4W) yet: the first colleague install is
  the real test

## v0.28.1: valley-form lines

Sam: "the valley form lines seem weird". Every line was the whole 12 km cut
(2 x max_search_m, capped 6 km per side on Ruidoso): 1,814 identical lines
over the map. Now the geometry is divide to divide (median 2.06 km on
Ruidoso), `valley_form_floors.gpkg` added, `station_m` was in feet (fixed).
Not changed (would move valley types, which Sam says work well; measure
first): section orientation from a +-1 m tangent on the 0.76 m D8 staircase
(34% at multiples of 45 deg; median 10 deg off the 50 m channel chord, 11%
> 30 deg; simplifying the line at 3 cells only brings the median to 7 deg --
a valley-axis baseline is the real fix), and the divide look-ahead / smoothing
scale with the search length (10% / 2% of 6 km = 600 m / 120 m).

## START HERE — v0.28.0: bankfull at the regional depth; stable/unstable form

* **Bankfull method (Sam chose option A):** `bankfull_method="regional_stage"`
  (default) sets bankfull at the regional curve's max depth above the
  thalweg; the DEM gives W, area, W/D and the flood-prone width at 2x.
  Width cap: if that depth spills over a flat floor (> 3x regional W) the
  stage is lowered (>= 0.1 m) until plausible (`stage_capped`); nothing
  channel-shaped -> `no bank found`. Picker still runs as a check (`picker_*`
  columns, purple on plots, `picker_bank_top` points); `"hybrid"` and
  `"picker"` available. Regional-stage reaches cap at medium confidence
  (`bankfull_source`). Replay on Ruidoso v26 sections (no DEM needed, 12 s):
  usable sections 1,875 -> 3,336; W/regional 1.71 -> 1.33; W/D median 14.5 ->
  26.8 (Moody NM curve 21-24); ER median 2.9 -> 1.6; C+E 380 -> 55 reaches,
  B/D/F up. Not validated against surveyed bankfull. Known weakness: a
  channel genuinely deeper than the curve (incised arroyo, meadow E) is
  measured at the curve's stage; E vanished on Ruidoso.
* **Stable / unstable form** (`landscape/stability.py`, `api.stream_stability`,
  end of `landscape_pass`): stream letter vs valley type from NRCS NEH 654
  TS3E Table TS3E-3 (after Rosgen 1996): stable / unstable (VI: G; VIII: D, F,
  G; X: G, F) / check / unknown, confidence = min of the two. Sam: "that's
  kind of the whole point". Cherokee v25 (picker types): stable 56, check 45,
  unknown 2.
* **Waiting on Sam:** Rosgen's newer descriptive valley-type names (he will
  send the table). Crosswalk goes in a CSV -> a name column beside
  `valley_type`, legend, docs. Do not guess the names.
* **Correction:** Moody et al.'s Eagle Creek (near Alto, NM) is not Sam's Eagle
  Creek. Removed the false "same basin / reach 39 matches / downstream of the
  DEM" claims from the PDF (now v0.28), NEXT.md, tests and comments.
* The Ruidoso mapped line sometimes runs on a bench beside the real low
  channel (reach 151, xs 758): the thalweg search is only +-1 regional width
  from the line. Not addressed.

## v0.27.0: shippable to colleagues

* `START_HERE.md` / `.html`: the colleague-facing guide — what it does, install,
  run, what to trust (with the Ruidoso numbers below), reading the plots.
* **Gully roughness in strips** (`terrain_roughness`, `block_rows`): was ~48–52
  B/cell for the whole grid (86–93 GB on Ruidoso's 1.785 G cells — the
  overnight crash). Now one strip (~3 GB budget, overlap = half the widest
  window) whatever the DEM; cell-for-cell identical (tests/test_roughness_strips.py,
  and 0 differing cells against the old code on Eagle Creek at 1.2 m). The
  rest of the gully step measured 34.9 B/cell and restream 30.1; planner 55 -> 40.
* Gully polygons opt-in (`polygons=True`): median 31 m wide x 11 m deep footprints
  on Cherokee Bill; Sam: "not great". Lines stay; their width/depth/volume share
  the caveat. In mountain terrain most detections are headwater ravines.
* **Bank picks, Ruidoso v26 (1,875 used) — the weak link, not yet fixed.**
  Picked max depth vs Moody 2003 NM max depth: median 2.59x (1-5 km2), 2.14x
  (5-20), 1.72x (>20); only 21-25% within a factor of 1.5. Width ratio 1.46-1.82x.
  W ~ A^0.197 and D ~ A^0.116 against Moody's 0.281 / 0.237: flatter, i.e. a
  floor on the picked scale. Within-reach CV: width 0.28, depth 0.47. 889 of
  1,875 picks had a single candidate. Two failure modes seen in the plots:
  small drainages -> top of a small incised channel (reach 764); wide sandy
  washes -> a low-flow thread (reach 151, 35 km2, W 3.9 m, ER 8.8). Sam also
  finds the bank points on the map "may not be the best". Options to discuss:
  (a) bankfull at the regional-curve stage with the DEM supplying the shape,
  picker kept as a check; (b) area-based candidate choice (`select_by_area`,
  experimental). Neither done.
* Not done yet: slope tolerance at Rosgen's 4% line (4 Cherokee reaches at
  4.1-4.2% with C/E measurements are forced to Ba + no_type_fits) — wait until
  the bank picks are trusted, since ER and W/D come from them.
* Ruidoso v26 landscape pass: 72 min at full resolution with routing cached;
  stream types 448 s (plots 346 s); 856 reaches, valley types II 539 / I 140 / V 32.

## v0.26.0: every section has a picture, linked to the map

Sam (23 Sep, on v25): "i've never seen in the outputs a way to reference these
cross section spatial locations to an image of cross sections".

* `landscape/section_plots.py` renders `section_plots/xs_<xs_id>.png` (whole
  section + channel close up, looking downstream, compass ends) for every
  stream-type section and `reach_plots/reach_<id>.png` per reach, plus
  `stream_type_plots.html`. Default on (`section_plots=`; the test suite turns
  it off in `tests/conftest.py` via `GEOLENS_SECTION_PLOTS=0`). Reads only the
  output folder, so `api.plot_stream_sections(folder)` works on v25 folders
  (stages derived from the profile; `_derive_stages`, checked to 2 cm against
  the recorded ones). Process pool above 800 sections, byte-identical to serial.
* Section rows now carry `thalweg_offset_m`, `thalweg_z_m`, `bankfull_stage_m`,
  `candidate_stages_m`, `floodprone_stage_m`, `regional_max_depth_m`.
* `gis_style.py`: `plot_file` (relative) + `plot_path` (absolute) columns and
  a sidecar QML QGIS auto-loads: map tip image, Open-URL action as the
  canvas default (Run Feature Action tool), image widget, status/letter
  colours. The link falls back to the layer's folder + `plot_file` when the
  folder moves. **Verified in real QGIS 3.34** (apt `python3-qgis` under
  python3.12 in the sandbox) by `tests/qgis/check_plot_links.py`, incl. a
  moved folder with spaces in the path. Style not embedded in the GeoPackage
  by default: a `layer_styles` table makes geopandas warn on every read.
* Reach-scale `export_cross_sections` gets the same links (+ `max_plots`);
  the notebook's CENTERLINE cell now writes that layer and titles its inline
  panels `xs <id>`. `sections._load_centerline` on a branched network took the
  longest input piece, not the longest joined line — fixed.
* 59 of Cherokee Bill's 103 reaches are digitized upstream (z_start < z_end),
  so "looking downstream" is decided per reach from z_start/z_end.
* **Riparian:** polygons were traced 4-connected from 8-connected labels, so
  a patch joined at a diagonal came out in pieces — 1,523 of 1,838 v25
  "patches" were fragments (median 11 m²), and single-cell squares took 939 of
  the 1,027 agriculture flags. Now one (multi)polygon per label.

## v0.25.0: stream types reworked, then reviewed

Sam on v23: valley types good; stream types mostly unclassified; gully
polygons and the riparian corridor not needed (do not extend either without
asking). v24 reworked stream types; an adversarial review of v24 found six
defects, all fixed in v25 (listed below so they are not reintroduced).

**How stream types work now**

* **Bankfull = stage–width curve** (`bankfull_stage_width`). Every stage
  >= min bank height where dW/dh stays 2x its median since the last candidate
  for 3 steps is a candidate; the lowest is bankfull. Search within 2x the
  regional width of the thalweg; stages capped at max(3 m, 1.5 x width).
  Known-answer profiles (`tests/bankfull_profiles.py`,
  `tests/test_bankfull_stage_width.py`): floodplain, trench, asymmetric pass at
  0.25/0.6/1.2 m with noise and with the expected width x0.6-x1.6; pure V
  returns None; confined passes at 0.25 m and is None at 0.6-1.2 m because its
  0.33 m bank face is below the sample spacing (the sampled profile has no
  break).
* **Four bases per reach** (`stream_basis`): `measured` (>= 2 sections with
  usable banks; ER and W/D from those), `partial` (one; its ER pooled with the
  regional ones, no W/D), `regional` (flood-prone width at 2x regional max
  depth over the regional width), `slope_sinuosity`. Only `measured` can be
  medium/high. Banks are searched only when the regional width is >= 3
  samples (sections are sampled at max(cell, 0.5 m)); unused picks: over 3x
  regional, under 3 samples, one-sided.
* **Regional curve**: `regional_curve="arid_sw_nm"|"arid_sw_az"` (Moody et al.
  2003, converted to m/km^2 and checked against their published Eagle
  Creek (Alto, NM) site values -- not Sam's Eagle Creek),
  `"bieger_us"`, a dict, or None (segments' generic width + Bieger depth).
  Max depth = mean / 0.58 (Moody 2003, riffles). Validated at the start of
  `run_landscape`, not at step 6.
* **Nearest-type key.** Misfit per type: ER/0.2 + WD/2.0 (Rosgen's continuum
  tolerances) + 0.5 x log2 slope distance + 0.25 x sinuosity/0.1. One table
  (`_SLOPE_SUFFIX`) says which slope classes exist per letter and how they
  are written; letters absent at a slope are excluded (no Ca, Ab, Ga, Gc-,
  DAc). DA never takes the letter. E +0.1 prior over C. Letter when best
  beats next by 0.05 (ER known) or 0.25 (not).

**v24 defects fixed in v25 (review 22 Sep)**

1. DA outranked C on every flat, straight reach (no sinuosity range).
2. Labels not in Rosgen's chart: Ab, Ca/Ea/Fa at exactly 4%, Gc-/Bc-; Cc-
   never produced. Now one slope-class table drives both.
3. Area-based stage selection (added after v24 packaging) picked a trench's
   terrace when the expected area was x1.6 off. Now off in the pipeline,
   restricted to plausible widths, documented experimental.
4. One-sided picks (water leaves the window) were used as widths.
5. `stream_basis` mislabelled mixed reaches; W/D from 2 sections was used
   under a "regional" label.
6. The feet/metres parity fixture wrote both passes to one folder, so it
   compared the metric result with itself -- and no fixture channel was wide
   enough to reach the new code. Separate folders now, plus
   `tests/test_stream_type_units.py` feeding known profiles in feet, metres and
   feet-with-metre-elevations straight to the reach typing.
   Also: sections wrapped in try (one bad section no longer kills the step),
   NaN samples dropped, sections-layer schema fixed.
7. **Found by fixing 6: valley width and confinement were 3.28x too large on
   every foot grid since v21.** `classify.py` multiplied `length_m` by the
   metres-per-unit factor a second time (network.py already writes metres),
   so `length_m` in the classified layer was 0.3048 of the truth and
   `valley_width_m`, `core_width_m` and `confinement_ratio` 3.28x too big.
   Eagle Creek v23: every one of 53 reaches "unconfined". Valley types read
   `confinement` and `valley_width_m`, so they are affected too; stream
   types are not (they use drainage area, gradient and geometry). Rerun any
   foot-grid landscape pass made with v21-v24.

**Memory, after Sam's full-Ruidoso run (v24, 23 Sep)**

His run: routing grid `Ruidoso_DEM_1m_2m_2m.tif` is really 2.5 ftUS = 0.762 m,
42,500 x 42,000 = 1.785 billion cells over 1,036 km^2. Landscape pass 3.2 h
(routing 2.1 h), 856 reaches, stream types 74 s (98.6% typed), valley types
699; cross-sections; gullies 81 min; then "OOM overnight" -- cause not yet
pinned (his saved notebook stops mid-gully; asked him for the dying cell).
NAIP at 1 m over that extent is 1.04e9 cells and is refused by
MAX_GRID_CELLS before allocating, so it is not the NAIP mosaic.

* **The planner was 2.6-3x low.** It said "86.8 GB, TIGHT -- should fit" on
  his 117 GB machine. Measured per-stage peaks (27.1 M-cell Eagle Creek at
  1.2 m, RSS sampled every 50 ms): routing 98.8 B/cell, valley bottom 111.7,
  network 15.5, classify 31.3, valley extent 12.0, gullies 48.0, stream
  types ~0. `plan.py` now uses measured bytes/cell (+~10%).
* **Memory cut, outputs identical:** valley bottom 111.7 -> 48.2 B/cell
  (float32 evidence built in place; evidence written and freed before
  despeckling; 0 of 1.4 M valley cells changed, evidence within 3e-6);
  routing 98.8 -> 80.8 B/cell (pysheds intermediates released as each next
  stage exists; products written and dropped; HAND float32 in place; HAND
  bit-identical). Routing is now the peak: ~161 GB on his grid. Route at
  2 m (23 GB) and pass the 1 m as DEM_FINE.
* **Units in the planner and the DEM-info cell:** a 2.5 ftUS grid read
  "@ 2.5 m, 11,156 km^2" (true 0.762 m, 1,036 km^2). Fixed; `describe_raster`
  now returns `linear_units` and `metres_per_unit`.
* Still to do: NAIP mosaic is O(grid) in memory (32 B/cell of accumulators
  plus a 16 B/cell full-grid array per scene) -- make it windowed so a
  1 m NAIP over a whole Ruidoso-size extent is possible; riparian_band is
  O(grid) too.

**Results**

* Sam's v23 run on Eagle Creek (0.61 m, EPSG:6529), replayed on its own
  saved sections: v23 left 33 of 53 reaches with no letter (49 of 61 bank
  picks thrown out); v25 with `arid_sw_nm` types all 53 -- Aa+ 21, Ba 15,
  Cb 9, C 3, A 3, B 1, E 1; basis measured 20, partial 3, regional 30.
* **CORRECTED (v0.28, Sam):** Moody et al.'s Eagle Creek (near Alto, NM) is
  a *completely different place* from Sam's Eagle Creek DEM. Earlier notes
  here and in the v0.25 PDF said its field sites were in or downstream of
  Sam's DEM and "matched reach 39" -- false. Their two field-typed sites
  (below South Fork: B4, ER 1.9, W/D 21.5, 2.2%; Trib #1: A4a+, ER 2.6,
  12.7%) only check that the key reproduces published field types
  (`test_moodys_eagle_creek_sites_key_to_their_field_types`). They are not a
  validation of geolens measurements on any of Sam's areas.

**Next, in order**

1. **Sam's full Ruidoso run** (DEM = 2 m for routing, DEM_FINE = the 1 m,
   `arid_sw_nm`). Look at `stream_basis` counts, `stream_type_banks.gpkg` on
   the hillshade, and runtime of the channel measurement at that scale.
2. **Roads.** Eagle Creek r10/r12/r13/r37 show a flat bench beside the
   channel on every section -- almost certainly a road. It reads as
   floodplain (regional ER 15-24). Pass the gully `exclude=` roads layer to
   stream types and mask section samples on it.
3. **Field bankfull vs DEM bankfull.** Main-stem picks run ~2x the NM
   curve's width: the DEM's first visible break is the top of the incised
   bank, and Moody et al. say SW bankfull is rarely that. Area-based
   selection among candidates (`expected_area=`) changed nothing on Eagle
   Creek -- the lowest candidate was already nearest. Needs Sam's field
   widths to go further.
4. **Flat-floored sections** (Eagle Creek 1.2 m r32 xs163): the floor fills
   in the first few cm and the pick lands on a wall bench. A
   width-at-min-bank-height > 3x regional test would return None instead.

## v0.23.0 — roads layer for gullies

* `detect_gullies(..., exclude="roads.gpkg", exclude_buffer_m=10)` and
  `geolens gullies --exclude`. Lines lying mostly along the layer are set
  aside into `gullies_excluded.gpkg` for review, not deleted.

## v0.22.0 — gully footprints from the ground, and the whole network

* **Gully footprints are measured, not joined.** Depth below the lower rim on
  a cross-profile every two cells, cells burned where the incision exceeds 5%
  of the local depth. Known fixture: 14.00 m wide (built 14), 1.95 m mean
  depth (2.00), 27.3 m³/m (28.0). Eagle Creek: 110/117 footprints (was 55),
  96% of each centreline inside (was 56%). `footprint_quality` flags the
  doubtful ones. `tests/test_gully_footprint.py`.
* **Roads are still detected as gullies.** Eagle Creek gully 84 is a road cut.
  The footprint flags it doubtful (0.23 m deep) but detection admits it.
  **Tried and failed: bed flatness.** A road bed is flat and a gully is a V,
  so the fraction of each profile near its deepest point should separate
  them — but the detector's line follows the road's inside *ditch*, not the
  bed, and a ditch is a small V: 84 scores 0.33, 17th of 110, inside the
  gully range. Width variability doesn't separate it either. Not shipped as a
  flag. Next candidates, untested: constant along-line gradient (engineered
  grade), smooth long-radius curvature, a parallel uphill cut-slope and
  downhill fill-slope, or simply an OSM / TIGER roads layer as a mask — the
  last is the honest one and probably the answer.
* **The stream threshold defaults to the DEM's size** (`None` → extent/500,
  capped at 1 km², floored at 0.01). Eagle Creek had 13 segments on 2
  branches at the old fixed 1 km² — only the main stems, so no tributary was
  ever classified.

## v0.21.0 — state at the end of the 22 Sep 2026 session

### Pick up here, in this order

1. **Bankfull detection in confined channels.** This is now THE limit on
   Rosgen stream types. On Eagle Creek (0.61 m, EPSG:6529) median measured
   bankfull was 3.7x the width the drainage area supports, up to 8.3x — the
   slope-break walker finds no floodplain break in a confined channel and
   climbs to a bench or the valley wall. v21 excludes sections over 3x and
   says so, which is honest but leaves 8 of 11 measured reaches on candidate
   sets. Candidates for a real fix, none tried yet:
   * bankfull stage from the width-vs-stage curve: the stage where
     width/depth is minimised, or where d(width)/d(stage) jumps (Williams
     1978; compared in Navratil et al. 2006). Needs no floodplain.
   * constrain the bank search to ~2-3x the regional width, so it cannot
     reach the valley wall;
   * a LOCAL regional curve (Moody et al. 2003 for NM/AZ) instead of the
     generic one, which would also tighten the 3x plausibility bound.
   Validate against Sam's field bankfull, not the fixture.
2. **Prove NAIP on Sam's machine.** Planetary Computer is blocked from the
   sandbox. v21 fixed the reason Eagle Creek never reached the network (a
   "1 m" grid built at 1 ft) and added a Python-HTTPS download fallback for
   the GDAL-certificate case, but neither has seen the live service. Look at
   `naip_dir/naip_sources.json` after one run.
3. **Geology works — confirm the substrate.** "auto" downloaded and was used
   by valley types on Eagle Creek (`geology_used: true`); stream types failed
   to use it (path passed where a layer was wanted). Fixed in v21 and tested
   offline; confirm `stream_type_full` is populated on a real run.
4. **Unit parity for the reach-scale path.** `tests/test_units_parity.py`
   covers the landscape pass, gullies and riparian on a feet/metres pair of
   the same ground; it found nine unit bugs on its first run. The reach-scale
   API (`delineate_channel`) takes CRS units by documented design and is not
   yet covered — worth a parity test that converts its inputs.
5. **The notebook test now spends minutes on network retries** against hosts
   the sandbox blocks (geology "auto", NAIP). The suite went from ~8 to ~10
   minutes. Make those cells skip fast when offline.

### What v19-v21 changed (short)

* Valley extent is each reach's local catchment — boundaries on the divides,
  tiling with no overlap. The stitched-ribbon construction is deleted.
* Rosgen stream types A-G. Entrenchment and W/D pick the letter; slope and
  sinuosity rank; slope outside the typical range is a suffix (Bc, Fb, Aa+).
* `dem_fine=` — route coarse, measure the channel fine.
* `geology="auto"`, with its status in the result.
* Conda is the default install route and the environment is permanent;
  `--venv` stays for testing a release.
* Unit bugs fixed on foot grids: network segment length, length_m, drop_m,
  HAND thresholds, slope-distance decay, gully sections/lengths/areas, fan
  detection, the geology buffer, riparian window/patch/areas/HAND, and the
  NAIP grid. All caught by the feet/metres parity fixture
  (`tests/make_terrain.py`), none by the metric fixtures before it.
* Crash fixes: the gully skeleton walked closed rings forever (the overnight
  crash); fragment merging was O(n^2).
* Cache: content-hashed rather than keyed on mtime, and a `reuse_routing=False`
  run no longer overwrites the shared cache.

## v0.13.0 — the cache and the environment, actually fixed this time

Sam, looking at v17: *"it doesn't look like you fixed the permanent conda env
or the cached outputs from previous runs."* Both were built and both were
useless in practice, for the same underlying reason: I fixed the mechanism and
not the thing that actually happens.

### The cache was keyed to the output folder

It lived under ``GEOLENS_OUT``. So it survived closing the notebook — the case
I set out to fix — and was thrown away by every other ordinary thing: a new
results folder, a session with a different ``OUT_DIR``, unzipping the next
release beside the last. The point is to pay for conditioning **once per DEM**,
and keying it to the output directory meant paying once per run
*configuration*.

Measured on the landscape fixture: 4.89 s cold, 0.07 s warm in the same
``OUT_DIR``, and **1.90 s again from a different one**.

The cache now lives beside the DEM, in a `.geolens_cache` folder next to the
data it belongs to, because the DEM is what it is about. Same test: 5.49 s
cold, then 0.07 s from each of two unrelated output directories. ``GEOLENS_CACHE``
overrides the location, and it falls back to a per-user cache folder when the
DEM sits on a read-only or network mount — which research data very often
does, and where the alternative is silently never caching at all.

### The installer destroyed the environment it was meant to preserve

`update.py` was the right tool and nobody was going to use it, because the
habit is to run the installer and the installer's short version still said to.
Worse, the installer's job on the second release is not the same as on the
first, and it did not know the difference:

* **`--conda` deleted the existing environment and rebuilt it from scratch** —
  hundreds of megabytes redownloaded to deliver a few hundred kilobytes of new
  Python. That was deliberate, and right, for an *install*: conda-forge
  packages into an env holding another channel's is what produces unsolvable
  conflicts. It is completely wrong for an update.
* The venv route built another environment beside the last one, which is how
  someone ends up with four and no idea which Jupyter is using.

`install.py` now looks for a working geolens install before it builds
anything — the named conda env, this interpreter, and any `.venv` in a sibling
`geolens*` folder, which is where the previous release's venv lives. When it
finds one it updates the code in place and leaves the environment untouched.
`--force-rebuild` gets the old behaviour.

The lesson is the same both times: a fix that requires someone to change what
they do is not a fix. The cache had to follow the data rather than the run, and
the update had to be what the installer does rather than a second script beside
it.

**Tests: 384.** New: `test_installer_update.py` (4) and two in
`test_routing_cache.py` — one that the cache survives a different output
directory, one that ``GEOLENS_CACHE`` is honoured.

## v0.12.1 — valley types were correct and unreachable

Having fixed the valley-type measurement, I checked whether it reaches the
workflow. It did not.

**The notebook never mentioned valley types at all** — not `valley_form`, not
`rosgen_valley_types`, nothing. Three rounds of work on Rosgen valley types,
and the entry point that produces the outputs anyone actually looks at did not
run any of it. That is why the report kept being "we still haven't figured out
valley types" while the code got steadily better: the outputs did not contain
them. `landscape_pass` does not run them either, so the only way to get a
valley type was to know the two api calls and chain them by hand.

There is now a step 7, between the class-break calibration and the plots. On a
DEM with real valleys it produces a real distribution — VIII 78, V 57, II 39,
I 14, 22 unresolved out of 210 segments — rather than one type for everything.

### The parameter that decides whether it works at all

`max_search_m` is how far to look for the divide. Too small and every section
returns a lower bound instead of a measurement, and **the run looks perfectly
healthy while measuring nothing** — which is what the fixed 3 km default did.

It is now derived from the DEM when not given, and the derivation had to be
corrected once: a sixth of the grid's shorter side gave 2,333 m on a fixture
whose divides sit up to 3,400 m out, and 20% of sections measured. The right
instinct is **generous**, because `_find_divide` takes the first crest walking
out from the floor — a wider search can never find a wronger divide, only let
more sections find one, and the cost is samples rather than correctness. At a
third of the shorter side, capped at 6 km: 140 of 140 sections, in 4.4 s.

The notebook also prints `n_measured` against `n_sections` and says plainly
when most sections never reached a divide, because that is the failure that
otherwise hides.

**Tests: 375 (up 2).** Both in `test_valley_types.py`: one that the derived
search distance actually reaches the fixture's known divides, and one that the
notebook runs valley types at all — which is the thing that was wrong.

## v0.12.0 — Rosgen valley types, actually measured to the ridgetop

Sam: *"we still haven't figured out rosgen valley types at the actual valley
level, not the valley floor and not the channel level but i want valley bottom
to ridgetop."* Third time of asking. This time I measured instead of adjusting,
and found three defects stacked on top of each other, each hiding the next.

### 1. There was no fixture with a ridge in it

`make_landscape.py` builds valley side slopes that rise at a constant gradient
and **never stop**. It has truth for valley-bottom width, which is what it was
built for, and it was the only landscape fixture there was.

So every test of valley form ran on terrain with no ridgetop in it. On a real
run: **5 of 750 sections (1%)** found both divides; 234 of 240 segments came
back `lower_bound`. The crest detector found nothing because there was nothing
to find, and for two releases that read as a tuning problem.

`make_valleys.py` builds five parallel valleys separated by real divides, each
with a different known form — a V-notch, a broad U, a gorge, a wide alluvial
valley, and a 70%-walled canyon. Ridge positions are measured off the surface
that was built rather than assumed to sit midway.

On it, **the same code finds both divides on 100% of sections and measures
valley top width to within 0.7% of truth**. The algorithm was never the
problem. Two rounds of work went into tuning something that was already right.

### 2. The shape exponent was fitted wrong

`rise + tol` was fitted rather than `rise`, to keep the logarithm finite near
the floor. That additive term is not part of a power law: it flattens the
bottom of the curve and the fit answers with a steeper slope.

| | true 1.0 | 1.5 | 2.0 | 2.5 |
|---|---|---|---|---|
| before, clean flank | 1.04 | — | **2.60** | — |
| before, with the valley floor included | — | — | **2.96** | — |
| before, built n=1.6 wide valley | — | — | — | **3.96** |
| after | 1.01 | 1.50 | 1.99 | 2.52 |

A built U came back as 4.74 in the pipeline — not U, not V, not anything. Both
wide valleys were classified on that number. Fixed by fitting `log(rise)`
directly, dropping points at or below the floor tolerance, and dropping the toe
(within 5% of the wall's height the rise is a metre or two and in log space it
is mostly noise: with 1.5 m of noise a true 2.0 still fitted 2.89 without it).

### 3. The "valley floor" was the channel

This is the one Sam kept naming. The floor elevation was taken **at the
thalweg**, so a 2 m tolerance band sat inside a 3 m incised channel:

| built floor | 80 m | 180 m | 900 m | 1800 m |
|---|---|---|---|---|
| measured | 20 m | 0 m | 0 m | **40 m** |
| after | 140 m | 220 m | 1180 m | **1900 m** |

An 1800 m alluvial floor read as 40 m. Vf was ~0.15 for every valley, so all
four classified as V-shaped whatever their form. That is exactly "measuring the
channel, not the valley".

The channel is distinguishable by being narrow, so the floor elevation is now
estimated from an annulus around the thalweg — outside this channel, but near
enough not to reach the next valley's. Both halves of that were learned the
hard way: taking the minimum over the whole section reached into the
**neighbouring** valley's channel, which is lower, and collapsed the tolerance
band the same way by a different route.

And stepping over the channel has to be conditional, because **a V-notch does
not have one**. Stepping out unconditionally landed 25 m up the flank of a
perfect V and measured a one-cell floor as 114 m — the same mistake mirrored.
A channel is a *step* (climb, then flatten); a V keeps climbing. Comparing the
rise across the channel window with the rise across the next window of equal
width separates them without assuming either.

The remaining bias is high by 9-75%, and it is real rather than tunable: a
valley flank is genuinely flat near its toe, most of all a parabolic one, so
any tolerance band includes some of it. Vf on a narrow floor is an upper bound.

### The classification

With the measurements right, two rules were wrong:

* `u_shaped` triggered at **n >= 1.6**, which is not a trough — it is a shape
  between the two. The built 1.6 wide alluvial valley tripped it and came back
  a glacial trough. Now 1.8, with 1.3-1.8 left as what it is, neither.
* The glacial-trough rule looked at shape alone. **Vf is the floor width over
  the valley's own depth; "very_broad" means a floor more than four times the
  depth.** Yosemite is about 1.7. A valley at 7.8 is an alluvial plain whatever
  its flanks fit to.

The five valleys now classify across the key: **I** for the canyon, **V** for
the trough, **II** for the V-notches, and the wide alluvial valley **declines**
with VIII/IX/X offered — geometry alone cannot separate alluvial from outwash
from lacustrine, and saying so is the correct answer, not a gap.

`valley_form` was also never exported from `geolens.landscape`; it had to be
imported from the module directly.

### QGIS groundwork, deprioritised mid-session

`qgis_bridge.py` detects how geolens can run from inside QGIS — in-process when
QGIS's Python can import it, subprocess against a separate environment when it
cannot, and an explicit "unavailable" naming the missing imports when neither
works. It is free of PyQGIS so it can be tested without QGIS, which is the
whole point, since QGIS cannot be installed here. The architecture question I
raised — install into QGIS's Python or drive an external environment — is
answered by not answering it: the plugin detects.

**Tests: 373 (up 26).** New: `test_valley_types.py` (15),
`test_qgis_bridge.py` (11), and `make_valleys.py` — the fixture that should
have existed before any of this was written.

## v0.11.0 — the watershed is the unit; condition once, not once per run

Sam, mid-session: *"tiling doesn't make sense for watershed work."* Correct,
and a bigger correction than it sounds — I had spent the previous hour
verifying the tiling path.

### What was actually wrong

`plan_landscape_run` is the first thing the notebook tells you to run, and when
a DEM will not fit it told you to split by watershed — pointing at
`derive_basins()`/`split_by_basins()`, which draw divides from a **coarsened**
copy of the DEM. Approximate boundaries, and never once exercised by a test.

Meanwhile `catchment()` has been sitting in `flow.py` the whole time, with a
docstring that says exactly the right thing: *"a complete watershed has no
inflow across its boundary by definition, so accumulation computed inside the
clip is exactly right."* It was reachable from neither the api nor the CLI.
The correct primitive existed and the documentation routed people past it.

### `watershed()` — three ways to name the unit, one way to check it

* `boundary=` — a polygon you already have (WBD HUC12, permit area).
* `pour_point=` — a coordinate; the contributing area is delineated from the
  DEM. Accepts lon/lat via `pour_crs=`.
* `huc=` — fetched from the USGS WBD. Network call, with its own selftest;
  **unverified here** because the ArcGIS services are blocked in this sandbox.

A supplied boundary is **checked, not trusted**. `check_inflow` asks which
cells outside the polygon drain into it — the reverse of how `catchment` works,
so the two are genuinely independent. Feeding a delineated watershed back in
returns zero inflow cells, which is the only non-self-referential check here.

**The number that makes the case:** a rectangle over the middle third of the
landscape fixture has 720 cells draining into it, carrying **3.65x the
rectangle's own area** in upstream contribution. Analyse that tile alone and
every contributing-area-derived quantity in it is missing most of its input.
That is now `test_a_rectangle_is_not_a_watershed`.

Pour points are snapped to the largest drainage within `snap_radius_m`, and the
distance moved is reported. One cell off the channel gives a watershed of a few
hundred square metres, which reads as a broken delineation rather than a
misplaced point.

`plan` now recommends picking a watershed rather than splitting a landscape,
`grid_tiles` carries a comment naming what it is and is not safe for, and
`derive_basins` says in its own docstring that its divides are approximate.

### Conditioning is cached

Also Sam: *"if we're doing multiple runs we should use the hydroconditioned dem
from the last run."* Right, and it was worse than one run to the next —
reusing a `TerrainResult` in memory already avoided it within a session, but
closing the notebook threw it all away. Open it the next morning and the first
cell re-fills every depression it filled yesterday.

Filling, flat resolution and D8 routing are deterministic for a given DEM and
backend, so they are now cached on disk and shared across runs. The backend
writes straight into the cache directory, so nothing is copied — a 7.7 GB
conditioned raster is not something to duplicate per run.

| | landscape fixture |
|---|---|
| cold | 2.01 s |
| warm, same threshold | 0.09 s |
| warm, different threshold | 0.24 s |

Bit-for-bit identical in all three cases, which is the only thing that makes a
cache acceptable. The key is the DEM's resolved path, size and mtime plus the
backend and a `_ROUTING_VERSION`, so an edited DEM, a different backend or a
change to the routing code all miss cleanly. A manifest whose rasters have gone
is ignored rather than trusted.

### `update.py` — install once, update in place

And: *"make a permanent environment rather than reinstalling venv every
update."* The environment is the slow, fragile part — the GDAL/rasterio/numba
solve. New code needs none of it rebuilt, but unzipping each release to its own
`geolens_15` folder puts every release at a new path, so the editable pointer
has to move, and the easiest way to move it looks like running the installer.

`update.py` finds whichever install this Python imports `geolens` from —
following the pointer rather than asking where you think it is — copies the
code in, and touches nothing else. It replaces rather than merges, so a module
deleted in a release actually disappears; keeps a backup of what it replaced;
reads the version from the file rather than importing (the import is the copy
being replaced, so it would report the same number twice); and names any
dependency the release needs that the environment lacks **without installing
it**.

That dependency check reads the optional extras as well as the core list —
scikit-image, scipy, numba and pysheds are what gully detection and flow
routing actually import. Writing it turned up that a comment containing
"someone else's dependency tree" was contributing a package called `s`.

### Known gaps

* The WBD fetch is written and unverified — the service is blocked here. It has
  a selftest and the offline path (`boundary=`) needs no network at all.
* The MCP server still exposes only reach-scale tools (carried over from v0.10).

**Tests: 345 (up 15).** New: `test_watershed.py`, `test_routing_cache.py`,
`test_update.py`.

## v0.10.0 — the platform it actually runs on, and the surface nothing had run

Every test in this suite runs on Linux, with a UTF-8 stream, in a process that
exits cleanly. It is run on Windows, from Jupyter, in a folder under Documents.
This session went looking in that gap.

### The progress bar could end the run

A Windows console is UTF-8 in modern Python. Redirect the output —
`geolens landscape dem.tif > run.log`, which is what anyone does for a job that
takes hours — and the stream falls back to the locale encoding, cp1252 on a
Western install.

cp1252 has the em dash and the superscript two this package uses freely. It
does **not** have U+2588 and U+2591, the block characters the progress bar is
drawn with. Writing one raises `UnicodeEncodeError`, and that ends the run: a
progress bar, whose entire job is to be reassuring, killing an overnight job at
its first redraw. Both bars (tqdm's and the fallback) now check the stream once
and use ASCII when they have to, `note` degrades instead of raising, and a
standing test scans every string literal in the package for characters a
redirected Windows console cannot encode.

### 10,739 GeoPackages, left in the output folder

`_xsect` takes a path, so each gully's centreline has to exist on disk for one
call. They were written to a directory that was never cleaned. The Ruidoso run
found 10,739 gullies, so it left 10,739 GeoPackages behind — in the output
folder, which on Windows is typically under Documents and therefore
cloud-synced. They are now deleted as they go: peak file count is 1, tested
from inside the loop rather than by checking the end state.

### Two raster handles that a failure would leak

They were closed by two `close()` calls at the end of the function, so any
exception skipped them. In Jupyter that is worse than a leak: `sys.last_traceback`
keeps the failed frame alive, which keeps the datasets open, and on Windows an
open handle blocks rewriting the file. One failed gully run locked `slope_std`
and `accumulation`, and the obvious next move — re-run the step that writes
them — was exactly what could not work until the kernel was restarted.
`try/finally` now, with a test that induces a failure mid-pass and asserts
every dataset opened during it is closed.

### Gullies were the one step with no record

`geolens.api` wraps every public function so results land in `runs/` without
the caller doing anything — and the notebook called
`geolens.landscape.detect_gullies` directly. The step whose parameters most
need a record of what was tried had none.

`api.detect_gullies` now exists and the notebook uses it. It reuses a
`landscape_pass` result rather than re-routing, scales the roughness window,
section width and minimum length to the cell size (logic the notebook used to
carry inline), and picks `slope_std_min` from the distribution when left
`None`, returning the evidence under `threshold_choice`.

`GullyResult.parameters` was also recording only the detection thresholds. The
measurement half — section width and spacing, the bank-detection limits —
decides width, depth, volume and whether a gully gets a polygon at all, and
none of it was saved. A run log that cannot answer "what section width produced
these widths" is not a run log.

**And a fresh instance of the ftUS bug, in code written the same week I fixed
that class of bug.** Given a DEM path and no stream threshold, the new wrapper
picks one from the DEM's extent — computed as `width * height * res[0] * res[1]`,
which on a CRS in US survey feet is square feet. On Sam's grid a tile that is
really 0.134 km2 reads as 1.440, out by 10.76x. It goes through `units.unit_info`
now, with a test on an EPSG:6529 fixture.

### 28 CLI subcommands, five of them ever run

The CLI is a documented entry point. Five subcommands were exercised anywhere
in the suite; the rest were an argparse definition and a handler branch nothing
had ever taken, including `valleyform`, `valleytypes`, `breaks`, `reclassify`,
`fans` and `headcuts` — all listed in RUNNING.md as things to run.

All 19 reachable ones now run against real fixtures in
`tests/test_cli_surface.py`, and every one passes. The commands needing the
network or the WhiteboxTools binary are named in `_UNREACHABLE` in that file
rather than quietly left out, so the gap stays visible.

Two things the sweep found:

* **`geolens gullies` did not exist**, while headcuts, fans and riparian all
  had a subcommand. It does now.
* **`geolens plan dem.tif` was in OVERVIEW.md and was not a command.**
  `api.plan_landscape_run` existed, the notebook's step 2 is built on it, and
  the docs told people to run it from a shell where it exited 2 with an
  argparse error. A documented command that does not exist is worse than an
  undocumented one. It exists now, and a test compares the commands shown in
  the docs against the parser so this cannot recur.

**Tests: 321 (up 24).** New files: `test_windows_console.py`, `test_cleanup.py`,
`test_cli_surface.py`, `test_gully_api.py`.

### Known gap, not fixed

The MCP server registers 13 tools and all of them work — but they are all
reach-scale: sections, profile, delineation, plotting, workspace. **None of the
landscape work is exposed**: no landscape pass, no HAND, no gullies, no valley
types, no fans, no headcuts. An assistant driving geolens through MCP cannot
reach anything built since v0.4. The api functions all exist, so this is schema
and testing rather than new computation — but it is new surface, and adding it
at the end of a session is how the last few rounds of self-inflicted defects
started. Left for a session that can start with it.

## v0.9.0 — HAND: three defects, one of them on every clipped DEM

"The hand output doesn't look like it worked." On the real run HAND came back
with a maximum of 20,628 on a DEM whose whole relief is 5,908, and 17,741,388
cells sat above that relief. `_sanitise_hand` caught and masked them — and I
left it there, which was the mistake. Catching a wrong number downstream is not
the same as not producing one, and a backstop only fires when the error happens
to be large.

### 1. Stream cells were landing on nodata

A nodata cell still collects flow from its valid neighbours. On a clipped
watershed — which is what most real DEMs are — the collar around the data is
exactly where all that flow ends up, so those cells sail past any stream
threshold and get marked as streams. Their elevation is the nodata value, and
every cell draining to them reports `elevation + 9999`.

Reproduced on a synthetic clipped basin: **794 of 1,053 stream cells, 75%, were
on nodata**, and 26,547 cells came back above the DEM's 96.2 m relief, the
worst 11,997. `_streams_from_acc` now restricts the network to valid ground at
all three in-memory derivation sites and says how many it dropped.

**Cost: none.** HAND coverage is identical before and after — 85.1% on a real
catchment, 16.5% on one that mostly drains off the edge — because those cells
never had usable HAND, they had a fabricated one the backstop then threw away.
`test_the_fix_does_not_cost_coverage` holds that, so a future over-eager mask
cannot quietly delete real drainage.

### 2. pysheds was leaving a quarter of the grid unresolved

Chasing the first defect turned up a second that had nothing to do with it.
`grid.compute_hand` and geolens' own `_hand_from_flow` agree to **1e-4 m**
wherever both return a value — and disagree sharply about how many cells get
one:

| fixture | pysheds | geolens tracer |
|---|---|---|
| landscape_dem | 99.5% | 100.0% |
| compound_dem | 89.8% | 95.3% |
| convergent basin | 76.0% | 100.0% |

I did not take the tracer's word for it. 400 cells pysheds left unresolved were
walked by hand along the same flow-direction raster: **every one reached a
stream cell.** The tracer is right. `build_terrain` now uses it, which also
ends a silent split — `restream` has always used the tracer, so the same
routing gave different HAND depending on which entry point you came through.

`tests/test_reuse.py` should have caught that and could not: it compared HAND
only where *both* rasters had data, so it was structurally blind to the two
producing different amounts of it. It now compares coverage too.

### 3. A 15.5 GB allocation, every call

`_hand_from_flow` allocated its walk buffer at `H*W` int64. On the 1.94-billion
cell DEM that is **15.5 GB on top of the 7.7 GB output — 23 GB before any other
array**, requested up front. It only ever holds the walk in progress. Capped at
1M entries (8 MB), which loses nothing: every cell is the first entry of its own
walk, so it is always inside the cap; a cell beyond it is resolved later when
the scan reaches it. Tested by running at a cap of 1, 3, 17 and 1000 against no
cap and requiring bit-identical output.

Capping it also nearly removed the guard against a cycle in the flow-direction
raster, which rode on the same counter and would have hung the walk forever.
`steps` and `n` are now separate.

### 4. A sentinel that collided with real elevations

`found >= 0.0` doubled as "nothing found yet" for a variable holding an
elevation, which silently means "only trust a base above sea level". On ground
below it the resolved-neighbour branch handed back a good negative base, the
test rejected it, and the whole upstream path was dropped to NaN with nothing
reported — 39,473 of 40,000 cells, a silent 1.3% loss. The arid Southwest has
plenty of ground below sea level (Salton Sea −69 m, Death Valley −86 m).

### The out-of-core backend had none of this

WhiteboxTools writes its rasters to disk and never touched `_sanitise_hand`, so
the invariant that caught the original bug did not apply to it at all. It now
gets a windowed stream mask (before `elevation_above_stream`, because
afterwards there is nothing left to repair) and a windowed invariant check.

Its binary cannot be downloaded here, so the two helpers are written as plain
functions over rasters and tested directly, and the wiring is covered by
stubbing `whitebox.WhiteboxTools` with a fake that writes the rasters it would
write. `_run_whitebox` previously had **no coverage of any kind**.

**Tests: 285 (up 14), all in `tests/test_hand_streams.py`.**

### What this means for the Ruidoso run

Re-run it. The 17.7M impossible cells should be gone, the warnings should name
the collar instead of the symptom, and HAND coverage should go up rather than
down. If the collar warning reports a large fraction, the DEM is not a single
connected catchment with its outlet inside the grid — which is worth knowing
before anything keyed off HAND is trusted.

## v0.8.0 — the gully detector had no size criterion

Sam's report was "gully has flagged almost every stream". It had. The diagnosis
was in the numbers from his own run: 10,739 features, 3,195 km against a
1,767 km stream network — 1.8x the whole drainage network — median contributing
area 0.21 km2 with 27.8% above 1 km2 and a maximum of 53.6 km2. Only 2.7% of
what came back was gully-scale.

**None of it was a computation error, which is why it took a while to see.**
Every number was right. The detector has four tests — slope variability, TPI,
slope, drainage area — and *three of them are just as true of a river as of a
gully*. `max_drainage_km2` is the only one that means **small**, and it shipped
defaulting to `None` with a docstring arguing for leaving it there. With no
upper bound, a detector for incised things returns the incised network. That is
a design hole, not a tuning problem, and no parameter advice fixes it.

It now defaults to 0.5 km2. The cost is real and documented: a gully that
collects more than the ceiling is truncated where it does, so its downstream
end is missing. That is a measurement error; returning 53 km2 creeks is a
category error, and the category error is worse. Passing `None` still works and
now warns.

### And then I got the correction wrong, twice

**First wrong correction.** The notebook said "gullies usually sit above p90;
start `slope_std_min` there" and then did exactly that. p90 admits 10% of a DEM
*by construction* — 190 million cells on Sam's — so I changed it to p99 and
wrote a warning that fired above a 2% pass rate. Then I ran it: **zero gullies
on both fixtures.** p97 and above return nothing, because the top of the
`slope_std` distribution is steep valley walls and mountain flanks, not small
incised features. A percentile is wrong in both directions, and the notebook
step returning zero on its own fixture is precisely the "passes on a trivial
case" failure I had written into that fixture's docstring the session before.

**Second wrong correction.** The 2% warning was invented from a single data
point. Measured: the threshold that correctly recovers the known gullies admits
**19–22%** of cells on the `slope_std` test alone, and the four tests together
plus skeletonizing cut that to six features. `slope_std` is not meant to be
selective by itself, so the warning fired on good runs. Deleted, with the
measurement left in the code where the warning was.

### What the distribution actually says

`slope_std` over gullied terrain is bimodal in the log — smooth ground and
incised ground, with a clear gap:

| fixture | p75 | p90 | jump |
|---|---|---|---|
| known 14 m gullies | 0.0173 | 0.1561 | 9x |
| branching valley network | 0.0085 | 0.1001 | 12x |

Where that gap falls in percentile terms depends on what fraction of the
landscape is incised — the thing being measured, so it cannot be assumed in
advance. `suggest_slope_std_min` puts Otsu's method on `log10(slope_std)` and
finds it without being told: p80.5 and p77.6 on the two fixtures, recovering
the known gullies at a measured 16.0 m against a built 14 m.

It is **advisory**. It returns a number and the evidence for it; `slope_std_min`
stays an ordinary explicit argument. `separation_decades` is what says whether
the split means anything at all, and the 0.4 cutoff is measured rather than
picked: 1.02 and 1.20 decades on the two gullied fixtures, 0.068 and 0.083 on
tilted planes with only noise on them.

The RUNNING.md validation table — 6 gullies, 6.53 km, matched one-to-one at
1383/1263/1146/1024/926/783 m — now runs at the threshold the selector chose on
its own rather than one hand-tuned to 0.05.

### The coupling that is invisible until you look

`exclude_streams=True` with `stream_threshold_km2` *below* `max_drainage_km2`
means every cell draining between the two is both gully-eligible and a mapped
stream, and the exclusion wins — it silently deletes the largest gullies.
`detect_gullies` now warns. Setting both to the same number makes the sets
disjoint by construction, which is what the notebook does.

I also over-corrected here and had to back it out: I scaled the doc example's
*ceiling* down to 0.05 km2 when the thing that broke on a 2.5 km2 fixture was
the *stream threshold*, and the ceiling change quietly invalidated the
validation table. Measured the configurations, restored the ceiling, lowered
only the threshold. The notebook now catches the too-small-extent case and
falls back with exclusion off rather than raising.

**Tests: 271 (up 12).** New: `tests/test_slope_std_suggestion.py` (6), plus 6 in
`test_gully_plausibility.py` and 1 in `test_notebook_runs.py`. One of them
asserts that p99 finds *nothing* on the fixture — so if that ever stops being
true, the argument in the docstring is out of date and gets rewritten instead
of standing as folklore.

### Still open

* HAND exceeds relief on 17,741,388 cells of Sam's run. Masked and warned; the
  routing-into-nodata cause is not fixed.
* `detect_gullies` is not wrapped by the api persistence layer, so gully runs
  do not appear in `runs/`.
* Unverified here because the proxy blocks the downloads: `compare-backends`,
  `geology --selftest`, `naip --selftest`, `inundation --selftest`.
* Never validated against field data: the riparian band, valley form.

## Session of 2026-09-21 — verification pass, and reading a real gully run

No new features. Sam's instruction was that I had been adding modules faster
than I was verifying them and then finding my own bugs afterwards, which is
exactly what the record shows. This session is the other half.

**The notebook had never been executed.** It is the entry point — what the
installer points at and how the tool is actually used — and it was built by a
script, syntax-checked cell by cell, and never once run.
`tests/test_notebook_runs.py` now executes it top to bottom with nbclient
against a synthetic branching valley and asserts every cell completes. Every
notebook failure reported to date would have been caught by it.

The first fixture I wrote for that test produced **4 segments over 2.5 km** —
the notebook ran green while exercising almost nothing, which reads as coverage
and is worse than no test. Replaced with a trunk valley, five tributaries and
cut gullies: 44 segments, 24 km, gullies found, polygons written.

That run also exposed that the shipped default `stream_threshold_km2=1.0` is a
landscape-scale number which, on a few-km2 DEM, yields a handful of segments
with no indication that the parameter rather than the terrain is the reason.
`build_terrain` now says so.

**An adversarial review of my own recent code found eight more defects.** All
in modules I wrote in the previous two sessions. The severe ones:

* **`classify.py` computed every area and width in CRS units.** On Sam's ftUS
  DEM that made `drainage_km2` **10.76x too large**, `valley_width_m` 3.28x,
  and `confinement_ratio` wrong by a different factor again because it divides
  a foot width by a metre channel width. `valleyform.py` had no unit handling
  at all: `max_search_m=3000` reached 914 m per side, `spacing_m=250` became
  76 m, and `valley_relief_m` was written in feet.
* **A steep U-shaped trough fell out of the Rosgen form path entirely.** The
  U-shape branch only accepted `moderate`/`steep` side slopes, and real glacial
  troughs are 0.6-1.0 — `very_steep`. So the common case dropped through to the
  channel proxies and was typed IX ("ground moraine") without the measured
  parabola ever being mentioned. That is precisely the case the feature exists
  for.
* **`restream()` decoded flow directions with ESRI codes regardless of
  backend.** WhiteboxTools writes a scheme rotated 45 degrees, and
  `d8_pointer` was being called without `esri_pntr=True` — so on a whitebox
  terrain every HAND value would have been traced down the wrong neighbour,
  silently, and the HAND invariant cannot catch a 45-degree error because it
  rarely exceeds the DEM's relief. One convention everywhere now.
* `valleyform` raised `KeyError` at the very end of a full run whenever the
  segments' id column was named `segment_id` (a pandas merge collision), after
  all the expensive work.
* `_find_divide` returned `found=True` from its fall-through path, handing out
  a "complete" flag for sections that found no crest — and `complete` is the
  one thing that module's honesty rests on.
* `mask_area_m2` was in CRS units beside two metric columns; one nodata cell
  under a centreline NaN'd a whole gully's statistics (`np.mean` for
  `np.nanmean`); the "window under 3 cells" warning was unreachable because the
  value was clamped before the test.

**Reading the Ruidoso gully run.** 10,739 features, **3,195 km against a
1,767 km stream network — 1.8x the entire drainage network.** Median width
31 m, depths to 37 m, 28% draining more than 1 km2, max 53.6 km2. Only **2.7%**
(287 features, 57 km) are gully-scale. The 90th, 95th and 99th percentile
widths are all **55.20 m**, which is the cross-section width — for those
features no bank was found inside the section and the "width" is the limit of
the measurement. 34.8% have zero usable cross-sections; 71.9% have fewer than
three, so only 28% got a polygon.

Nothing computed it wrongly. It is a parameter and scale problem: the
notebook's own advice ("gullies usually sit above p90") selects the channel
network on terrain whose only rough ground IS the channel network, and
`exclude_streams` defaults off with no upper drainage bound.

But the code should have said so and did not — it reported 31 m wide, 37 m deep
"gullies" draining 53 km2 without comment. `_gully_scale_warnings()` now checks
the four conditions that distinguish "found gullies" from "found the drainage
network", and it is a separate function because the fixtures are too small to
provoke several of them from a real run. A test reproduces the Ruidoso
distribution from its own numbers.

259 tests.

## Session of 2026-09-17e — calibration that is actually iterative

**"Why does step 6 calibrate breaks need to condition the DEM again?"** It
doesn't, and it was. Same class of waste as the gully step, and worse.

`confined_max` and `partly_confined_max` are two thresholds applied to
`confinement_ratio` in the last few lines of `classify_segments` — and
`confinement_ratio` is already a column in the segments layer the pass wrote.
Changing a break is a relabel. The notebook told you to call `landscape_pass`
again: conditioning, flow routing, HAND, valley bottom and re-segmentation,
every time, in the step whose entire purpose is trying several values. On a
1.94-billion-cell DEM that is **2.7 hours per iteration to relabel a column**.

`reclassify_confinement()` reads the table, relabels, writes. Verified
**bit-identical** to a full re-run (0 of 1547 segments differ, and
`rosgen_candidates` matches too), 38x faster on a small fixture, and a test
monkeypatches `rasterio.open` to assert it **never opens a raster at all**.

`confinement_break_sweep()` is the other half: class counts and length
fractions under several candidate break pairs, reading one table and writing
nothing. That is what calibration needs — compare, then commit — rather than
committing in order to find out. Notebook step 6 now sweeps, then applies.

**Rosgen, measured.** With the chain fixed and the crosswalk consulting form
first, on the 222-segment fan fixture with no geology at all:

| | unresolved | types asserted |
|---|---|---|
| without valley form | 96.8% | I x6, VIII x1 |
| with valley form | **82.0%** | I x18, V x13, II x9 |

V and II are now proposed from cross-section shape where before they needed a
geologic map. Confidence stays mostly `low`, which is correct: without geology
the module's own documentation says II/V/VI, VIII/IX and X/XI cannot be
separated, and shape narrows those groups without resolving them. A basis
string now reads:

> side slopes moderate (22%); cross-section U (n=2.23); 155 m floor-to-divide.
> No glacial material mapped, so a broad colluvial valley (II) is not excluded.

Worth stating plainly: a smooth range front can fit n > 1.6 where there was
never any ice, so shape alone proposes V rather than asserting it, and II stays
on the alternatives list until material confirms one or the other.

247 tests.

## Session of 2026-09-17d — the Rosgen chain was broken in five places

Everything in the valley-type chain was tested in isolation and passing. Then I
ran it end to end on a real fixture for the first time, and **it did not work at
all** — 97% unresolved, and every one of the five causes was invisible to the
unit tests because each component was correct on its own.

This is the entry to read if you want to know why integration tests earn their
keep.

**1. The api took the parameter, then passed a function.**
`api.rosgen_valley_types` had no `valley_form` parameter — an earlier patch
script asserted after writing and died before the write landed — but its body
already read `valley_form=valley_form`. Python resolved that to the
module-level `valley_form` FUNCTION and passed it as a file path. No NameError,
no type error, just `pd.read_csv(<function>)` downstream. This was live in v10.

**2. A branching network is not one line.**
`extract_cross_sections` merges a multi-feature layer into a single centreline,
which is right for one reach and wrong for a network. On a 222-segment layer it
produced **20 sections covering 9 segments**, all tagged `segment_id = 0`.
`valley_form` now walks the features itself, reusing the exact geometry
convention (origin on the line, left-hand normal from the local tangent) so
offsets and bearings stay interchangeable, with one DEM handle for the network
rather than one open per feature. 20 sections became 394.

**3. The two sides of the join were keyed differently.**
The segments layer names its id `seg_id`; the valley-form table keys on
`segment_id`. The join looked only for `segment_id`, never matched, and
discarded the evidence — with a warning that blamed the segments for a naming
mismatch on our own side. Both ends now auto-detect.

**4. The thalweg was in the next valley.**
A valley section has to be long enough to reach the divides, so at
`max_search_m = 2500` it is 5 km wide and routinely crosses into the
neighbouring drainage. `nanargmin` over the whole transect then finds the lowest
ground anywhere in 5 km. Measured: the thalweg came back at offset **−2494 m**,
the far edge of the section, 886 m against a local channel near 1100 m.
Everything downstream was measured from the wrong origin — a 446 m "valley
floor" in the wrong basin, no divide on one side, nonsense Vf. It affected 436
of 445 sections. The section origin sits on the mapped channel, so the floor is
searched within `thalweg_search_m` of offset 0.

**5. One bad side nulled the good one.**
`side_slope`, `shape_n` and `valley_relief_m` were computed only inside
`if L and R`, so a section with one side running off the DEM lost both. On the
fan fixture `side_slope` existed for **23 of 222** segments. A section with one
good wall still measures a side slope and a shape; only Vf and asymmetry need
both divides by definition. Now 212 of 222.

**And two design errors underneath them.**

*Requiring both divides threw away the informative cases.* Only 12 of 394
sections reached a crest on both sides — on real terrain a segment on a basin
floor has no divide within kilometres — and the aggregation then filtered to
complete sections, collapsing coverage from 222 segments to 5. But "no divide
within 1.5 km and only 0.5 m of rise" is not a missing measurement. It says the
valley is at least 3 km wide and flat, which is exactly what separates Rosgen
VIII/IX/X from I/II. Sections now carry `quality` (`measured` /
`lower_bound` / `unbounded_broad`) and `vf_is_lower_bound`, aggregation keeps
every segment, and an unbounded flat search is reported as `vf_class =
'very_broad'` rather than unknown.

*The crosswalk consulted the proxies first.* Form was only reached in two
branches, both requiring `confinement != "unconfined"` — and 216 of 222
segments on that fixture are unconfined, so the measured side slopes and shapes
were never looked at. `classify_from_form()` is now the primary path, using
Rosgen's own discriminators (side-slope class, V-versus-U exponent, relief
floor-to-divide, Vf breadth) and falling back to confinement and channel
gradient only when the form is too thin to say anything. It returns `None`
rather than guessing, because a type asserted from a truncated measurement is
worse than an honest unresolved.

*Also:* a one-cell valley floor measured **0 m** wide, because the width was
`|off[hi] - off[lo]|` rather than `(hi - lo + 1) * step`. A single-cell floor is
exactly what an incised V-notch canyon has, so Vf came back 0.0 for every
incised section — the case Vf is most often asked about.

`tests/test_valleytype_chain.py` pins all of it.

## Session of 2026-09-17c — the TIN question, answered with numbers

`tests/experiments/resolution_vs_tin.py`. One analytic channel of exactly known
geometry (bank top at +/-8.0 m), **one** bank-top finder, and five ways of
sampling the same ground — so the comparison measures the sampling, not the
detector. 25 trials each, median error in metres:

| sampling | 0.5 m | 1 m | 2.4 m | 5 m |
|---|---|---|---|---|
| raster (what geolens does) | +0.25 | +0.25 | +1.25 | +2.25 |
| TIN built from that raster | **+0.25** | **+0.25** | **+1.25** | **+2.25** |
| scattered points, same density | +0.50 | +0.75 | +1.75 | +3.00 |
| TIN + breakline at the TRUE bank top | +0.25 | +0.25 | **+0.25** | **+0.25** |
| TIN + breakline at the ESTIMATED bank top | +0.50 | +0.50 | +1.50 | +2.50 |

Four findings, in order of how much they change what to do:

1. **A TIN built from a raster is identical to the raster.** Not similar —
   identical, at every cell size, to the last digit. It is a piecewise-linear
   interpolation of samples you already have, so there is no information in it
   that was not in the grid. Rebuilding the package on one would cost D8
   routing, priority-flood, HAND, every distance transform and the tiling
   architecture, and buy exactly nothing.

2. **Irregular points at the same density are WORSE than the grid**, and the
   gap widens with cell size (+3.00 vs +2.25 at 5 m, and the 90th-percentile
   error is 5.55 m against 2.25 m). A regular grid guarantees a sample near the
   break; scattered points only do so on average. This was the opposite of what
   I expected.

3. **A breakline makes the measurement resolution-independent.** +0.25 m at
   0.5 m cells and +0.25 m at 5 m cells — a ninefold improvement over the
   raster on coarse data. The geometry is carried by the line, not by the
   sample density. This is why hydraulic modellers use breaklines, and it is
   the real answer to "would a TIN help".

4. **But the breakline cannot be bootstrapped, and I had proposed exactly
   that.** In `REVIEW.md` I suggested a two-pass scheme: detect bank tops on
   the raster, feed them back as breaklines, re-measure. Tested, it does not
   work — the two-pass estimate is *slightly worse* than the raster it started
   from (+1.50 vs +1.25 at 2.4 m; +2.50 vs +2.25 at 5 m). A breakline in the
   wrong place creates a break there, so the second pass returns the first
   pass's error with a confident-looking TIN wrapped around it. The gain in (3)
   comes entirely from knowing where the bank is, which is external information:
   a survey, photogrammetric breaklines, or hand digitising.

**What this means for the Ruidoso data specifically.** The cells are 2.400045
ftUS = **0.73 m**, not 2.4 m. At that resolution the raster already measures the
bank top to +0.25 m — the same as a breakline TIN. There is nothing left for a
TIN to recover. The lever that matters is not the data structure, it is not
resampling away from native resolution.

Also fixed this session, from the review list:

* **`distance_transform_edt(..., return_indices=True)`** allocated a full-size
  float64 distance array and discarded it, in three places. `return_distances=False`
  removes it: measured 3.4x lower peak, and on a 1.94-billion-cell grid the
  discarded array alone is **15.5 GB** (the review said 7.2 — it is float64, so
  twice that).
* **Valley gap closing bridged twice its documented width.** A morphological
  closing of radius k bridges 2k, because the dilation advances k cells from
  each side — verified, radius 5 bridges 10 cells. So `close_gaps_m=250` welded
  valley strips 500 m apart into one polygon, doubling their `valley_width_m`
  and pushing the confinement ratio across the unconfined break. It now uses
  half the gap width, and the window is in true metres rather than CRS units.
* **`hand_mean_m` was structurally always 0.** It averaged HAND over each
  segment's own stream cells, and HAND is zero at a stream cell by
  construction. It now averages over the segment's allocated valley cells,
  which is the vertical counterpart to `valley_width_m`. Reading it exposed a
  second bug in the same line: HAND was read raw, so the -9999 fill averaged
  in and gave `hand_mean_m = -4233 m` on a landscape with a few hundred metres
  of relief. Now 0 to 174 m, median 0.17.
* **Run-log records collided within a second.** The JSON was overwritten while
  the manifest was appended, so N rows pointed at one file holding the last
  result — and a calibration sweep is exactly a loop of fast calls.
* **`detect_headcuts` returned a path it had not written** when nothing
  matched, which on a re-run was the *previous* run's headcuts reported as this
  run's, with `headcut_count=0` beside them.
* **The inundation valid mask counted never-observed ground as observable.**
  `~excluded` is True where no scene ever looked, so with zero scenes
  `valid_fraction` came back 1.00 over the whole grid and the low-observability
  guard could not fire. Each scene's footprint is now accumulated and the claim
  is bounded by it.
* **The geology unit cache was shared across services**, so a `quaternary` call
  after a `surface` call joined against the wrong DescriptionOfMapUnits table —
  silently wrong where a MapUnit code existed in both.
* **Riparian rasterised a full-size array per patch** to ask whether it touched
  a stream. The label array already knows; it is now one pass.

231 tests.

## Session of 2026-09-17b — gully memory, and gullies as 2D

**Gully detection on a grid that does not fit.** The mask criteria are four
elementwise thresholds on four rasters. Each was read whole and cast to
**float64**: 8 bytes x 1.94 billion cells x 4 is **62 GB**, plus a fifth array
for drainage area — after the two expensive steps before it had already
succeeded. There is no neighbourhood term in any of the four tests, so the mask
tiles perfectly with no overlap; it is now built a block at a time into a bool
array. Tests assert the windowed mask is bit-identical to a full read at block
sizes 37, 64, 128 and 4096, and a source guard forbids `astype("float64")` from
coming back.

Three smaller wins from the same pass: slope_std and drainage area are now
sampled by bounding window on the centrelines only (a few thousand cells, not
two full grids); the gully directory stopped writing a second copy of the
multi-gigabyte slope_std raster; and gully density now divides by **valid**
cells rather than the bounding box, which on a DEM that is a third nodata was
understating density by that fraction.

`detect_gullies` now also states the expected peak before spending it. Thinning
and labelling are whole-grid operations on a boolean array and there is no way
to know that from outside.

**"No gullies found" now names the culprit.** The mask builder returns how many
cells passed each criterion on its own. It is nearly always one test doing all
the rejecting, and without the counts there was no way to tell which — so the
warning now says "the tightest test was slope_std, 0 cells passed it against
4.1M for the loosest; start by relaxing slope_std_min=0.04".

**Gullies as 2D footprints.** The detector already cuts cross-sections along
every centreline and runs the verified bank detection on them — that is where
`width_m` and `mean_depth_m` come from. The bank *positions* were reduced to a
median and thrown away. Closing a polygon through them gives a footprint with
measured edges: left bank tops in station order, right bank tops in reverse.
World coordinates are rebuilt with the same convention `extract_cross_sections`
used to cut the section (origin at `center_x`/`center_y`, left-hand normal from
`bearing_deg`), so left and right cannot silently swap and tie the polygon in a
bow. Same `gully_id`, so it joins straight back to the lines and the sections.

**And the polygon test found a real defect on its way in.** I asserted the
polygon area and `mask_area_m2` should roughly agree. They do not — the polygon
is **6.5x larger** — and the polygon is the one that is right. On the fixture of
known 14 m gullies:

* polygon implies **15.75 m** (the one-sample bank-top offset already documented
  at reach scale)
* detector mask implies **2.45 m** — about one cell

The thresholds (slope variability + negative TPI) select the steepest, most
incised part of the section, which is a ribbon near the centreline, not the
footprint. So `mask_area_m2` had been reported as the gully's area while
understating it roughly six-fold, and `gullied_area_km2` inherited it. Both are
fixed: the headline number now comes from the measured footprints, the mask
field is documented as what it actually is, and the ratio is pinned by a test so
nobody quietly "fixes" it back.

226 tests.

## Session of 2026-09-17 — the commodity layer, and what can actually be reused

**The question: stop rebuilding VBET and confinement, reuse them instead.**
Right instinct. The answer is not the one either of us expected, because of
licensing.

### What is actually available

| Candidate | Verdict |
|---|---|
| **Riverscapes VBET / ConfinementTool** | **GPL-3.0.** geolens is MIT. Importing GPL code into an MIT package and distributing it makes the combined work GPL. Also not on PyPI, and built around a Riverscapes project XML / Cybercastor workflow rather than a library API |
| **jtgilbert/VBET-2** | **No licence file at all** — which defaults to all rights reserved. Worse than GPL for reuse |
| `pip install vbet` | A **gambling bot** (`vbet` 6.20.4, "a virtual betting bot server"). The name is taken on PyPI |
| `pip install fct` | A **radio-astronomy image viewer** (FCT 1.0.5, "Basic Library for Viewing Radio Images"). Not the Fluvial Corridor Toolbox |
| **WhiteboxTools** | **MIT.** Rust binary, 567 tools, already an optional backend here |

Two of those name collisions would have installed something entirely unrelated
into a working environment. Worth knowing before typing `pip install vbet`.

So "reuse VBET" as literally stated is not available to an MIT package. But the
*goal* — stop maintaining a parallel commodity layer — is, through
WhiteboxTools, which is MIT and already wired in. It has every primitive the
hand-rolled code duplicates:

* `breach_depressions_least_cost` — carves an outlet instead of flooding a
  basin, so valley-floor elevations survive conditioning
* `d8_pointer`, `d8_flow_accumulation`
* **`elevation_above_stream` — this *is* HAND**
* `extract_streams`, `relative_topographic_position`, `standard_deviation_filter`

### What I did about it

The whitebox backend existed and had **never been verified**. Making it the
default on that basis would be trading one unverified implementation for
another, so instead:

* **`compare_backends()`** runs both on the same DEM and reports max
  contributing area, stream-mask IoU, HAND difference and the speedup, with a
  verdict. It builds its own collared V-valley fixture — collared deliberately,
  because a clipped DEM is the normal case and the one that broke depression
  filling.
* **The `>=` / `>` split between backends is fixed.** WhiteboxTools'
  `ExtractStreams` applies `>=` where geolens applies `>`, so the same DEM
  through two backends put the channel head one cell apart and shifted every
  segment index below it. Accumulation is an integer cell count, so `>= t+1` is
  exactly `> t` and they now agree by construction.
* **`backend_notes()`** explains availability in words. `pip install whitebox`
  succeeding tells you nothing: the binary is downloaded on first construction
  of `WhiteboxTools()`, and behind a proxy that fails with a 403 much later,
  during an analysis.

**I could not verify whitebox here** — the binary download is blocked in this
container (403). That is why this ships as a measurement tool rather than a
switched default.

### Rosgen, rebuilt on the right evidence

`landscape/valleyform.py` now measures the valley floor-to-divide, and the
crosswalk takes it. Tested against valleys of exactly known geometry:

    z(x) = relief * ((|x| - half_floor)/run)**n

so Vf, side slope and the shape exponent all have analytic truth.

**One real subtlety, found by the test failing.** The shape exponent separates a
glacial trough (n≈2) from a V-notch (n≈1), and the first implementation
returned **1.33 for a true 2.0** — which would read as a V. The cause is not a
coding slip: a parabola is genuinely flat near its vertex, so a 2 m floor
tolerance on a 300 m-relief trough calls the first **41 m of a 500 m wall**
"floor". The toe is cut off, and with it the part of the curve carrying the n=2
signal. Fitting log(rise) against log(distance) from the wrong origin is then
biased low.

Fitting the offset instead of assuming it is zero recovers it: **1.02, 1.51,
2.00, 2.50** against true 1.0, 1.5, 2.0, 2.5. The offset is a physical quantity
— how far into the wall the floor detection reached — not a free parameter.

What the crosswalk can now do that it could not:

* **Assert V (glacial trough) without a geologic map.** Shape does it; no
  fluvial process makes a U at valley scale.
* **Exclude V on shape** in the confined-and-moderate case, which used to
  return "II, V or VI, material decides" and now returns II with a reason.
* **Raise VI from never-assertable to a flagged candidate** where the valley is
  persistently one-sided. Asymmetry is not proof of structural control, but it
  is its signature, and it is the first DEM-derived evidence for it here.

### Also fixed

* **`_slope` fabricated terrain.** nodata became slope 0.0 with no nodata tag
  (630 million fake zeros on the Ruidoso DEM), and the ring of *valid* cells
  around each hole also read 0.0 because NaN propagates one cell through a
  centred difference. `valley._fuzzy_high` reads slope 0.0 as **maximum**
  evidence for "flat valley floor", so a closed ring of strongest-possible
  valley evidence formed around every void. Now uses a one-sided difference at
  the ring — verified to return the true 0.6 rather than 0.0 or the 0.3 a
  nearest-fill would give — and float32 rather than float64, halving every
  temporary.
* **TPI and curvature filled nodata with the global mean elevation**, turning a
  void into an artificial plateau and producing a ring of confident false
  gullies around every hole. Now a masked local mean; a test asserts zero
  gullies on a uniform plane with a hole in it.
* **`raster.percentiles()`** reads decimated. The notebook used to read a whole
  raster and partition four copies of it — 15 GB in one cell on the Ruidoso
  grid, after the two expensive steps had already succeeded.
* **`restream()`** re-derives the network at a new threshold with no re-routing,
  bit-identical to a full rebuild.

212 tests.

## Session of 2026-09-16c — inline plots, riparian band, NAIP streaming

**Inline plotting (`geolens/view.py`).** Step 7 of the notebook used to be QGIS
`iface` calls, which render as markdown outside QGIS — so on Sam's machine it
was a block of text pretending to be a map. Replaced with real matplotlib:
`show_dem`, `show_mask`, `show_vector`, `show_sections`, `show_landscape`, and a
`show(thing)` that dispatches on what it is given.

Rasters are read **decimated** via rasterio's `out_shape`, which uses overviews
when they exist, so drawing a multi-gigabyte DEM costs the same as drawing a
small one. Confinement classes and valley types get fixed colour tables, because
a categorical layer drawn with a continuous colormap changes colour between runs
whenever the class counts change.

Two silent failures found writing it, both of which rendered nothing and raised
nothing: `show_sections` read `offsets`/`elevations` off the section result,
which produces `points`; and `show_landscape` looked for `valley_bottom` where
the key is `valley_raster`, quietly dropping a panel.

**Riparian band (`geolens/riparian.py`).** The arid-country answer to the
inundation problem from the last session. Phreatophytes rooted in the alluvial
aquifer mark the floodplain whether or not water is flowing today, which is the
opposite of a flood map's problem.

Measured as **NDVI in excess of the local upland background**, the same relative
construction as `excess_drop_m` on headcuts and `min_above_base_m` on fans. That
is what makes a fixed threshold survive a drought year, a different sensor or a
different basin — a test shifts the whole scene by -0.08 NDVI and asserts the
area moves less than 10%. Scaled-integer NDVI (Sentinel-2 ships x10000) is
detected and rescaled.

Cut to low HAND, because greenness alone is not riparian: a north-facing slope
or a mesa-top pivot is green and is not floodplain. A test asserts the green
hilltop is excluded, and a second test asserts it **leaks in** when HAND is not
supplied, so the argument is understood as load-bearing rather than optional.

On the fixture: **100% of the corridor recovered, 0.481 km2 connected against
0.481 km2 of truth.** A detached irrigated field lands in `unconnected_km2` and
in `suspect_patches`, by connectivity to the stream network and by
rectangularity.

Two things worth keeping:

* **`percentile_filter` is not slow, it is impossible.** A moving percentile is
  a rank filter — it sorts every cell in the window per output pixel. A 2 km
  background window on 10 m cells is 201x201, 40,401 values sorted per pixel,
  and it raised MemoryError on a *400x300* test raster. On 1 m NAIP the same
  window is 2001x2001, four million per pixel. Replaced with `_coarse_percentile`:
  block-reduce, filter on the coarse grid, resample back. The background is a
  smooth "how green is this part of the country" surface and never needed the
  resolution.
* **I overstated a limitation and the test caught me.** I wrote that a field
  contiguous with the corridor can never be flagged; the test showed it
  sometimes is, by luck, depending on whether the field dominates the merged
  patch's shape. The docstring was wrong, not the test. The test now asserts the
  *inflation*, which is certain, and deliberately does not assert that the flags
  catch it.

**NAIP streaming (`geolens/naip.py`).** 1 m four-band imagery over a DEM's
extent, from Microsoft Planetary Computer's STAC, as **Cloud Optimised GeoTIFFs
read by HTTP range request** through `/vsicurl/`. Only the windows overlapping
the DEM are ever transferred — a full NAIP quad is ~500 MB and a typical reach
needs a few percent of one.

Scenes are mosaicked **newest-first, not averaged**: averaging across vintages
blends a 2021 flight with a 2018 one into an image of no particular date. The
contributing years come back in `years`, so a mixed-vintage result is visible.

Tiles are cached to `<output>/naip/` (or `GEOLENS_NAIP_CACHE`) **with the flight
year in the filename**, at Sam's request, so the imagery a classification was
made against is still on disk when it is time to truth it.

Two bugs: `/vsicurl/` was being prefixed onto local paths as well as URLs
("Missing url parameter"); and the nodata fixture zeroed only the NIR band,
leaving red valid, which is not how real NAIP nodata looks — it is zero in all
four.

**And a third, worse one, found writing the notebook step: the module defeated
its own purpose.** NDVI was built on the *AOI raster's* grid. Point it at a 10 m
DEM — which is the normal case — and every output cell is the mean of a hundred
NAIP pixels. The result is a correct, clean, 10 m NDVI, and it throws away
exactly the resolution NAIP was streamed for: a 20 m desert riparian corridor is
two cells at 10 m and twenty at 1 m, and two cells is an edge effect rather than
a measurement. Nothing failed. Nothing warned. It would have been invisible.

The fix is in three parts, because the obvious one alone makes things worse:

* `resolution_m` now defaults to **1.0**, so NDVI is built at NAIP's resolution
  over the AOI's extent. `resolution_m=None` restores the AOI grid and warns
  about what that costs. A cell-count ceiling refuses a county at 1 m with a
  message instead of being OOM-killed.
* **The resolution is part of the cache key.** Without that, a 10 m window
  cached by an earlier run answers a 1 m request: right shape, right CRS, 10 m
  information inside it. That is the same class of silent-wrong as the original
  bug.
* **`riparian_band` now resamples HAND and streams onto the NDVI grid** instead
  of skipping the restriction on a shape mismatch. This was the part that made
  the rest safe to change: HAND at 10 m and NDVI at 1 m never match, and the old
  code dropped the height cutoff with a warning — leaving every green mesa top
  in the band and still returning something that looks like an answer. A test
  asserts a 50 m HAND still excludes the hilltop, and another asserts the
  "resampled up, so the edge is 50 m fuzzy" warning is raised rather than the
  mismatch passing quietly.

178 tests.

**Open question for Sam, not yet answered:** he raised object detection from
NAIP. *What* is being detected decides everything. Discrete things — headcuts,
structures, individual trees, bank erosion scars — need labelled examples and a
model, and the labelling is the expensive part, not the model. Riparian *extent*
is a segmentation problem and is already served by the NDVI-excess band above.
The `learn/loop.py` worksheet machinery is the natural place to collect labels
if he wants the first kind.

## Session of 2026-09-16b — BigTIFF, inundation, calibration loop

**The 4 GB TIFF limit, hit on a real DEM.** `TIFFAppendToStrip: Maximum TIFF
file size exceeded`. Classic TIFF offsets are 32-bit; float32 crosses 4 GB at
~1.07 billion cells, a 32,000 x 32,000 grid — 64 km square at 2 m. Every fixture
here is a few hundred cells across, so no test would ever have caught it. All 13
raster writes now go through `raster.gtiff_profile`, which sets
`BIGTIFF=IF_SAFER` (not `IF_NEEDED`: with compression GDAL cannot predict the
size and `IF_NEEDED` decides from an estimate a compressed write can overshoot)
and tiles anything large. A test greps the source for hand-built GeoTIFF
profiles so a fourteenth call site cannot reintroduce it.

**Observed flood extent (`geolens/inundation.py`).** Copernicus GFM via EODC's
STAC API — 20 m, 2015 to now, CC BY 4.0, no API key. Chosen because
`ensemble_flood_extent` excludes normally-wet pixels by construction, so it is
the overbank anomaly rather than total water.

Three obvious alternatives are **circular** and are documented as such rather
than quietly used: NOAA FIM is HAND-based, FEMA NFHL is hydraulic modelling over
a DEM, OPERA DSWx-S1 masks by HAND threshold. Validating a DEM-derived
floodplain against any of them compares a model to a model.

The metric is **recall only**, over observable pixels, and there is a test
asserting precision/IoU/F1 never appear in the output. A correct floodplain
exceeds any observed extent; reporting precision invites optimising it, and
optimising it means shrinking the floodplain to whatever SAR caught.

**Arid systems: documented as the wrong tool.** Sam works ephemeral to
intermittent country. Two problems compound. A monsoon flood lasts hours against
a 6-12 day revisit, so coincidence is a couple of percent per event. Worse, a dry
sandy channel bed is smooth at C-band and reads dark exactly like open water —
GFM lists "water look-alikes (flat impervious, sandy soil)" among its exclusions,
so a desert wash either gets excluded (removing the corridor of interest) or read
as flooded while bone dry. `arid_signature` in the result flags the pattern.
**The arid-region equivalent is riparian vegetation** — phreatophytes rooted in
the alluvial aquifer map the floodplain far more stably than flow does. Not built
yet; it is the obvious next module for the Southwest.

**The calibration loop (`learn/loop.py`).** The pieces existed; nothing joined
them. `write_worksheet` -> fill it in in GIS -> `ingest_worksheet` ->
`run_cycle`. Design points that carried the weight:

* **It can refuse its own output.** `keep_current` when the new fit does not beat
  the incumbent on identical held-out folds.
* **A permutation null, and it took three attempts to get right.** The first
  version recommended `adopt` on *pure random data* — held-out 0.448 against an
  incumbent 0.337 looked like an improvement. In-sample separability overfits,
  and cross-validation alone does not save you when folds are small. Fix: run the
  whole pipeline on shuffled labels. Comparing against the null's mean + 0.05 was
  too lenient; against its 95th percentile the real score cleared it by 0.005,
  a coin flip reported as a result. The bar that works is **the null's mean plus
  two of its own standard deviations** — self-scaling, because with few labels
  the null is wide (mean 0.34, sigma 0.06 on 60 points) and with many it tightens.
* Stratified folds, or a 5-fold split on 5%-minority data produces folds with no
  minority class at all.
* A learning curve, because "should I label more" is the question people actually
  have.
* Worksheets do **not** pre-fill the human column — people confirm a default far
  more readily than they generate an answer. Agreements are stored alongside
  corrections, or every fit is biased toward the cases where the tool failed.

134 tests.

## Session of 2026-09-16 — fans solved, Rosgen built

**Alluvial fans: 3/3, no false positives.** Apex within 7 m, area -4.9%, radius
+2.9%, radial gradient -1.8%.

The apex is the **concave break in the channel's own long profile** -- gradient
falling from the canyon's value to the fan's, 10% to 5% within a hundred metres
on the fixture. That is `detect_headcuts` with the sign reversed, walking the
same D8 graph. Window length matters more than anything else: 100 m puts every
apex within one cell, 250 m moves the median error to 351 m, because a long
window averages canyon and fan together.

**The fixture was wrong a THIRD time, and that was the whole problem.** The cone
was built at `basin_elevation + radius x slope`, putting the apex **87 m above
the canyon floor feeding it** -- a dam across the mouth. Flow could not cross
it, priority-flood flooded the canyon, and routing escaped around the fan's
lateral margin. Every apex detector for three sessions landed on that margin
channel, 500-800 m off in *y*, and I read the error as a tuning problem because
I only ever looked at the distance, never the direction. Measuring dx and dy
separately is what broke it open: dx was +145 m and dy was +735 m, which is a
point on the fan's edge, not its toe.

`make_fans.py` now asserts its own geometry on every build -- step across the
mouth, apex elevation against the canyon floor. Each of the three fixture bugs
would have been caught by one line of that and instead cost a session each.

Two other things that had to be fixed, both of which returned confident nonsense:

* **The grow died two cells from the apex.** The apex sits ON a slope
  discontinuity -- that is what makes it an apex -- so smoothing bleeds the 30%
  range face into the first fan cells. The apex cell's smoothed slope reads
  0.532 against a 0.01-0.15 band. The frontier hit that, marked every cell seen
  and rejected, and returned nothing. The grow now suspends its slope test
  within one smoothing radius of the apex.
* **The slope-noise trap, now self-correcting.** `auto_slope_floor` measures how
  steep the flattest ground in the DEM actually reads (a low percentile of
  smoothed slope) and raises `fan_slope_min` above it. With it off, the same DEM
  gives 3.34 km2 fans instead of 0.73 km2. This is the trap that once produced
  139 km2 of fans on a 49 km2 DEM, and it is no longer a warning you can ignore.

**Rosgen valley types: built, logic verified, thresholds uncalibrated.**
`landscape/rosgen.py` crosswalks confinement + gradient + surficial material +
detected fans to types I-XI. 23 tests, including an exhaustive sweep asserting
that every returned type is real and that **VI is never asserted** -- it is
defined by structural control, which neither a DEM nor a geologic map shows.

The honest result, measured on the fan fixture: **without geology 94% of
segments are unresolved; with it, 19%.** The types geometry cannot separate
(II/V/VI, VIII/IX, X/XI) are most of the classification. Unresolved segments
return their `alternatives` and a `basis` string saying which evidence is
missing, rather than a confident guess.

Still to do here: the gradient breaks (0.5 / 2 / 4 / 10%) are my encoding of
Rosgen's descriptive classes, not values he published. Field-check a dozen
valleys and move them.

## Session of 2026-09-15

**Install, fixed properly.** `pysheds/__init__.py` contains one line
(`__version__ = "0.5"`) and imports nothing, so `import pysheds` succeeds with
none of its dependencies present. Both the installer's dependency-filling probe
and `terrain.available_backends()` tested exactly that, passed, and reported a
backend as ready that then died on a missing `geojson` at first real use. Both
now probe `pysheds.grid`, and `geojson`/`looseversion`/`affine` are declared
outright. The installer's smoke test now runs terrain + HAND, so this class of
failure surfaces at install time rather than in a notebook.

**Headcuts: done, verified.** `detect_headcuts` now uses
`topotoolbox.StreamObject.knickpointfinder` and walks the stream *graph* rather
than a traced raster path — an edge is a real connection by construction, which
makes the old 380 m-drop bug impossible. **6 of 6 on the fixture, no false
positives, every point within 3–6 m of its known position.**

Three things that had to be right, each of which scored 0/6 when it was not:

* **`excess_drop_m`, not `drop_m`.** Subtract what the reach below would have
  lost over the same distance. Raw drop reads 3.1 m on a 2.2 m headcut because a
  12 m window on a 4.5% hillslope collects half a metre of hillslope fall.
* **Upstream-most per line, by ELEVATION.** A headcut is the migrating head, so
  it is the furthest-upstream step, not the biggest — selecting the largest
  scores 1/6. Selecting by smallest contributing area scores 0/6, because short
  headwater stubs of unrelated streams sit within the join distance of a gully's
  downstream end and carry less drainage than the gully's own head. Elevation is
  the only unambiguous measure. (Smallest `x` also scores 6/6 here, but only
  because these gullies happen to run west to east. That is the fixture, not a
  rule.)
* **Do not call `removeedgeeffects`.** It removes every stream touching the
  boundary and takes the gully heads with it: 6/6 becomes 0/6. Edge artefacts
  are exposed as an `edge_m` column instead, so the caller filters rather than
  having real features silently deleted.

Also: `stream_threshold_km2` must be small (0.005 default) or the upstream gully
heads are not in the network to be found at all. And pass `lines` — without it
recall is identical but precision is ~20%.

**Geology fetching: added.** `geolens.geology` fetches surficial and bedrock
material by geometry from the **USGS Cooperative National Geologic Map**
(released Aug 2025, v2 Sept 2026) — public domain, no key, polygon queries, and
a 37-term Quaternary material vocabulary that maps nearly 1:1 onto Rosgen valley
types. This unblocks the Rosgen crosswalk.

Findings from the survey worth keeping:

* CNGM supersedes SGMC and Soller DS-425 for this purpose. DS-425 is 1:5,000,000
  and download-only — a hundred times too coarse.
* **Macrostrat's polygon endpoint is silently broken.** `/carto/{scale}?shape=`
  documents a WKT parameter, ignores it, and returns global results led by
  Arctic Ocean basalts — for a point *and* for a polygon. It looks like success.
  Its point API is fine and reaches finer source maps; use it only that way.
* HyRiver does not cover geology at all. No maintained Python package fetches
  geology by geometry, so this is a genuine gap rather than a re-wrap.
* `macrostratpy` has no licence file and was last touched in 2024. Do not depend
  on it.

**Untested in one specific way:** outbound HTTPS to `services.arcgis.com` is
blocked in the build container, so the parsing, joining, clipping and
summarising are tested against recorded response shapes (26 tests) but the live
call never ran. `geolens geology --selftest` exercises it against a known area
of the Colorado Front Range. Run it once before trusting a geology result.

**Persistence: added.** Every `geolens.api` call now writes its result,
arguments, duration and output paths under `<output>/runs/`, with a
`manifest.jsonl` index and `api.run_history()` to read it back. Cheap read-only
calls are excluded so they do not bury the expensive runs. `GEOLENS_PERSIST=0`
turns it off. One bug found writing it: `np.float64` subclasses `float`, so a
NaN passed straight through `isinstance(obj, float)` and `json.dumps` wrote a
bare `NaN` — invalid JSON that breaks every strict parser reading the file back.

**Alluvial fans: narrowed, not solved.** Two real findings, no shipped fix.

1. **The fixture was wrong again.** It carved canyons through the range but left
   the range surface sheeting east past them, so each canyon collected only its
   own 80 m width — about 0.2 km² at the mouth, below any sensible stream
   threshold. The entire range held **9 stream cells**. There was no confined
   reach above any apex, so an apex detector driven by confinement transitions
   had nothing to find, and the detector looked broken when the terrain was.
   Fixed by tilting the range surface toward the nearest canyon
   (`RANGE_CROSS_SLOPE = 0.12`); the range now carries 3.8 km² of drainage and
   3899 stream cells. **This is the second time a fan result was a fixture bug.
   Check the fixture first.**
2. **Neighbourhood mean slope cannot measure confinement here.** A fan surface
   at 5.5% is steeper than the basin at 0.4%, so it reads as "confined" and the
   downstream-most confined cell lands at the fan *toe*. The signature is a
   miss of ~720 m, which is the fan radius. Switching to incision (how far a
   cell sits below its surroundings) cut candidates from 97 to 5–7 but did not
   move that 720 m — the toe is genuinely incised relative to its surroundings
   too.

   The fix remains what it was: drive apex finding from the confinement
   transition `classify_segments` already computes per segment, not from a
   raster proxy. Everything needed for that now exists and the fixture is finally
   honest.


**To run it: [RUNNING.md](RUNNING.md).**

## TL;DR

Reach scale and landscape scale both work and are verified. So do gullies,
headcuts, fans, the Rosgen crosswalk and the riparian band. The three fetchers
(geology, NAIP, inundation) have tested logic and **untested live calls** — run
their selftests. A research pass found that several pieces of this package
duplicate mature libraries — read "Don't rebuild these" before writing more
code.

*(Everything below this line predates the session log above and is kept for the
failures it records. Where the two disagree, the session log is current.)*

---

## Working and verified

**Reach scale** — cross-sections, profiles, channel/bank/floodplain delineation
by slope break. Against a synthetic compound channel: floodplain width 0.00 %
error, entrenchment ratio 2.59 %, bank top within one sample.

**Landscape scale** — HAND, valley bottom, network segmentation, confinement
classes. Against a synthetic landscape: wide valleys (60-93 cells) 0.0-0.7 %
error; narrow valleys (10-16 cells) 25-30 %, which is the resolution limit.
667 k cells end to end in 5.8 s.

**Gully detection** — slope standard deviation + negative TPI + slope + drainage
band, thinned, vectorized, and merged into whole features. 6/6 on the fixture.

`python quickstart.py` · `geolens landscape <dem>` · `python -m pytest tests/ -q`

`python tests/_doccheck.py` executes every snippet in RUNNING.md — 18/18 pass.
Run it after any API change so the docs cannot drift from the code.

**Gully detection** finds exactly 6 of 6 known gullies on the fixture and 91 %
of total length (6.53 of 7.20 km), matched one-to-one. Over-segmentation is
fixed: `merge_fragments` now joins fragments that continue each other
(endpoints within tolerance, outward directions roughly opposite) after
`linemerge`, which alone cannot resolve junctions. Insensitive to the merge
angle across 30-60°, so it is not a knob you have to tune.

Width and depth are now **measured**, not proxied: cross-sections are cut along
each detected centreline and the verified reach-scale bank detection runs on
them. Mean depth lands within 1 % of truth (2.97 vs 3.0 m); width is one sample
wide (16.0 vs 14.0 m at 2 m cells), the same bank-top offset measured at reach
scale. `volume_m3` assumes a parabolic section (0.667 x w x d x L) — replace
that shape factor if your gullies are closer to trapezoidal.

## Memory and tiling: SOLVED

Superseded the earlier "coarsening is the only safe option" position. It was
wrong, and the correction matters.

**The insight: only flow accumulation has a global dependency.** A cell's
contributing area depends on everything upstream. Slope, TPI, distance
transforms, region grows, cross-sections — all local. So route once over the
full grid, then tile the local work against that.

`landscape/flow.py` does the routing in the smallest dtypes that carry the
information: uint8 flow direction, uint8 in-degree, float32 accumulation.
At 2 m over 3 M acres (3.0 billion cells) that is ~30 GB — fits in 128 GB.
Priority-flood filling, Kahn-sweep accumulation, reverse-traversal catchments;
all O(n) or O(n log n), no recursion.

Verified: **accumulation matches pysheds exactly** (max 651,086 = 651,086,
network IoU 99.2%) and runs faster in 10 MB of arrays on the test fixture.

`build_terrain(..., flow=...)` clips the global flow to a tile so contributing
area is inherited rather than restarted. With that, **grid tiles reproduce an
untiled run to 0.1%** (187.6 vs 187.4 km at two different tile sizes). Watershed
tiling is no longer needed — tile shape stops mattering once the global quantity
is solved. `complete_watersheds()` is still there and verified to preserve
drainage area exactly (0.00% error) for when a true watershed unit is wanted.

**Two bugs found on the way, both silent:**

* **float32 epsilon.** The depression-fill increment was a fixed `1e-5`, but
  float32 spacing at 1900 m elevation is `1.2e-4`. The addition was a no-op:
  filled cells stayed exactly level, 4,144 cells ended up with no downslope
  neighbour, and each became a spurious outlet that shattered the network into
  fragments (max accumulation 20k instead of 651k). The increment now scales
  with elevation magnitude. **Any fill-by-epsilon code has this bug latent.**
* **Per-tile output collision.** Every analysis writes to fixed filenames under
  the guard's output directory, so tiles sharing one guard overwrote each other
  and every returned path pointed at the last tile's files. The merge then
  produced one tile's features repeated N times — invisible, because the paths
  and the feature counts all looked plausible. `run_tiled` now gives each tile
  its own directory and warns if `fn` does not accept a guard.

A third, in the merge: clipping shortens geometry but leaves stored attributes
alone, so summing `length_m` after clipping overstated the total (117% of the
untiled run). Length is recomputed from clipped geometry; ratio-derived width
fields are set to NaN on clipped features, with `length_m_preclip` retained.

## Cross-sections as GIS features

`api.export_cross_sections()` writes section traces as a shapefile and
GeoPackage with a lookup table, plus per-section plots. Read `xs_id` off the
attribute table in GIS, then `api.plot_cross_section_by_id(result, N)`.
The stacked overlay plot remains, but it is for eyeballing a reach, not working.

`api.representative_sections()` picks the **medoid** of each reach — the real
section closest to the centre of the reach's distribution, standardised per
metric. Deliberately not a mean: an average cross-section is a shape that exists
nowhere on the ground. Group by `confinement` (follows the geomorphology) or
`station` (equal lengths).

One bug worth remembering: the medoid initially required only "some metric
present", but `relief_m` survives a failed delineation, so a section with no
bankfull at all was median-filled to the centre and chosen as most typical. It
now requires measured channel geometry, and returns nothing rather than
inventing a representative for a reach where nothing was measurable.

## Superseded: the old tiling caveat

`plan_run()` estimates peak RAM from the DEM header and, when it will not fit,
names the cell size that would. `api.resample_dem()` does the coarsening.
**That is the recommended fix and it is correct** — the network stays whole.

`api.landscape_tiled()` splits into subwatersheds via D8 pointer-jumping over
the flow graph, absorbing at stream cells so tiles are subwatersheds rather than
one terminal basin. It works, and tiles are genuinely smaller — but **tiling
truncates drainage area at every boundary**, so the merged network is badly
short (6.4 km of 187 km on the test landscape). The warning and a
`network_loss` block are in the result; a test pins the behaviour so nobody
"fixes" the warning away. Use it only when coarsening is impossible.

An unsolved version of this would be worth building: route flow once on the full
(or coarse) extent, then pass that accumulation into each tile so contributing
area survives the cut. That is the standard approach and would make tiling
correct.

## Fixed, with the failure kept on record

**`detect_headcuts`** used to compute gradient assuming consecutive traced-path
points were adjacent cells; when a trace merged disconnected fragments it
reported 380 m drops on a DEM with 85 m of relief. Replaced 2026-09-15 with
`topotoolbox.knickpointfinder` walking the stream graph. 6/6 on the fixture,
no false positives, 3-6 m position error. See the 2026-09-15 session notes.

**Alluvial fan apex detection** scored 0/3 for three sessions. Solved
2026-09-16: the apex is the concave break in the channel's long profile, and
the fixture had been damming its own canyon the whole time. 3/3, apex within
7 m, area -4.9%. See the 2026-09-16 session notes for the three approaches that
failed first, which are worth not repeating.

---

## Research findings: don't rebuild these

A deliberate survey of existing open-source tooling. Several parts of this
package duplicate mature, validated work.

### Replace outright

| What | Use instead | Why |
|---|---|---|
| `detect_headcuts` | **`topotoolbox.StreamObject.knickpointfinder`** | Implements the published Schwanghart & Scherler method. Python bindings exist and install cleanly (`pip install topotoolbox`, v3.0.1). Validated; mine is broken. |
| 3DEP DEM download | **`py3dep`** (HyRiver) | Exactly the "point at a shapefile, get a buffered DEM" function requested. Accepts geometries and GeoDataFrames. |
| SSURGO / soil download | **`pygeohydro`** (HyRiver) | gNATSGO, SoilGrids, derived soil properties, NLCD land cover, and WBD watershed boundaries — all by geometry. 90 stars, actively released. |

Writing our own fetchers for 3DEP and SSURGO would be pure duplication. HyRiver
already does both, by geometry, and handles the service quirks.

### Strongly consider replacing

**`topotoolbox`** (Python, v3.0.1) also provides `flow_accumulation`,
`drainagebasins`, `vertdistance2stream` (which *is* HAND), `chitransform`,
`ksn`, `streamorder`, `trunk`, `quantcarve`, `to_geodataframe`. That covers most
of `landscape/terrain.py` and much of `landscape/network.py`, in a C library,
faster and better tested than the pysheds path. It also brings chi analysis,
which we do not have and which is the standard basis for knickpoint work.

**Valley bottom / confinement** already exist:

* [Riverscapes/riverscapes-tools](https://github.com/Riverscapes/riverscapes-tools) — open-source Python 3, includes VBET
* [Riverscapes/ConfinementTool](https://github.com/Riverscapes/ConfinementTool) — confinement specifically
* [EVS-GIS/python-fct](https://github.com/EVS-GIS/python-fct) — Fluvial Corridor Toolbox, Python

Our valley-bottom extraction already borrows VBET's calibrated evidence weights
(0.65 HAND / 0.35 slope, thresholds 0.65 / 0.85). Worth evaluating whether to
depend on riverscapes-tools directly rather than maintain a parallel
implementation.

### On pytRIBS

**Its license is GPL v2, not MIT.** That matters: GPL v2 is copyleft, so its code
cannot be copied into this MIT-licensed package without relicensing the whole
thing. Read it for ideas, do not lift code.

Scope-wise it is a pre/post-processor for the tRIBS hydrologic model — TIN mesh
generation, met forcing, model runs — not a geomorphology toolkit. Small (10
stars, 244 commits). Its `Soil` and `Land` preprocessing classes are the only
overlapping ideas, and HyRiver covers that ground with a permissive license.

### What stays genuinely ours

* **Reach-scale bank/floodplain delineation with the valley-confinement cutoff.**
  No direct equivalent found. VBET does valley bottoms at landscape scale;
  ConfinementTool does confinement; neither does per-section bank tops,
  bankfull geometry and entrenchment with a valley-wall stop. Verified.
* **The MCP server.** Nothing else exposes this analysis to Claude directly.
* **The skill / workflow layer** — CRS hygiene, when to plot, how to tune.
* **The two-tier framing** — same definitions at lidar and coarse scale, so the
  coarse pass is calibratable against the fine one.

---

## Recommended next session

1. **Swap in the libraries.** `topotoolbox` for knickpoints and (probably) flow
   routing/HAND; `py3dep` + `pygeohydro` for the data fetchers Sam asked for.
   This deletes more code than it adds.
2. **Then the deployment layer.** Notebooks are the cheapest path and are
   testable — one notebook per workflow (inspect / reach / landscape / erosion),
   with the tuning notes inline as markdown. A QGIS Processing plugin is the
   nicer distribution but could not be tested in the build container, so it
   should be written only where a QGIS install is available to run it against.
3. **Calibrate.** Confinement breaks and the channel-width relation are both
   uncalibrated defaults. Run real data, read
   `summary.confinement_ratio_percentiles`, set breaks from the distribution.
4. **Rosgen crosswalk** needs surficial geology joined in. The crosswalk table is
   `_rosgen_candidates()` in `landscape/classify.py`.

## Future: machine learning (not scoped yet)

The idea raised: run the workflow, verify the output by hand, feed the corrected
samples back in, repeat. That is **active learning over human-corrected labels**,
and it is a good fit here — but only if the plumbing exists first.

What matters is that the groundwork is mostly already in place, and one piece is
missing:

* **Features exist.** Every segment and gully already carries a numeric vector:
  `valley_width_m`, `confinement_ratio`, `gradient`, `hand_mean_m`,
  `drainage_km2`, `mean_slope_std`, `mean_depth_m`, `width_m`. That is a design
  matrix already — the per-segment CSV is one `pandas.read_csv` from trainable.
* **Parameters are reported with every result.** `result["parameters"]` carries
  the full threshold set, so a corrected sample can be tied to the exact
  configuration that produced it. Reproducibility is the hard part of this kind
  of loop and it is already handled.
* **What is missing: a label store.** There is nowhere to record "this segment
  was classified confined, a human says partly confined". That is the first
  thing to build — a simple table keyed by segment id + run parameters, with the
  model's answer, the human's answer, and who corrected it. Everything else
  depends on it.

Two framings worth separating before writing code, because they lead to
different systems:

1. **Learn the thresholds.** Keep the physical algorithm, fit its parameters
   (`bank_slope`, `slope_std_min`, confinement breaks) to corrected examples.
   Small data, interpretable, and it directly solves the calibration problem
   that is currently manual. **This is the one to do first** — it needs tens of
   corrections, not thousands, and the result is a better default parameter set
   per physiographic region.
2. **Learn the classification.** Replace the rule-based classifier with a model
   trained on the feature vectors. More powerful, needs far more labelled data,
   and gives up the explainability that makes the current output defensible in a
   design report. Worth it only once (1) is exhausted.

A caution worth writing down now: **corrections are not ground truth.** A
human-drawn bank top is itself an interpretation with its own error, often
larger than the one-sample offset the detector already has. Before training on
corrections, measure inter-operator variability on the same reach — if two
people disagree by more than the algorithm's error, the algorithm is not the
thing to fix.

## Smaller open items

* `whitebox` backend in `landscape/terrain.py` is written but **untested** — the
  build container's proxy blocked the binary download.
* `pysheds` 0.5 needs a `np.in1d` shim on NumPy 2 (`_pysheds_numpy2_shim`).
  Drop it when pysheds ships a NumPy 2 release.
* Gully detection thresholds were set from the fixture's own percentiles. Do the
  same on real data — read the `slope_std` raster's distribution first.
* `merge_fragments` collinear joining is generic. `landscape/network.py` still
  calls it in exact-endpoint mode only; passing a snap tolerance there would
  likely reduce stream-network fragmentation the same way. Untested.
