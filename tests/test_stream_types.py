"""Rosgen STREAM types, which are not Rosgen valley types.

The package classified valleys and then put the valley type on the stream
network, which is a category error: it labels a line with a property of the
ground either side of it. Rosgen is two classifications and this is the other
one.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from geolens.landscape import classify_stream_type
from geolens.landscape.streamtype import _floodprone_width, _sinuosity


# Rosgen's own archetypes, one per letter. If the key cannot recover these
# from their defining numbers it is not the Rosgen key.
ARCHETYPES = [
    ("Aa+", 1.1, 8.0, 1.05, 0.14),
    ("A", 1.2, 9.0, 1.10, 0.06),
    ("B", 1.8, 18.0, 1.30, 0.03),
    ("C", 3.5, 25.0, 1.60, 0.008),
    ("D", 2.0, 60.0, 1.10, 0.015),      # at 2-4% Rosgen writes Db
    ("E", 4.0, 8.0, 2.20, 0.006),
    ("F", 1.2, 22.0, 1.50, 0.010),
    ("G", 1.2, 8.0, 1.30, 0.03),
]


@pytest.mark.parametrize("want,er,wd,sin,slope", ARCHETYPES)
def test_the_key_recovers_every_archetype(want, er, wd, sin, slope):
    r = classify_stream_type(er, wd, sin, slope)
    assert r["stream_type"] == want, (
        f"{want} archetype (ER {er}, W/D {wd}, sinuosity {sin}, slope "
        f"{slope:.1%}) came back as {r['stream_type']} with candidates "
        f"{r['candidates']}"
    )
    assert r["confidence"] == "high"


def test_an_unmeasured_criterion_widens_the_answer_rather_than_guessing():
    """The whole point of candidates.

    A 10 m DEM cannot measure bankfull width on a 4 m channel, so
    entrenchment and width/depth come back unknown. The honest answer is the
    set of types still consistent with slope and sinuosity, not a confident
    letter.
    """
    r = classify_stream_type(None, None, 1.6, 0.008)
    assert r["stream_type"] is None
    assert set(r["candidates"]) == {"C", "E", "F"}
    assert r["missing"] == ["er", "wd"]
    assert r["confidence"] == "low"


def test_sinuosity_ranks_candidates_and_never_eliminates_them():
    """Rosgen gives sinuosity as a typical value, not a cut.

    Treating it as hard stops the key being usable: a straight reach at 3%
    fails B and G (both want > 1.2) and fails A (wants > 4% slope), so it fits
    nothing. Measured on 10 m Ruidoso that was 1,478 reaches returning "no
    type fits" — a statement about the key, not about the streams.
    """
    r = classify_stream_type(None, None, 1.08, 0.03)
    # Not A: an entrenched, low width/depth channel at 3% is a G in Rosgen's
    # chart -- A begins at 4%, and slope is the only thing separating the
    # two. (v24 kept A here and wrote "Ab", which is not a Rosgen type.)
    # Sinuosity 1.08 is below both B's and G's typical range and removes
    # neither.
    assert set(r["candidates"]) == {"B", "G"}, r["candidates"]
    assert r["stream_type"] is None
    assert r["no_type_fits"] is False


def test_slope_outside_the_typical_range_is_a_suffix_not_a_new_type():
    """Rosgen writes a B at 1.5% as Bc, not as nothing.

    The first real run at channel-resolving resolution (Eagle Creek, 0.61 m)
    returned "no type fits" for 6 of 13 reaches. One of them measured
    ER 1.51, W/D 29, sinuosity 1.2, slope 1.5% -- a textbook Bc.
    """
    r = classify_stream_type(1.51, 29.0, 1.2, 0.015)
    assert r["stream_letter"] == "B"
    assert r["slope_class"] == "c"
    assert r["stream_type"] == "Bc"

    fb = classify_stream_type(1.03, 20.7, 1.16, 0.036)      # Eagle Creek 12
    assert fb["stream_type"] == "Fb", fb

    typical = classify_stream_type(1.8, 18.0, 1.3, 0.03)
    assert typical["stream_type"] == "B" and typical["slope_class"] is None


def test_eagle_creek_has_no_reach_that_fits_nothing():
    """All thirteen reaches as measured on the v20 run. Before the key was
    corrected six of them fit no type."""
    measured = [
        (1.514, 28.950, 1.195, 0.015), (1.459, 39.454, 1.480, 0.018),
        (1.283, 39.197, 1.272, 0.019), (1.008, 76.714, 1.218, 0.020),
        (2.053, 24.072, 1.197, 0.023), (4.332, 10.653, 1.279, 0.095),
        (1.274, 33.240, 1.287, 0.053), (1.625, 64.793, 1.449, 0.044),
        (1.665, 67.371, 1.177, 0.035), (2.122, 18.866, 1.152, 0.036),
        (1.031, 20.673, 1.156, 0.036),
    ]
    none = [m for m in measured
            if classify_stream_type(*m)["no_type_fits"]]
    assert len(none) <= 1, f"still fitting no type: {none}"


def test_braiding_is_never_asserted_without_a_measured_width_depth():
    """D survives the key on slope alone, and then swallows the landscape.

    Before this, every low-gradient reach whose width/depth was unmeasured
    came back "possibly braided" — 1,376 of them in a steep mountain
    catchment, which is the key answering a question it was not given the
    data for.
    """
    unmeasured = classify_stream_type(None, None, 1.1, 0.01)
    assert unmeasured["braided_possible"] is False
    assert "D" not in unmeasured["candidates"]

    measured = classify_stream_type(2.0, 60.0, 1.1, 0.02)
    assert measured["braided_possible"] is True


def test_nothing_measured_means_nothing_excluded():
    r = classify_stream_type(None, None, None, None)
    assert len(r["all_candidates"]) >= 7
    assert len(r["candidates"]) >= 6       # DA carries a small rarity prior
    assert r["stream_type"] is None
    assert r["confidence"] == "low"


def test_a_steep_reach_is_an_A_with_or_without_a_bankfull():
    """Sam, on v23: most reaches had no letter. Above 10% only A exists in
    Rosgen's chart, so slope alone names it -- at low confidence."""
    r = classify_stream_type(None, None, 1.215, 0.187)
    assert r["stream_type"] == "Aa+"
    assert r["confidence"] == "low"


