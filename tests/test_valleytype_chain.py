"""The full Rosgen chain, end to end on a real fixture.

Every piece of this was tested in isolation and passing, and the chain was
still broken in five separate places:

1. ``api.rosgen_valley_types`` had no ``valley_form`` parameter, but its body
   passed ``valley_form=valley_form`` — which silently resolved to the
   module-level ``valley_form`` FUNCTION, not a path.
2. ``valley_form`` merged a 222-feature segments layer into one centreline, so
   it produced 20 sections covering 9 segments, all tagged ``segment_id = 0``.
3. The join looked for ``segment_id`` on the segments, which name their id
   ``seg_id`` — so it never matched and the evidence was silently discarded.
4. The thalweg was the minimum of a 5 km transect, which on real terrain is
   often the floor of the NEXT valley.
5. One side of a section running off the DEM nulled the other side too, so
   ``side_slope`` existed for 23 of 222 segments.

None of the unit tests could see any of it, because each component was correct
on its own. That is what this file is for.
"""
from __future__ import annotations

import os
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "chain_test_out"
FIX = ROOT / "fans" / "fan_dem.tif"


@pytest.fixture(scope="module", autouse=True)
def _env():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"


@pytest.fixture(scope="module")
def chain():
    if not FIX.exists():
        pytest.skip("fan fixture not built — run tests/make_fans.py")
    from geolens import api

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = api.landscape_pass(str(FIX), stream_threshold_km2=0.2)
        seg = res["classified"]["segments"]
        vf = api.valley_form(str(FIX), seg, spacing_m=400.0,
                             max_search_m=1500.0, min_rise_m=5.0)
    return {"segments": seg, "vf": vf,
            "seg_gdf": gpd.read_file(seg),
            "vf_seg": pd.read_csv(vf["segment_table"]),
            "vf_sec": pd.read_csv(vf["table"])}


# -- the api surface ----------------------------------------------------------

def test_rosgen_accepts_valley_form():
    """It did not, and the body passed the FUNCTION of that name instead."""
    import inspect
    from geolens import api

    sig = inspect.signature(api.rosgen_valley_types)
    assert "valley_form" in sig.parameters
    assert sig.parameters["valley_form"].default is None


def test_calling_rosgen_without_valley_form_does_not_pass_a_function(chain):
    """The shadowing bug: `valley_form=valley_form` with no such parameter."""
    from geolens import api

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = api.rosgen_valley_types(chain["segments"], out_prefix="chain_bare")
    assert Path(r["segments"]).exists()


# -- section attribution ------------------------------------------------------

def test_sections_are_cut_along_every_segment(chain):
    """A branching network is not one line.

    Merging the layer gave 20 sections over 9 of 222 segments.
    """
    sec = chain["vf_sec"]
    seg = chain["seg_gdf"]
    assert len(sec) > 5 * len(seg) / 10, (
        f"only {len(sec)} sections for {len(seg)} segments — the features were "
        f"merged into a single centreline"
    )
    covered = chain["vf_seg"]["segment_id"].nunique()
    assert covered >= 0.9 * len(seg), (
        f"valley form covers {covered} of {len(seg)} segments"
    )


def test_section_ids_match_the_segments_own_id_column(chain):
    """The join key. `seg_id` on one side, `segment_id` on the other."""
    seg_ids = set(chain["seg_gdf"]["seg_id"].tolist())
    form_ids = set(chain["vf_seg"]["segment_id"].tolist())
    overlap = len(seg_ids & form_ids)
    assert overlap >= 0.9 * len(seg_ids), (
        f"only {overlap} of {len(seg_ids)} segment ids appear in the valley "
        f"form table — the two layers are keyed differently"
    )
    assert form_ids != {0}, "every section carried the same id"


# -- the measurements themselves ----------------------------------------------

