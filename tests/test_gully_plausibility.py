"""Does the gully detector say when what it found is not a gully?

The module warned when the DEM was too coarse to HOLD a gully. It said nothing
about the far more common failure: correct code, wrong parameters, and an
output full of creeks.

Measured on a real 1.94-billion-cell run: 10,739 features totalling 3,195 km
against a 1,767 km stream network — 1.8x the whole drainage network — median
width 31 m, depths to 37 m, 28% draining more than 1 km². Only 2.7% were
gully-scale. Every number was computed correctly and none of it was gullies.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "gplaus_test_out"
FIX = ROOT / "gullies" / "gully_dem.tif"


@pytest.fixture(scope="module", autouse=True)
def _env():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"


@pytest.fixture(scope="module")
def terrain():
    if not FIX.exists():
        pytest.skip("gully fixture not built")
    from geolens.landscape import build_terrain
    return build_terrain(str(FIX), stream_threshold_km2=0.2, out_prefix="gp_t")


def test_a_well_posed_run_is_not_warned_about(terrain):
    """The fixture at sane parameters must stay quiet, or the checks are noise."""
    from geolens.landscape import terrain_roughness, detect_gullies

    rg = terrain_roughness(terrain, window_m=40.0)
    g = detect_gullies(terrain, roughness=rg, slope_std_min=0.04, tpi_max=-0.3,
                       min_length_m=100.0, section_width_m=60.0,
                       out_prefix="gp_ok")
    noisy = [w for w in g.warnings
             if "drain more than" in w or "drainage network, not gullies" in w]
    assert not noisy, f"a good run was warned about: {noisy}"
    assert g.gully_count > 0


def test_it_says_when_it_has_mapped_the_stream_network():
    """The Ruidoso failure, reproduced from its own numbers.

    The fixtures here are too small to provoke this from a real run — the
    stream network is a few hundred metres — so the condition is tested
    directly, which is why it lives in its own function.
    """
    import pandas as pd
    from geolens.landscape.features import _gully_scale_warnings

    n = 10_739
    rng = np.random.default_rng(0)
    gdf = pd.DataFrame({
        "width_m": rng.uniform(15, 55, n),
        # 28% draining over 1 km2, median 0.21 — the measured distribution
        "drainage_km2_max": rng.lognormal(np.log(0.21), 1.9, n),
        "bank_sections": rng.integers(0, 6, n),
    })
    w = _gully_scale_warnings(gdf, section_width_m=60.0,
                              total_km=3195.0, network_km=1766.7)
    joined = " ".join(w)
    assert "drain more than 1 km2" in joined, w
    assert "drainage network, not gullies" in joined, w
    assert "1.8x" in joined, w


def test_a_short_network_does_not_trigger_the_ratio_check():
    """On a small extent the network can be 200 m; every honest run beats it."""
    import pandas as pd
    from geolens.landscape.features import _gully_scale_warnings

    gdf = pd.DataFrame({"width_m": [5.0, 6.0], "drainage_km2_max": [0.01, 0.02],
                        "bank_sections": [8, 9]})
    w = _gully_scale_warnings(gdf, section_width_m=60.0, total_km=7.0,
                              network_km=0.2)
    assert not any("drainage network" in x for x in w), w


def test_width_saturation_is_flagged():
    import pandas as pd
    from geolens.landscape.features import _gully_scale_warnings

    # 20% pinned at the section width, the rest real
    w_vals = [55.2] * 20 + list(np.linspace(4, 30, 80))
    gdf = pd.DataFrame({"width_m": w_vals,
                        "drainage_km2_max": [0.01] * 100,
                        "bank_sections": [8] * 100})
    w = _gully_scale_warnings(gdf, section_width_m=60.0, total_km=5.0,
                              network_km=50.0)
    assert any("width of the cross-section itself" in x for x in w), w


def test_a_clean_result_produces_no_scale_warnings():
    """These must be silent on a good run or they are noise."""
    import pandas as pd
    from geolens.landscape.features import _gully_scale_warnings

    rng = np.random.default_rng(1)
    gdf = pd.DataFrame({
        "width_m": rng.uniform(3, 12, 200),
        "drainage_km2_max": rng.uniform(0.005, 0.05, 200),
        "bank_sections": rng.integers(5, 20, 200),
    })
    w = _gully_scale_warnings(gdf, section_width_m=60.0, total_km=20.0,
                              network_km=400.0)
    assert w == [], w


def test_it_says_when_most_features_are_barely_measured(terrain):
    from geolens.landscape import terrain_roughness, detect_gullies

    rg = terrain_roughness(terrain, window_m=40.0)
    g = detect_gullies(terrain, roughness=rg, slope_std_min=0.0,
                       tpi_max=-0.01, min_slope=0.0, min_length_m=20.0,
                       section_width_m=60.0, out_prefix="gp_thin")
    if g.gully_count:
        import geopandas as gpd
        gdf = gpd.read_file(g.gully_lines)
        thin = float((gdf["bank_sections"].astype(float) < 3).mean())
        if thin > 0.5:
            assert any("fewer than 3 cross-sections" in w for w in g.warnings), (
                f"{thin:.0%} of gullies rest on <3 sections, unremarked"
            )


# -- the size ceiling ---------------------------------------------------------
#
# Added after the Ruidoso run. Slope variability, TPI and slope are every bit
# as true of a river as of a gully; `max_drainage_km2` is the only criterion in
# the whole detector that means *small*. It shipped defaulting to None, with a
# docstring that argued for leaving it there, and the result was the drainage
# network returned as gullies. These two tests are what stop that regressing.

def test_the_drainage_ceiling_is_on_by_default(terrain):
    """Not "None is a reasonable default" — None is the failure mode."""
    import inspect
    from geolens.landscape import detect_gullies

    default = inspect.signature(detect_gullies).parameters["max_drainage_km2"].default
    assert default is not None and default > 0, (
        "max_drainage_km2 defaulted back to None. With no area ceiling nothing "
        "in the criteria distinguishes a gully from a creek, and the detector "
        "returns the drainage network."
    )


def test_disabling_the_ceiling_says_what_that_costs(terrain):
    """If the user turns the only size test off, they should be told."""
    from geolens.landscape import terrain_roughness, detect_gullies

    rg = terrain_roughness(terrain, window_m=40.0)
    g = detect_gullies(terrain, roughness=rg, slope_std_min=0.04, tpi_max=-0.3,
                       max_drainage_km2=None, min_length_m=100.0,
                       section_width_m=60.0, out_prefix="gp_unbounded")
    assert any("unbounded" in w for w in g.warnings), (
        f"no warning when the area ceiling was disabled: {g.warnings}"
    )


def test_the_ceiling_actually_bounds_what_comes_back(terrain):
    """The warning is worthless if the filter does not bite."""
    from geolens.landscape import terrain_roughness, detect_gullies
    import geopandas as gpd

    rg = terrain_roughness(terrain, window_m=40.0)
    g = detect_gullies(terrain, roughness=rg, slope_std_min=0.04, tpi_max=-0.3,
                       max_drainage_km2=0.02, min_length_m=100.0,
                       section_width_m=60.0, out_prefix="gp_tight")
    if not g.gully_count:
        pytest.skip("ceiling admitted nothing on this fixture")
    gdf = gpd.read_file(g.gully_lines)
    over = gdf["drainage_km2_max"].dropna()
    assert (over <= 0.02 + 1e-9).all(), (
        f"max drainage {float(over.max()):.4f} km2 exceeds the 0.02 ceiling"
    )


def test_it_says_when_exclude_streams_will_eat_the_gullies(terrain):
    """The coupling that is invisible until you look at the numbers.

    ``terrain`` here is built at stream_threshold_km2=0.2. Asking for gullies
    up to 0.5 km2 while excluding streams means every cell draining between
    0.2 and 0.5 km2 is gully-eligible AND a mapped stream, and the exclusion
    wins — so the largest gullies vanish with no indication that they were
    ever candidates.
    """
    from geolens.landscape import terrain_roughness, detect_gullies

    rg = terrain_roughness(terrain, window_m=40.0)
    g = detect_gullies(terrain, roughness=rg, slope_std_min=0.04, tpi_max=-0.3,
                       max_drainage_km2=0.5, exclude_streams=True,
                       min_length_m=100.0, section_width_m=60.0,
                       out_prefix="gp_clash")
    assert any("both gully-eligible and a mapped stream" in w
               for w in g.warnings), f"the clash went unreported: {g.warnings}"


def test_matched_thresholds_are_not_warned_about(terrain):
    """And the guard must stay quiet when the two agree."""
    from geolens.landscape import terrain_roughness, detect_gullies

    rg = terrain_roughness(terrain, window_m=40.0)
    g = detect_gullies(terrain, roughness=rg, slope_std_min=0.04, tpi_max=-0.3,
                       max_drainage_km2=terrain.stream_threshold_km2,
                       exclude_streams=True, min_length_m=100.0,
                       section_width_m=60.0, out_prefix="gp_matched")
    assert not any("both gully-eligible" in w for w in g.warnings), (
        f"matched thresholds were warned about: {g.warnings}"
    )
