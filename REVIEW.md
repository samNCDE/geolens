# Review before shipping — 2026-09-17

Short version: **do not ship `geolens_6.zip`, and do not trust the Ruidoso
outputs produced with it.** Two bugs in it are severe enough to invalidate the
run, and both are now fixed. A third thing worth knowing is that some of my own
verification yesterday was not verifying what I said it was.

---

## 1. My verification was compromised, and I found it by accident

While checking the zip, I ran `python install.py --here` inside the extracted
copy. An editable install is a pointer, and that repointed this machine's
`import geolens` at `/tmp/.../verify/geolens` — the frozen zip — instead of the
working tree.

From that moment, edits to the working tree did nothing and the tests still
passed, because they were testing the frozen copy. I only caught it because a
fix I had just written produced a result that was impossible for either the old
or the new code, which forced me to check what was actually being imported.

So: **any "N tests pass" I reported between packaging the zip and finding this
was measuring the wrong tree.** The 201 passing now are against the real one.

Fixed in `install.py`: `--here` now prints where geolens is currently imported
from and where it is about to be imported from, when those differ. It is a
warning, not a refusal, because repointing is sometimes what you want — but it
is no longer silent.

I am flagging this first because it is the failure that could have let
everything else through.

---

## 2. Ship blockers, found in your Ruidoso run

### 2a. Your DEM is in US survey feet. Everything assumed metres.

`EPSG:6529` is NAD83(2011) / New Mexico Central (**ftUS**). Consequences on the
run you already did:

| | asked for | actually got |
|---|---|---|
| `stream_threshold_km2=1.0` | 1,868,657 cells | 173,605 cells — **10.8× too low** |
| cell size | 2.4 m | 2.4 ft = **0.73 m** |
| `segment_length_m=500` | 500 m | 500 ft = 152 m |
| any vertical threshold | metres | feet |

The stream network in `classified_dir` is an order of magnitude denser than you
asked for, and everything keyed off it — valley bottom, segments, confinement —
inherits that. **Those outputs need regenerating, not reinterpreting.**

Two things worth noticing. First, your data is *finer* than you thought: 0.73 m,
not 2.4 m. That changes the answer to your TIN question (see §5). Second, the
package warned about geographic CRS (degrees) but had no check at all for
projected-but-not-metres, which is the far more common case in the US.

Fixed: new `geolens/units.py`. Parameters stay in metres and are converted at
the point of use; `TerrainResult.cell_size` is now **true metres** with
`cell_size_crs` alongside it. The vertical unit is not declarable in a GeoTIFF,
so it is assumed to match the horizontal and the assumption is stated with the
relief converted both ways:

> Elevations run 6,066 to 11,974, which under that assumption is 1,849 to
> 3,650 m.

Ruidoso is ~2,100 m and Sierra Blanca is 3,652 m, so you can confirm the guess
at a glance. Pass `vertical_unit="m"` if it is ever wrong.

### 2b. HAND was measured against the nodata value

You said HAND "doesn't look like it worked". It didn't. From
`hand.tif.aux.xml`:

- HAND maximum: **20,628.76**
- Your DEM's total relief: **5,908**

HAND is a height above a drainage cell *in the same grid*, so it cannot exceed
the DEM's relief. The excess is exactly 9,999: flow paths ran into nodata and
the height was taken above a cell holding −9999 instead of above a stream.
Every affected cell reported `elevation + 9999`.

It also had **10.5 million more valid cells than the DEM has** — HAND assigned
where there is no ground.

Fixed: HAND is now masked to the DEM's valid cells, and the invariant is
enforced with a warning naming the cause. A test reproduces your exact numbers.

### 2c. Depression filling silently did nothing on a clipped DEM

The numba priority-flood seeded only the **outer ring** of the array. A DEM
clipped to a watershed has nodata all the way around, so every ring cell was
closed before the loop started and nothing was seeded: the heap was empty, the
loop never ran, and it returned the DEM **unfilled** with `filled=0` and no
error. The pure-Python fallback seeded nodata borders correctly — so the
correct path was the one that almost never ran, since numba is a declared
dependency.

