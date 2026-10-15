# geolens

> **Sharing this with a colleague? Point them at [START_HERE.md](START_HERE.md)**
> (also `START_HERE.html`): what it does, how to install and run it, and how
> much to trust each result.

Inspect geospatial data and extract channel geometry from a DEM — from Claude, a
terminal, or Python.

**No ArcGIS licence.** Everything runs on GDAL/OGR, rasterio and shapely, on
Windows, macOS and Linux. Esri File Geodatabases are read through GDAL's
open-source `OpenFileGDB` driver with no ArcGIS installed.

## What it does

**Examine data** — catalogue a folder, describe CRS/schema/extent/statistics,
summarize attributes, sample real rows, render a plan-view map.

**Measure channels** — longitudinal profiles with reach slope, perpendicular
cross-sections with thalweg detection, and channel/bank/floodplain delineation by
slope break.

The delineation walks outward from each thalweg:

```
flat bed  →  steep bank face  →  bank top  →  flat floodplain  →  valley wall (STOP)
```

Closing the floodplain at the valley wall is the point — it stops the floodplain
climbing a confining hillslope indefinitely. Per section you get bank tops, bank
heights, bankfull width and depth, width/depth ratio, entrenchment ratio, and
whether each side is valley-confined.

> **New here? Read [RUNNING.md](RUNNING.md)** — install, Python/notebook/QGIS
> recipes, a troubleshooting table, and a test checklist. Every snippet in it is
> executed by `tests/_doccheck.py`.

## Two scales

**Reach scale** — precise geometry on one channel from a lidar DEM: cross-sections,
profiles, bank/floodplain delineation.

**Landscape scale** — generalization across a whole drainage network from a coarse
DEM: HAND, valley bottom extraction, network segmentation, confinement
classification. Sized for multi-million-acre areas.

They share definitions, so the coarse pass can be calibrated against the fine one.

## Install

**Windows** — double-click `install_windows.bat`
**macOS / Linux** — `bash install.sh`

That builds a separate venv, installs everything into it with pip wheels, then
verifies itself by running the workflow on a synthetic valley with a known 16 m
channel. It does not touch your Anaconda base or ArcGIS Pro's Python.
`--conda` builds a conda-forge environment instead.

If anything goes wrong:

```bash
python install.py --check    # diagnose; installs nothing
python install.py --smoke    # run the real workflow on a test DEM
```

See `INSTALL.md`. Manual install still works (`pip install -e ".[all]"`), but
the installer exists because the GDAL stack underneath this package is the part
that breaks, and it breaks with error messages that point nowhere useful.

Python 3.9+, 3.11 tested.

## Use it from Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "geolens": {
      "command": "python",
      "args": ["-m", "geolens.mcp_server"],
      "env": {
        "GEOLENS_OUT": "C:/Users/you/geolens_out",
        "GEOLENS_ROOTS": "C:/Projects/RiverData"
      }
    }
  }
}
```

Restart Claude Desktop. Then just talk to it:

> *What's in my RiverData folder?*
> *Cut cross-sections every 25 m along the centerline and show me the stack.*
> *Delineate the channel and floodplain — is this reach confined?*

Copy `skill/SKILL.md` into your skills folder so Claude knows the workflow
conventions (CRS hygiene, when to plot, how to tune the delineation).

## Landscape scale

```bash
pip install pysheds            # pure Python, good to ~50M cells
pip install whitebox           # Rust binary, better above that

geolens backends               # which are installed
geolens landscape dem.tif --nhd nhd_flowlines.shp \
    --stream-threshold-km2 1.0 --segment-length 500
```

Pipeline: condition DEM → flow routing → HAND → valley bottom (HAND + slope
evidence) → network segmentation into DGOs → per-segment metrics → confinement
classes.

**Valley width is computed as allocated valley area ÷ segment length**, not by
cutting sections. That is what makes it tractable: one zonal statistic per
segment instead of thousands of DEM samples each.

### Sizing

| Area | Resolution | Cells | Notes |
|---|---|---|---|
| 3 M acres (12,140 km²) | 30 m | 13.5 M | one pass, ~2 min |
| 3 M acres | 10 m | 121 M | tile by HUC8, ~5 GB working set |
| any | 1 m lidar | — | reach scale only |

Measured: 667 k cells in 5.8 s end to end (pysheds, 4-core container).

### Calibrate the breaks before trusting the classes

The default confinement breaks (2 / 5 channel widths) follow convention and
**rarely transfer between landscapes**. Run the pass once, read
`summary.confinement_ratio_percentiles`, and set breaks from that distribution:

```bash
geolens landscape dem.tif --confined-max 14 --partly-confined-max 47
```

Channel width defaults to `W = 2.7 · A^0.35` (A in km²). Confinement ratio scales
directly with it — substitute a local hydraulic-geometry relation if you have one.

### On Rosgen valley types

Segments carry `rosgen_candidates` and a `needs_geology` flag rather than a single
type. Several Rosgen types are distinguished by **genesis** — colluvial vs alluvial
vs glacial vs lacustrine — which terrain cannot see. Confinement classes are fully
DEM-defensible; Rosgen types need surficial geology joined in.

## Use it from the terminal

```bash
geolens list ./data
geolens describe ./data/reach.gdb --layer streams
geolens attrs ./data/sites.gpkg --layer sites
geolens sample ./data/sites.gpkg --layer sites --where "bankfull_w > 12"