@pytest.mark.parametrize("slope", [0.05, 0.08, 0.13, 0.25])
def test_no_letter_is_returned_where_it_does_not_exist(slope):
    """A 13% headwater with a wide flood-prone width came back "Ca+" in the
    first cut of the nearest-type key. There is no Ca or Ca+: above 4% only A
    and B exist, above 10% only A."""
    for er in (1.2, 1.8, 3.3, 8.0, 20.0):
        for wd in (None, 8.0, 20.0, 50.0):
            r = classify_stream_type(er, wd, 1.2, slope)
            assert r["stream_letter"] in ("A", "B"), (er, wd, slope, r)
            if slope > 0.10:
                assert r["stream_letter"] == "A"


def test_a_measured_entrenchment_always_gives_a_letter():
    """With entrenchment known the misfit separates the types; the v23
    in-range count left every near-tie as a candidate set."""
    grid = [(er, sin, slope)
            for er in (1.0, 1.3, 1.5, 2.0, 2.5, 4.0, 10.0)
            for slope in (0.005, 0.015, 0.03, 0.06, 0.15)
            for sin in (1.05, 1.25, 1.6)]
    none = [g for g in grid if classify_stream_type(g[0], None, g[1], g[2])
            ["stream_type"] is None]
    # An exact tie is still possible -- ER 1.5 at 0.5% with sinuosity 1.05
    # is equally far from B and F -- and then the candidates are the answer.
    assert len(none) <= 0.05 * len(grid), none


