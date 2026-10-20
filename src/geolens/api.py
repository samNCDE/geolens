"""One flat surface over every capability.

The CLI and the MCP server are both thin wrappers over this module, so a tool
added here appears in both without further wiring.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from . import discover as _discover
from . import morphology as _morph
from . import raster as _raster
from . import render as _render
from . import sections as _sections
from . import vector as _vector
from .safety import Guard


# -- inspection ---------------------------------------------------------------


def list_datasets(path: str, recursive: bool = True) -> dict[str, Any]:
    """Catalogue every geospatial dataset under a folder."""
    return _discover.discover(path, recursive=recursive)


def describe_vector(path: str, layer: str | None = None) -> dict[str, Any]:
    """CRS, geometry, extent, field schema and data-quality flags for a layer."""
    return _vector.describe_vector(path, layer=layer)


def describe_raster(path: str, stats: bool = True) -> dict[str, Any]:
    """CRS, grid, bands, nodata and statistics for a raster or DEM."""
    return _raster.describe_raster(path, stats=stats)


def attribute_summary(
    path: str, layer: str | None = None, fields: list[str] | None = None,
    top_n: int = 10,
) -> dict[str, Any]:
    """Per-field statistics: numeric ranges, category frequencies, null counts."""
    return _vector.attribute_summary(path, layer=layer, fields=fields, top_n=top_n)


def sample_features(
    path: str, layer: str | None = None, n: int = 5, where: str | None = None,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Return real attribute rows, optionally filtered with a pandas expression."""
    return _vector.sample_features(path, layer=layer, n=n, where=where, fields=fields)


def sample_raster(path: str, points: list[list[float]], band: int = 1) -> dict[str, Any]:
    """Sample raster values at (x, y) points given in the raster's own CRS."""
    return _raster.sample_points(path, points, band=band)


# -- analysis -----------------------------------------------------------------


def longitudinal_profile(
    dem: str, centerline: str, spacing: float = 10.0,
    line_layer: str | None = None, feature_index: int | None = None,
) -> dict[str, Any]:
    """Elevation along a centerline at fixed stations, with reach slope."""
    return _sections.longitudinal_profile(
        dem, centerline, spacing=spacing, line_layer=line_layer,
        feature_index=feature_index,
    )


def cross_sections(
    dem: str, centerline: str, spacing: float = 25.0, width: float = 60.0,
    point_spacing: float | None = None, line_layer: str | None = None,
    feature_index: int | None = None,
) -> dict[str, Any]:
    """Cut perpendicular cross-sections along a centerline and sample the DEM."""
    return _sections.extract_cross_sections(
        dem, centerline, spacing=spacing, width=width,
        point_spacing=point_spacing, line_layer=line_layer,
        feature_index=feature_index,
    )


def delineate_channel(
    dem: str, centerline: str, spacing: float = 25.0, width: float = 160.0,
    bank_slope: float = 0.15, flat_slope: float = 0.05, valley_slope: float = 0.20,
    min_bank_height: float = 0.3, run_length: int = 2, smooth_window: int = 3,
    max_floodplain_offset: float | None = None, point_spacing: float | None = None,
    line_layer: str | None = None, feature_index: int | None = None,
) -> dict[str, Any]:
    """Cut sections and classify channel / banks / floodplain by slope break.

    Walks outward from each thalweg: flat bed, steep bank face, bank top, flat
    floodplain, then closes the floodplain where the terrain resumes a sustained
    climb (valley confinement). Returns bankfull width, width/depth ratio and
    entrenchment ratio per section plus reach summary statistics.
    """
    secs = _sections.extract_cross_sections(
        dem, centerline, spacing=spacing, width=width,
        point_spacing=point_spacing, line_layer=line_layer,
        feature_index=feature_index,
    )
    result = _morph.delineate(
        secs, bank_slope=bank_slope, flat_slope=flat_slope,
        valley_slope=valley_slope, min_bank_height=min_bank_height,
        run_length=run_length, smooth_window=smooth_window,
        max_floodplain_offset=max_floodplain_offset,
    )
    result["_sections"] = secs
    return result


# -- output -------------------------------------------------------------------


def plot_profile(result: dict[str, Any], name: str = "profile.png") -> dict[str, Any]:
    """Render a longitudinal profile to PNG."""
    return _render.plot_profile(result, name=name)


def plot_cross_section(
    result: dict[str, Any], station: float | None = None,
    index: int | None = None, name: str = "cross_section.png",
) -> dict[str, Any]:
    """Render one cross-section to PNG."""
    return _render.plot_cross_section(result, station=station, index=index, name=name)


def plot_section_stack(
    result: dict[str, Any], name: str = "section_stack.png", max_sections: int = 40,
) -> dict[str, Any]:
    """Render every cross-section overlaid, colored by station."""
    return _render.plot_section_stack(result, name=name, max_sections=max_sections)


def plot_delineation(
    result: dict[str, Any], station: float | None = None,
    index: int | None = None, name: str = "delineation.png",
) -> dict[str, Any]:
    """Render a section with channel, banks and floodplain marked."""
    secs = result.get("_sections")
    if not secs:
        raise ValueError(
            "Pass the result of delineate_channel, which carries its sections."
        )
    return _render.plot_delineation(
        secs, result, station=station, index=index, name=name
    )


def plot_map(
    dem: str | None = None, centerline: str | None = None,
    overlays: list[str] | None = None, name: str = "map.png",
) -> dict[str, Any]:
    """Render a plan-view map: shaded DEM, centerline, and any overlays."""
    return _render.plot_map(
        dem=dem, centerline=centerline, overlays=overlays, name=name
    )


def export_csv(
    result: dict[str, Any], kind: str, name: str | None = None
) -> dict[str, Any]:
    """Write a result to CSV. ``kind`` is profile, sections, or delineation."""
    if kind == "profile":
        return _sections.profile_to_csv(result, name=name or "profile.csv")
    if kind == "sections":
        src = result.get("_sections", result)
        return _sections.sections_to_csv(src, name=name or "cross_sections.csv")
    if kind == "delineation":
        return _morph.delineation_to_csv(
            result, name=name or "channel_delineation.csv"
        )
    raise ValueError(f"kind must be profile, sections or delineation — got {kind!r}")


def workspace() -> dict[str, Any]:
    """Report where outputs are written and which roots are readable."""
    g = Guard.from_env()
    return {
        "output_directory": str(g.out_dir),
        "read_roots": [str(r) for r in g.roots] or ["(unrestricted)"],
        "note": (
            "Source data is never modified. Set GEOLENS_OUT to change the output "
            "directory and GEOLENS_ROOTS to restrict what may be read."
        ),
    }


# -- landscape scale ----------------------------------------------------------


def compare_backends(dem: str | None = None,
                     stream_threshold_km2: float = 0.0005) -> dict[str, Any]:
    """Run both flow-routing backends on one DEM and report how far they differ.

    WhiteboxTools is MIT, Rust, and much lighter on memory than the pure-Python
    path — the right engine for a multi-billion-cell DEM. It has never been
    verified against pysheds here because its binary could not be downloaded in
    the build environment, so this measures the agreement instead of asserting
    it. Run it once where the binary exists.
    """
    from .landscape import compare_backends as _cmp

    return _cmp(dem, stream_threshold_km2=stream_threshold_km2)


def restream(terrain: dict[str, Any], stream_threshold_km2: float,
             out_prefix: str = "restream") -> dict[str, Any]:
    """Re-extract the stream network at a new threshold without re-routing.

    The stream threshold does not affect flow routing — filling, direction and
    accumulation are all computed before any threshold is applied. So changing
    it costs seconds, not another routing pass. Pass ``res["terrain"]`` from
    :func:`landscape_pass`.

    HAND is recomputed, since it is a height above the *nearest stream* and
    that set has changed.
    """
    from .landscape import restream as _rs

    return _rs(terrain, stream_threshold_km2=stream_threshold_km2,
               out_prefix=out_prefix).as_dict()


def valley_form(dem: str, centerline: str, segments: str | None = None,
                id_field: str | None = None, spacing_m: float = 250.0,
                max_search_m: float | None = None, min_rise_m: float = 5.0,
                thalweg_search_m: float = 200.0,
                out_prefix: str = "valley_form") -> dict[str, Any]:
    """Measure valley cross-section form from the floor up to the divides.

    This is what Rosgen's valley types actually turn on — side-slope gradient,
    V-versus-U shape, relief floor-to-divide — rather than the channel-centric
    proxies (confinement ratio, channel gradient) the crosswalk used at first.

    Returns the Vf index (Bull & McFadden 1977), a shape exponent (n~1 is a V,
    n~2 is a glacial U), side slopes, relief and asymmetry, per section and
    aggregated to segments on medians.

    Pass ``segments`` — the classified segments layer — so each section is
    attributed to the reach it crosses. Without it every section carries
    ``segment_id = 0`` and the join in :func:`rosgen_valley_types` matches
    nothing, discarding the measurement silently.

    ``max_search_m`` decides whether any of this works: it is how far to look
    for the divide, and too small means every section returns a lower bound
    instead of a measurement while the run looks perfectly healthy. Left at
    ``None`` it is derived from the DEM — a sixth of the grid's smaller side,
    capped at 4 km — because a fixed 3 km asked of a 3 km-wide clip cannot
    reach anything, and that was the notebook's default. Check ``n_measured``
    against ``n_sections`` in the result whatever you pass.
    """
    from .landscape.valleyform import valley_form as _vf

    if max_search_m is None:
        import rasterio as _rio

        from .units import unit_info as _ui

        with _rio.open(str(dem)) as _src:
            _u = _ui(_src.crs, abs(_src.res[0]), abs(_src.res[1]))
            _short_m = min(_src.width, _src.height) * _u.cell_size_m
        # Generous, not conservative. `_find_divide` takes the FIRST crest
        # walking out from the floor, so a wider search can never find a
        # wronger divide — it can only let more sections find one at all. The
        # cost is samples, not correctness. A sixth of the shorter side was
        # the first try and it was 2,333 m on a fixture whose divides sit up
        # to 3,400 m out: 20% of sections measured instead of 100%.
        max_search_m = float(min(6000.0, max(500.0, _short_m / 3.0)))

    return _vf(dem, centerline, segments=segments, id_field=id_field,
               spacing_m=spacing_m, max_search_m=max_search_m,
               min_rise_m=min_rise_m, thalweg_search_m=thalweg_search_m,
               out_prefix=out_prefix).as_dict()


