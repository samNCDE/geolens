"""Stream-type measurement gives the same answer in feet as in metres.

The landscape parity test (test_units_parity.py) cannot reach this code: on
its fixture every channel is under three cells wide, so no bank is ever
searched for. So the known-answer profiles are fed straight to the reach
typing, once in metres and once as a US-survey-foot grid -- with metre
elevations as well, the mixed case that made width/depth wrong by 3.28 before.
"""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString

import bankfull_profiles as B
from geolens.landscape.streamtype import (REGIONAL_CURVES, _type_reaches,
                                          classify_stream_type)

FT_US = 1200.0 / 3937.0


def _setup(mpu, vpu):
    cases = {0: "floodplain", 1: "trench", 2: "pure_v", 3: "asymmetric"}
    widths = {0: 14.0, 1: 14.0, 2: 6.0, 3: 8.0}
    seg = gpd.GeoDataFrame(
        {"seg_id": list(cases), "channel_width_m": [widths[k] for k in cases],
         "drainage_km2": [50.0, 50.0, 5.0, 20.0],
         "gradient": [0.008, 0.012, 0.06, 0.02]},
        geometry=[LineString([(0, 0), (500 / mpu, 30 / mpu)])] * len(cases))
    secs = []
    for sid, case in cases.items():
        for k in range(3):
            o, z, _ = B.make(case, spacing=0.6, noise=0.02, seed=k + 1)
            secs.append({"segment_id": sid, "station": 100.0 * k / mpu,
                         "points": [{"offset": float(a) / mpu,
                                     "elevation": float(b) / vpu}
                                    for a, b in zip(o, z)]})
    return seg, secs


@pytest.mark.parametrize("method", ["regional_stage", "picker"])
@pytest.mark.parametrize("mpu,vpu", [(FT_US, FT_US), (FT_US, 1.0)])
def test_the_reach_answer_does_not_depend_on_the_units(mpu, vpu, method):
    seg_m, secs_m = _setup(1.0, 1.0)
    seg_f, secs_f = _setup(mpu, vpu)
    rows_m, _ = _type_reaches(seg_m, secs_m, "seg_id", 0.6, 1.0, 1.0, [],
                              method=method)
    rows_f, _ = _type_reaches(seg_f, secs_f, "seg_id", 0.6, mpu, vpu, [],
                              method=method)
    for a, b in zip(rows_m, rows_f):
        assert a["stream_basis"] == b["stream_basis"], (a, b)
        assert a["stream_type"] == b["stream_type"], (a, b)
        for col in ("bankfull_width_m", "bankfull_mean_depth_m",
                    "width_depth_ratio", "entrenchment_ratio"):
            x, y = a[col], b[col]
            if np.isnan(x) and np.isnan(y):
                continue
            assert y == pytest.approx(x, rel=0.02), (a["seg_id"], col, x, y)


def test_the_known_channels_are_measured_and_the_v_is_not():
    """The bank picker's known answers (it is still run, as a check)."""
    seg, secs = _setup(1.0, 1.0)
    rows, sec_rows = _type_reaches(seg, secs, "seg_id", 0.6, 1.0, 1.0, [],
                                   method="picker")
    r = {x["seg_id"]: x for x in rows}
    assert r[0]["stream_basis"] == "measured"
    assert r[0]["bankfull_width_m"] == pytest.approx(14.0, rel=0.08)
    assert r[1]["bankfull_width_m"] == pytest.approx(14.0, rel=0.08), \
        "the trench terrace is not bankfull"
    # A pure V has no bank: typed on the regional basis, not a guess.
    assert r[2]["stream_basis"] == "regional"
    assert np.isnan(r[2]["width_depth_ratio"])


def test_basis_says_what_the_letter_used():
    """One measured section is a station, not a reach (v24 called it
    'regional' and still used its width/depth)."""
    seg, secs = _setup(1.0, 1.0)
    rows, _ = _type_reaches(seg, secs[:1] + secs[3:], "seg_id", 0.6, 1.0,
                            1.0, [], method="picker")
    r0 = [x for x in rows if x["seg_id"] == 0][0]
    assert r0["n_sections_measured"] == 1
    assert r0["stream_basis"] == "partial"
    assert np.isnan(r0["width_depth_ratio"])
    assert r0["stream_confidence"] == "low"