def test_floodprone_width_is_measured_at_twice_the_bankfull_depth():
    """Rosgen's definition, not the floodplain breakpoint geolens already has.

    A symmetric V channel 2 m deep inside a flat bench: at twice the bankfull
    depth the section is exactly as wide as the bench, and that is the number
    the entrenchment ratio is defined on.
    """
    # offsets -50..50, a 2 m deep V in the middle of a 40 m bench, then walls
    pts = []
    for o in np.arange(-50, 50.5, 1.0):
        if abs(o) <= 4:
            z = 2.0 * abs(o) / 4.0           # V, 2 m deep, 8 m wide
        elif abs(o) <= 20:
            z = 2.0                          # bench at the bankfull elevation
        else:
            z = 2.0 + (abs(o) - 20) * 1.0    # valley walls
        pts.append((float(o), float(z)))

    w = _floodprone_width(pts, thalweg_z=0.0, max_depth=2.0)
    # stage = 0 + 2*2 = 4 m, reached 2 m up each wall => 22 m each side
    assert w == pytest.approx(44.0, abs=1.0), w


def test_floodprone_width_refuses_a_section_that_never_reaches_the_stage():
    """A width that ran to the end of the section is not a measurement."""
    pts = [(float(o), 0.5 * abs(o) / 10.0) for o in np.arange(-10, 10.5, 1.0)]
    assert _floodprone_width(pts, thalweg_z=0.0, max_depth=5.0) is None


def test_sinuosity_is_length_over_chord():
    from shapely.geometry import LineString

    straight = LineString([(0, 0), (100, 0)])
    assert _sinuosity(straight) == pytest.approx(1.0)

    zig = LineString([(0, 0), (25, 25), (50, 0), (75, 25), (100, 0)])
    assert _sinuosity(zig) == pytest.approx(
        4 * math.hypot(25, 25) / 100.0, rel=1e-6)


def test_a_width_depth_ratio_is_corrected_when_the_units_differ():
    """Width is horizontal and depth is vertical.

    On a CRS whose vertical and horizontal units differ the ratio is not
    dimensionless, and a factor of 3.28 lands a C in the middle of E. This
    checks the correction exists rather than re-deriving it: a US survey foot
    grid with metre elevations must not report the raw ratio.
    """
    import inspect

    from geolens.landscape import streamtype

    src = inspect.getsource(streamtype._type_reaches)
    # Offsets and elevations are converted to metres, each with its own
    # factor, before anything is measured -- so every width, depth and ratio
    # downstream is in metres and needs no correction.
    assert 'float(p["offset"]) * mpu' in src, "offsets not converted"
    assert 'float(p["elevation"]) * vpu' in src, "elevations not converted"
    # ...and vpu is the VERTICAL factor, not the horizontal one again.
    caller = inspect.getsource(streamtype.rosgen_stream_types)
    assert "vpu = units.vertical_metres_per_unit" in caller


def test_the_substrate_table_covers_every_material_class():
    """A material class with no substrate mapping silently drops the number."""
    from geolens.geology import MATERIAL_CLASSES
    from geolens.landscape.streamtype import (_SUBSTRATE_FROM_MATERIAL,
                                              SUBSTRATE_DESC)

    from geolens.geology import _MATERIAL_KEYWORDS

    produced = set(MATERIAL_CLASSES) | {c for c, _ in _MATERIAL_KEYWORDS}
    missing = produced - set(_SUBSTRATE_FROM_MATERIAL)
    assert not missing, (
        f"the geology module can return {sorted(missing)} and the substrate "
        f"table has no entry for it, so the number would silently vanish. "
        f"Add an entry — None is a valid one where the unit implies no "
        f"dominant calibre."
    )
    for n, why in _SUBSTRATE_FROM_MATERIAL.values():
        assert n is None or n in SUBSTRATE_DESC
        assert why, "every substrate prior has to say what it rests on"


