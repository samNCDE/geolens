"""Smoke tests. Run: python -m pytest tests/ -q  (after generating fixtures)"""
import os, subprocess, sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent
COMPOUND = ROOT / "compound"
OUT = ROOT / "out"


@pytest.fixture(scope="session", autouse=True)
def fixtures():
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    if not (COMPOUND / "compound_dem.tif").exists():
        subprocess.run([sys.executable, str(ROOT / "make_compound.py")], check=True)


def test_describe_raster():
    from geolens import api
    r = api.describe_raster(str(COMPOUND / "compound_dem.tif"))
    assert r["crs"]["epsg"] == 32612
    assert r["grid"]["square_cells"]
    assert r["bands"][0]["stats"]["min"] < r["bands"][0]["stats"]["max"]


def test_profile_slope():
    from geolens import api
    r = api.longitudinal_profile(
        str(COMPOUND / "compound_dem.tif"), str(COMPOUND / "centerline.shp"), spacing=5
    )
    assert r["valid_samples"] > 100
    assert 0.002 < r["reach_slope"] < 0.006


def test_sections_thalweg_centered():
    from geolens import api
    r = api.cross_sections(
        str(COMPOUND / "compound_dem.tif"), str(COMPOUND / "centerline.shp"),
        spacing=25, width=60,
    )
    offs = [s["thalweg_offset"] for s in r["sections"] if s["thalweg_offset"] is not None]
    assert offs and max(abs(o) for o in offs) < 8.0


def test_delineation_matches_truth():
    """Compound fixture: bankfull W 16 m, floodplain W 110 m, all confined."""
    import statistics as st
    from geolens import api
    from geolens.morphology import delineate
    from geolens.sections import extract_cross_sections

    secs = extract_cross_sections(
        str(COMPOUND / "compound_dem.tif"), str(COMPOUND / "centerline.shp"),
        spacing=5, width=200,
    )
    d = delineate(secs)
    low = [
        r for r, s in zip(d["sections"], secs["sections"])
        if r["status"] == "ok" and abs(s["bearing_deg"] - 90) < 12
    ]
    assert len(low) > 10
    bw = st.median([r["bankfull"]["width"] for r in low])
    fw = st.median([r["floodplain"]["total_width"] for r in low])
    er = st.median([r["floodplain"]["entrenchment_ratio"] for r in low])
    assert abs(bw - 16.0) / 16.0 < 0.06
    assert abs(fw - 110.0) / 110.0 < 0.03
    assert abs(er - 6.875) / 6.875 < 0.06
    assert all(
        r["floodplain"]["confined_left"] and r["floodplain"]["confined_right"]
        for r in low
    )


def test_read_only_guard():
    from geolens.safety import Guard, ReadOnlyViolation
    g = Guard(roots=[ROOT], out_dir=ROOT / "nested")
    with pytest.raises(ReadOnlyViolation):
        g.write_path("x.csv")


def test_valley_slope_validation():
    from geolens.morphology import delineate
    from geolens.safety import GeolensError
    with pytest.raises(GeolensError):
        delineate({"sections": [{"station": 0, "points": [], "thalweg_offset": 0}]},
                  flat_slope=0.2, valley_slope=0.1)


def test_plots_render():
    from geolens import api
    d = api.delineate_channel(
        str(COMPOUND / "compound_dem.tif"), str(COMPOUND / "centerline.shp"),
        spacing=50, width=160,
    )
    for fn, kw in (
        (api.plot_delineation, {"name": "t_del.png"}),
        (api.plot_section_stack, {}),
    ):
        pass
    assert Path(api.plot_delineation(d, name="t_del.png")["path"]).exists()
    assert Path(api.plot_section_stack(d["_sections"], name="t_stack.png")["path"]).exists()
    assert Path(api.export_csv(d, "delineation", name="t_del.csv")["path"]).exists()


# -- landscape scale ----------------------------------------------------------

LANDSCAPE = ROOT / "landscape"


