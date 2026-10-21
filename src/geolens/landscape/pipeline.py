"""One call that runs the whole landscape pass."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..safety import Guard, note, GeolensError
from .terrain import build_terrain
from .valley import extract_valley_bottom
from .network import extract_network
from .classify import classify_segments


def _geology_for_dem(dem: str, guard: Guard, warnings: list) -> str | None:
    """Fetch USGS surficial geology over this DEM's footprint.

    ``fetch_geology`` has existed for a while and **nothing called it**, so
    every landscape pass anyone has run has been geology-blind -- which is
    where the `needs_geology` flags and the unresolved Rosgen types come from.
    Rosgen II/V/VI, VIII/IX and X/XI are separated by depositional history and
    cannot be told apart from shape, so without this the answer is a candidate
    set no matter how good the DEM is.

    It takes a vector AOI, so the DEM's footprint is written out and handed to
    it. A download failure warns and returns None: losing the network must
    never cost the four expensive steps that already succeeded.
    """
    import tempfile

    import geopandas as gpd
    import rasterio
    from shapely.geometry import box

    try:
        from ..geology import fetch_geology

        with rasterio.open(str(guard.read_path(dem))) as src:
            foot = gpd.GeoDataFrame(
                {"id": [1]}, geometry=[box(*src.bounds)], crs=src.crs)
        with tempfile.TemporaryDirectory() as td:
            aoi = Path(td) / "dem_footprint.gpkg"
            foot.to_file(str(aoi), driver="GPKG")
            note("  fetching USGS surficial geology over the DEM footprint")
            g = fetch_geology(str(aoi), guard=guard)
        note(f"  geology: {g.n_polygons:,} polygons from {g.service_label}")
        return g.polygons
    except Exception as exc:                                 # noqa: BLE001
        warnings.append(
            f"Geology could not be downloaded ({type(exc).__name__}: "
            f"{str(exc)[:200]}). The pass continues without it, which means "
            f"Rosgen types that differ only by depositional history "
            f"(II/V/VI, VIII/IX, X/XI) stay unresolved and stream types get "
            f"no substrate number. Fetch it once with "
            f"geolens.geology.fetch_geology and pass the .gpkg as geology=."
        )
        return None


def _soils_for_dem(dem: str, guard: Guard, warnings: list) -> str | None:
    """SSURGO soils over this DEM's footprint, as the geology layer.

    Finer than the national geologic map (about 1:24,000), so a narrow
    valley's own alluvium is usually mapped. Saved in ``soils_dir`` in the
    run folder. A failure warns and returns None, as for geology.
    """
    try:
        from ..soils import fetch_soils

        note("  fetching USDA SSURGO soils over the DEM footprint")
        return fetch_soils(str(guard.read_path(dem)), guard=guard)["soils"]
    except Exception as exc:                                 # noqa: BLE001
        warnings.append(
            f"SSURGO soils could not be downloaded ({type(exc).__name__}: "
            f"{str(exc)[:200]}). The pass continues without geology, so "
            f"Rosgen types that differ only by depositional history stay "
            f"unresolved. Try geology=\"auto\" (USGS geology) instead.")
        return None


def run_landscape(
    dem: str,
    nhd: str | None = None,
    stream_threshold_km2: float | None = None,
    segment_length_m: float = 500.0,
    backend: str = "auto",
    channel_width_m: float | None = None,
    confined_max: float = 2.0,
    partly_confined_max: float = 5.0,
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
    flow: Any = None,
    guard: Guard | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Condition -> route -> HAND -> valley bottom -> segment -> classify
    -> valley form -> Rosgen valley type.

    Returns every intermediate path plus the classification summary. Extra
    keyword arguments are forwarded to ``extract_valley_bottom``.

    ``dem_fine`` is a finer DEM over the same ground, used only where
    resolution actually matters: the channel cross-sections behind the Rosgen
    **stream** types. Routing, HAND and the valley catchments stay on ``dem``.
    That is the whole point -- 0.73 m cells add nothing to a divide three
    kilometres away and cost 1.94 billion cells, while bankfull width at 10 m
    is one cell and means nothing. A cross-section is a line of point samples,
    so the fine grid only has to exist where the measurement happens and
    nothing is resampled.

    ``flow`` is a FlowResult computed over a larger extent. Pass it when running
    on a tile so contributing area is inherited from upstream instead of
    restarting at zero — that is what makes tiled results match an untiled run.

    ``valley_types``
        Measure valley form floor-to-divide and classify Rosgen valley types.
        **On by default**, and it is on by default for a specific reason: it
        was opt-in, the notebook never opted in, and three rounds of work on
        valley types went into something no run ever produced. Opt-in is how
        that happens.

        It is the slowest step after routing — one cross-section every
        ``valley_form_spacing_m`` along the network, each sampled out to the
        divide on both sides. Pass ``False`` on a run where the valley type is
        not the question.
    ``geology``
        A geology polygon layer. Rosgen types II/V/VI, VIII/IX and X/XI are
        separated by depositional history, not by shape, so without this those
        come back unresolved with their candidates listed — which is the
        honest answer, not a gap to fill by guessing.
    ``section_plots``
        A picture of every stream-type section and a sheet per reach, linked
        to the map (hover in QGIS to see one). ~70 ms a section.
    ``fans``
        Search for alluvial fans before typing valleys, so a reach that lies
        mostly on a fan is Rosgen Type III instead of an unresolved III/VII.
        Runs on a coarser copy of the DEM above ``FAN_MAX_CELLS`` cells. Only
        cone-shaped regions count; the rest go to ``fan_candidates.gpkg``.
    """
    # A typo in the curve name would otherwise surface at step 6, after the
    # routing it took an hour to reach.
    if stream_types and regional_curve not in (None, "auto"):
        from .streamtype import _resolve_curve
        _resolve_curve(regional_curve)
    t0 = time.monotonic()
    guard = guard or Guard.from_env()

    note("STEP 1/4  terrain")
    terrain = build_terrain(dem, stream_threshold_km2=stream_threshold_km2,
                            backend=backend, flow=flow, guard=guard)
    note("STEP 2/4  valley bottom")
    valley = extract_valley_bottom(terrain, guard=guard, **kwargs)
    note("STEP 3/4  network")
    network = extract_network(terrain, segment_length_m=segment_length_m,
                              nhd=nhd, guard=guard)
    note("STEP 4/4  classify")
    classified = classify_segments(
        terrain, valley, network, channel_width_m=channel_width_m,
        confined_max=confined_max, partly_confined_max=partly_confined_max,
        guard=guard,
    )

    out: dict[str, Any] = {
        "terrain": terrain.as_dict(),
        "valley": valley.as_dict(),
        "network": network.as_dict(),
        "classified": classified.as_dict(),
    }
    warnings = (terrain.warnings + valley.warnings + network.warnings
                + classified.warnings)

    # "auto" is the discoverable spelling. The download is opt-in because it
    # is a network call, but a pass that runs without geology now says so and
    # says the word that turns it on, instead of leaving the user to find a
    # function nothing references.
    # Say in the RESULT what happened to geology. On the first real run the
    # download worked, the valley types used it, and the only evidence was
    # geology_used=True three levels down in a JSON log -- so it looked
    # broken. A top-level entry the notebook can print is the fix.
    asked = geology
    fetched = isinstance(asked, str) and asked.lower() in ("auto", "ssurgo")
    if fetched and asked.lower() == "auto":
        geology = _geology_for_dem(dem, guard, warnings)
    elif fetched:
        geology = _soils_for_dem(dem, guard, warnings)
    out["geology"] = {
        "requested": asked,
        "path": str(geology) if geology is not None else None,
        "status": ("downloaded" if fetched and geology is not None
                   else "download failed — see warnings" if fetched
                   else "supplied" if geology is not None else "none"),
    }
    if asked is None:
        warnings.append(
            "No geology supplied, so Rosgen types that differ only by "
            "depositional history cannot be resolved and stream types get no "
            "substrate number. Pass geology=\"auto\" to download USGS "
            "surficial geology over this DEM, or geology=<path.gpkg> if you "
            "already have it."
        )

    if valley_types:
        note("STEP 5/6  valley form, alluvial fans and Rosgen valley types")
        # A failure here must not lose the four steps that already
        # succeeded: the landscape pass is expensive and its earlier outputs
        # are on disk and valid.
        try:
            vt_out, vt_warn = valley_types_step(
                terrain, network.segments, classified.segments,
                geology=geology, fans=fans,
                valley_form_spacing_m=valley_form_spacing_m,
                valley_max_search_m=valley_max_search_m,
                segment_length_m=segment_length_m, guard=guard)
            out.update(vt_out)
            warnings.extend(vt_warn)
            if vt_out.get("classified_segments"):
                out["classified"]["segments"] = vt_out["classified_segments"]
        except Exception as exc:                             # noqa: BLE001
            warnings.append(
                f"Valley types could not be computed ({type(exc).__name__}: "
                f"{exc}). Everything above is unaffected; run "
                f"api.valley_types_for_run on this folder to see the full "
                f"error."
            )

    if stream_types:
        note("STEP 6/6  Rosgen stream types")
        try:
            from .streamtype import rosgen_stream_types

            st = rosgen_stream_types(
                terrain.dem, out["classified"]["segments"], dem_fine=dem_fine,
                geology=geology, regional_curve=regional_curve,
                section_plots=section_plots,
                bankfull_method=bankfull_method, guard=guard)
            out["stream_types"] = st.as_dict()
            warnings.extend(st.warnings)
            note(f"  stream types: {st.type_counts}; typed on "
                 f"{st.basis_counts}"
                 + ("" if st.resolvable else
                    "  (most channels under three cells — dem_fine= "
                    "measures more of them)"))
        except Exception as exc:                             # noqa: BLE001
            warnings.append(
                f"Stream types could not be computed ({type(exc).__name__}: "
                f"{exc}). Everything above is unaffected."
            )

    # The point of both classifications: is each reach's stream type a
    # stable form for its valley type?
    if out.get("stream_types") and out.get("valley_types"):
        note("       stable or unstable form (stream type vs valley type)")
        try:
            from .stability import stream_stability
            out["stability"] = stream_stability(
                out["valley_types"]["segments"],
                out["stream_types"]["segments"], guard=guard)
        except Exception as exc:                             # noqa: BLE001
            warnings.append(
                f"Stable/unstable form could not be judged "
                f"({type(exc).__name__}: {exc}); run "
                f"api.stream_stability(valley_types, stream_types) to see why."
            )

    elapsed = time.monotonic() - t0
    note(f"landscape pass complete in {elapsed:.1f}s")
    out["seconds"] = elapsed
    out["warnings"] = warnings
    return out