geolens profile dem.tif centerline.shp --spacing 5 --plot --csv
geolens sections dem.tif centerline.shp --spacing 25 --width 60 --plot --csv
geolens delineate dem.tif centerline.shp --width 160 --plot --csv --summary
```

## Use it from Python

```python
from geolens import api

d = api.delineate_channel("dem.tif", "centerline.shp", spacing=25, width=160)
print(d["summary"]["entrenchment_ratio"])

api.plot_delineation(d, station=340)
api.export_csv(d, "delineation")
```

## Configuration

| Variable | Meaning |
|---|---|
| `GEOLENS_OUT` | Where outputs are written. Default `./geolens_out`. |
| `GEOLENS_ROOTS` | Allowlist of readable folders (`os.pathsep`-separated). Default: unrestricted. |
| `GEOLENS_NO_PROGRESS` | Set to silence progress bars. |

**Source data is never written to.** Outputs go only to `GEOLENS_OUT`, and the
tool refuses to run if that directory sits inside a read root. Progress goes to
stderr so it never corrupts the MCP JSON-RPC stream on stdout.

## Tuning the delineation

All thresholds are slopes as rise/run. Defaults suit a clear channel on a ~1 m DEM.

| Symptom | Change |
|---|---|
| Banks not found on subtle channels | lower `bank_slope` (0.15 → 0.08) |
| Bed roughness read as banks | raise `min_bank_height` |
| Noisy lidar, junk breaks | raise `run_length`, then `smooth_window` |
| Floodplain never closes | widen `width`, or set `max_floodplain_offset` |
| Floodplain closes immediately | raise `valley_slope` above the floodplain's own slope |

## Accuracy

Verified against a synthetic compound channel with every breakpoint known
exactly (flat bed → 0.9 bank face → flat floodplain → 0.35 valley walls, 0.5 m
cells, 2 cm noise). Scored on sections where the fixture geometry is exact:

| Quantity | Truth | Measured | Error |
|---|---|---|---|
| Floodplain total width | 110.0 m | 110.0 m | **0.00 %** |
| Bank height | 1.80 m | 1.84 m | 1.99 % |
| Entrenchment ratio | 6.875 | 6.697 | 2.59 % |
| Bankfull width | 16.0 m | 16.5 m | 3.12 % |
| Bankfull mean depth | 1.575 m | 1.524 m | 3.26 % |
| Width/depth ratio | 10.16 | 10.83 | 6.60 % |
| Bank top offset | 8.0 m | 8.5 m | 6.25 % (= 1 sample) |
| Valley confinement | all | 25/25 | — |

Reach slope on a separate V-valley fixture: 0.26 % error against an analytic
0.4 % gradient.

### Landscape scale

Verified against a synthetic landscape whose valley half-width follows a known
step function (30 m cells, 600 km², two tributaries):

| Truth valley width | In cells | Median abs error |
|---|---|---|
| 2800 m | 93 | **0.7 %** |
| 1800 m | 60 | **0.0 %** |
| 480 m | 16 | 25 % |
| 300 m | 10 | 30 % |

**Wide valleys are measured to under 1 %; narrow valleys are at the resolution
limit.** A 300 m valley is 10 cells at 30 m — you cannot do better than ±2–3
cells. This is the reason to keep a lidar tier for confined reaches. Confinement
*classification* is more robust than the width measurement, since a 300 m valley
read as 210–390 m still falls the same side of any sensible class break.

Bank top sits one sample outboard of truth — the discretization limit at this
cell size. Reproduce with `tests/make_compound.py`.

## Caveats

- Derived ratios are **DEM geometry, not a field bankfull determination**. A DEM
  sees a slope break that is usually near a bankfull indicator; it does not see
  the indicator. Report them as geometry.
- Sections are cut perpendicular to the centerline, so a poor centerline gives
  poor sections. Plot the map and check.
- Floodplain widths on sections that reach the section edge without meeting a
  valley wall are a floor, not a measurement. The tool flags these.

## Layout

```
src/geolens/
  safety.py      path guard, read-only discipline, progress bars
  discover.py    dataset cataloguing
  vector.py      vector inspection
  raster.py      raster / DEM inspection
  sections.py    profiles + cross-sections
  morphology.py  channel / floodplain delineation
  render.py      maps and plots
  landscape/     landscape-scale pass
    terrain.py     DEM conditioning, flow routing, HAND (pysheds/whitebox)
    valley.py      valley bottom from HAND + slope evidence
    network.py     network extraction, segmentation, NHD conflation
    classify.py    per-segment metrics and confinement classes
    pipeline.py    one call that runs all four
  api.py         flat surface over everything
  cli.py         terminal interface
  mcp_server.py  MCP stdio server
skill/SKILL.md   workflow playbook for Claude
tests/           synthetic fixtures with known truth
```

CLI and MCP server are both thin wrappers over `api.py` — add a function there
and it appears in both.

## License

MIT