def test_geology_given_as_a_path_assigns_substrate(tmp_path):
    """The way the pipeline actually calls it.

    geology="auto" and landscape_pass both hand over a PATH. The substrate
    step passed that straight to attach_material, which wants a loaded layer,
    and then treated its (materials, covers) tuple as a list. Every unit test
    above calls classify_stream_type directly and none touched geology, so
    the first real run — Eagle Creek, v20 — was the first time this line ever
    executed, and it failed with "'str' object has no attribute 'columns'".
    The valley types on the same run said geology_used=True, which made the
    download look broken when it was this.
    """
    import os

    import geopandas as gpd
    from shapely.geometry import box

    from geolens.landscape import (build_terrain, extract_network,
                                   rosgen_stream_types)

    dem = Path(__file__).resolve().parent / "landscape" / "landscape_dem.tif"
    if not dem.exists():
        pytest.skip("landscape fixture not built")
    os.environ["GEOLENS_OUT"] = str(tmp_path)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"

    t = build_terrain(str(dem), stream_threshold_km2=0.3, out_prefix="st_g")
    n = extract_network(t, segment_length_m=300.0, out_prefix="st_gn")

    import rasterio
    with rasterio.open(str(dem)) as src:
        b, crs = src.bounds, src.crs
    geo = gpd.GeoDataFrame(
        {"material_class": ["alluvial"]},
        geometry=[box(b.left, b.bottom, b.right, b.top)], crs=crs)
    gpath = tmp_path / "geology.gpkg"
    geo.to_file(str(gpath), driver="GPKG")

    r = rosgen_stream_types(t.dem, n.segments, geology=str(gpath))
    assert r.substrate_used, (
        "substrate was not assigned from a geology PATH: "
        + "; ".join(w for w in r.warnings if "Substrate" in w)
    )
    g = gpd.read_file(r.segments)
    assert (g["substrate_class"] == 4).all(), "alluvium should map to gravel, 4"
    full = g["stream_type_full"].dropna().tolist()
    assert not any(str(x).startswith("nan") for x in full), (
        f"a missing letter was glued to the number: {full[:5]}"
    )
    # Rosgen order: letter, substrate number, slope class -- A4a+, not Aa+4.
    for letter, sc, whole in zip(g["stream_letter"], g["slope_class"],
                                 g["stream_type_full"]):
        if letter is None or (isinstance(letter, float) and letter != letter):
            assert whole is None or whole != whole
        else:
            suffix = sc if isinstance(sc, str) else ""
            assert whole == f"{letter}4{suffix}", (letter, sc, whole)


def test_the_channel_sections_are_written_out(tmp_path):
    """They were measured and thrown away -- `sections=None` -- so the one thing
    needed to check a stream type, where the banks were picked, was not on
    disk. Found by Sam on the first real run."""
    import os

    import geopandas as gpd
    import pandas as pd

    from geolens.landscape import (build_terrain, extract_network,
                                   rosgen_stream_types)

    dem = Path(__file__).resolve().parent / "landscape" / "landscape_dem.tif"
    if not dem.exists():
        pytest.skip("landscape fixture not built")
    os.environ["GEOLENS_OUT"] = str(tmp_path)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"
    t = build_terrain(str(dem), stream_threshold_km2=0.3, out_prefix="st_s")
    n = extract_network(t, segment_length_m=300.0, out_prefix="st_sn")
    r = rosgen_stream_types(t.dem, n.segments, out_prefix="st_sec")

    assert r.sections and Path(r.sections).exists(), "no sections layer"
    secs = gpd.read_file(r.sections)
    assert len(secs) > 0
    assert set(secs.geom_type) == {"LineString"}
    for col in ("bankfull_width_m", "used", "station_m", "xs_id"):
        assert col in secs.columns, col

    out = Path(r.sections).parent
    banks = gpd.read_file(out / "stream_type_banks.gpkg")
    assert set(banks["kind"]) <= {"bank_top", "floodprone_edge"}
    assert len(banks) > 0

    prof = pd.read_csv(out / "stream_type_profiles.csv")
    assert {"xs_id", "offset_m", "elevation_m"} <= set(prof.columns)
    assert prof["xs_id"].nunique() == len(secs)

    # Every bank point sits ON its section line.
    lines = secs.set_index("xs_id").geometry
    for _, b in banks.iterrows():
        assert lines[b["xs_id"]].distance(b.geometry) < 1e-6


