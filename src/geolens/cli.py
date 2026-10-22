"""Command-line interface. Same capabilities as the MCP server."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import api
from .safety import GeolensError


def _print(obj: Any, compact: bool = False) -> None:
    if compact and isinstance(obj, dict):
        obj = {k: v for k, v in obj.items() if not isinstance(v, list)}
    json.dump(obj, sys.stdout, indent=1, default=str)
    sys.stdout.write("\n")


def project_folder(boundary: str, out: str | None = None):
    """Where ``geolens project`` writes: ``--out``, else a folder named for
    the boundary file beside it, else (a map service) ``geolens_project`` in
    the current folder."""
    from pathlib import Path

    if out:
        return Path(out).expanduser().resolve()
    if str(boundary).lower().startswith(("http://", "https://")):
        return Path.cwd().resolve() / "geolens_project"
    b = Path(boundary).expanduser().resolve()
    return b.parent / f"{b.stem}_geolens"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="geolens",
        description=(
            "Inspect geospatial data and extract channel geometry from a DEM. "
            "Source data is never modified; outputs go to GEOLENS_OUT."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list", help="catalogue datasets in a folder")
    s.add_argument("path")
    s.add_argument("--flat", action="store_true", help="do not recurse")

    s = sub.add_parser("describe", help="describe a vector layer or raster")
    s.add_argument("path")
    s.add_argument("--layer")
    s.add_argument("--raster", action="store_true", help="treat as raster")

    s = sub.add_parser("attrs", help="attribute statistics for a vector layer")
    s.add_argument("path")
    s.add_argument("--layer")
    s.add_argument("--fields", nargs="*")

    s = sub.add_parser("sample", help="show real attribute rows")
    s.add_argument("path")
    s.add_argument("--layer")
    s.add_argument("-n", type=int, default=5)
    s.add_argument("--where", help="pandas query expression")

    s = sub.add_parser("profile", help="longitudinal profile along a centerline")
    s.add_argument("dem")
    s.add_argument("centerline")
    s.add_argument("--spacing", type=float, default=10.0)
    s.add_argument("--layer", dest="line_layer")
    s.add_argument("--plot", action="store_true")
    s.add_argument("--csv", action="store_true")

    s = sub.add_parser("sections", help="perpendicular cross-sections from a DEM")
    s.add_argument("dem")
    s.add_argument("centerline")
    s.add_argument("--spacing", type=float, default=25.0)
    s.add_argument("--width", type=float, default=60.0)
    s.add_argument("--point-spacing", type=float, default=None)
    s.add_argument("--layer", dest="line_layer")
    s.add_argument("--plot", action="store_true")
    s.add_argument("--csv", action="store_true")

    s = sub.add_parser(
        "delineate", help="channel / bank / floodplain delineation by slope break"
    )
    s.add_argument("dem")
    s.add_argument("centerline")
    s.add_argument("--spacing", type=float, default=25.0)
    s.add_argument("--width", type=float, default=160.0)
    s.add_argument("--bank-slope", type=float, default=0.15)
    s.add_argument("--flat-slope", type=float, default=0.05)
    s.add_argument("--valley-slope", type=float, default=0.20)
    s.add_argument("--min-bank-height", type=float, default=0.3)
    s.add_argument("--run-length", type=int, default=2)
    s.add_argument("--smooth-window", type=int, default=3)
    s.add_argument("--max-floodplain-offset", type=float, default=None)
    s.add_argument("--point-spacing", type=float, default=None)
    s.add_argument("--layer", dest="line_layer")
    s.add_argument("--plot", action="store_true")
    s.add_argument("--csv", action="store_true")
    s.add_argument("--summary", action="store_true", help="omit per-section detail")

    s = sub.add_parser("map", help="plan-view map PNG")
    s.add_argument("--dem")
    s.add_argument("--centerline")
    s.add_argument("--overlay", action="append", dest="overlays")
    s.add_argument("--name", default="map.png")

    s = sub.add_parser(
        "landscape", help="landscape-scale valley delineation and confinement"
    )
    s.add_argument("dem")
    s.add_argument("--nhd", help="NHD flowlines to join identity from")
    s.add_argument("--stream-threshold-km2", type=float, default=1.0)
    s.add_argument("--segment-length", type=float, default=500.0)
    s.add_argument("--channel-width", type=float, default=None)
    s.add_argument("--confined-max", type=float, default=2.0)
    s.add_argument("--partly-confined-max", type=float, default=5.0)
    s.add_argument("--backend", default="auto",
                   choices=["auto", "pysheds", "whitebox"])
    s.add_argument("--no-valley-types", action="store_true",
                   help="skip valley form and Rosgen valley types (the "
                        "slowest step after routing)")
    s.add_argument("--geology", default=None,
                   help="geology polygons; resolves VIII/IX and X/XI, which "
                        "shape alone cannot")
    s.add_argument("--valley-search-m", type=float, default=None,
                   help="how far to look for the divide (default: from the DEM)")

    s = sub.add_parser("breaks", help="compare candidate confinement breaks")
    s.add_argument("segments")

    s = sub.add_parser("reclassify", help="apply new confinement breaks (no re-run)")
    s.add_argument("segments")
    s.add_argument("--confined-max", type=float, required=True)
    s.add_argument("--partly-confined-max", type=float, required=True)
    s.add_argument("--out-prefix", default="reclassified")

    s = sub.add_parser("calibrate-cycle", help="one round of the calibration loop")
    s.add_argument("segments")
    s.add_argument("--dataset", default=None)
    s.add_argument("--field", default="confinement")
    s.add_argument("--value-field", default="confinement_ratio")
    s.add_argument("--n-suggest", type=int, default=20)
    s.add_argument("--out-prefix", default="calibration")

    s = sub.add_parser("ingest-labels", help="read a filled worksheet into the store")
    s.add_argument("worksheet")
    s.add_argument("--dataset", required=True)
    s.add_argument("--field", default="confinement")
    s.add_argument("--author", default="")

    sub.add_parser("calibration-history", help="calibration cycles run here")

    s = sub.add_parser("naip", help="stream NAIP and build 1 m NDVI")
    s.add_argument("dem", nargs="?", help="DEM defining the grid")
    s.add_argument("--start", default=None)
    s.add_argument("--end", default=None)
    s.add_argument("--no-rgb", action="store_true")
    s.add_argument("--no-cache", action="store_true")
    s.add_argument("--resolution-m", type=float, default=1.0,
                   help="NDVI cell size (default 1 m, NAIP's own). "
                        "Pass 0 to build on the DEM's grid instead.")
    s.add_argument("--out-prefix", default="naip")
    s.add_argument("--selftest", action="store_true")

    s = sub.add_parser("riparian", help="riparian band from an NDVI raster")
    s.add_argument("ndvi")
    s.add_argument("--hand", default=None)
    s.add_argument("--streams", default=None)
    s.add_argument("--background-window-m", type=float, default=2000.0)
    s.add_argument("--min-excess", type=float, default=0.12)
    s.add_argument("--max-hand-m", type=float, default=15.0)
    s.add_argument("--out-prefix", default="riparian")

    s = sub.add_parser("inundation", help="observed flood extent for a DEM's area")
    s.add_argument("dem", nargs="?", help="DEM defining the grid (omit with --selftest)")
    s.add_argument("--start", default="2015-01-01")
    s.add_argument("--end", default=None)
    s.add_argument("--out-prefix", default="inundation")
    s.add_argument("--selftest", action="store_true",
                   help="check the flood service is reachable and correct")

    s = sub.add_parser("validate-floodplain",
                       help="score a floodplain against observed flood extent")
    s.add_argument("floodplain", help="floodplain polygons")
    s.add_argument("--flood-envelope", required=True)
    s.add_argument("--valid-mask", required=True)

    s = sub.add_parser("valleyform",
                       help="valley cross-section form, floor to divide")
    s.add_argument("dem")
    s.add_argument("centerline")
    s.add_argument("--segments", default=None,
                   help="classified segments, so sections carry a segment id")
    s.add_argument("--id-field", default=None)
    s.add_argument("--spacing-m", type=float, default=250.0)
    s.add_argument("--max-search-m", type=float, default=3000.0)
    s.add_argument("--min-rise-m", type=float, default=5.0)
    s.add_argument("--out-prefix", default="valley_form")

    sub.add_parser("compare-backends",
                   help="check pysheds and whitebox agree on this machine")

    s = sub.add_parser("valleytypes", help="Rosgen valley types for classified segments")
    s.add_argument("segments", help="segments_classified.gpkg from the landscape pass")
    s.add_argument("--geology", default=None, help="geology.gpkg from `geolens geology`")
    s.add_argument("--fans", default=None, help="fans.gpkg from `geolens fans`")
    s.add_argument("--valley-form", default=None,
                   help="valley_form_segments.csv from `geolens valleyform`")
    s.add_argument("--buffer-m", type=float, default=200.0)
    s.add_argument("--min-cover", type=float, default=0.25)
    s.add_argument("--out-prefix", default="valley_types")

    s = sub.add_parser("fans", help="find alluvial fans at canyon mouths")
    s.add_argument("dem")
    s.add_argument("--stream-threshold-km2", type=float, default=0.1)
    s.add_argument("--fan-slope-min", type=float, default=0.02)
    s.add_argument("--fan-slope-max", type=float, default=0.15)
    s.add_argument("--min-drainage-km2", type=float, default=0.5)
    s.add_argument("--max-radius-m", type=float, default=1500.0)
    s.add_argument("--out-prefix", default="fans")

    s = sub.add_parser(
        "watershed",
        help="clip a DEM to one complete watershed (the unit to work in)")
    s.add_argument("dem")
    s.add_argument("--boundary", default=None,
                   help="a polygon you already have (WBD HUC12, permit area)")
    s.add_argument("--pour-point", default=None,
                   help="'x,y' -- the contributing area is delineated for you")
    s.add_argument("--pour-crs", default=None,
                   help="CRS of --pour-point (e.g. EPSG:4326 for lon/lat)")
    s.add_argument("--huc", default=None,
                   help="'auto' or a HUC code, fetched from the USGS WBD")
    s.add_argument("--buffer-m", type=float, default=0.0)
    s.add_argument("--snap-radius-m", type=float, default=150.0)
    s.add_argument("--no-verify-inflow", action="store_true",
                   help="skip checking whether --boundary really is a watershed")
    s.add_argument("--run", action="store_true",
                   help="also run the landscape pass on the clipped watershed")
    s.add_argument("--stream-threshold-km2", type=float, default=1.0)
    s.add_argument("--segment-length-m", type=float, default=1000.0)
    s.add_argument("--out-prefix", default="watershed")

    s = sub.add_parser(
        "plan", help="will a landscape pass fit in memory? run this FIRST")
    s.add_argument("dem")
    s.add_argument("--dtype", default="float32")

    s = sub.add_parser("gullies", help="find gullies on a DEM")
    s.add_argument("dem", help="a DEM, or a runs/ record from a landscape pass")
    s.add_argument("--slope-std-min", type=float, default=None,
                   help="roughness threshold; omitted, it is chosen from the "
                        "shape of the distribution. Do not pass a percentile: "
                        "p90 admits 10%% of the DEM and p99 finds nothing")
    s.add_argument("--tpi-max", type=float, default=-0.3)
    s.add_argument("--min-drainage-km2", type=float, default=0.005)
    s.add_argument("--max-drainage-km2", type=float, default=0.5,
                   help="the only criterion that means SMALL; 0 disables it "
                        "and you will get the drainage network back")
    s.add_argument("--min-slope", type=float, default=0.02)
    s.add_argument("--stream-threshold-km2", type=float, default=None,
                   help="set equal to --max-drainage-km2 so streams and "
                        "gullies partition cleanly")
    s.add_argument("--exclude-streams", action="store_true")
    s.add_argument("--window-m", type=float, default=None)
    s.add_argument("--section-width-m", type=float, default=None)
    s.add_argument("--min-length-m", type=float, default=None)
    s.add_argument("--exclude", default=None,
                   help="a roads (or other not-a-gully) layer; lines lying "
                        "along it are set aside into gullies_excluded.gpkg")
    s.add_argument("--exclude-buffer-m", type=float, default=10.0)
    s.add_argument("--out-prefix", default="gullies")

    s = sub.add_parser("geology", help="fetch geology polygons for a shapefile's extent")
    s.add_argument("path", nargs="?", help="AOI vector file (omit with --selftest)")
    s.add_argument("--buffer-m", type=float, default=1000.0)
    s.add_argument("--service", default="surface",
                   choices=["surface", "quaternary", "sgmc"])
    s.add_argument("--out-prefix", default="geology")
    s.add_argument("--selftest", action="store_true",
                   help="check the geology services are reachable and correct")

    s = sub.add_parser("headcuts", help="find headcuts/knickpoints on a DEM")
    s.add_argument("dem")
    s.add_argument("--lines", default=None,
                   help="restrict to these lines (use gully_lines) -- recommended")
    s.add_argument("--stream-threshold-km2", type=float, default=0.005)
    s.add_argument("--tolerance-m", type=float, default=1.0)
    s.add_argument("--window-m", type=float, default=12.0)
    s.add_argument("--min-excess-drop-m", type=float, default=0.8)
    s.add_argument("--out-prefix", default="headcuts")

    s = sub.add_parser(
        "project",
        help="a whole project from one boundary: valleys everywhere, "
             "channels on lidar; runs on its own and resumes")
    s.add_argument("boundary", help="a boundary layer (shp, gpkg, geojson, "
                   "kml; lines and points are buffered) or an ArcGIS REST "
                   "layer URL")
    s.add_argument("--where", default=None,
                   help="which features of a map service layer, e.g. "
                        "ADM_UNIT_CD='AZC01000'")
    s.add_argument("--out", default=None,
                   help="project folder (default: <boundary name>_geolens "
                        "beside a boundary file)")
    s.add_argument("--level-b", default="pilot",
                   choices=["pilot", "all", "listed", "none"])
    s.add_argument("--pilots", type=int, default=3)
    s.add_argument("--pilot-by", default="representative",
                   choices=["representative", "unconfined"])
    s.add_argument("--huc12s", default=None,
                   help="comma-separated HUC12 codes for --level-b listed")
    s.add_argument("--geology", default="ssurgo",
                   choices=["ssurgo", "auto", "none"],
                   help="valley material: SSURGO soils (default), USGS "
                        "geology, or none")
    s.add_argument("--curve", default="auto", help="regional bankfull curve")
    s.add_argument("--threshold-km2", type=float, default=1.0)
    s.add_argument("--hours", type=float, default=None,
                   help="stop level B after this many hours")
    s.add_argument("--no-plots", action="store_true")
    s.add_argument("--no-soils", action="store_true")
    s.add_argument("--data", default="all",
                   help="watershed data per HUC12: all (default), none, or "
                        "some of fire,ecosites,prism,atlas14,rap")
    s.add_argument("--no-calibration", action="store_true",
                   help="keep the published confinement breaks instead of "
                        "fitting them to the lidar pilots")
    s.add_argument("--keep-lidar-dem", action="store_true",
                   help="keep every HUC12's 1 m mosaic (about 300 MB each) "
                        "after its channels are measured")
    s.add_argument("--major-km2", type=float, default=25.0,
                   help="drainage at the mouth a stream needs to be in "
                        "major_streams (default 25)")
    s.add_argument("--weights", default=None,
                   help="JSON file of HUC12 summary column -> weight for "
                        "the sensitivity table (default: the built-in set; "
                        "merged/sensitivity_weights.json shows it)")
    s.add_argument("--cell-a", type=float, default=None,
                   help="level A grid in metres (default 10; 20 or 30 for a "
                        "reconnaissance run on a laptop or a cloud runner)")

    sub.add_parser("backends", help="show which flow-routing backends are installed")
    s = sub.add_parser("history", help="what has been run in this output directory")
    s.add_argument("--limit", type=int, default=25)
    s.add_argument("--function", default=None)

    sub.add_parser("workspace", help="show output directory and read roots")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "list":
            _print(api.list_datasets(args.path, recursive=not args.flat))

        elif args.cmd == "describe":
            if args.raster or args.path.lower().endswith(
                (".tif", ".tiff", ".img", ".vrt", ".asc", ".dem")
            ):
                _print(api.describe_raster(args.path))
            else:
                _print(api.describe_vector(args.path, layer=args.layer))

        elif args.cmd == "history":
            _print(api.run_history(limit=args.limit, function=args.function))

        elif args.cmd == "headcuts":
            _print(api.detect_headcuts(
                args.dem, lines=args.lines,
                stream_threshold_km2=args.stream_threshold_km2,
                tolerance_m=args.tolerance_m, window_m=args.window_m,
                min_excess_drop_m=args.min_excess_drop_m,
                out_prefix=args.out_prefix))

        elif args.cmd == "breaks":
            _print(api.confinement_break_sweep(args.segments))

        elif args.cmd == "reclassify":
            _print(api.reclassify_confinement(
                args.segments, confined_max=args.confined_max,
                partly_confined_max=args.partly_confined_max,
                out_prefix=args.out_prefix))

        elif args.cmd == "calibrate-cycle":
            _print(api.calibration_cycle(
                args.segments, dataset=args.dataset, field_name=args.field,
                value_field=args.value_field, n_suggest=args.n_suggest,
                out_prefix=args.out_prefix))

        elif args.cmd == "ingest-labels":
            _print(api.ingest_labels(args.worksheet, dataset=args.dataset,
                                     field_name=args.field, author=args.author))

        elif args.cmd == "calibration-history":
            _print(api.calibration_history())

        elif args.cmd == "naip":
            if args.selftest:
                _print(api.naip_selftest())
            elif not args.dem:
                raise SystemExit("naip: give a DEM, or use --selftest")
            else:
                _print(api.naip_imagery(
                    args.dem, start=args.start, end=args.end,
                    want_rgb=not args.no_rgb, use_cache=not args.no_cache,
                    resolution_m=args.resolution_m or None,
                    out_prefix=args.out_prefix))

        elif args.cmd == "riparian":
            _print(api.riparian_band(
                args.ndvi, hand=args.hand, streams=args.streams,
                background_window_m=args.background_window_m,
                min_excess=args.min_excess, max_hand_m=args.max_hand_m,
                out_prefix=args.out_prefix))

        elif args.cmd == "inundation":
            if args.selftest:
                _print(api.inundation_selftest())
            elif not args.dem:
                raise SystemExit("inundation: give a DEM, or use --selftest")
            else:
                _print(api.fetch_inundation(args.dem, start=args.start,
                                            end=args.end,
                                            out_prefix=args.out_prefix))

        elif args.cmd == "validate-floodplain":
            _print(api.validate_floodplain(
                args.floodplain,
                {"flood_envelope": args.flood_envelope,
                 "valid_mask": args.valid_mask}))

        elif args.cmd == "valleytypes":
            _print(api.rosgen_valley_types(
                args.segments, geology=args.geology, fans=args.fans,
                valley_form=args.valley_form,
                buffer_m=args.buffer_m, min_cover=args.min_cover,
                out_prefix=args.out_prefix))

        elif args.cmd == "valleyform":
            _print(api.valley_form(
                args.dem, args.centerline, segments=args.segments,
                id_field=args.id_field, spacing_m=args.spacing_m,
                max_search_m=args.max_search_m, min_rise_m=args.min_rise_m,
                out_prefix=args.out_prefix))

        elif args.cmd == "compare-backends":
            _print(api.compare_backends())

        elif args.cmd == "fans":
            _print(api.detect_alluvial_fans(
                args.dem, stream_threshold_km2=args.stream_threshold_km2,
                fan_slope_min=args.fan_slope_min,
                fan_slope_max=args.fan_slope_max,
                min_drainage_km2=args.min_drainage_km2,
                max_radius_m=args.max_radius_m, out_prefix=args.out_prefix))

        elif args.cmd == "watershed":
            if args.run:
                _print(api.watershed_pass(
                    args.dem, boundary=args.boundary,
                    pour_point=args.pour_point, pour_crs=args.pour_crs,
                    huc=args.huc, buffer_m=args.buffer_m,
                    stream_threshold_km2=args.stream_threshold_km2,
                    segment_length_m=args.segment_length_m,
                    out_prefix=args.out_prefix))
            else:
                _print(api.watershed(
                    args.dem, boundary=args.boundary,
                    pour_point=args.pour_point, pour_crs=args.pour_crs,
                    huc=args.huc, buffer_m=args.buffer_m,
                    snap_radius_m=args.snap_radius_m,
                    verify_inflow=not args.no_verify_inflow,
                    out_prefix=args.out_prefix))

        elif args.cmd == "plan":
            _print(api.plan_landscape_run(args.dem, dtype=args.dtype))

        elif args.cmd == "gullies":
            _src: "str | dict" = args.dem
            if str(args.dem).endswith(".json"):
                # A runs/ record from an earlier landscape pass: reuse its
                # routing instead of spending hours re-deriving it.
                _src = api.load_run(args.dem)["result"]
            _print(api.detect_gullies(
                _src, slope_std_min=args.slope_std_min, tpi_max=args.tpi_max,
                min_drainage_km2=args.min_drainage_km2,
                max_drainage_km2=(None if args.max_drainage_km2 == 0
                                  else args.max_drainage_km2),
                min_slope=args.min_slope,
                stream_threshold_km2=args.stream_threshold_km2,
                exclude_streams=args.exclude_streams,
                window_m=args.window_m, section_width_m=args.section_width_m,
                min_length_m=args.min_length_m, exclude=args.exclude,
                exclude_buffer_m=args.exclude_buffer_m,
                out_prefix=args.out_prefix))

        elif args.cmd == "geology":
            if args.selftest:
                _print(api.geology_selftest())
            elif not args.path:
                raise SystemExit("geology: give an AOI file, or use --selftest")
            else:
                _print(api.fetch_geology(
                    args.path, buffer_m=args.buffer_m, service=args.service,
                    out_prefix=args.out_prefix))

        elif args.cmd == "attrs":
            _print(
                api.attribute_summary(
                    args.path, layer=args.layer, fields=args.fields or None
                )
            )

        elif args.cmd == "sample":
            _print(
                api.sample_features(
                    args.path, layer=args.layer, n=args.n, where=args.where
                )
            )

        elif args.cmd == "profile":
            r = api.longitudinal_profile(
                args.dem, args.centerline, spacing=args.spacing,
                line_layer=args.line_layer,
            )
            out = {k: v for k, v in r.items() if k != "profile"}
            if args.plot:
                out["plot"] = api.plot_profile(r)["path"]
            if args.csv:
                out["csv"] = api.export_csv(r, "profile")["path"]
            _print(out)

        elif args.cmd == "sections":
            r = api.cross_sections(
                args.dem, args.centerline, spacing=args.spacing, width=args.width,
                point_spacing=args.point_spacing, line_layer=args.line_layer,
            )
            out = {k: v for k, v in r.items() if k != "sections"}
            out["sections"] = [
                {k: v for k, v in s.items() if k != "points"} for s in r["sections"]
            ]
            if args.plot:
                out["plots"] = [
                    api.plot_section_stack(r)["path"],
                    api.plot_cross_section(r)["path"],
                ]
            if args.csv:
                out["csv"] = api.export_csv(r, "sections")["path"]
            _print(out)

        elif args.cmd == "delineate":
            r = api.delineate_channel(
                args.dem, args.centerline, spacing=args.spacing, width=args.width,
                bank_slope=args.bank_slope, flat_slope=args.flat_slope,
                valley_slope=args.valley_slope,
                min_bank_height=args.min_bank_height, run_length=args.run_length,
                smooth_window=args.smooth_window,
                max_floodplain_offset=args.max_floodplain_offset,
                point_spacing=args.point_spacing, line_layer=args.line_layer,
            )
            out = {k: v for k, v in r.items() if k not in ("sections", "_sections")}
            if not args.summary:
                out["sections"] = [
                    {
                        "station": s["station"], "status": s["status"],
                        "bankfull": s.get("bankfull"),
                        "floodplain": s.get("floodplain"),
                    }
                    for s in r["sections"]
                ]
            if args.plot:
                out["plots"] = [
                    api.plot_delineation(r)["path"],
                    api.plot_section_stack(r["_sections"])["path"],
                ]
            if args.csv:
                out["csv"] = api.export_csv(r, "delineation")["path"]
            _print(out)

        elif args.cmd == "map":
            _print(
                api.plot_map(
                    dem=args.dem, centerline=args.centerline,
                    overlays=args.overlays, name=args.name,
                )
            )

        elif args.cmd == "landscape":
            r = api.landscape_pass(
                args.dem, nhd=args.nhd,
                stream_threshold_km2=args.stream_threshold_km2,
                segment_length_m=args.segment_length,
                channel_width_m=args.channel_width,
                confined_max=args.confined_max,
                partly_confined_max=args.partly_confined_max,
                backend=args.backend,
                valley_types=not args.no_valley_types,
                geology=args.geology,
                valley_max_search_m=args.valley_search_m,
            )
            _print({
                "seconds": round(r["seconds"], 1),
                "segments": r["classified"]["segments"],
                "table": r["classified"]["table"],
                "valley_types": (r.get("valley_types") or {}).get("segments"),
                "type_counts": (r.get("valley_types") or {}).get("type_counts"),
                "valley_polygons": r["valley"]["valley_polygons"],
                "hand": r["terrain"]["hand"],
                "network_km": r["network"]["total_length_km"],
                "nhd_joined": r["network"]["nhd_joined"],
                "summary": r["classified"]["summary"],
                "warnings": r["warnings"],
            })

        elif args.cmd == "project":
            import os

            out = project_folder(args.boundary, args.out)
            out.mkdir(parents=True, exist_ok=True)
            os.environ["GEOLENS_OUT"] = str(out)
            print(f"geolens project -> {out}", file=sys.stderr)
            r = api.run_project(
                args.boundary, where=args.where, level_b=args.level_b,
                pilot_count=args.pilots, pilot_by=args.pilot_by,
                huc12s=args.huc12s, stream_threshold_km2=args.threshold_km2,
                geology=None if args.geology == "none" else args.geology,
                regional_curve=args.curve, section_plots=not args.no_plots,
                level_b_hours=args.hours, soils=not args.no_soils,
                data=args.data,
                calibrate_confinement=not args.no_calibration,
                keep_lidar_dem=True if args.keep_lidar_dem else None,
                major_streams_km2=args.major_km2,
                sensitivity_weights=args.weights,
                level_a_cell_m=args.cell_a)
            _print({"project": r.get("project"),
                    "results": str(out / "RESULTS.md"),
                    "huc12_summary": r.get("huc12_summary"),
                    "major_streams": r.get("major_streams"),
                    "huc12_sensitivity": r.get("huc12_sensitivity"),
                    "level_b": r.get("level_b"),
                    "warnings": r.get("warnings"),
                    "seconds": r.get("seconds")})

        elif args.cmd == "backends":
            _print(api.landscape_backends())

        elif args.cmd == "workspace":
            _print(api.workspace())

    except GeolensError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