def test_the_thalweg_is_near_the_centreline(chain):
    """A 3 km half-section usually crosses into the next drainage.

    Taking the minimum of the whole transect found that neighbour's floor:
    measured at offset -2494 m on a section 2500 m wide either side.
    """
    from geolens.landscape.valleyform import _thalweg_index

    off = np.linspace(-2500, 2500, 1001)
    z = 1000 + np.abs(off) * 0.1
    z[off < -2400] = 800.0          # a deeper valley beyond the divide
    ti = _thalweg_index(off, z, 200.0)
    assert abs(off[ti]) <= 200.0, (
        f"thalweg picked at offset {off[ti]:+.0f} — that is the next valley"
    )


def test_one_side_running_off_the_dem_does_not_null_the_other(chain):
    """side_slope existed for 23 of 222 segments because of this."""
    sg = chain["vf_seg"]
    have = int(sg["side_slope"].notna().sum())
    assert have >= 0.8 * len(sg), (
        f"side_slope measured for only {have} of {len(sg)} segments"
    )


def test_a_truncated_search_is_reported_not_discarded(chain):
    """97% of real sections never reach a crest on both sides.

    Requiring both collapsed coverage from 222 segments to 5. A search that
    runs out flat is itself evidence of a broad valley.
    """
    sg = chain["vf_seg"]
    assert "quality" in sg.columns
    assert set(sg["quality"]) <= {"measured", "lower_bound", "unbounded_broad"}
    # The fan fixture is a range plus a wide basin, so both kinds must appear.
    assert (sg["quality"] == "unbounded_broad").any(), (
        "no segment was recognised as an unbounded broad valley"
    )
    assert "vf_is_lower_bound" in chain["vf_sec"].columns


def test_a_v_notch_floor_is_one_cell_not_zero(chain):
    """`|off[hi] - off[lo]|` is 0 for a single-cell floor, which is exactly
    what an incised canyon has — so Vf came back 0.0 for every V-notch."""
    from geolens.landscape.valleyform import _measure_section

    off = np.arange(-200, 201, 2.0)
    z = 1000 + np.abs(off) * 0.5          # a perfect V, no flat floor
    m = _measure_section(off, z, int(np.argmin(z)), 2.0, 5.0)
    assert m["valley_floor_width_m"] > 0, "a one-cell floor measured 0 m wide"
    assert m["valley_floor_width_m"] <= 12.0


# -- does the evidence actually change the answer? ----------------------------

def test_valley_form_changes_the_classification(chain):
    """The whole point. If the two agree exactly, the evidence is being
    computed and then ignored — which is what was happening."""
    from geolens import api

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        bare = gpd.read_file(api.rosgen_valley_types(
            chain["segments"], out_prefix="chain_nf")["segments"])
        withf = gpd.read_file(api.rosgen_valley_types(
            chain["segments"], valley_form=chain["vf"]["segment_table"],
            out_prefix="chain_wf")["segments"])

    a = bare["valley_type"].fillna("NONE").tolist()
    b = withf["valley_type"].fillna("NONE").tolist()
    assert len(a) == len(b)
    changed = sum(1 for x, y in zip(a, b) if x != y)
    assert changed > 0.1 * len(a), (
        f"valley form changed only {changed} of {len(a)} segments — it is "
        f"being read and discarded"
    )

    unresolved_bare = float(bare["valley_type"].isna().mean())
    unresolved_with = float(withf["valley_type"].isna().mean())
    assert unresolved_with < unresolved_bare, (
        f"valley form did not reduce unresolved ({unresolved_with:.0%} vs "
        f"{unresolved_bare:.0%})"
    )


def test_every_basis_string_says_what_decided_it(chain):
    from geolens import api

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        g = gpd.read_file(api.rosgen_valley_types(
            chain["segments"], valley_form=chain["vf"]["segment_table"],
            out_prefix="chain_basis")["segments"])
    named = g[g["valley_type"].notna()]
    assert len(named) > 0
    assert (named["basis"].str.len() > 20).all(), "a type was asserted with no basis"