def test_the_arid_southwest_curves_are_moodys_converted_correctly():
    """Moody et al. (2003) publish in ft and mi2. Their Eastern AZ/NM curve
    at 8.1 mi2 gives 17.8 ft wide, 0.77 ft mean depth; their field site on
    Eagle Creek below the South Fork near Alto, NM measured 15.8 x 0.7 ft.
    (That Eagle Creek is Moody's site, NOT the Eagle Creek DEM Sam tests
    geolens on -- a different place.)"""
    from geolens.landscape.streamtype import REGIONAL_CURVES

    km2 = 8.1 * 2.589988110336
    nm = REGIONAL_CURVES["arid_sw_nm"]
    w_ft = nm["width"][0] * km2 ** nm["width"][1] / 0.3048
    d_ft = nm["depth"][0] * km2 ** nm["depth"][1] / 0.3048
    assert w_ft == pytest.approx(9.9059 * 8.1 ** 0.2811, rel=1e-3)
    assert d_ft == pytest.approx(0.4663 * 8.1 ** 0.237, rel=1e-3)
    assert abs(w_ft - 15.8) / 15.8 < 0.15 and abs(d_ft - 0.7) / 0.7 < 0.15

    az = REGIONAL_CURVES["arid_sw_az"]
    w_az = az["width"][0] * km2 ** az["width"][1] / 0.3048
    assert w_az == pytest.approx(15.763 * 8.1 ** 0.3169, rel=1e-3)


def test_moodys_eagle_creek_sites_key_to_their_field_types():
    """Field-typed reaches from Moody et al. (2003), Appendix 3, on an Eagle
    Creek near Alto, NM. That is NOT the Eagle Creek DEM geolens is tested on
    (a different place): this checks the key against their published field
    types, not geolens's measurements against the field. Tributary #1 is
    A4a+ at ER 2.6: above 10% only A exists, whatever the entrenchment."""
    below_south_fork = classify_stream_type(1.9, 21.5, 1.1, 0.022)
    assert below_south_fork["stream_letter"] == "B"
    trib_1 = classify_stream_type(2.6, 11.3, 1.1, 0.127)
    assert trib_1["stream_type"] == "Aa+"


def test_every_label_the_key_can_return_is_in_rosgens_chart():
    """Swept over a dense grid of inputs, including boundary values, zero,
    negative and missing slope. v24 could write Ab, Ca at exactly 4%, Gc-,
    DAc -- none of which are Rosgen types."""
    import itertools

    chart = {"A", "Aa+", "Ba", "B", "Bc", "Cb", "C", "Cc-", "Db", "D", "DA",
             "Eb", "E", "Fb", "F", "G", "Gc"}
    ers = [None, 0.8, 1.2, 1.39, 1.4, 1.41, 1.6, 2.2, 2.21, 3.0, 6.0, 20.0]
    wds = [None, 5.0, 11.9, 12.0, 12.1, 20.0, 40.0, 41.0, 80.0]
    sins = [None, 1.0, 1.1, 1.2, 1.4, 1.5, 2.2]
    slopes = [None, -0.01, 0.0, 0.0005, 0.001, 0.004, 0.005, 0.0051, 0.015,
              0.02, 0.03, 0.04, 0.0401, 0.06, 0.1, 0.1001, 0.3,
              float("nan")]
    bad = {}
    for er, wd, sn, sl in itertools.product(ers, wds, sins, slopes):
        t = classify_stream_type(er, wd, sn, sl)["stream_type"]
        if t is not None and t not in chart:
            bad.setdefault(t, (er, wd, sn, sl))
    assert not bad, bad