Fixed, with a regression test on a collared DEM, plus an error when a grid has
no outlet at all.

### 2d. Flow accumulation was float32, and accumulation is a cell count

float32 carries 24 bits. Above 2²⁴ = 16.7 M cells the spacing exceeds 1, so
`acc += 1` is a no-op and hillslope cells draining into a large trunk
contribute **nothing**. At your resolution that ceiling is a 67 km² watershed.
Above ~5×10⁸ the spacing is 64 and whole tributaries vanish.

Fixed: `uint32` — same 4 bytes, exact to 4.29 billion cells.

---

## 3. Three bugs I introduced yesterday, now fixed

Worth calling out separately because they were mine, in new code, and two were
the silent kind.

1. **`riparian._align` compared shape and CRS but not the transform.** Two
   40×40 EPSG:32612 rasters 100 km apart match on both. A HAND raster covering
   completely different ground was accepted verbatim, with no warning.
2. **A HAND raster with no CRS was filled with `+inf`**, so `h <= max_hand_m`
   was False everywhere. The warning said the layer "was ignored"; it had in
   fact deleted 100% of the result, and the next warning blamed `min_excess`.
3. **The NAIP cache key omitted the AOI extent.** What is cached is a clip
   already reprojected onto the run's grid, so two AOIs at the same resolution
   touching the same NAIP quad collided — and the second got the first's
   pixels, correctly georeferenced to the wrong place.

All three now have regression tests.

---

## 4. What I fixed for the crash, and what I still need from you

`roughness_dir` is complete on your machine, so `terrain_roughness` succeeded
and it died after. The next thing that runs is a cell **I wrote**:

```python
with rasterio.open(rg.slope_std) as s:
    ss = s.read(1)                       # 7.7 GB
pcts = {f"p{q}": np.percentile(ss, q) for q in (50,75,90,95)}   # +7.7 GB, ×4
```

~15 GB in one notebook cell, after the two expensive steps had already
succeeded. That is my best candidate, but **it is a candidate — send me the
traceback and I will confirm it rather than assume.**

Fixed regardless:

- `geolens.raster.percentiles()` reads decimated (uses overviews when present).
  Verified against a full read: agrees to 5×10⁻⁴ using 170k cells instead of 9M.
- The notebook step now reuses the routing instead of redoing it — which is
  your original question, below.

Still open, and it will bite next: `detect_gullies` holds roughly four
full-size float32 arrays at once, ~31 GB on your grid. The percentile fix gets
you past the cell that crashed and into one that may also not fit. Gully
detection needs to tile, or to run on a clipped sub-area. I have not done that
yet.

---

## 5. Your three design questions

### Reusing the routing instead of redoing it — you were right

`stream_threshold_km2` does not affect flow routing at all. Depression filling,
flow direction and accumulation are computed *before* any threshold is applied;
the threshold only decides which accumulation values get called a stream. So
changing it should never have cost a second routing pass — and it always did,
because the only way to change it was `build_terrain`, which starts from the
DEM.

The reuse machinery existed but was unreachable: `run_landscape` returns
`terrain.as_dict()` — paths, not the object — and `build_terrain(flow=...)`
only accepted a `FlowResult`, which the pipeline never returns.

New: `restream(terrain, stream_threshold_km2)` and `terrain_from_result()`.
Tested to produce **bit-identical streams and HAND** to a full rebuild, with a
test asserting the router is never called. Your 2.7-hour second pass becomes
seconds. Three thresholds — 1 km² for valleys, 0.2 for gullies, 0.005 for
headcuts — now come off one routing pass.

### Rosgen: you are right and I had it wrong

I built the crosswalk on confinement ratio (valley-bottom width ÷ **channel**
width) and **channel** gradient. Those are stream-type inputs. Read Rosgen's
definitions and every discriminator is a hillslope property measured from the
floor to the divide:

- I — "V-notched" → cross-section **shape**
- II — "gentler side slopes" → **side-slope** gradient, not channel gradient
- V — "U-shaped glacial troughs" → **shape**
- VII — "high-relief alluvial slopes" → **relief**, floor to divide
- VIII/IX/X — "wide" / "broad" → valley width **at the divides**