def valley_types_step(
    terrain: Any,
    network_segments: str,
    classified_segments: str,
    geology: str | None = None,
    fans: bool | str | None = True,
    valley_form_spacing_m: float | None = None,
    valley_max_search_m: float | None = None,
    segment_length_m: float = 500.0,
    guard: Guard | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Valley form floor-to-divide, alluvial fans, Rosgen valley types and the
    valley as an area. Step 5 of the landscape pass, callable on its own.

    ``fans``: True searches for them, a path uses that fan layer, False or
    None skips them (Type III then stays unresolved).

    Returns ``(outputs, warnings)`` with keys ``valley_form``, ``fans``,
    ``valley_types`` and ``valley_extent``.
    """
    from .rosgen import rosgen_valley_types
    from .valleyform import valley_polygons, valley_form as _valley_form

    guard = guard or Guard.from_env()
    out: dict[str, Any] = {}
    warnings: list[str] = []

    spacing = (valley_form_spacing_m if valley_form_spacing_m is not None
               else max(segment_length_m / 2.0, terrain.cell_size * 10))
    search = (valley_max_search_m if valley_max_search_m is not None
              else _derive_search_m(terrain))
    vform = _valley_form(terrain.dem, network_segments,
                         segments=classified_segments, spacing_m=spacing,
                         max_search_m=search, guard=guard)
    out["valley_form"] = vform.as_dict()
    warnings.extend(vform.warnings)

    rate = vform.n_measured / max(vform.n_sections, 1)
    note(f"  {vform.n_measured}/{vform.n_sections} sections reached "
         f"both divides ({rate:.0%})")

    # Confinement from the floor the sections measured, not the HAND area
    # (v0.38). The layer every later step reads is the rewritten one.
    try:
        from .classify import confinement_from_form

        cf = confinement_from_form(classified_segments, vform.segment_table,
                                   guard=guard)
        classified_segments = cf["segments"]
        out["confinement_form"] = cf
        out["classified_segments"] = classified_segments
    except Exception as exc:                                 # noqa: BLE001
        warnings.append(
            f"Confinement could not be measured from the valley floor "
            f"({type(exc).__name__}: {exc}); the HAND-based classes stand.")
    if rate < 0.5:
        warnings.append(
            f"Only {rate:.0%} of valley sections reached a divide on both "
            f"sides within max_search_m={search or 0:,.0f} m. The rest are "
            f"LOWER BOUNDS, not measurements — valley width can only be "
            f"larger and side slopes gentler than reported. Raise "
            f"valley_max_search_m, or clip to terrain whose divides are "
            f"inside the DEM."
        )

    # Alluvial fans: the one valley type that is detected, not inferred.
    # Without them every steep-but-unconfined reach stays an unresolved
    # III/VII. A failure here costs only that.
    fans_path = fans if isinstance(fans, (str, Path)) else None
    if fans is True:
        note("  alluvial fans")
        try:
            fr = _fans_for_pass(terrain, guard)
            out["fans"] = fr.as_dict()
            warnings.extend(fr.warnings)
            note(f"  fans: {fr.fan_count}, {fr.total_area_km2:.2f} km2"
                 + (f" ({fr.rejected_count} candidate(s) failed the cone "
                    f"test)" if fr.rejected_count else ""))
            if fr.fan_count:
                fans_path = fr.fans
        except Exception as exc:                             # noqa: BLE001
            warnings.append(
                f"The alluvial fan search failed ({type(exc).__name__}: "
                f"{exc}). Valley types ran without it, so Type III stays "
                f"unresolved.")

    vtypes = rosgen_valley_types(
        classified_segments, geology=geology,
        fans=str(fans_path) if fans_path else None,
        valley_form=vform.segment_table, guard=guard)
    out["valley_types"] = vtypes.as_dict()
    warnings.extend(vtypes.warnings)
    note(f"  valley types: {vtypes.type_counts}")

    # The valley as an AREA, not a classified stream line. Everything above
    # produces a table about a valley attached to the channel down its
    # middle; opened on a map that is a coloured stream, which is not a
    # valley and does not look like one. The area is the valley bottom the
    # evidence found, split between the reaches (v0.38) -- not the
    # catchment, which is a watershed (valley_catchments still draws those).
    try:
        out["valley_extent"] = valley_polygons(
            _valley_alloc_for(classified_segments), attributes=vtypes.segments,
            guard=guard)
    except Exception as exc:                                 # noqa: BLE001
        warnings.append(
            f"Valley extent polygons could not be built "
            f"({type(exc).__name__}: {exc}). The per-segment valley types "
            f"are unaffected."
        )
    return out, warnings


def _valley_alloc_for(classified_segments: str | Path) -> Path:
    """The valley allocation raster of a classified (or reclassified)
    segments layer: beside it, or in the pass's classified_dir."""
    seg = Path(classified_segments)
    for d in (seg.parent, seg.parent.parent / "classified_dir"):
        if (d / "valley_alloc.tif").exists():
            return d / "valley_alloc.tif"
    raise GeolensError(f"no valley_alloc.tif beside {seg} (a pass made "
                       f"before v0.38 has none; run step 1 again)")


# The fan search holds about ten full-size arrays and grows each fan cell by
# cell, so above this it runs on a coarser copy of the DEM. Fans are hundreds
# of metres to kilometres across: 5-10 m cells lose nothing that matters.
FAN_MAX_CELLS = 60_000_000
FAN_MIN_CELL_M = 5.0


def coarsen_dem(dem: str | Path, max_cells: int, guard: Guard,
                name: str = "coarse_dem", min_cell_m: float = 0.0) -> tuple[str, float]:
    """Write an averaged copy of ``dem`` with at most ``max_cells`` cells.

    Returns ``(path, cell_size_m)``. Cell sizes are worked out in TRUE metres
    (a foot grid is converted), and the copy keeps the CRS and nodata.
    """
    import math

    import rasterio
    from rasterio.enums import Resampling

    from ..raster import gtiff_profile
    from ..units import unit_info

    with rasterio.open(str(dem)) as src:
        u = unit_info(src.crs, abs(src.res[0]), abs(src.res[1]))
        cells = src.width * src.height
        factor = max(1.0, math.sqrt(cells / max_cells),
                     (min_cell_m / u.cell_size_m) if min_cell_m else 1.0)
        w = max(1, int(src.width / factor))
        h = max(1, int(src.height / factor))
        note(f"  averaging {src.width:,}x{src.height:,} to {w:,}x{h:,} cells "
             f"({u.cell_size_m * factor:.1f} m)")
        arr = src.read(1, out_shape=(h, w), resampling=Resampling.average,
                       masked=True)
        from affine import Affine
        transform = src.transform * Affine.scale(src.width / w,
                                                 src.height / h)
        nd = src.nodata if src.nodata is not None else -9999.0
        prof = gtiff_profile(src.profile.copy(), dtype="float32", nodata=nd,
                             width=w, height=h, transform=transform)
    out_dir = Path(guard.write_path(f"{name}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{Path(str(dem)).stem}_{u.cell_size_m * factor:.0f}m.tif"
    with rasterio.open(str(path), "w", **prof) as dst:
        dst.write(arr.filled(nd).astype("float32"), 1)
    return str(path), u.cell_size_m * factor


def _fans_for_pass(terrain: Any, guard: Guard) -> Any:
    """Fans on the pass's own terrain, or on a coarser copy when it is big."""
    from .fans import detect_alluvial_fans

    if int(terrain.cells) <= FAN_MAX_CELLS:
        return detect_alluvial_fans(terrain, guard=guard)
    path, cell_m = coarsen_dem(terrain.dem, FAN_MAX_CELLS, guard,
                               name="fans_dem", min_cell_m=FAN_MIN_CELL_M)
    note(f"  fan search on a {cell_m:.1f} m copy ({int(terrain.cells):,} "
         f"cells is too many for it)")
    coarse = build_terrain(path, stream_threshold_km2=0.5,
                           out_prefix="fans_terrain", guard=guard)
    return detect_alluvial_fans(coarse, guard=guard)


def _derive_search_m(terrain: Any) -> float:
    """How far to look for the divide, from the DEM's own size.

    Generous on purpose. ``_find_divide`` takes the FIRST crest walking out
    from the floor, so a wider search can never find a wronger divide — only
    let more sections find one at all, at the cost of samples rather than
    correctness. A sixth of the shorter side was tried first and gave 2,333 m
    on a fixture whose divides sit up to 3,400 m out: 20% of sections measured
    instead of 100%.
    """
    import rasterio

    with rasterio.open(str(terrain.dem)) as src:
        short = min(src.width, src.height) * float(terrain.cell_size)
    return float(min(6000.0, max(500.0, short / 3.0)))
