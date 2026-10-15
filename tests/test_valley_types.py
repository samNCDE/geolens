"""Rosgen valley types, measured valley bottom to ridgetop.

The report was that valley types were still being classified off the channel
and the valley floor rather than the valley — "valley bottom to ridgetop".
Three separate defects, each hidden by the one before it:

**1. There was no fixture with a ridge in it.** ``make_landscape.py`` builds
side slopes that rise at a constant gradient and never stop. It has truth for
valley-bottom width, which is what it was built for, and it was the only
landscape fixture. So every test of valley form ran on terrain with no ridgetop
at all: on it, the divide was found on **1% of sections**, and the failure read
as a tuning problem for two releases. On ``make_valleys.py``, which has real
divides, the same code finds both divides on **100%** of sections and measures
valley top width to within 0.7% of truth. The algorithm was never the problem.

**2. The shape exponent was fitted wrong.** ``rise + tol`` was fitted instead
of ``rise``; that additive term is not part of a power law and it flattens the
bottom of the curve, which the fit answers with a steeper slope. A built U
(n = 2.0) came back as 4.74 — not U, not V, not anything. See
``_shape_exponent`` for the numbers.

**3. The "valley floor" was the channel.** The floor elevation was taken at the
thalweg, so a 2 m tolerance band sat inside a 3 m incised channel. Built floors
of 80, 180, 900 and 1800 m measured **20, 0, 0 and 40 m**; Vf was ~0.15 for
every valley and all four classified as V-shaped whatever their form. That is
precisely "measuring the channel, not the valley".

With all three fixed the five valleys classify across the key: a canyon as I, a
glacial trough as V, V-notches as II, and a broad alluvial valley declined with
VIII/IX/X offered — because geometry alone genuinely cannot separate alluvial
from outwash from lacustrine, and saying so is the correct answer.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "vt_test_out"
FIX = ROOT / "valleys"
DEM = FIX / "valleys_dem.tif"
AXES = FIX / "valleys_axes.gpkg"
TRUTH = FIX / "valleys_truth.csv"

CELL_M = 20.0                    # the fixture's cell size

NAMES = {0: "narrow V-notch", 1: "broad U (glacial-trough-like)",
         2: "confined gorge", 3: "wide shallow alluvial",
         4: "steep V-notch canyon"}


@pytest.fixture(scope="module", autouse=True)
def _env():
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"
    if not DEM.exists():
        subprocess.run([sys.executable, str(ROOT / "make_valleys.py")],
                       check=True, capture_output=True)
    if not DEM.exists():
        pytest.skip("valleys fixture could not be built")


@pytest.fixture(scope="module")
def measured():
    import pandas as pd
    from geolens.landscape.valleyform import valley_form

    vf = valley_form(str(DEM), str(AXES), id_field="valley_id",
                     spacing_m=500.0, max_search_m=4000.0, out_prefix="vt")
    return vf, pd.read_csv(vf.segment_table), pd.read_csv(str(TRUTH))


# -- the fixture itself -------------------------------------------------------

def test_the_fixture_actually_contains_ridges():
    """Guards the thing that made this untestable for two releases.

    A landscape whose flanks never crest cannot test a crest detector, and
    nothing said so — the detector just returned nothing, forever.
    """
    import rasterio
    import pandas as pd

    truth = pd.read_csv(str(TRUTH))
    assert len(truth) >= 5, "the fixture lost valleys"
    with rasterio.open(str(DEM)) as src:
        z = src.read(1).astype("float64")
    mid = z[z.shape[0] // 2]
    # A row across the grid must rise and fall several times: one maximum
    # would mean a single hillslope, which is what the old fixture was.
    from scipy.signal import find_peaks
    peaks, _ = find_peaks(mid, prominence=50.0)
    assert len(peaks) >= 4, (
        f"only {len(peaks)} ridges across the fixture — it has gone back to "
        f"being terrain with nothing to find"
    )


# -- measurement --------------------------------------------------------------

def test_both_divides_are_found_on_essentially_every_section(measured):
    """1% on the ridgeless fixture; this must be ~100%."""
    vf, _seg, _truth = measured
    rate = vf.n_measured / max(vf.n_sections, 1)
    assert rate > 0.95, (
        f"only {rate:.0%} of {vf.n_sections} sections reached both divides"
    )


def test_valley_top_width_matches_the_built_geometry(measured):
    """Floor to ridgetop, against a DEM whose ridges are known."""
    _vf, seg, truth = measured
    for _, tr in truth.iterrows():
        vid = int(tr.valley_id)
        row = seg[seg.segment_id == vid]
        assert len(row), f"valley {vid} produced no measurement"
        got = float(row.iloc[0].valley_top_width_m)
        want = float(tr.valley_top_width_m)
        # 3%, or two cells, whichever is larger. A relative tolerance alone
        # fails the narrowest valley on a 40 m discrepancy that is exactly two
        # 20 m cells — the grid is a floor on how well any of this can be
        # measured, and pretending otherwise makes the test about resolution
        # rather than about the algorithm.
        tol = max(0.03 * want, 2.0 * CELL_M)
        assert abs(got - want) <= tol, (
            f"valley {vid} ({NAMES[vid]}): top width {got:.0f} m against a "
            f"built {want:.0f} m (tolerance {tol:.0f} m)"
        )


def test_the_shape_exponent_separates_u_from_v(measured):
    """The measurement Rosgen V turns on. A built 2.0 used to read 4.74."""
    _vf, seg, truth = measured
    for _, tr in truth.iterrows():
        vid = int(tr.valley_id)
        got = float(seg[seg.segment_id == vid].iloc[0].shape_n)
        want = float(tr.built_shape_n)
        assert abs(got - want) < 0.35, (
            f"valley {vid} ({NAMES[vid]}): shape n {got:.2f} against a built "
            f"{want:.1f}"
        )


def test_the_valley_floor_is_not_the_channel(measured):
    """Built floors of 80/180/900/1800/60 m once measured 20/0/0/40 m.

    The tolerance is loose on purpose: a valley flank is genuinely flat near
    its toe, so any tolerance band includes some of it and the measurement is
    biased high. The bias is real and documented; reading the *channel* is not.
    """
    _vf, seg, truth = measured
    # The fixture incises a channel 60 m wide along every axis. Where the
    # valley floor is only a little wider than that, the floor and the channel
    # are not separable even in principle — a V-notch's floor IS essentially
    # its channel, which is what makes it a V-notch. The lower bound is
    # therefore only asserted where the built floor is at least twice the
    # channel; below that the requirement is simply that a floor was found.
    channel_w = 60.0
    for _, tr in truth.iterrows():
        vid = int(tr.valley_id)
        got = float(seg[seg.segment_id == vid].iloc[0].valley_floor_width_m)
        want = 2.0 * float(tr.floor_half_width_m)
        if want >= 2.0 * channel_w:
            assert got >= want * 0.75, (
                f"valley {vid} ({NAMES[vid]}): floor {got:.0f} m against a "
                f"built {want:.0f} m — this is the channel, not the valley "
                f"floor"
            )
        else:
            assert got > 0.0, f"valley {vid}: no floor measured at all"
        assert got <= want * 2.5 + 100.0, (
            f"valley {vid}: floor {got:.0f} m against a built {want:.0f} m — "
            f"the tolerance band has run up the flank"
        )


def test_vf_discriminates_between_valleys(measured):
    """It was ~0.15 for every valley, so it discriminated nothing."""
    _vf, seg, _truth = measured
    vfs = seg["vf"].astype(float).dropna()
    assert len(vfs) >= 4
    assert vfs.max() / max(vfs.min(), 1e-9) > 4.0, (
        f"Vf spans only {vfs.min():.2f}..{vfs.max():.2f} across valleys built "
        f"with floors from 60 m to 1800 m"
    )


# -- classification -----------------------------------------------------------

def _classify(seg, vid):
    from geolens.landscape.rosgen import classify_from_form

    r = seg[seg.segment_id == vid].iloc[0]
    return classify_from_form(r.side_slope, r.shape_n, r.vf_class,
                              r.valley_relief_m, r.asymmetry) or {}


def test_the_glacial_trough_is_typed_as_v(measured):
    _vf, seg, _truth = measured
    out = _classify(seg, 1)
    assert out.get("valley_type") == "V", (
        f"the U-shaped trough (built n=2.0) typed as "
        f"{out.get('valley_type')!r}: {out.get('why')}"
    )


def test_the_steep_canyon_is_typed_as_i(measured):
    """Unreachable before: every valley in the old fixture was kilometres wide,
    so nothing ever had steep enough flanks to enter the canyon branch."""
    _vf, seg, _truth = measured
    out = _classify(seg, 4)
    assert out.get("valley_type") == "I", (
        f"the 70%-walled canyon typed as {out.get('valley_type')!r}: "
        f"{out.get('why')}"
    )


def test_a_broad_alluvial_valley_is_not_called_a_glacial_trough(measured):
    """It was. n = 1.61 tripped a `u_shaped` threshold of 1.6, and the rule
    looked at shape alone — a floor 7.8x the valley's own depth is an alluvial
    plain whatever its flanks fit to."""
    _vf, seg, _truth = measured
    out = _classify(seg, 3)
    assert out.get("valley_type") != "V", (
        f"the wide alluvial valley is still being typed as a glacial trough: "
        f"{out.get('why')}"
    )
    alts = out.get("alternatives") or ()
    assert out.get("valley_type") in (None, "VIII", "IX", "X") or \
        any(a in ("VIII", "IX", "X") for a in alts), (
        f"expected a depositional type or an honest decline, got "
        f"{out.get('valley_type')!r} / {alts}"
    )


def test_the_five_valleys_do_not_all_get_the_same_answer(measured):
    """The point of the whole exercise."""
    _vf, seg, truth = measured
    got = {int(tr.valley_id): _classify(seg, int(tr.valley_id)).get("valley_type")
           for _, tr in truth.iterrows()}
    distinct = {v for v in got.values() if v}
    assert len(distinct) >= 3, (
        f"five valleys of quite different form produced {distinct} — the "
        f"classification is not discriminating: {got}"
    )


# -- the fit, on its own ------------------------------------------------------

@pytest.mark.parametrize("true_n", [1.0, 1.5, 2.0, 2.5])
def test_the_shape_fit_is_accurate_on_a_clean_flank(true_n):
    from geolens.landscape.valleyform import _shape_exponent

    reach, depth = 2000.0, 280.0
    d = np.arange(1.0, reach, 20.0)
    r = depth * (d / reach) ** true_n
    got = _shape_exponent(d, r, 2.0)
    assert abs(got - true_n) < 0.1, f"true {true_n} fitted {got:.3f}"


def test_the_shape_fit_survives_noise_and_a_wide_floor():
    """Both of which a real cross-section has and a clean flank does not."""
    from geolens.landscape.valleyform import _shape_exponent

    rng = np.random.default_rng(3)
    reach, depth, floor = 2150.0, 260.0, 450.0
    got = []
    for _ in range(15):
        d = np.arange(1.0, reach + floor, 20.0)
        r = np.where(d <= floor, 0.0,
                     depth * (np.clip(d - floor, 0, None) / reach) ** 2.0)
        r = r + rng.normal(0, 1.5, d.size)
        got.append(_shape_exponent(d, r, 2.0))
    med = float(np.nanmedian(got))
    assert abs(med - 2.0) < 0.2, (
        f"a built parabola with a 450 m floor and 1.5 m of noise fitted "
        f"{med:.3f}"
    )


# -- reachability -------------------------------------------------------------

def test_the_search_distance_is_derived_wide_enough_to_reach_the_divides():
    """``max_search_m`` is the parameter that decides whether any of this works.

    Too small and every section returns a lower bound instead of a
    measurement, and the run looks perfectly healthy while measuring nothing —
    which is exactly what the notebook's fixed 3 km default did. Derived from
    the DEM it must be generous: ``_find_divide`` takes the FIRST crest walking
    out from the floor, so a wider search can never find a wronger divide, only
    let more sections find one. A sixth of the shorter side was tried first and
    gave 2,333 m against divides sitting up to 3,400 m out — 20% measured.
    """
    import pandas as pd
    import geolens.api as api

    truth = pd.read_csv(str(TRUTH))
    furthest = max(float(truth.ridge_left_distance_m.max()),
                   float(truth.ridge_right_distance_m.max()))

    vf = api.valley_form(str(DEM), str(AXES), id_field="valley_id",
                         spacing_m=500.0, out_prefix="vt_auto")
    rate = vf["n_measured"] / max(vf["n_sections"], 1)
    assert rate > 0.95, (
        f"the derived search distance reached both divides on only {rate:.0%} "
        f"of sections; the furthest divide in this fixture is {furthest:.0f} m "
        f"from the axis"
    )


def test_the_notebook_runs_valley_types_at_all():
    """Three rounds of work on Rosgen valley types, and the notebook — the
    entry point, the thing that produces the outputs anyone looks at — never
    mentioned them. Not ``valley_form``, not ``rosgen``, nothing. The fix was
    correct and unreachable."""
    import json

    nb_path = ROOT.parent / "notebooks" / "geolens_start.ipynb"
    if not nb_path.exists():
        pytest.skip("notebook not built")
    nb = json.loads(nb_path.read_text(encoding="utf-8"))
    code = "\n".join("".join(c["source"]) for c in nb["cells"]
                     if c["cell_type"] == "code")
    assert "valley_form" in code, "the notebook does not measure valley form"
    assert "rosgen_valley_types" in code, (
        "the notebook does not classify valley types"
    )


# -- reachability, continued: the landscape pass ------------------------------

def test_the_landscape_pass_produces_valley_types_by_default():
    """The correction for how all of this went missing.

    Valley types were opt-in. The notebook never opted in, ``landscape_pass``
    never ran them, and so three rounds of work on Rosgen valley types went
    into something no run ever produced. Opt-in is how that happens, so they
    are on by default now.
    """
    import geolens.api as api

    r = api.landscape_pass(str(DEM), stream_threshold_km2=0.5,
                           segment_length_m=1000.0, out_prefix="vt_pass")
    assert "valley_types" in r, (
        "the landscape pass no longer produces valley types"
    )
    assert "valley_form" in r
    counts = r["valley_types"]["type_counts"]
    assert counts, "valley types came back empty"
    assert len(counts) >= 3, (
        f"five valleys of quite different form produced {counts}"
    )
    assert Path(r["valley_types"]["segments"]).exists()


def test_it_can_be_turned_off_for_a_run_that_does_not_need_it():
    """It is the slowest step after routing; a run that is not asking about
    valley type should not pay for it."""
    import geolens.api as api

    r = api.landscape_pass(str(DEM), stream_threshold_km2=0.5,
                           segment_length_m=1000.0, valley_types=False,
                           out_prefix="vt_off")
    assert "valley_types" not in r
    assert "valley_form" not in r
    assert r["classified"]["segments"], "the rest of the pass still has to run"


def test_a_valley_type_failure_does_not_lose_the_rest_of_the_pass(monkeypatch):
    """The landscape pass is expensive and its earlier outputs are valid.

    Losing a conditioned DEM, a routed network and a classified segment layer
    because the last step raised would be a bad trade, and on a large DEM an
    extremely expensive one.
    """
    import geolens.api as api
    from geolens.landscape import pipeline

    def _boom(*a, **k):
        raise RuntimeError("induced valley-form failure")

    monkeypatch.setattr(pipeline, "_derive_search_m", _boom)
    r = api.landscape_pass(str(DEM), stream_threshold_km2=0.5,
                           segment_length_m=1000.0, out_prefix="vt_boom")
    assert r["classified"]["segments"], "the pass lost its earlier outputs"
    assert any("Valley types could not be computed" in w
               for w in r["warnings"]), (
        f"the failure was swallowed without a word: {r['warnings']}"
    )


def test_section_sampling_is_fine_near_the_axis_and_coarse_out_to_the_divide():
    """Uniform sampling at the DEM's cell size does not scale.

    On 2.4 ftUS cells with a 6 km search that is 16,405 points per section,
    and across a 1,767 km network at 500 m spacing it comes to 116 million
    samples — the difference between a step that runs and one that does not.
    Coarsening uniformly is not the answer either: the floor, the channel and
    the toe of the wall all live within a few hundred metres of the axis and
    decide Vf and the shape exponent.
    """
    from geolens.landscape.valleyform import _section_offsets

    off = _section_offsets(6000.0, 0.7315)
    assert off.size < 2000, f"{off.size} points is still a uniform grid"
    assert off.size > 300, "so coarse the divide could be missed by a cell"

    d = np.diff(off)
    # Native resolution where it matters...
    near = np.abs(off[:-1]) < 150.0
    assert d[near].max() <= 0.74, "the fine zone is not at cell spacing"
    # ...and coarse where only reach matters.
    far = np.abs(off[:-1]) > 3000.0
    assert d[far].min() > 5.0, "the far zone is still being sampled finely"

    # Monotonic and centred, which _measure_section's searchsorted relies on.
    assert np.all(d > 0), "offsets are not strictly increasing"
    assert abs(float(off.min()) + float(off.max())) < 2.0 * 0.7315


def test_a_small_section_is_left_uniform():
    """No point splitting a section that is already cheap."""
    from geolens.landscape.valleyform import _section_offsets

    off = _section_offsets(150.0, 5.0)
    d = np.diff(off)
    assert np.allclose(d, d[0]), "a short section was needlessly split"


# -- the valley as an AREA ----------------------------------------------------

def test_the_landscape_pass_produces_valley_polygons():
    """"It shows me a classification on a stream centerline."

    Everything up to here produced a table about a valley attached to the
    channel down its middle. Opened on a map that is a coloured stream, which
    is not a valley and does not look like one. A valley-types layer has to
    draw valleys.
    """
    import geopandas as gpd
    import geolens.api as api

    r = api.landscape_pass(str(DEM), stream_threshold_km2=0.5,
                           segment_length_m=1000.0, out_prefix="vt_area")
    assert "valley_extent" in r, "the pass produced no valley extent"
    g = gpd.read_file(r["valley_extent"])
    assert len(g) > 10
    assert set(g.geom_type) <= {"Polygon", "MultiPolygon"}, (
        f"valley extent is {set(g.geom_type)} — still not an area"
    )
    assert g.geometry.is_valid.all(), "invalid polygons were written"
    assert "valley_type" in g.columns, (
        "the polygons carry no valley type, so they cannot be styled by it"
    )
    typed = int(g["valley_type"].notna().sum())
    assert typed > 0.8 * len(g), (
        f"only {typed}/{len(g)} polygons got a valley type — the attribute "
        f"join matched almost nothing, which is the seg_id/segment_id "
        f"mismatch again"
    )


def test_the_polygons_span_the_valley_not_the_channel():
    """A valley polygon should be kilometres across on this fixture, and the
    total mapped area should be the same order as the DEM — not a sliver."""
    import geopandas as gpd
    import rasterio
    import geolens.api as api

    r = api.landscape_pass(str(DEM), stream_threshold_km2=0.5,
                           segment_length_m=1000.0, out_prefix="vt_area2")
    g = gpd.read_file(r["valley_extent"])
    with rasterio.open(str(DEM)) as src:
        dem_km2 = src.width * src.height * abs(src.res[0]) * abs(src.res[1]) / 1e6

    total = float(g["valley_area_km2"].sum())
    assert total > 0.3 * dem_km2, (
        f"the valley extent covers {total:.0f} km2 of a {dem_km2:.0f} km2 DEM "
        f"— that is a ribbon along the channel, not the valleys"
    )
    # Reaches overlap where the valley bends, so some excess is expected; an
    # enormous excess means polygons are reaching into neighbouring valleys.
    assert total < 2.5 * dem_km2, (
        f"the valley extent covers {total:.0f} km2 of a {dem_km2:.0f} km2 DEM"
    )


def test_section_bearings_are_stable_along_a_straight_valley():
    """Guards the measurement that stopped me over-smoothing the tangent.

    Two sections 500 m apart on a straight valley once came out at bearings of
    22 and 135 degrees, and I lengthened the tangent baseline to fix it. Then
    I measured properly: the bearing spread within a reach is already 0.0
    degrees at every baseline, including one cell — that pair was an outlier
    in a reach holding pieces of two branches — and lengthening the baseline
    costs a third of all measurements, because an over-smoothed tangent stops
    being perpendicular to a sinuous valley.

    ==========  ==============  ==============
    baseline    bearing spread  sides measured
    one cell        0.0 deg          95%
    3x cell         0.0 deg          87%
    spacing/4       0.0 deg          75%
    spacing/2       0.0 deg          62%
    ==========  ==============  ==============

    So this asserts the median, which is what the sweep actually showed, and
    the twisted pairs are handled in ``valley_polygons`` where they belong.
    """
    import math
    import geopandas as gpd
    import geolens.api as api

    r = api.landscape_pass(str(DEM), stream_threshold_km2=0.5,
                           segment_length_m=1000.0, out_prefix="vt_bear")
    sec = gpd.read_file(r["valley_form"]["sections"])

    def _bearing(geom):
        (x0, y0), (x1, y1) = geom.coords[0], geom.coords[-1]
        return math.degrees(math.atan2(x1 - x0, y1 - y0)) % 180.0

    sec["bear"] = [_bearing(g) for g in sec.geometry]
    spread = sec.groupby("segment_id")["bear"].agg(lambda s: s.max() - s.min())
    assert float(spread.median()) < 10.0, (
        f"sections within a reach differ in bearing by a median of "
        f"{spread.median():.0f} degrees"
    )


def test_most_sides_are_still_measured_on_a_sinuous_network():
    """The cost side of the same trade, so it cannot regress silently.

    Over-smoothing the section tangent took this from 95% to 62% while
    improving nothing, and only a test that watches it would have said so.
    """
    import pandas as pd
    import geolens.api as api

    fan = ROOT / "fans" / "fan_dem.tif"
    if not fan.exists():
        pytest.skip("fan fixture not built")
    r = api.landscape_pass(str(fan), stream_threshold_km2=0.2,
                           valley_types=False, out_prefix="vt_sin")
    vf = api.valley_form(str(fan), r["classified"]["segments"],
                         spacing_m=400.0, max_search_m=1500.0,
                         out_prefix="vt_sinvf")
    sg = pd.read_csv(vf["segment_table"])
    frac = float(sg["side_slope"].notna().mean())
    assert frac > 0.85, (
        f"side slopes measured for only {frac:.0%} of segments on a sinuous "
        f"network — the section tangent is being over-smoothed again"
    )