A 20 m channel in a 200 m valley bottom scores identical confinement whether
the walls rise 30 m at 8% or 600 m at 70% — the difference between Rosgen VIII
and Rosgen I.

New: `landscape/valleyform.py`, which cuts long sections, walks from the floor
to the drainage divide on each side, and measures:

- **Vf** — valley floor width to height ratio (Bull & McFadden 1977), the
  standard index for exactly this. Dimensionless, so it compares across basins
  and cell sizes, and you can check it against published values for the region.
- **shape_n** — fit `z ~ |x|ⁿ`; n≈1 is a V, n≈2 is a U. This is what separates
  a glacial trough from a fluvial V-notch at similar side-slope gradients.
- **side_slope** left/right, **valley_relief_m**, **asymmetry**.
- **divide_found** per side — a section that ran out of search distance is a
  lower bound, not a measurement, and is reported as such.

One real gain: persistent one-sided asymmetry is the classic signature of
structural control, so **Type VI moves from "never assertable" to "flagged as a
candidate"**, which is the first DEM-derived evidence for it this package has
had.

**Caveat, and it is the one I would not ship on:** this module was written
today, has no test file, and has never run on a real DEM. One synthetic V-valley
section returns Vf 0.55, side slope 0.39, n 0.92 — correct for that shape. That
is all the evidence there is. It needs a fixture with known geometry before you
rely on a number from it.

### TIN vs raster — the short answer, and why I can't give you the long one yet

Two things are true and they point opposite ways.