def test_a_named_curve_is_used_for_the_expected_width():
    seg, secs = _setup(1.0, 1.0)
    rows, _ = _type_reaches(seg, secs, "seg_id", 0.6, 1.0, 1.0, [],
                            curve=REGIONAL_CURVES["arid_sw_nm"])
    nm = REGIONAL_CURVES["arid_sw_nm"]["width"]
    assert rows[0]["regional_width_m"] == pytest.approx(
        nm[0] * 50.0 ** nm[1], rel=1e-9)


def test_a_flat_straight_reach_is_not_anastomosed():
    """DA had no sinuosity range and outranked C on every flat, straight
    reach; the parity fixture typed everything under 0.5% as DA."""
    r = classify_stream_type(3.0, 20.0, 1.1, 0.003)
    assert r["stream_letter"] == "C"
    for s in (0.0, 0.0005, 0.002, 0.004):
        assert classify_stream_type(3.0, 20.0, 1.05, s)["stream_letter"] \
            != "DA"


# -- bankfull at the regional depth (v0.28 default) ---------------------------


def _trapezoid(bottom=4.0, depth=1.0, side=0.5, floor_half=40.0, dx=0.25):
    """A trapezoid channel (side = rise/run of the banks) cut into a flat
    floor; returns offsets, elevations."""
    o = np.arange(-floor_half, floor_half + dx / 2, dx)
    run = depth / side
    edge = bottom / 2 + run
    z = np.where(np.abs(o) <= bottom / 2, 0.0,
                 np.where(np.abs(o) >= edge, depth,
                          (np.abs(o) - bottom / 2) * side))
    return o, z


def test_regional_stage_measures_the_section_at_the_given_depth():
    from geolens.landscape.streamtype import regional_stage_width

    o, z = _trapezoid(bottom=4.0, depth=1.0, side=0.5)
    r = regional_stage_width(o, z, 0.4, near=3.0)
    # at 0.4 m the water is 4 + 2 * 0.4 / 0.5 = 5.6 m wide
    assert r["width"] == pytest.approx(5.6, abs=0.02)
    assert r["max_depth"] == pytest.approx(0.4)
    # area = 4 * 0.4 + 2 * (0.5 * 0.8 * 0.4) = 1.92 m2
    assert r["mean_depth"] == pytest.approx(1.92 / 5.6, rel=0.01)
    # flood-prone at 0.8 m, still inside the 1 m channel: 7.2 m, ER 1.29
    assert r["floodprone_width"] == pytest.approx(7.2, abs=0.02)
    assert not r["one_sided"] and not r["stage_capped"]


def test_a_small_channel_on_a_flat_floor_is_not_the_floor():
    """At the full regional depth the water leaves a 0.2 m channel and
    covers the floor; the width cap lowers the stage back into it."""
    from geolens.landscape.streamtype import regional_stage_width

    o, z = _trapezoid(bottom=2.0, depth=0.2, side=0.5, floor_half=40.0)
    wide = regional_stage_width(o, z, 0.4, near=3.0)
    assert wide["one_sided"] or wide["width"] > 60      # the floor
    r = regional_stage_width(o, z, 0.4, near=3.0, max_width=3 * 2.0)
    assert r["stage_capped"]
    assert r["max_depth"] < 0.2 + 1e-9
    assert r["width"] <= 6.0 + 1e-9
    # with the stage back in the channel, the floor is flood-prone ground
    assert r["floodprone_width"] > 2.2 * r["width"]


def test_no_channel_at_all_returns_nothing():
    from geolens.landscape.streamtype import regional_stage_width

    o = np.arange(-40, 40.01, 0.25)
    z = np.zeros_like(o) + 0.001 * np.cos(o)          # a flat floor
    assert regional_stage_width(o, z, 0.4, near=3.0, max_width=6.0) is None


def test_the_default_method_is_the_regional_stage_with_the_picker_kept():
    seg, secs = _setup(1.0, 1.0)
    rows, sec_rows = _type_reaches(seg, secs, "seg_id", 0.6, 1.0, 1.0, [])
    used = [r for r in sec_rows if r["used"]]
    assert used and all(r["picked_by"] == "regional depth" for r in used)
    assert any(r.get("picker_width_m") for r in sec_rows)
    by = {r["seg_id"]: r for r in rows}
    assert by[0]["bankfull_source"] == "regional depth"
    assert by[0]["stream_confidence"] in ("medium", "low")   # never high