def reclassify_confinement(segments: str, confined_max: float,
                           partly_confined_max: float,
                           out_prefix: str = "reclassified") -> dict[str, Any]:
    """Apply new confinement breaks without recomputing anything.

    The breaks are two thresholds on ``confinement_ratio``, which the landscape
    pass already computed and stored. Changing them is a relabel, not a re-run:
    seconds instead of the full conditioning, routing, HAND, valley-bottom and
    segmentation chain.
    """
    from .landscape import reclassify_confinement as _rc

    return _rc(segments, confined_max=confined_max,
               partly_confined_max=partly_confined_max,
               out_prefix=out_prefix).as_dict()


def confinement_break_sweep(segments: str,
                            candidates: list | None = None) -> dict[str, Any]:
    """Show how the landscape divides under several candidate breaks.

    Writes nothing and re-runs nothing — it reads the segments table once.
    This is what calibrating the breaks actually needs: compare the options,
    then apply one with :func:`reclassify_confinement`.
    """
    from .landscape import confinement_break_sweep as _sweep

    return _sweep(segments, candidates=candidates)


def landscape_backends() -> dict[str, Any]:
    """Report which flow-routing backends are installed."""
    from .landscape import available_backends

    avail = available_backends()
    return {
        "available": avail,
        "note": (
            "pysheds is pure Python (good to ~50M cells). whitebox is a Rust "
            "binary, faster and leaner on larger grids: pip install whitebox."
        ),
    }


