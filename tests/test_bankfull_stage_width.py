"""Bankfull from the stage-width curve, on profiles with a known answer.

The slope-break walker it replaces needed a floodplain flat to stop at. In a
confined channel there is none, so it climbed to a bench, a terrace or the
valley wall: on Eagle Creek median bankfull was 3.7x what the drainage area
supports. These profiles are the forms that separate the two
(``bankfull_profiles.py``), sampled finely and at lidar-like spacing with
noise, with the expected width deliberately wrong by x0.6 and x1.6 because in
use it comes from a generic regional curve.
"""
from __future__ import annotations

import pytest

from geolens.landscape.streamtype import bankfull_stage_width

import bankfull_profiles as B

SAMPLING = [(0.25, 0.0), (0.6, 0.03), (1.2, 0.03)]
FACTORS = (0.6, 1.0, 1.6)


def _ok(r, t, spacing):
    return (abs(r["width"] - t["width"]) <= max(0.12 * t["width"],
                                                1.5 * spacing)
            and abs(r["max_depth"] - t["max_depth"]) <= 0.15)


@pytest.mark.parametrize("case", ["floodplain", "trench", "asymmetric"])
@pytest.mark.parametrize("spacing,noise", SAMPLING)
@pytest.mark.parametrize("fac", FACTORS)
def test_bankfull_is_recovered(case, spacing, noise, fac):
    o, z, t = B.make(case, spacing=spacing, noise=noise)
    r = bankfull_stage_width(o, z, t["width"] * fac)
    assert r is not None, f"{case}: no bankfull found"
    assert _ok(r, t, spacing), (case, spacing, fac, r["width"],
                                r["max_depth"])


def test_the_trench_terrace_is_not_bankfull():
    """The tempting wrong answer: the old walker read 22.5-24 m, 4 m deep."""
    o, z, t = B.make("trench", spacing=0.6, noise=0.03)
    r = bankfull_stage_width(o, z, 14.0)
    assert r["width"] < 16.0 and r["max_depth"] < 1.3
    # and the flood-prone width is read at 2x that depth, inside the trench
    assert r["floodprone_width"] == pytest.approx(t["floodprone_width"],
                                                  abs=1.5)


@pytest.mark.parametrize("spacing,noise", SAMPLING)
def test_a_pure_v_has_no_bankfull(spacing, noise):
    o, z, _ = B.make("pure_v", spacing=spacing, noise=noise)
    for fac_w in (3.0, 6.0, 10.0):
        assert bankfull_stage_width(o, z, fac_w) is None


def test_a_confined_channel_is_found_when_the_bank_face_is_sampled():
    o, z, t = B.make("confined", spacing=0.25)
    r = bankfull_stage_width(o, z, 5.0)
    assert r is not None and _ok(r, t, 0.25)
    assert r["floodprone_width"] == pytest.approx(t["floodprone_width"],
                                                  abs=0.5)


@pytest.mark.parametrize("spacing", [0.6, 1.2])
def test_a_confined_bank_below_the_sample_spacing_is_indeterminate(spacing):
    """The bank face is 0.33 m wide. Sampled at 0.6 m the profile reads
    0.86, 0.67 then 0.83 m of width per metre of stage -- no break exists in
    the data, so the honest answer is None, not the valley wall."""
    o, z, t = B.make("confined", spacing=spacing, noise=0.03)
    for fac in FACTORS:
        r = bankfull_stage_width(o, z, 5.0 * fac)
        assert r is None or _ok(r, t, spacing), r


def test_units_are_metres_in_and_out():
    """Feet in would be a unit bug; the caller converts. Scaling offsets and
    elevations together scales every length and leaves ratios alone."""
    o, z, _ = B.make("floodplain", spacing=0.25)
    a = bankfull_stage_width(o, z, 14.0)
    b = bankfull_stage_width(o * 2, z * 2, 28.0, min_bank_height=0.6,
                             dh=0.04)
    assert b["width"] == pytest.approx(2 * a["width"], rel=0.02)
    assert (b["width"] / b["mean_depth"]) == pytest.approx(
        a["width"] / a["mean_depth"], rel=0.03)