@pytest.fixture(scope="session")
def landscape_fixture():
    if not (LANDSCAPE / "landscape_dem.tif").exists():
        subprocess.run([sys.executable, str(ROOT / "make_landscape.py")], check=True)
    return LANDSCAPE


def test_backends_available(landscape_fixture):
    from geolens.landscape import available_backends
    a = available_backends()
    assert a["pysheds"] or a["whitebox"], "no flow-routing backend installed"


def test_landscape_pass(landscape_fixture, valley_types=False):
    """Full pass, and valley width accurate on WIDE valleys.

    The fixture's wide segments are 1800 m and 2800 m. Narrow segments (300 m =
    10 cells at 30 m) are at the resolution limit and deliberately not asserted.
    """
    import rasterio
    import numpy as np
    from geolens import api

    r = api.landscape_pass(
        str(landscape_fixture / "landscape_dem.tif"),
        nhd=str(landscape_fixture / "nhd_flowline.shp"),
    )
    assert r["classified"]["segment_count"] > 50
    assert r["network"]["total_length_km"] > 50
    assert r["network"]["nhd_joined"] > 0, "NHD conflation matched nothing"

    s = r["classified"]["summary"]
    assert s["confinement_ratio_percentiles"] is not None
    assert s["valley_width_m"]["n"] > 50

    # Valley width against truth on the two wide segments.
    with rasterio.open(r["valley"]["valley_raster"]) as src:
        vb = src.read(1).astype(bool)
        cell = abs(src.res[0])
    for x_km, truth in ((12.0, 1800.0), (27.0, 2800.0)):
        col = int(x_km * 1000 / cell)
        det = int(vb[:, col].sum()) * cell
        assert abs(det - truth) / truth < 0.05, (
            f"valley width at {x_km} km: {det} vs truth {truth}"
        )


def test_hand_is_nonnegative_and_finite(landscape_fixture):
    import rasterio
    import numpy as np
    from geolens.landscape import build_terrain

    t = build_terrain(str(landscape_fixture / "landscape_dem.tif"))
    with rasterio.open(t.hand) as src:
        h = src.read(1, masked=True)
    assert float(h.min()) >= 0.0
    assert np.isfinite(h.compressed()).all()


# -- gullies ------------------------------------------------------------------

GULLIES = ROOT / "gullies"


@pytest.fixture(scope="session")
def gully_fixture():
    if not (GULLIES / "gully_dem.tif").exists():
        subprocess.run([sys.executable, str(ROOT / "make_gullies.py")], check=True)
    return GULLIES


def test_gully_detection_matches_truth(gully_fixture):
    """6 known gullies, 7.20 km total.

    Detection must recover the COUNT exactly (collinear merging) and at least
    85 % of total length. Each gully reads ~9 % short because its head is not
    yet incised — the detector finds the incised part, which is correct.
    """
    import geopandas as gpd
    from geolens.landscape import build_terrain, terrain_roughness, detect_gullies

    # Threshold above the gullies' drainage area, or they count as streams.
    t = build_terrain(str(gully_fixture / "gully_dem.tif"), stream_threshold_km2=0.2)
    rg = terrain_roughness(t, window_m=40)
    g = detect_gullies(t, roughness=rg, slope_std_min=0.05, tpi_max=-0.3,
                       min_length_m=100)

    assert g.gully_count == 6, f"expected 6 gullies, got {g.gully_count}"
    assert g.total_length_km >= 0.85 * 7.20, f"only {g.total_length_km:.2f} km"
    assert g.total_length_km <= 1.10 * 7.20

    gd = gpd.read_file(g.gully_lines)
    lengths = sorted(gd.length_m, reverse=True)
    truth = [1500, 1380, 1260, 1140, 1020, 900]
    for det, tru in zip(lengths, truth):
        assert 0.80 * tru <= det <= 1.05 * tru, f"{det} vs truth {tru}"