**A TIN built from your raster adds nothing.** It is a piecewise-linear
interpolation of samples you already have. Same information, larger, slower,
and it would cost you the entire package: D8 routing, priority-flood, HAND,
distance transforms, TPI and every region grow are array operations, and the
tiling insight this package is built on ("only accumulation has a global
dependency; everything else is local") depends on array locality. pysheds and
topotoolbox are raster-only, so you would lose the verified headcut detector
too. `pytRIBS` is the real TIN-based option and it is a *hydrologic model*, not
a terrain-analysis toolkit — and it generates its TIN from a raster DEM anyway.

**A TIN built from the source point cloud, with breaklines, is genuinely
better** for channel and gully geometry — that is why hydraulic modellers use
them. But the gain comes from going back to the LAS, not from triangulating a
raster. And note the hydraulics community moved the other way: HEC-RAS 2D uses
a raster terrain with breakline-enforced cell faces rather than a TIN.

Where that leaves you: your data is **0.73 m**, not 2.4 m. Before rebuilding
the package on a TIN, the cheap experiment is to run a reach at native
resolution and see whether the bank tops sharpen. If they do, the answer is a
finer raster over the corridor, not a new data structure.

I built an experiment to quantify this and **scored it against the wrong truth**
— the fixture defines "bankfull width" as the 12 m flat bed while the code
measures 16 m top-to-top, so my error percentages are meaningless. The trend
was right (bank-top error 2.0 m at 2.4 m cells vs 0.5 m at 0.5 m cells) but I
am not asking you to act on a number I got wrong once. It needs redoing.

### Gullies as 2D rather than lines

Your instinct is right and the pieces are already there. `detect_gullies`
already cuts cross-sections along each centreline and runs the verified
bank-detection on them — that is where `mean_width_m` and `mean_depth_m` come
from. Those bank points are in the section results and are currently thrown
away after being reduced to a mean.

So the 2D extent is a join, not a new detector: take the left and right bank
points from each section of gully *N*, order them by station, and close the
polygon. That gives you a gully footprint with real measured edges rather than
a buffered line, and it inherits `gully_id` so it joins straight back to the
line and the sections.

The scanning approach you described — classify every cross-section and
cross-reference to location — would work but does more: the detector already
knows which sections belong to which gully. I would do the join first and see
whether it is enough. I have not built it.

---

## 6. Still unfixed (two audits, 16 findings, 9 fixed)

Ranked by what I would fix next:

1. **`_slope` writes nodata as slope 0.0 with no nodata tag.** Confirmed on
   your data: `slope.tif` has 1,936,812,500 valid cells against the DEM's
   1,305,630,482 — **630 million fake zeros**. Worse, the ring of *valid* cells
   next to nodata also reads 0.0 (NaN propagates one cell through
   `np.gradient`), and the valley-bottom evidence function reads slope 0.0 as
   maximum "this is a flat valley floor" — a complete inversion, in a closed
   ring that `binary_fill_holes` can then swallow whole.
2. **`features.py` sets nodata to the global mean elevation before TPI**, so a
   void becomes an artificial plateau and every cell near it reads a huge
   negative TPI — a ring of confident false gullies around every hole.
3. **`persist.py` timestamps to the second**, so runs in the same second
   overwrite each other while the manifest keeps all the entries. A calibration
   sweep is exactly that pattern.
4. **`detect_headcuts` returns a path it did not write** when nothing matched —
   on a re-run that is the *previous* run's headcuts.
5. **`inundation` marks never-observed ground as observable**, so
   `valid_fraction` can be 1.00 with zero scenes.
6. **`geology`'s unit cache is shared across services**, so a `quaternary` call
   after a `surface` call reads the wrong lookup table.
7. `valley.py` gap closing bridges **twice** the documented width (a closing of
   radius *k* bridges 2*k*), so `close_gaps_m=250` welds valleys 500 m apart.
8. `distance_transform_edt(..., return_indices=True)` allocates a float64
   distance array that is immediately discarded — 7.2 GB at your grid size, in
   three places.
9. `riparian` rasterizes a full-size array **per patch** — O(patches × H × W).
10. whitebox uses `>=` for the stream threshold where pysheds uses `>`, and the
    log line says `>=` in both cases.
11. `hand_mean_m` is structurally always 0 (HAND is 0 at stream cells by
    construction, and it averages over stream cells).

---

## 7. The harder question you asked: usefulness and novelty

You asked me to be critical about this and not just about bugs. So:

**A large fraction of this package re-implements mature, validated tools, and
that is where essentially every bug above lives.** The priority-flood seeding
bug, the float32 accumulation bug, the `>`/`>=` split, the slope nodata bug —
none of these exist in WhiteboxTools, RichDEM, TauDEM or TopoToolbox. They
exist here because flow routing and valley-bottom extraction were rebuilt from
scratch, and rebuilding a solved thing costs you the decade of other people's
bug reports.

Riverscapes VBET and ConfinementTool already do valley bottoms and confinement,
in open-source Python, validated on real basins. `topotoolbox` already provides
`flow_accumulation`, `drainagebasins` and `vertdistance2stream` (which *is*
HAND) in a C library. This package's own `NEXT.md` says so and has said so for
sessions.

**What is actually novel here, and worth your time:**

- The tiling insight — route once globally, tile everything else — is real,
  verified to 0.1%, and I have not seen it packaged this way elsewhere.
- The relative-measurement pattern: `excess_drop_m` for headcuts, NDVI excess
  over local background for riparian, `min_above_base_m` for fans. Measuring
  against the local background rather than an absolute threshold is what makes
  these transfer between basins, and it is applied consistently.
- **Riparian band as the arid-country floodplain proxy.** This is the strongest
  idea in the package. Phreatophytes mark the floodplain when flow does not,
  radar flood mapping genuinely fails on ephemeral washes, and nobody else
  packages that substitution.
- Valley form / Vf at scale (new today, untested).
- The calibration loop with a permutation null that can say "this feature does
  not separate your classes at all" — most tools will happily fit noise.
- Recall-only flood validation, with a test forbidding precision from appearing.

**What I would actually do:** stop maintaining a parallel flow-routing and
valley-bottom implementation. Depend on WhiteboxTools or TopoToolbox for the
commodity layer, and spend the effort on the arid-Southwest work that nothing
else does — riparian, fans, valley form, gully geometry, the calibration loop.
That is a smaller package, a lot less surface area for the class of bug that
cost you an overnight run, and the part that is genuinely yours gets better
instead of competing for attention.

That is a bigger decision than a bug fix, so I have not started it.