def landscape_pass(
    dem: str,
    nhd: str | None = None,
    stream_threshold_km2: float | None = None,
    segment_length_m: float = 500.0,
    channel_width_m: float | None = None,
    confined_max: float = 2.0,
    partly_confined_max: float = 5.0,
    backend: str = "auto",
    valley_types: bool = True,
    stream_types: bool = True,
    dem_fine: str | None = None,
    valley_form_spacing_m: float | None = None,
    valley_max_search_m: float | None = None,
    geology: str | None = None,
    regional_curve: str | dict | None = None,
    section_plots: bool | None = None,
    bankfull_method: str = "regional_stage",
    fans: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run the whole landscape-scale valley delineation on one DEM.

    Conditions the DEM, routes flow, computes HAND, extracts the valley bottom,
    segments the network into DGOs, classifies confinement per segment, then
    measures valley form floor-to-divide and assigns Rosgen valley types.

    **Valley types are on by default**, and that is a deliberate correction:
    they were opt-in, nothing opted in, and three rounds of work went into
    something no run ever produced. Pass ``valley_types=False`` on a run where
    the valley type is not the question — it is the slowest step after routing.

    Supply ``geology`` if you have it. Rosgen II/V/VI, VIII/IX and X/XI are
    separated by depositional history rather than shape, so without it those
    come back unresolved with their candidates listed. ``"auto"`` downloads
    USGS geology over the DEM; ``"ssurgo"`` downloads USDA SSURGO soils,
    mapped finely enough to show a narrow valley's own alluvium.

    ``regional_curve="arid_sw_nm"`` (or ``"arid_sw_az"``) types streams
    against Moody et al. (2003)'s arid Southwest bankfull curves instead of
    the generic one -- see ``rosgen_stream_types``.
    Read ``summary.confinement_ratio_percentiles`` from the result and set
    ``confined_max`` / ``partly_confined_max`` from that distribution before
    trusting the classes — the defaults rarely transfer between landscapes.
    """
    from .landscape import run_landscape

    return run_landscape(
        dem, nhd=nhd, stream_threshold_km2=stream_threshold_km2,
        segment_length_m=segment_length_m, channel_width_m=channel_width_m,
        confined_max=confined_max, partly_confined_max=partly_confined_max,
        backend=backend, valley_types=valley_types,
        stream_types=stream_types, dem_fine=dem_fine,
        valley_form_spacing_m=valley_form_spacing_m,
        valley_max_search_m=valley_max_search_m, geology=geology,
        regional_curve=regional_curve, section_plots=section_plots,
        bankfull_method=bankfull_method, fans=fans, **kwargs,
    )


# -- learning from corrections ------------------------------------------------


def record_correction(
    dataset: str, feature_id: str, field: str, human_value: Any,
    model_value: Any = None, author: str = "", kind: str = "categorical",
    x: float | None = None, y: float | None = None,
    params: dict[str, Any] | None = None, notes: str = "",
    confidence: str = "certain",
) -> dict[str, Any]:
    """Record one human correction of a predicted value.

    ``dataset`` should come from ``learn.dataset_key(dem_path)`` so labels stay
    attached to the data even when the project folder moves. ``params`` should
    be the ``parameters`` dict from the result being corrected — a correction is
    only meaningful against the configuration that produced the prediction.
    """
    from .learn import add_label

    return add_label(
        dataset=dataset, feature_id=str(feature_id), field=field,
        human_value=human_value, model_value=model_value, author=author,
        kind=kind, x=x, y=y, params=params or {}, notes=notes,
        confidence=confidence,
    )


def corrections_summary() -> dict[str, Any]:
    """What corrections have been recorded, and how often the tool agreed."""
    from .learn import label_summary

    return label_summary()


def operator_disagreement(field: str | None = None) -> dict[str, Any]:
    """Inter-operator variability on features labelled by more than one person.

    Run this before fitting. If two people disagree with each other more than
    the algorithm disagrees with either, the algorithm is not what needs fixing.
    """
    from .learn import disagreement

    return disagreement(field_name=field)


def calibrate_confinement(
    segments_csv: str,
    classes: Sequence[str] = ("confined", "partly confined", "unconfined"),
    ratio_field: str = "confinement_ratio",
    id_field: str = "seg_id",
    balanced: bool = True,
) -> dict[str, Any]:
    """Fit confinement breaks to recorded corrections.

    Joins corrections on ``field="confinement"`` to the per-segment table, then
    finds the break points that best reproduce them. Returns breaks to pass
    straight back into ``landscape_pass`` as ``confined_max`` /
    ``partly_confined_max``.

    ``balanced=True`` scores by mean per-class recall. Keep it on: landscapes
    are heavily imbalanced, and plain accuracy will happily recommend breaks
    that call everything unconfined and never find a confined reach.

    **Choose what to label with** :func:`suggest_labels`, not at random.
    Random sampling spends most of its labels on obvious cases and can leave the
    fit worse than the defaults; boundary sampling is what makes the loop
    converge.
    """
    import pandas as pd

    from .learn import load_labels, fit_ordered_breaks
    from .learn.calibrate import CONFIDENCE_WEIGHT

    df = pd.read_csv(segments_csv)
    if ratio_field not in df.columns:
        raise ValueError(
            f"{ratio_field!r} not in {segments_csv}. Columns: {list(df.columns)}"
        )

    labels = load_labels(field_name="confinement")
    if not labels:
        return {
            "error": "no corrections recorded for field 'confinement'",
            "hint": (
                "Record some with record_correction(field='confinement', ...), "
                "then call this again. Use suggest_labels() to choose which "
                "segments to look at first."
            ),
        }

    by_id = {str(r["feature_id"]): r for r in labels}
    df["_id"] = df[id_field].astype(str)
    matched = df[df["_id"].isin(by_id)]
    if matched.empty:
        return {
            "error": "no corrections matched a segment in this table",
            "hint": f"Corrections reference ids not present in {id_field}.",
        }

    values = matched[ratio_field].to_numpy()
    truth = [by_id[i]["human_value"] for i in matched["_id"]]
    weights = [
        CONFIDENCE_WEIGHT.get(by_id[i].get("confidence", "certain"), 1.0)
        for i in matched["_id"]
    ]

    fit = fit_ordered_breaks(values, truth, list(classes), weights=weights,
                             balanced=balanced)
    fit["matched_labels"] = int(len(matched))
    fit["unmatched_labels"] = int(len(labels) - len(matched))
    if len(matched) < 20:
        fit["warning"] = (
            f"Only {len(matched)} labels. These breaks are an exact fit to very "
            "little data and will move as you add more. Treat them as a starting "
            "point, not a calibration."
        )
    if len(fit["breaks"]) == 2:
        fit["apply"] = {
            "confined_max": fit["breaks"][0],
            "partly_confined_max": fit["breaks"][1],
        }
    return fit


def suggest_labels(
    segments_csv: str, n: int = 20, ratio_field: str = "confinement_ratio",
    id_field: str = "seg_id", breaks: Sequence[float] | None = None,
    strategy: str = "auto",
) -> dict[str, Any]:
    """Which segments to label next, to move the fit most per unit of effort.

    Early on (under ~25 labels) this returns a spread across the value range
    rather than features near a boundary — the breaks are not yet trustworthy
    enough to say what is uncertain, and clustering there would miss the rare
    classes entirely. It switches to boundary sampling on its own.
    """
    import pandas as pd

    from .learn import load_labels, suggest_next

    df = pd.read_csv(segments_csv)
    labels = load_labels(field_name="confinement")
    done = [str(r["feature_id"]) for r in labels]

    if breaks is None:
        fit = calibrate_confinement(segments_csv, ratio_field=ratio_field,
                                    id_field=id_field)
        breaks = fit.get("breaks") or [2.0, 5.0]

    return suggest_next(
        df[ratio_field].to_numpy(), breaks, df[id_field].astype(str).tolist(),
        n=n, already_labelled=done, strategy=strategy,
    )


# -- memory planning and tiling -----------------------------------------------


def plan_landscape_run(dem: str, dtype: str = "float32") -> dict[str, Any]:
    """Estimate whether a landscape pass will fit in memory, before running it.

    Run this FIRST on any unfamiliar DEM. An out-of-memory kill gives no message
    and no traceback; this says how many cells, how much RAM, and — when it will
    not fit — what resolution or how many watershed tiles would.
    """
    from .landscape import plan_run

    return plan_run(dem, dtype=dtype).as_dict()


def split_landscape(
    dem: str, basins: str | None = None, target_area_km2: float = 200.0,
    buffer_m: float | None = None,
) -> dict[str, Any]:
    """Cut a DEM into buffered watershed tiles for processing one at a time.

    Supply ``basins`` (HUC12 polygons are ideal) or leave it out and they are
    derived from a coarsened copy of the DEM.

    Watersheds, not a grid: flow accumulation depends on everything upstream, so
    a rectangular tile boundary truncates flow paths and silently understates
    drainage area. A watershed divide is the one boundary where a split is
    exact.
    """
    from .landscape import split_by_basins

    return split_by_basins(dem, basins=basins, target_area_km2=target_area_km2,
                           buffer_m=buffer_m).as_dict()


def landscape_tiled(
    dem: str, basins: str | None = None, target_area_km2: float = 200.0,
    stream_threshold_km2: float = 1.0, segment_length_m: float = 500.0,
    merge: bool = True, **kwargs: Any,
) -> dict[str, Any]:
    """Run the landscape pass tile by tile and merge the segment outputs.

    Peak memory is one tile rather than the whole landscape.

    .. warning::
       **Drainage area is not preserved across tile boundaries.** A tile only
       sees the flow generated inside it, so a mainstem entering from upstream
       arrives with its contributing area reset to zero. Streams that depended
       on that area fall below ``stream_threshold_km2`` and disappear. On a test
       landscape, tiling into 12 subwatersheds recovered only 6.4 km of a 187 km
       network — a 97 % loss, silently.

       This is inherent to tiling flow accumulation, not a bug in the merge, and
       it applies to HUC boundaries too. Mitigations, best first:

       1. **Coarsen instead of tiling.** A landscape-scale generalization does
          not need 1 m cells. Going from 1 m to 10 m cuts memory 100x and keeps
          the whole drainage network intact and correct. ``plan_landscape_run``
          tells you the cell size that fits.
       2. **Lower ``stream_threshold_km2`` per tile**, in proportion to tile
          area, so headwater-sized tiles still produce a network. The network is
          then internally consistent but not comparable between tiles.
       3. **Tile only if the DEM cannot be coarsened** — for example when you
          need lidar detail — and treat each tile as a standalone analysis
          rather than merging them into one network.

       The result carries a ``network_loss`` block quantifying what was lost.
    """
    from .landscape import split_by_basins, run_tiled, merge_vector_outputs
    from .landscape import run_landscape

    ts = split_by_basins(dem, basins=basins, target_area_km2=target_area_km2)

    def one(tile_dem: str) -> dict[str, Any]:
        r = run_landscape(
            tile_dem, stream_threshold_km2=stream_threshold_km2,
            segment_length_m=segment_length_m, **kwargs,
        )
        return {
            "segments": r["classified"]["segments"],
            "table": r["classified"]["table"],
            "valley_polygons": r["valley"]["valley_polygons"],
            "summary": r["classified"]["summary"],
        }

    run = run_tiled(ts, one)
    out: dict[str, Any] = {
        "tiles": ts.as_dict(), "run": {k: v for k, v in run.items()
                                       if k != "results"},
        "failures": run["failures"],
    }

    # Quantify the truncation rather than let the user discover it later.
    tiled_km = 0.0
    for r in run["results"]:
        try:
            import geopandas as _gpd
            g = _gpd.read_file(r["result"]["segments"])
            tiled_km += float(g["length_m"].sum()) / 1000.0
        except Exception:
            pass
    out["network_loss"] = {
        "tiled_network_km": round(tiled_km, 2),
        "note": (
            "Compare this against an untiled run at a coarser cell size. A large "
            "shortfall means streams fell below stream_threshold_km2 because "
            "their upstream contributing area was cut off at a tile boundary. "
            "Coarsening the DEM is usually the better answer than tiling."
        ),
    }

    if merge and run["results"]:
        ok_ids = [r["tile_id"] for r in run["results"]]
        wkts = [t["clip_geometry_wkt"] for t in ts.tiles if t["tile_id"] in ok_ids]
        out["merged_segments"] = merge_vector_outputs(
            [r["result"]["segments"] for r in run["results"]],
            name="merged_segments.gpkg", clip_wkts=wkts,
        )
        out["merged_valley"] = merge_vector_outputs(
            [r["result"]["valley_polygons"] for r in run["results"]],
            name="merged_valley.gpkg", clip_wkts=wkts,
        )
    return out


def resample_dem(
    dem: str, target_cell_m: float, name: str | None = None,
    method: str = "average",
) -> dict[str, Any]:
    """Resample a DEM to a coarser cell size.

    This is usually the right answer to an out-of-memory landscape run. Memory
    scales with the square of resolution: 1 m to 10 m is 100x less. A
    landscape-scale generalization over millions of acres does not need metre
    cells, and unlike tiling it keeps the drainage network whole and correct.

    ``method`` is ``average`` for elevation (the sensible default — it preserves
    mean surface height), or ``bilinear``. Do not use ``nearest`` on elevation:
    it keeps whichever cell happened to land on the sample point and makes slope
    noisy.
    """
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling

    from .safety import Guard, GeolensError, note

    guard = Guard.from_env()
    p = guard.read_path(dem)
    methods = {"average": Resampling.average, "bilinear": Resampling.bilinear,
               "cubic": Resampling.cubic, "nearest": Resampling.nearest}
    if method not in methods:
        raise GeolensError(f"method must be one of {list(methods)}")

    from .units import unit_info

    with rasterio.open(str(p)) as src:
        # TRUE metres. The raster's own resolution is in CRS units: on a
        # US-survey-foot DEM, "10 m" used to give 10 ft (3.05 m) cells.
        cell = unit_info(src.crs, abs(src.res[0]), abs(src.res[1])).cell_size_m
        if target_cell_m <= cell:
            raise GeolensError(
                f"target_cell_m ({target_cell_m}) must exceed the current cell "
                f"size ({cell:g} m). This function coarsens; it cannot invent "
                f"detail."
            )
        factor = target_cell_m / cell
        w, h = max(1, int(src.width / factor)), max(1, int(src.height / factor))
        note(f"resampling {src.width:,}x{src.height:,} ({cell:g} m) -> "
             f"{w:,}x{h:,} ({target_cell_m:g} m): "
             f"{src.width * src.height / max(1, w * h):.0f}x fewer cells")
        arr = src.read(1, out_shape=(h, w), resampling=methods[method],
                       masked=True)
        from affine import Affine
        transform = src.transform * Affine.scale(src.width / w,
                                                 src.height / h)
        profile = src.profile.copy()

    nd = profile.get("nodata")
    profile = _raster.gtiff_profile(
        profile, dtype="float32",
        nodata=nd if nd is not None else -9999.0,
        width=w, height=h, transform=transform,
    )
    out = guard.write_path(name or f"{Path(p).stem}_{int(target_cell_m)}m.tif")
    with rasterio.open(str(out), "w", **profile) as dst:
        dst.write(arr.filled(profile["nodata"]).astype("float32"), 1)

    return {
        "path": str(out), "cell_size_m": target_cell_m,
        "width": w, "height": h, "cells": w * h,
        "reduction_factor": round((src.width * src.height) / max(1, w * h), 1),
    }


# -- cross-sections as GIS features -------------------------------------------


def export_cross_sections(
    dem: str, centerline: str, spacing: float = 25.0, width: float = 160.0,
    plot: str | bool = "representative", n_reaches: int = 1,
    group_by: str = "station", name: str = "cross_sections",
    max_plots: int = 200, **kwargs: Any,
) -> dict[str, Any]:
    """Cut sections and export them as GIS lines with a lookup table.

    Produces a shapefile (and GeoPackage) of section traces, a CSV of the same
    attributes, and per-section plots. Each row carries ``xs_id`` — read it off
    the map in GIS, then call :func:`plot_cross_section_by_id` to get that
    section's figure.

    ``plot="representative"`` renders only the medoid section of each reach,
    which is usually what you want: fifty sections through a uniform reach carry
    about as much information as three. Every row still gets a ``plot_path`` so
    any section can be rendered later.
    """
    import geopandas as gpd

    from .xsections import export_sections

    secs = cross_sections(dem, centerline, spacing=spacing, width=width)
    dl = _morph.delineate(secs, **kwargs)
    crs = gpd.read_file(centerline).crs

    if group_by == "confinement" or n_reaches > 1:
        from .xsections import select_representative

        rep = select_representative(secs, dl, n_reaches=n_reaches, by=group_by)
    else:
        rep = None

    out = export_sections(secs, delineation=dl, crs=crs, name=name, plot=plot,
                          max_plots=max_plots)
    res = out.as_dict()
    res["reach_summary"] = rep
    res["hint"] = (
        "Add the GeoPackage or shapefile to QGIS and hover over a section "
        "(View > Show Map Tips) to see its plot, or click it with Run Feature "
        "Action to open it. Sections without a rendered plot: "
        "plot_cross_section_by_id(result, xs_id=N). is_rep marks the "
        "representative section of each reach."
    )
    res["_sections"] = secs
    res["_delineation"] = dl
    return res


def plot_cross_section_by_id(result: dict[str, Any], xs_id: int) -> dict[str, Any]:
    """Render one cross-section by the ``xs_id`` shown in the GIS table."""
    from .xsections import plot_section_by_id

    secs = result.get("_sections")
    if not secs:
        raise ValueError("Pass the result of export_cross_sections.")
    return plot_section_by_id(secs, xs_id, delineation=result.get("_delineation"))


def plot_stream_sections(stream_types_dir: str, sections: bool = True,
                         reaches: bool = True, skip_existing: bool = False,
                         workers: int | None = None,
                         reach_ids: list | None = None) -> dict[str, Any]:
    """Picture every stream-type section and link it to the map.

    Point it at a run's ``stream_types_dir`` -- any version, no DEM needed.
    Writes ``section_plots/xs_<xs_id>.png`` (whole section + channel close
    up, looking downstream), ``reach_plots/reach_<id>.png`` (all sections of
    a reach under its type), ``stream_type_plots.html``, and adds
    ``plot_file``/``plot_path`` plus a QGIS style to both GeoPackages.

    In QGIS: add stream_type_sections.gpkg, turn on View > Show Map Tips,
    and rest the pointer on a section; or pick the Run Feature Action tool
    and click one to open its plot. stream_types.gpkg does the same with the
    reach sheets. In ArcGIS Pro: Configure Pop-ups > add an Image element
    with source ``{plot_path}``.

    Runs by itself at the end of the stream types unless
    ``section_plots=False``; ``skip_existing`` resumes an interrupted render.
    """
    from .landscape.section_plots import plot_stream_sections as _pss

    return _pss(stream_types_dir, sections=sections, reaches=reaches,
                skip_existing=skip_existing, workers=workers,
                reach_ids=reach_ids)


def stream_stability(valley_types: str, stream_types: str,
                     out_prefix: str = "stability",
                     association_table: str | None = None) -> dict[str, Any]:
    """Is each reach's stream type a stable form for its valley type?

    Joins ``valley_types.gpkg`` and ``stream_types.gpkg`` from one run on
    ``seg_id`` and writes ``stability_dir/stream_stability.gpkg``:
    ``form_status`` is ``stable`` (a stream type Rosgen associates with the
    valley type), ``unstable`` (one he lists for it only out of balance --
    D, F, G in valley VIII; G and F in X; G in VI), ``check`` (not associated
    with that valley type at all: a departure, or one classification is
    wrong) or ``unknown``; ``form_note`` says why and ``form_confidence`` is
    the lower of the two classifications' confidence. Source: NRCS NEH 654
    TS3E Table TS3E-3, after Rosgen (1996). Runs by itself at the end of
    ``landscape_pass``; call this on an earlier run's two layers.

    ``association_table``: a CSV in the form of
    ``docs/valley_stream_associations.csv`` to judge with instead. The run
    keeps a copy (``association_table.csv``) and later steps re-judge with
    it; ``"builtin"`` goes back to the built-in table.
    """
    from .landscape.stability import stream_stability as _ss

    return _ss(valley_types, stream_types, out_prefix=out_prefix,
               association_table=association_table)


def association_table(path: str, write: bool = False) -> dict[str, Any]:
    """Check a valley/stream association table CSV, or write the built-in one.

    ``write=True`` writes the built-in table to ``path`` as a CSV to edit.
    Otherwise reads ``path`` and returns its rows, raising a plain message on
    the first problem (an unknown letter, a valley type listed twice, a
    letter both stable and unstable).
    """
    from .landscape.stability import (read_association_table,
                                      write_association_table)

    if write:
        write_association_table(path)
    table = read_association_table(path)
    return {"path": str(path), "valley_types": len(table),
            "rows": {v: {"stable": sorted(ok), "unstable": sorted(bad),
                         "wording": words}
                     for v, (ok, bad, words) in table.items()}}


def representative_sections(
    dem: str, centerline: str, spacing: float = 25.0, width: float = 160.0,
    n_reaches: int = 5, group_by: str = "confinement", **kwargs: Any,
) -> dict[str, Any]:
    """Pick the one section that best represents each reach.

    ``group_by="confinement"`` cuts reaches where the confinement class changes,
    which follows the geomorphology rather than an arbitrary interval.
    ``group_by="station"`` splits into ``n_reaches`` equal lengths instead.

    Each pick is a **medoid** — the real section closest to the centre of its
    reach's distribution. Not an average: an average cross-section is a shape
    that exists nowhere on the ground, which makes it useless as "the
    representative section" in a report.
    """
    from .xsections import select_representative

    secs = cross_sections(dem, centerline, spacing=spacing, width=width)
    dl = _morph.delineate(secs, **kwargs)
    return select_representative(secs, dl, n_reaches=n_reaches, by=group_by)


# -- geology ------------------------------------------------------------------


def fetch_geology(
    aoi: str,
    buffer_m: float = 1000.0,
    service: str = "surface",
    out_prefix: str = "geology",
) -> dict[str, Any]:
    """Download geology polygons covering a shapefile's extent.

    Point at any vector file — a watershed boundary, a centerline, a DEM
    footprint — and get the geology underneath it from the USGS Cooperative
    National Geologic Map, joined to a material vocabulary (alluvial, glacial
    till, ice-marginal, lacustrine, bedrock...) that a Rosgen valley-type call
    can use. Public domain data, no API key.

    Read ``geolens.geology``'s module docstring before trusting the result on
    small streams: the source maps run 1:50,000 to 1:1,000,000, so a valley
    narrower than the minimum mapping unit comes back as its hillslope's
    bedrock. ``mapped_fraction`` and the warnings list are there to catch that.

    Conterminous US only.
    """
    from .geology import fetch_geology as _fetch

    return _fetch(aoi, buffer_m=buffer_m, service=service,
                  out_prefix=out_prefix).as_dict()


def geology_selftest() -> dict[str, Any]:
    """Check the geology services are up and still answering the same shape.

    Queries a known area of the Colorado Front Range and reports what came
    back. Run this before relying on ``fetch_geology`` — it is the one part of
    geolens that depends on a third-party service staying up, and it could not
    be exercised where it was written.
    """
    from .geology import geology_selftest as _t

    return _t()


def detect_headcuts(
    dem: str,
    lines: str | None = None,
    stream_threshold_km2: float = 0.005,
    tolerance_m: float = 1.0,
    window_m: float = 12.0,
    min_excess_drop_m: float = 0.8,
    out_prefix: str = "headcuts",
) -> dict[str, Any]:
    """Find headcuts (knickpoints) on a DEM.

    Pass ``lines`` — the ``gully_lines`` from :func:`detect_gullies` — unless
    you want every convexity in the network. With it, the fixture scores 6 of 6
    with no false positives, each point within 3-6 m of its known position.
    Without it, recall is the same but you get about 29 features for 6 real
    headcuts and have to sort them yourself.

    Needs ``topotoolbox``: ``pip install topotoolbox``.
    """
    from .landscape import detect_headcuts as _hc

    return _hc(dem, lines=lines, stream_threshold_km2=stream_threshold_km2,
               tolerance_m=tolerance_m, window_m=window_m,
               min_excess_drop_m=min_excess_drop_m,
               out_prefix=out_prefix).as_dict()


# -- run log ------------------------------------------------------------------


def run_history(limit: int = 50, function: str | None = None) -> dict[str, Any]:
    """What has been run in this output directory, newest first.

    Every api call records itself: the arguments, how long it took, the full
    result, and the files it produced. This reads that back, so "which
    threshold produced these segments" is answerable without re-running
    anything.
    """
    from .persist import history

    return {"runs": history(limit=limit, function=function)}


def load_run(record: str) -> dict[str, Any]:
    """Load a previous result back from its saved record."""
    from .persist import load_run as _load

    return _load(record)


def detect_alluvial_fans(
    dem: str,
    stream_threshold_km2: float = 0.5,
    fan_slope_min: float = 0.02,
    fan_slope_max: float = 0.15,
    min_drainage_km2: float = 0.5,
    max_radius_m: float = 3000.0,
    min_sector_deg: float = 60.0,
    out_prefix: str = "fans",
) -> dict[str, Any]:
    """Find alluvial fans at the mouths of confined drainages.

    An apex is the concave break in the channel's long profile -- where
    gradient drops abruptly because the stream has left its canyon and begun
    depositing. The fan is then grown outward from there within a slope band,
    above local base level, descending away from the apex, and kept only if it
    is cone-shaped (``min_sector_deg`` at the apex).

    On a fixture with three fans of known geometry this finds 3 of 3 with no
    false positives: apexes within 7 m, area within 5%, radius within 3%, mean
    radial gradient within 2%. On Ruidoso (10 m) it found no fans: its 26
    candidates were strips of trunk-valley floor below tributary mouths, which
    the cone test rejects. Every candidate is in ``fan_candidates.gpkg`` with
    the reason, for review.

    A DEM over 60 million cells is averaged to a coarser copy first (at least
    5 m cells): fans are hundreds of metres across, and the search holds ten
    full-size arrays.
    """
    from .landscape import build_terrain, detect_alluvial_fans as _fans
    from .landscape.pipeline import FAN_MAX_CELLS, FAN_MIN_CELL_M, coarsen_dem
    from .safety import Guard

    import rasterio

    guard = Guard.from_env()
    src_dem = str(guard.read_path(dem))
    with rasterio.open(src_dem) as s:
        cells = s.width * s.height
    if cells > FAN_MAX_CELLS:
        src_dem, _ = coarsen_dem(src_dem, FAN_MAX_CELLS, guard,
                                 name=f"{out_prefix}_dem",
                                 min_cell_m=FAN_MIN_CELL_M)
    terrain = build_terrain(src_dem, stream_threshold_km2=stream_threshold_km2,
                            out_prefix=f"{out_prefix}_terrain")
    return _fans(terrain, fan_slope_min=fan_slope_min,
                 fan_slope_max=fan_slope_max,
                 min_drainage_km2=min_drainage_km2,
                 max_radius_m=max_radius_m, min_sector_deg=min_sector_deg,
                 out_prefix=out_prefix).as_dict()


_PASS_RECORDS = ("*_landscape_pass.json", "*_watershed_pass.json")


def _pass_records(run: Path) -> list[Path]:
    """Landscape-pass records in ``run/runs``, newest first (the names start
    with a UTC timestamp)."""
    recs = [r for pat in _PASS_RECORDS for r in (run / "runs").glob(pat)]
    return sorted(recs, key=lambda r: r.name, reverse=True)


def _run_argument(run: Path, name: str) -> Any:
    """An argument of the newest landscape pass recorded in ``run/runs``."""
    import json

    for rec in _pass_records(run):
        try:
            args = json.loads(rec.read_text(encoding="utf-8")).get(
                "arguments", {})
        except Exception:                                    # noqa: BLE001
            continue
        nested = args.get("landscape_kwargs") or args.get("kwargs") or {}
        if name in args:
            return args[name]
        if isinstance(nested, dict) and name in nested:
            return nested[name]
    return None


def _run_record(run: Path) -> dict[str, Any]:
    """The newest landscape-pass record in ``run/runs`` (arguments + result)."""
    import json

    for rec in _pass_records(run):
        try:
            return json.loads(rec.read_text(encoding="utf-8"))
        except Exception:                                    # noqa: BLE001
            continue
    return {}


def _run_terrain(run: Path) -> Any:
    """The TerrainResult of an earlier run: from its record when the rasters
    are still there, else rebuilt (the routing cache makes that quick)."""
    from .landscape.terrain import TerrainResult, build_terrain
    from .safety import GeolensError

    rec = _run_record(run)
    d = (rec.get("result") or {}).get("terrain") or {}
    keys = ("dem", "conditioned", "flow_dir", "accumulation", "streams",
            "hand", "slope")
    if d and all(d.get(k) and Path(d[k]).exists() for k in keys):
        return TerrainResult(**{k: d[k] for k in TerrainResult.__dataclass_fields__
                                if k in d})
    dem = (rec.get("arguments") or {}).get("dem") or d.get("dem")
    if not dem:
        raise GeolensError(f"{run} has no landscape pass record to find its "
                           f"DEM and routing in.")
    return build_terrain(dem, stream_threshold_km2=d.get("stream_threshold_km2"))


def _run_segments(run: Path) -> Path:
    """The run's reaches: the calibrated ones (reclassified_dir) when the
    confinement breaks have been calibrated, else the pass's own."""
    for d in ("reclassified_dir", "classified_dir"):
        p = run / d / "segments_classified.gpkg"
        if p.exists():
            return p
    return run / "classified_dir" / "segments_classified.gpkg"


def calibrate_confinement_for_run(
    run_dir: str,
    confined_max: float | None = None,
    partly_confined_max: float | None = None,
    retype_valleys: bool = True,
) -> dict[str, Any]:
    """Confinement breaks for an EARLIER run: the sweep, and optionally new
    breaks applied.

    Without breaks it only reports: the confinement-ratio percentiles of this
    landscape and how its reaches divide under five candidate pairs (the
    literature's 2 and 5 channel widths rarely transfer). With both breaks it
    writes ``reclassified_dir`` and, with ``retype_valleys``, re-types the
    valleys from the run's existing valley-form measurements (nothing is
    re-measured) and re-judges stable/unstable form. Later steps use the
    calibrated reaches.
    """
    from .safety import GeolensError

    run = Path(run_dir)
    seg = run / "classified_dir" / "segments_classified.gpkg"
    if not seg.exists():
        raise GeolensError(f"No classified_dir/segments_classified.gpkg in "
                           f"{run}: point at the output folder of a "
                           f"landscape pass.")
    out: dict[str, Any] = {"sweep": confinement_break_sweep(str(seg))}
    if confined_max is None or partly_confined_max is None:
        return out
    if confined_max >= partly_confined_max:
        raise GeolensError("The confined break must be below the partly "
                           "confined one.")
    out["reclassified"] = reclassify_confinement(str(seg), confined_max,
                                                 partly_confined_max)
    vf = run / "valley_form_dir" / "valley_form_segments.csv"
    if retype_valleys and vf.exists():
        fans = run / "fans_dir" / "fans.gpkg"
        geo = run / "geology_dir" / "geology.gpkg"
        new_seg = run / "reclassified_dir" / "segments_classified.gpkg"
        vt = rosgen_valley_types(
            str(new_seg), geology=str(geo) if geo.exists() else None,
            fans=str(fans) if fans.exists() else None, valley_form=str(vf),
            out_prefix="valley_types")
        out["valley_types"] = vt
        st = run / "stream_types_dir" / "stream_types.gpkg"
        if st.exists():
            out["stability"] = stream_stability(vt["segments"], str(st))
    return out


def valley_types_for_run(
    run_dir: str,
    geology: str | None = "auto",
    fans: bool | str = True,
    valley_form_spacing_m: float | None = None,
    valley_max_search_m: float | None = None,
) -> dict[str, Any]:
    """Valley form, alluvial fans and Rosgen valley types for an EARLIER run.

    Step 5 of the landscape pass on its own, for a run made with
    ``valley_types=False`` or to redo it (with fans, or new geology). Reads
    the run's terrain, reaches and confinement; nothing is routed again.
    Replaces the run's valley_form_dir, valley_types_dir and valley_extent_dir,
    and re-judges stable/unstable form when the run has stream types.

    ``geology``: "auto" uses the run's downloaded geology if it has one, else
    downloads it; "ssurgo" does the same with USDA SSURGO soils (finer: a
    narrow valley's alluvium is usually mapped); a path uses that layer;
    None runs without.
    """
    from .landscape.pipeline import _geology_for_dem, valley_types_step
    from .safety import GeolensError

    run = Path(run_dir)
    rec = _run_record(run)
    res = rec.get("result") or {}
    seg = _run_segments(run)
    net = Path((res.get("network") or {}).get("segments")
               or run / "network_dir" / "segments.gpkg")
    if not seg.exists() or not net.exists():
        raise GeolensError(f"{run} has no classified_dir / network_dir: point "
                           f"at the output folder of a landscape pass.")
    terrain = _run_terrain(run)
    warnings: list[str] = []
    if isinstance(geology, str) and geology.lower() == "auto":
        g = run / "geology_dir" / "geology.gpkg"
        geology = str(g) if g.exists() else _geology_for_dem(
            terrain.dem, Guard.from_env(), warnings)
    elif isinstance(geology, str) and geology.lower() == "ssurgo":
        from .landscape.pipeline import _soils_for_dem

        s = run / "soils_dir" / "soils.gpkg"
        geology = str(s) if s.exists() else _soils_for_dem(
            terrain.dem, Guard.from_env(), warnings)
    seg_len = (rec.get("arguments") or {}).get("segment_length_m") or 500.0
    out, w = valley_types_step(
        terrain, str(net), str(seg), geology=geology, fans=fans,
        valley_form_spacing_m=valley_form_spacing_m,
        valley_max_search_m=valley_max_search_m, segment_length_m=seg_len)
    warnings.extend(w)
    st = run / "stream_types_dir" / "stream_types.gpkg"
    if st.exists() and out.get("valley_types"):
        out["stability"] = stream_stability(out["valley_types"]["segments"],
                                            str(st))
    out["geology"] = {"path": geology}
    out["warnings"] = warnings
    return out


def stream_types_for_run(
    run_dir: str,
    dem: str | None = None,
    dem_fine: str | None = None,
    regional_curve: str | None = "arid_sw_nm",
    bankfull_method: str = "regional_stage",
    section_plots: bool | None = None,
    out_prefix: str = "stream_types",
) -> dict[str, Any]:
    """Rosgen stream types and stable/unstable form for an EARLIER run.

    Nothing is routed or measured again except the channel sections: the
    run's reaches, geology and valley types are read from its folder. ``dem``
    and ``dem_fine`` default to what the run's landscape pass used (from its
    run record). Writes ``<out_prefix>_dir`` and the matching stability
    folder: the default name replaces the run's stream types, and any other
    (``stream_types_v29``) writes beside them and keeps the old ones.
    """
    from .landscape import rosgen_stream_types
    from .safety import GeolensError

    run = Path(run_dir)
    seg = run / "classified_dir" / "segments_classified.gpkg"
    if not seg.exists():
        raise GeolensError(f"No classified_dir/segments_classified.gpkg in "
                           f"{run}: point at the output folder of a "
                           f"landscape pass.")
    dem = dem or _run_argument(run, "dem")
    if not dem:
        raise GeolensError("Give the DEM: the run folder has no landscape "
                           "pass record to take it from.")
    if dem_fine is None:
        dem_fine = _run_argument(run, "dem_fine")
    geo = run / "geology_dir" / "geology.gpkg"
    st = rosgen_stream_types(
        dem, str(seg), dem_fine=dem_fine,
        geology=str(geo) if geo.exists() else None,
        regional_curve=regional_curve, section_plots=section_plots,
        bankfull_method=bankfull_method, out_prefix=out_prefix).as_dict()
    out: dict[str, Any] = {"stream_types": st}
    vt = next((run / d / "valley_types.gpkg"
               for d in ("valley_types_fans_dir", "valley_types_dir")
               if (run / d / "valley_types.gpkg").exists()), None)
    if vt is not None:
        suffix = out_prefix.replace("stream_types", "", 1).strip("_")
        out["stability"] = stream_stability(
            str(vt), st["segments"],
            out_prefix=f"stability_{suffix}" if suffix else "stability")
    return out


def riparian_for_run(
    run_dir: str,
    ndvi: str | None = None,
    start: str | None = None,
    end: str | None = None,
    resolution_m: float = 1.0,
    min_excess: float = 0.12,
    max_hand_m: float | None = 15.0,
) -> dict[str, Any]:
    """The riparian band over an EARLIER run, from NAIP NDVI.

    Give ``ndvi`` (for example the mosaic :func:`naip_tiles` wrote into a
    folder of your choosing); without it, NAIP is fetched into the run's
    naip_dir. The run's HAND and streams limit the band to ground near a
    channel, so irrigated mesa tops stay out. Writes riparian_dir into the
    run folder.
    """
    from .safety import GeolensError

    run = Path(run_dir)
    terrain = _run_terrain(run)
    if ndvi:
        img = {"ndvi": str(ndvi), "warnings": []}
    else:
        if not Path(terrain.dem).exists():
            raise GeolensError(f"The run's DEM is gone: {terrain.dem}")
        img = naip_imagery(terrain.dem, start=start, end=end,
                           resolution_m=resolution_m)
    if not img.get("ndvi"):
        raise GeolensError("No NAIP imagery covered this run's extent; see "
                           "the warnings.")
    rip = riparian_band(img["ndvi"], hand=terrain.hand,
                        streams=terrain.streams, min_excess=min_excess,
                        max_hand_m=max_hand_m)
    return {"naip": img, "riparian": rip}


def retype_valleys_with_fans(run_dir: str, fans: str,
                             out_prefix: str = "valley_types_fans"
                             ) -> dict[str, Any]:
    """Re-type an earlier run's valleys with a fan layer, and re-judge form.

    Reads the run's segments, geology and valley-form table, so nothing is
    re-routed or re-measured. Writes ``<out_prefix>_dir/valley_types.gpkg``
    and, when the run has stream types, ``stability_fans_dir`` beside the
    originals; the run's own layers are left as they are.
    """
    from .safety import GeolensError

    run = Path(run_dir)
    seg = next((run / d / "segments_classified.gpkg"
                for d in ("reclassified_dir", "classified_dir")
                if (run / d / "segments_classified.gpkg").exists()), None)
    if seg is None:
        raise GeolensError(f"No classified_dir/segments_classified.gpkg in "
                           f"{run}: point at the output folder of a "
                           f"landscape pass.")
    geo = run / "geology_dir" / "geology.gpkg"
    vf = run / "valley_form_dir" / "valley_form_segments.csv"
    vt = rosgen_valley_types(str(seg), geology=str(geo) if geo.exists() else None,
                             fans=fans, valley_form=str(vf) if vf.exists() else None,
                             out_prefix=out_prefix)
    out: dict[str, Any] = {"valley_types": vt}
    st = run / "stream_types_dir" / "stream_types.gpkg"
    if st.exists():
        out["stability"] = stream_stability(vt["segments"], str(st),
                                            out_prefix="stability_fans")
    return out


def rosgen_valley_types(
    segments: str,
    geology: str | None = None,
    fans: str | None = None,
    valley_form: str | None = None,
    buffer_m: float = 200.0,
    min_cover: float = 0.25,
    out_prefix: str = "valley_types",
) -> dict[str, Any]:
    """Assign Rosgen valley types (I-XI) to classified segments.

    A crosswalk, not a measurement: Rosgen's types describe landform history,
    which a DEM does not contain. It combines confinement and gradient from
    :func:`landscape_pass` with surficial material from :func:`fetch_geology`
    and detected fans from :func:`detect_alluvial_fans`.

    Pass ``geology``. Without it the types that geometry alone cannot separate
    — II/V/VI, VIII/IX, X/XI — stay unresolved, and those are most of the
    classification.

    Every segment gets a ``confidence`` and a ``basis`` saying what decided it.

    Pass ``valley_form`` (from :func:`valley_form`) for the evidence the types
    are actually defined on — cross-section shape, side slope and relief
    measured floor-to-divide. With it, a glacial trough is separable from a
    V-notch without geology, and Type VI is raised from never-assertable to a
    flagged candidate where the valley is persistently one-sided.
    """
    from .landscape import rosgen_valley_types as _rvt

    return _rvt(segments, geology=geology, fans=fans,
                valley_form=valley_form, buffer_m=buffer_m,
                min_cover=min_cover, out_prefix=out_prefix).as_dict()


def valley_type_reference() -> dict[str, Any]:
    """The Rosgen valley types and what separates them here."""
    from .landscape import VALLEY_TYPES
    from .landscape.rosgen import GRADIENT_BREAKS

    return {"types": dict(VALLEY_TYPES), "gradient_breaks": dict(GRADIENT_BREAKS)}


# -- observed flood extent ----------------------------------------------------


def fetch_inundation(
    aoi_raster: str,
    start: str = "2015-01-01",
    end: str | None = None,
    out_prefix: str = "inundation",
) -> dict[str, Any]:
    """Build an observed-flood envelope from the Copernicus GFM archive.

    Sentinel-1 derived, 20 m, 2015 to now, no API key. GFM's flood extent
    excludes water that is normally there, so it is the overbank anomaly —
    which is what a floodplain is.

    Pass the DEM as ``aoi_raster`` so the output aligns with everything else.
    The default date range is the whole archive on purpose: one scene is one
    flood, and a floodplain envelope needs every event there was.
    """
    from .inundation import fetch_inundation as _fi

    return _fi(aoi_raster, start=start, end=end, out_prefix=out_prefix).as_dict()


def validate_floodplain(floodplain: str, inundation: dict) -> dict[str, Any]:
    """Score a delineated floodplain against observed flood extent.

    Reports recall over observable pixels only. Precision and IoU are
    deliberately not reported — a correct floodplain extends beyond any observed
    extent, and penalising that drives calibration toward under-delineation.
    """
    from .inundation import validate_floodplain as _vf

    return _vf(floodplain, inundation).as_dict()


def export_flood_labels(
    inundation: dict,
    above_confinement: str | None = None,
    out_prefix: str = "flood_labels",
) -> dict[str, Any]:
    """Write positive / negative / unlabelled classes for the learning loop.

    Observed flood is positive, ground above valley confinement is negative,
    and everything else stays UNLABELLED — never negative. A 6-12 day revisit
    rarely catches a small stream at peak, so treating unobserved as dry would
    teach the model that floodplains are narrower than they are.
    """
    from .inundation import export_labels as _el

    return _el(inundation, above_confinement=above_confinement,
               out_prefix=out_prefix)


def inundation_selftest() -> dict[str, Any]:
    """Check the flood service is up and its pixel encoding is as assumed.

    Queries Hurricane Helene over western North Carolina. Run it once before
    trusting a validation number — the encoding assumptions are conventions,
    not contracts, and this could not be exercised where it was written.
    """
    from .inundation import inundation_selftest as _t

    return _t()


# -- the calibration loop -----------------------------------------------------


def calibration_cycle(
    segments: str,
    dataset: str | None = None,
    field_name: str = "confinement",
    value_field: str = "confinement_ratio",
    current_breaks: Sequence[float] | None = (2.0, 5.0),
    n_suggest: int = 20,
    out_prefix: str = "calibration",
) -> dict[str, Any]:
    """One round of calibration: fit from labels, evaluate, suggest what is next.

    Returns a ``recommendation`` that is allowed to be **no**:

    ``adopt``                     the new breaks beat the ones you use, held out
    ``keep_current``              they do not — keep what you have
    ``label_more``                too few labels for a held-out score to mean anything
    ``feature_does_not_separate`` the feature cannot tell these classes apart at
                                  this label count, and more labels will not fix it

    Every score is cross-validated, the incumbent breaks are scored on the same
    folds, and the fit is additionally tested against its own null — the same
    pipeline run on shuffled labels. Without that last check the loop recommends
    adopting fits on pure noise; it did exactly that before the check existed.
    """
    from .learn import run_cycle

    return run_cycle(segments, dataset=dataset, field_name=field_name,
                     value_field=value_field, current_breaks=current_breaks,
                     n_suggest=n_suggest, out_prefix=out_prefix).as_dict()


def label_worksheet(
    segments: str, feature_ids: Sequence[Any],
    field_name: str = "confinement", out_prefix: str = "worksheet",
) -> dict[str, Any]:
    """Write chosen features as a GeoPackage to fill in by hand."""
    from .learn import write_worksheet

    return write_worksheet(segments, feature_ids, field_name=field_name,
                           out_prefix=out_prefix)


def ingest_labels(worksheet: str, dataset: str,
                  field_name: str = "confinement",
                  author: str = "") -> dict[str, Any]:
    """Read a filled worksheet back into the label store."""
    from .learn import ingest_worksheet

    return ingest_worksheet(worksheet, dataset=dataset, field_name=field_name,
                            author=author)


def calibration_history() -> dict[str, Any]:
    """Every calibration cycle run here, oldest first.

    Whether calibration is still helping is only visible across rounds: a
    held-out score that has not moved in three batches means stop labelling.
    """
    from .learn import cycle_history

    return {"cycles": cycle_history()}


# -- imagery ------------------------------------------------------------------


def naip_imagery(
    aoi_raster: str,
    start: str | None = None,
    end: str | None = None,
    want_rgb: bool = True,
    use_cache: bool = True,
    resolution_m: float | None = 1.0,
    out_prefix: str = "naip",
) -> dict[str, Any]:
    """Stream NAIP over a DEM's extent and build 1 m NDVI plus true colour.

    NAIP carries near-infrared at 0.6-1 m, so NDVI comes out at 1 m where
    Sentinel-2 gives 10 m. For a 20 m wide desert riparian corridor that is the
    difference between two pixels and twenty — which is the difference between
    an edge effect and a measurement.

    Nothing is downloaded whole: NAIP is published as Cloud Optimised GeoTIFFs,
    so only the bytes covering your extent are fetched. Clipped windows are
    cached under ``<output>/naip/`` (or ``GEOLENS_NAIP_CACHE``) for truthing.

    The NDVI is built at ``resolution_m`` (1 m by default), **not** on the DEM's
    grid. :func:`riparian_band` resamples HAND and streams onto it, so a 10 m
    DEM and 1 m NDVI work together.

    Feed the result straight to :func:`riparian_band`.
    """
    from .naip import naip_ndvi

    return naip_ndvi(aoi_raster, start=start, end=end, want_rgb=want_rgb,
                     use_cache=use_cache, resolution_m=resolution_m,
                     out_prefix=out_prefix).as_dict()


def fetch_dem(
    aoi: str,
    source: str = "best",
    resolution_m: float | None = None,
    crs: str | None = None,
    buffer_m: float = 0.0,
) -> dict[str, Any]:
    """USGS 3DEP elevation over an area, into the output folder: each tile
    clipped to the area in ``dem_tiles/``, and ``dem_mosaic.tif``.

    ``aoi`` is a polygon layer or a raster (its extent). ``source="best"``
    takes 1 m lidar where it exists and fills the rest from the 1/3
    arc-second DEM (the result says how much); ``"1m"`` or ``"1/3"`` for one
    or the other. ``buffer_m`` widens the area, in metres. The output is in
    the lidar's UTM zone unless ``crs`` names another; on a foot CRS the
    elevations are converted to feet.
    """
    from .dem_fetch import fetch_dem as _fd

    return _fd(aoi, source=source, resolution_m=resolution_m, crs=crs,
               buffer_m=buffer_m)


def naip_tiles(
    aoi: str,
    start: str | None = None,
    end: str | None = None,
    resolution_m: float = 1.0,
    true_colour: bool = True,
    keep_imagery: bool = True,
) -> dict[str, Any]:
    """NAIP over an area into the output folder: an NDVI tile and an imagery
    tile per scene, an NDVI mosaic and a true-colour mosaic.

    ``aoi`` is a raster (normally the DEM) or a polygon layer. Scenes are
    taken newest first and older ones skipped where newer imagery already
    covers the area. No size limit beyond memory for a byte a cell. Feed
    ``ndvi`` to :func:`riparian_band` or :func:`riparian_for_run`.
    """
    from .naip import naip_tiles as _nt

    return _nt(aoi, start=start, end=end, resolution_m=resolution_m,
               true_colour=true_colour, keep_imagery=keep_imagery)


def run_project(
    boundary: str,
    where: str | None = None,
    level_b: str = "pilot",
    pilot_count: int = 3,
    pilot_by: str = "representative",
    huc12s: str | None = None,
    stream_threshold_km2: float = 1.0,
    geology: str | None = "ssurgo",
    regional_curve: str | None = "auto",
    lidar_min_fraction: float = 0.8,
    section_plots: bool = True,
    level_b_hours: float | None = None,
    soils: bool = True,
    data: str | list | None = "all",
) -> dict[str, Any]:
    """A whole project area, any size: valleys everywhere, channels on lidar.

    ``boundary`` is a polygon layer, or an ArcGIS REST layer URL with
    ``where`` (a BLM field office, a forest district). Level A runs the whole
    area at 10 m, widened to the HUC12 watersheds it touches and those
    draining into them: routing, valley bottoms, confinement, valley types.
    Level B then runs HUC12 by HUC12 on 3DEP 1 m lidar where it exists:
    stream types, section plots and stable / unstable form. ``level_b`` is
    "pilot" (``pilot_count`` HUC12s), "all", "listed" (``huc12s``,
    comma-separated) or "none". ``pilot_by`` chooses the pilot:
    "representative" (landscape projects: HUC12s that together stand for the
    whole area, by valley types and soils, so their results scale up) or
    "unconfined" (stream projects: the most unconfined valley). The output
    folder gets ``merged`` layers, ``RESULTS.md`` (what ran at what
    resolution, and warnings) and ``project.json``; running the project
    again carries on where it stopped. ``geology="ssurgo"`` (the default)
    types the valleys by the SSURGO soils fetched over the boundary;
    ``"auto"`` uses USGS geology instead, which tends to call valleys
    bedrock. ``regional_curve="auto"`` picks the
    Moody et al. (2003) province from the location, or the generic US curve
    outside the arid Southwest. ``data`` is the watershed data fetched per
    HUC12 inside the boundary, each its own resumable step: "all" (the
    default), "none", or some of ``fire`` (NIFC perimeters: share burned,
    last fire), ``ecosites`` (SSURGO ecological sites), ``prism`` (1991-2020
    precipitation, monsoon share, temperature), ``atlas14`` (NOAA storm
    depths) and ``rap`` (Rangeland Analysis Platform cover, 1986 on, with
    trends). They and the project's own results meet in ``huc12_summary``,
    one row per HUC12.
    """
    from .project import run_project as _rp

    return _rp(boundary, where=where, level_b=level_b,
               pilot_count=pilot_count, pilot_by=pilot_by, huc12s=huc12s,
               stream_threshold_km2=stream_threshold_km2, geology=geology,
               regional_curve=regional_curve,
               lidar_min_fraction=lidar_min_fraction,
               section_plots=section_plots, level_b_hours=level_b_hours,
               soils=soils, data=data)


def check_services(timeout: int = 20) -> dict[str, Any]:
    """Try each service geolens downloads from (3DEP, the WBD, geology,
    SSURGO, NAIP, NASA) once, and say which this computer can reach."""
    from .services import check_services as _cs

    return _cs(timeout=timeout)


def fetch_soils(aoi: str, buffer_m: float = 0.0) -> dict[str, Any]:
    """USDA SSURGO soil map units over an area, from Soil Data Access.

    ``aoi`` is a raster (its extent, normally the DEM) or a polygon layer;
    ``buffer_m`` widens it. Writes ``soils_dir/soils.gpkg``: each map unit
    polygon clipped to the area, with ``material_class`` (alluvial,
    mass_movement, residual, eolian, ...) from its dominant component's
    parent material, so it can be passed as ``geology=`` to valley typing.
    Also the component name, landform, flooding frequency, drainage class,
    hydrologic group and depth to bedrock. ``geology="ssurgo"`` in
    ``landscape_pass`` or ``valley_types_for_run`` fetches it for the DEM.
    """
    from .soils import fetch_soils as _fs

    return _fs(aoi, buffer_m=buffer_m)


def sentinel1_stats(
    aoi: str,
    start: str | None = None,
    end: str | None = None,
    polygons: str | None = None,
    min_passes: int = 8,
) -> dict[str, Any]:
    """Sentinel-1 radar statistics for an area, from NASA's OPERA RTC-S1.

    For telling woody riparian vegetation from irrigated fields, which NDVI
    cannot (docs/SAR_FEASIBILITY.md). Writes a VV/VH tile per pass
    (``sar_tiles``, reused on a rerun), per-track and combined statistics
    (``sar_stats.tif``: median VH and VV in dB, VH/VV in dB, VH's
    coefficient of variation, passes) and, given ``polygons``, the
    statistics per polygon (``sar_zonal.gpkg``). ``aoi`` is a raster or a
    polygon layer; the period defaults to the year before ``end`` (default
    today). Needs a free NASA Earthdata login saved with
    ``geolens.sar.save_login`` (toolbox: 0 Setup > Save Earthdata login).
    """
    from .sar import sentinel1_stats as _s

    return _s(aoi, start=start, end=end, polygons=polygons,
              min_passes=min_passes)


def earthdata_login_status() -> dict[str, Any]:
    """Whether a NASA Earthdata login is saved on this computer, and for
    whom. Never returns the password."""
    from .sar import login_status

    return login_status()


def naip_selftest() -> dict[str, Any]:
    """Check the catalog is up and NAIP is still 4-band RGB+NIR.

    The NDVI arithmetic assumes band 1 is red and band 4 is near-infrared. If
    that changed, NDVI would keep computing and silently mean nothing.
    """
    from .naip import naip_selftest as _t

    return _t()


def riparian_band(
    ndvi: str,
    hand: str | None = None,
    streams: str | None = None,
    background_window_m: float = 2000.0,
    min_excess: float = 0.12,
    max_hand_m: float | None = 15.0,
    out_prefix: str = "riparian",
) -> dict[str, Any]:
    """Map the riparian band as NDVI in excess of the local upland background.

    For arid and intermittent systems this is the floodplain proxy that works,
    because phreatophytes are there every growing season while the flow is not.
    See :mod:`geolens.inundation` for why flood extent fails in that country.

    It measures NDVI **relative to the local background**, not an absolute
    threshold, so a drought year or a different sensor does not move the answer.

    Pass ``hand`` — without it, any green ground qualifies, including irrigated
    mesa tops. And read the flagged patches: irrigated fields sit on floodplains
    by definition and nothing in NDVI separates alfalfa from cottonwood.
    """
    from .riparian import riparian_band as _rb

    return _rb(ndvi, hand=hand, streams=streams,
               background_window_m=background_window_m, min_excess=min_excess,
               max_hand_m=max_hand_m, out_prefix=out_prefix).as_dict()


def watershed(
    dem: str,
    boundary: str | None = None,
    pour_point: str | None = None,
    pour_crs: str | None = None,
    huc: str | None = None,
    buffer_m: float = 0.0,
    snap_radius_m: float = 150.0,
    verify_inflow: bool = True,
    out_prefix: str = "watershed",
) -> dict[str, Any]:
    """Clip a DEM to one complete watershed, so it can be analysed whole.

    **This is the unit to work in, not a tile.** A rectangular tile cuts across
    drainage divides, so flow paths are truncated at every edge; on the
    landscape fixture a rectangle over the middle third is missing 3.65x its
    own area in upstream contribution. A complete watershed has no inflow
    across its boundary by definition, so accumulation inside the clip is
    exactly right.

    Give exactly one of ``boundary`` (a polygon you already have — a WBD HUC12,
    a permit boundary), ``pour_point`` (``"x,y"``; the contributing area is
    delineated from the DEM) or ``huc`` (fetched from the USGS WBD; a network
    call, see :func:`huc_selftest`).

    A supplied ``boundary`` is checked rather than trusted: ``verify_inflow``
    reports whether anything drains into it across its edge, which is the
    difference between a watershed and a polygon someone drew.
    """
    from .landscape import watershed as _ws

    return _ws(dem, boundary=boundary, pour_point=pour_point,
               pour_crs=pour_crs, huc=huc, buffer_m=buffer_m,
               snap_radius_m=snap_radius_m, verify_inflow=verify_inflow,
               out_prefix=out_prefix)


def watershed_pass(
    dem: str,
    boundary: str | None = None,
    pour_point: str | None = None,
    pour_crs: str | None = None,
    huc: str | None = None,
    buffer_m: float = 0.0,
    stream_threshold_km2: float = 1.0,
    segment_length_m: float = 1000.0,
    out_prefix: str = "watershed_pass",
    **landscape_kwargs: Any,
) -> dict[str, Any]:
    """Clip to a complete watershed, then run the landscape pass on it.

    The whole point of doing it in this order: everything downstream of the
    clip — the stream network, HAND, valley bottoms, confinement, valley types
    — is computed over a unit with no truncated inflow, so none of it carries
    an edge artefact that has to be explained away later.
    """
    # The landscape-level function, not the api wrapper next to it: going
    # through the wrapper would write a second run record for a step that is
    # part of this one, and two records for one call is how a run log stops
    # being readable.
    from .landscape import run_landscape, watershed as _ws

    ws = _ws(dem, boundary=boundary, pour_point=pour_point, pour_crs=pour_crs,
             huc=huc, buffer_m=buffer_m, out_prefix=out_prefix + "_clip")

    res = run_landscape(ws["dem"], stream_threshold_km2=stream_threshold_km2,
                        segment_length_m=segment_length_m, **landscape_kwargs)
    res["watershed"] = ws
    res.setdefault("warnings", []).extend(ws.get("warnings", []))
    return res


def huc_selftest() -> dict[str, Any]:
    """Is the USGS Watershed Boundary Dataset reachable?

    Separate from any analysis, so a failure is unambiguously the service
    rather than the data or the code. If it is down, download the HUC once and
    pass it to :func:`watershed` as ``boundary`` — that path needs no network.
    """
    from .landscape import huc_selftest as _st

    return _st()


def detect_gullies(
    terrain: "str | dict[str, Any]",
    slope_std_min: float | None = None,
    tpi_max: float = -0.3,
    min_drainage_km2: float = 0.005,
    max_drainage_km2: float | None = 0.5,
    min_slope: float = 0.02,
    exclude_streams: bool = False,
    window_m: float | None = None,
    section_width_m: float | None = None,
    min_length_m: float | None = None,
    stream_threshold_km2: float | None = None,
    exclude: str | None = None,
    exclude_buffer_m: float = 10.0,
    polygons: bool = False,
    out_prefix: str = "gullies",
) -> dict[str, Any]:
    """Find gullies, reusing a terrain run rather than re-routing the DEM.

    ``exclude`` is a line or polygon layer of things that are not gullies --
    roads, above all, which are incised, steep-sided and linear. Lines lying
    mostly within ``exclude_buffer_m`` of it are set aside into
    ``gullies_excluded.gpkg`` for review rather than measured.

    This is the only gully entry point that lands in the run log. The notebook
    called ``geolens.landscape.detect_gullies`` directly, so the one step whose
    parameters most need a record of what was tried was the one step with no
    record at all.

    Parameters
    ----------
    terrain
        A previous ``landscape_pass`` result (or its ``terrain`` sub-dict, or a
        ``TerrainResult``), which is reused as-is -- **no re-routing**. A DEM
        path also works and routes it, which on a large DEM is hours; pass a
        result if you have one.
    slope_std_min
        Left at ``None`` this calls :func:`~geolens.landscape.suggest_slope_std_min`,
        which finds the gap between the smooth and incised modes of the
        roughness distribution. Do not substitute a percentile: p90 admits 10%
        of the DEM by construction, and p99 finds nothing at all. The
        suggestion and its evidence come back under ``threshold_choice``.
    max_drainage_km2
        The only criterion that means *small*. See
        :func:`~geolens.landscape.detect_gullies`.
    stream_threshold_km2
        Re-extract the network at this threshold first. Set it equal to
        ``max_drainage_km2`` and streams and gullies partition cleanly, which
        is what makes ``exclude_streams`` safe.
    window_m, section_width_m, min_length_m
        Left at ``None`` these scale to the cell size -- a 40 m roughness
        window is barely one cell on a coarse DEM and the result is
        meaningless. This is the scaling the notebook used to carry inline.
    """
    from .landscape import (detect_gullies as _dg, restream,
                            suggest_slope_std_min, terrain_from_result,
                            terrain_roughness)
    from .landscape import build_terrain

    if isinstance(terrain, (str, Path)):
        thresh = stream_threshold_km2
        if thresh is None:
            # Scale to the DEM, not to a constant. Gully work is a reach-scale
            # question -- the docs say so -- and a 0.5 km2 stream threshold on
            # a 2.5 km2 lidar clip leaves no network at all, which is a hard
            # error from build_terrain rather than a warning. Divide-by-50 is
            # the same ratio build_terrain already warns above, so the default
            # lands just inside its own advice.
            # Through units.unit_info, not res[0]*res[1]. On a CRS in US
            # survey feet -- which is what the DEM this was built for uses --
            # the raw product is ft2, and calling it m2 is out by 10.76x. That
            # exact mistake is why geolens.units exists.
            import rasterio as _rio

            from .units import unit_info as _unit_info

            with _rio.open(str(terrain)) as _src:
                _u = _unit_info(_src.crs, abs(_src.res[0]), abs(_src.res[1]))
                _extent_km2 = _src.width * _src.height * _u.cell_area_m2 / 1e6
            thresh = min(max_drainage_km2 or 0.5, max(_extent_km2 / 50.0, 1e-6))
        t = build_terrain(str(terrain), stream_threshold_km2=thresh)
    else:
        t = terrain_from_result(terrain)
        if (stream_threshold_km2 is not None
                and abs(stream_threshold_km2 - t.stream_threshold_km2) > 1e-12):
            t = restream(t, stream_threshold_km2=stream_threshold_km2,
                         out_prefix=out_prefix + "_restream")

    cell = t.cell_size
    window_m = window_m if window_m is not None else max(40.0, cell * 5)
    section_width_m = (section_width_m if section_width_m is not None
                       else max(60.0, cell * 14))
    min_length_m = (min_length_m if min_length_m is not None
                    else max(100.0, cell * 5))

    rough = terrain_roughness(t, window_m=window_m)

    choice: dict[str, Any] = {}
    if slope_std_min is None:
        choice = suggest_slope_std_min(rough.slope_std)
        slope_std_min = choice["slope_std_min"]

    out = _dg(t, roughness=rough, slope_std_min=slope_std_min, tpi_max=tpi_max,
              min_drainage_km2=min_drainage_km2,
              max_drainage_km2=max_drainage_km2, min_slope=min_slope,
              exclude_streams=exclude_streams, window_m=window_m,
              section_width_m=section_width_m, min_length_m=min_length_m,
              exclude=exclude, exclude_buffer_m=exclude_buffer_m,
              polygons=polygons, out_prefix=out_prefix).as_dict()
    out["threshold_choice"] = choice
    out["slope_std_raster"] = rough.slope_std
    out["stream_threshold_km2"] = t.stream_threshold_km2
    return out


# -- automatic persistence ----------------------------------------------------
#
# Wrap every public function in this module so results land on disk without the
# caller doing anything. Done here, in one place, rather than as a decorator on
# each definition: a decorator per function is one more thing to forget, and the
# functions added next week would silently not be covered.
#
# Excluded below are the calls that are cheap, read-only, or are themselves the
# log — recording those would bury the expensive runs that matter in noise.

_NO_PERSIST = {
    "workspace", "run_history", "load_run", "landscape_backends",
    "list_datasets", "describe_vector", "describe_raster", "attribute_summary",
    "sample_features", "plan_landscape_run", "corrections_summary",
    "operator_disagreement", "geology_selftest", "valley_type_reference",
    "inundation_selftest", "calibration_history", "naip_selftest",
    "huc_selftest", "check_services", "earthdata_login_status",
}


def _install_persistence() -> None:
    import types

    from .persist import persisted

    for _name, _obj in list(globals().items()):
        if _name.startswith("_") or _name in _NO_PERSIST:
            continue
        if not isinstance(_obj, types.FunctionType):
            continue
        if getattr(_obj, "__module__", None) != __name__:
            continue
        if hasattr(_obj, "__wrapped__"):        # already wrapped
            continue
        globals()[_name] = persisted(_name)(_obj)


_install_persistence()