def test_gully_merge_is_angle_insensitive(gully_fixture):
    """The merge angle should not be a knob that has to be tuned."""
    from geolens.landscape import build_terrain, terrain_roughness, detect_gullies

    t = build_terrain(str(gully_fixture / "gully_dem.tif"), stream_threshold_km2=0.2)
    rg = terrain_roughness(t, window_m=40)
    counts = {
        a: detect_gullies(t, roughness=rg, slope_std_min=0.05, tpi_max=-0.3,
                          min_length_m=100, merge_max_angle_deg=a).gully_count
        for a in (30, 40, 60)
    }
    assert len(set(counts.values())) == 1, f"count varies with angle: {counts}"


def test_exclude_streams_warns_when_it_deletes_everything(gully_fixture):
    """Low stream threshold + exclude_streams removes the gullies — must warn."""
    from geolens.landscape import build_terrain, terrain_roughness, detect_gullies

    t = build_terrain(str(gully_fixture / "gully_dem.tif"), stream_threshold_km2=0.02)
    rg = terrain_roughness(t, window_m=40)
    g = detect_gullies(t, roughness=rg, slope_std_min=0.05, tpi_max=-0.3,
                       min_length_m=100, exclude_streams=True)
    if g.gully_count == 0:
        assert any("exclude_streams" in w for w in g.warnings)


def test_gully_geometry_matches_truth(gully_fixture):
    """Fixture gullies are parabolic, 14 m wide and 3.0 m deep at the axis.

    So the MEAN depth across the gully is 2/3 x 3 = 2.0 m and the MAX is 3.0.
    This test used to require mean_depth_m of 2.7-3.3, which was encoding a
    mislabel: the old section method reported the median of each section's
    maximum depth under the name mean_depth_m. The footprint measures the
    real mean, and max_depth_m carries the axis depth.
    """
    import geopandas as gpd
    import numpy as np
    from geolens.landscape import build_terrain, terrain_roughness, detect_gullies

    t = build_terrain(str(gully_fixture / "gully_dem.tif"), stream_threshold_km2=0.2)
    rg = terrain_roughness(t, window_m=40)
    g = detect_gullies(t, roughness=rg, slope_std_min=0.05, tpi_max=-0.3,
                       min_length_m=100)

    assert 1.8 <= g.mean_depth_m <= 2.2, f"mean depth {g.mean_depth_m}"
    assert 13.0 <= g.mean_width_m <= 15.0, f"mean width {g.mean_width_m}"
    assert g.eroded_volume_m3 > 0

    gd = gpd.read_file(g.gully_lines)
    assert gd["width_m"].notna().all()
    assert gd["mean_depth_m"].notna().all()
    assert np.median(gd["max_depth_m"]) == __import__("pytest").approx(3.0,
                                                                      abs=0.2)
    assert (gd["volume_m3"] > 0).all()


# -- memory planning, resampling, tiling ---------------------------------------


def test_plan_run_reports_and_advises(landscape_fixture):
    from geolens.landscape import plan_run
    p = plan_run(str(landscape_fixture / "landscape_dem.tif"))
    assert p.cells == 667_000
    assert p.peak_gb > 0
    assert p.verdict
    assert "terrain (condition + flow routing)" in p.per_stage_gb


def test_resample_reduces_cells(landscape_fixture):
    from geolens import api
    r = api.resample_dem(str(landscape_fixture / "landscape_dem.tif"), 90.0,
                         name="t_resample.tif")
    assert r["cells"] < 667_000 / 5
    assert r["cell_size_m"] == 90.0
    from geolens.landscape import plan_run
    assert plan_run(r["path"]).cells == r["cells"]


def test_resample_refuses_to_upsample(landscape_fixture):
    from geolens import api
    from geolens.safety import GeolensError
    with pytest.raises(GeolensError):
        api.resample_dem(str(landscape_fixture / "landscape_dem.tif"), 5.0)


def test_tiling_splits_into_subwatersheds(landscape_fixture):
    """Tiles must actually subdivide, and stay smaller than the whole grid.

    Also pins the documented caveat: tiling truncates drainage area, so the
    merged network is much shorter than an untiled run. That is inherent, not a
    regression — the test exists so nobody 'fixes' the warning away.
    """
    from geolens import api
    r = api.landscape_tiled(str(landscape_fixture / "landscape_dem.tif"),
                            target_area_km2=50.0, stream_threshold_km2=1.0,
                            segment_length_m=500)
    assert r["tiles"]["tile_count"] > 1, "did not subdivide"
    cells = [t["cells"] for t in r["tiles"]["tiles"]]
    assert max(cells) < 667_000, "a tile was as big as the whole grid"
    assert r["run"]["succeeded"] == r["tiles"]["tile_count"]
    assert "network_loss" in r


def test_gully_warns_on_coarse_dem(landscape_fixture):
    """A 30 m DEM cannot resolve gullies — the tool must say so."""
    from geolens.landscape import build_terrain, terrain_roughness, detect_gullies
    t = build_terrain(str(landscape_fixture / "landscape_dem.tif"),
                      stream_threshold_km2=1.0)
    rg = terrain_roughness(t, window_m=150)
    g = detect_gullies(t, roughness=rg, slope_std_min=0.01, tpi_max=-0.3,
                       min_length_m=200, section_width_m=420)
    assert any("cell size" in w for w in g.warnings), g.warnings


# -- flow routing and correct tiling -------------------------------------------


def test_flow_routing_matches_pysheds(landscape_fixture):
    """Our D8 must agree with pysheds on the major network.

    Pins the float32 epsilon fix: the fill increment has to exceed float32
    spacing at the DEM's elevation, or 'filled' cells stay exactly level, every
    cell in a flat gets no downslope neighbour, and the network shatters into
    thousands of spurious outlets.
    """
    import numpy as np
    import rasterio
    from geolens.landscape import flow_routing, build_terrain

    dem = str(landscape_fixture / "landscape_dem.tif")
    f = flow_routing(dem)
    t = build_terrain(dem, stream_threshold_km2=1.0)

    with rasterio.open(f.flow_dir) as s:
        fd = s.read(1)
    with rasterio.open(f.accumulation) as s:
        a_mine = s.read(1)
    with rasterio.open(t.accumulation) as s:
        a_ps = s.read(1)

    assert (fd == 0).mean() < 0.01, "too many cells with no downslope neighbour"
    assert abs(a_mine.max() - a_ps.max()) / a_ps.max() < 0.01

    bm, bp = a_mine > 1000, a_ps > 1000
    iou = (bm & bp).sum() / max(1, (bm | bp).sum())
    assert iou > 0.95, f"network IoU with pysheds only {iou:.2%}"


def test_complete_watershed_preserves_drainage(landscape_fixture):
    """Clipping to a complete watershed must not truncate contributing area."""
    import numpy as np
    import rasterio
    from geolens.landscape import flow_routing, complete_watersheds
    import geopandas as gpd

    dem = str(landscape_fixture / "landscape_dem.tif")
    f = flow_routing(dem)
    ws = gpd.read_file(complete_watersheds(f, target_area_km2=40.0))
    assert len(ws) >= 1
    # The fixture is one drainage, so the only complete watershed is the whole
    # grid — which is the correct answer, and what the assertion checks.
    assert ws.area_km2.sum() > 500


def test_tiling_with_precomputed_flow_matches_untiled(landscape_fixture):
    """THE test: tiled results must match an untiled run.

    Accumulation is the only quantity with a global dependency. Route once over
    the full grid, hand that to every tile, and a plain grid tiling reproduces
    the untiled network. Without it, tiles restart contributing area at zero and
    most of the network disappears.
    """
    import geopandas as gpd
    from geolens import api
    from geolens.landscape import (
        flow_routing, grid_tiles, run_tiled, merge_vector_outputs, run_landscape,
    )

    dem = str(landscape_fixture / "landscape_dem.tif")
    base = api.landscape_pass(dem, stream_threshold_km2=1.0, segment_length_m=500, valley_types=False)
    baseline_km = base["network"]["total_length_km"]

    f = flow_routing(dem)
    ts = grid_tiles(dem, tile_cells=300, buffer_cells=100)
    assert len(ts.tiles) > 1
    assert max(t["cells"] for t in ts.tiles) < 667_000

    def one(d, g):
        return run_landscape(d, stream_threshold_km2=1.0, segment_length_m=500,
                             flow=f, guard=g, valley_types=False)["classified"]["segments"]

    run = run_tiled(ts, one)
    assert run["succeeded"] >= len(ts.tiles) - 1

    ok = [r["tile_id"] for r in run["results"]]
    wk = [t["clip_geometry_wkt"] for t in ts.tiles if t["tile_id"] in ok]
    m = merge_vector_outputs([r["result"] for r in run["results"]],
                             name="t_merged.gpkg", clip_wkts=wk)

    recovery = m["total_length_km"] / baseline_km
    assert 0.9 < recovery < 1.1, (
        f"tiled network is {recovery:.1%} of untiled ({m['total_length_km']:.1f} "
        f"vs {baseline_km:.1f} km)"
    )


def test_run_tiled_gives_each_tile_its_own_output_dir(landscape_fixture):
    """Tiles sharing one output directory silently overwrite each other."""
    from geolens.landscape import grid_tiles, run_tiled

    dem = str(landscape_fixture / "landscape_dem.tif")
    ts = grid_tiles(dem, tile_cells=300, buffer_cells=50)
    seen = []

    def one(d, g):
        seen.append(str(g.out_dir))
        return str(g.out_dir)

    run_tiled(ts, one)
    assert len(set(seen)) == len(seen), "tiles shared an output directory"


# -- cross-sections as GIS features --------------------------------------------


def test_export_sections_makes_a_usable_lookup(fixtures):
    """Shapefile + table + per-section plots, joined by xs_id."""
    import geopandas as gpd
    from geolens import api

    r = api.export_cross_sections(
        str(COMPOUND / "compound_dem.tif"), str(COMPOUND / "centerline.shp"),
        spacing=25, width=160, plot="representative", n_reaches=4,
        group_by="station",
    )
    g = gpd.read_file(r["lines"])
    assert len(g) == r["section_count"] > 5
    assert g.geom_type.isin(["LineString"]).all()
    for col in ("xs_id", "station", "is_rep"):
        assert col in g.columns, f"{col} missing from the attribute table"
    assert g["xs_id"].is_unique
    assert Path(r["table"]).exists()
    # Linked to the map: a QGIS style beside the layer, and the plot of each
    # rendered section where plot_path / plot_file say it is.
    assert Path(r["lines"]).with_suffix(".qml").exists()
    rep = g[g["is_rep"] == 1]
    assert len(rep) and all(Path(p).exists() for p in rep["plot_path"])
    folder = Path(r["lines"]).parent
    assert all((folder / f).resolve() == Path(p).resolve()
               for f, p in zip(g["plot_file"], g["plot_path"]))

    one = api.plot_cross_section_by_id(r, 3)
    assert one["xs_id"] == 3
    assert Path(one["path"]).exists()


def test_representative_is_a_real_measured_section(fixtures):
    """A representative must have measured channel geometry.

    Pins the bug where a section whose delineation failed was median-filled to
    the centre of the distribution and chosen as the most typical one.
    """
    from geolens import api

    rc = api.representative_sections(
        str(COMPOUND / "compound_dem.tif"), str(COMPOUND / "centerline.shp"),
        spacing=25, width=160, n_reaches=4, group_by="station",
    )
    assert rc["reaches"] >= 2
    assert rc["total_sections"] > rc["reaches"], "no reduction achieved"
    for p in rc["representatives"]:
        assert p["width_m"] is not None and p["width_m"] > 0, (
            f"reach {p['reach']} representative xs {p['xs_id']} has no measured "
            "width — a failed section cannot represent a reach"
        )
