"""Where should the gully roughness threshold go?

The notebook shipped "gullies usually sit above p90; start slope_std_min
there". On a 1.94-billion-cell DEM p90 is 190 million cells by construction,
and the run came back with 3,195 km of "gullies" against a 1,767 km stream
network. The obvious correction -- p99 -- is arbitrary in the same way and
measurably worse: on both fixtures here p97 and above return nothing at all,
because the top of the slope_std distribution is valley walls and mountain
flanks, not small incised features.

So neither percentile is a threshold; they are both statements about how much
of the landscape you have decided to call gullied. The distribution itself has
the answer -- it is bimodal in the log, and the threshold belongs in the gap.
These tests hold that claim to its numbers.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "sss_test_out"
FIX = ROOT / "gullies" / "gully_dem.tif"


@pytest.fixture(scope="module", autouse=True)
def _env():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"


def _flat_dem(path: Path, roughness: float = 0.15) -> str:
    """A tilted plane with noise on it. No gullies, so no gap to find."""
    import rasterio
    from rasterio.transform import from_origin

    n, cell = 400, 5.0
    yy, xx = np.mgrid[0:n, 0:n].astype("float64")
    z = 1500.0 - 0.02 * yy * cell + 0.004 * np.abs(xx - n / 2) * cell
    z = (z + np.random.default_rng(3).normal(0, roughness, z.shape)).astype("float32")
    prof = {"driver": "GTiff", "width": n, "height": n, "count": 1,
            "dtype": "float32", "crs": "EPSG:32612", "nodata": -9999.0,
            "transform": from_origin(500000, 4000000, cell, cell)}
    with rasterio.open(str(path), "w", **prof) as dst:
        dst.write(z, 1)
    return str(path)


@pytest.fixture(scope="module")
def gullied():
    if not FIX.exists():
        pytest.skip("gully fixture not built")
    from geolens.landscape import build_terrain, terrain_roughness
    t = build_terrain(str(FIX), stream_threshold_km2=0.2, out_prefix="sss_t")
    return t, terrain_roughness(t, window_m=40.0)


def test_the_suggestion_lands_in_the_gap_not_at_a_percentile(gullied):
    """p75 = 0.0173, p90 = 0.1561 on this fixture — a 9x jump. In there."""
    from geolens.landscape import suggest_slope_std_min
    from geolens.raster import percentiles

    t, rg = gullied
    p = percentiles(rg.slope_std, [75, 90])
    s = suggest_slope_std_min(rg.slope_std)

    assert p["p75"] < s["slope_std_min"] < p["p90"], (
        f"suggestion {s['slope_std_min']:.4f} fell outside the gap "
        f"p75={p['p75']:.4f}..p90={p['p90']:.4f}"
    )
    assert s["bimodal"], f"gullied terrain read as unimodal: {s}"


def test_it_recovers_gullies_of_the_width_they_were_built_at(gullied):
    """A threshold that finds nothing is not a better threshold."""
    from geolens.landscape import detect_gullies, suggest_slope_std_min

    t, rg = gullied
    s = suggest_slope_std_min(rg.slope_std)
    g = detect_gullies(t, roughness=rg, slope_std_min=s["slope_std_min"],
                       tpi_max=-0.3, max_drainage_km2=0.5, exclude_streams=True,
                       min_length_m=100.0, section_width_m=60.0,
                       out_prefix="sss_g")
    assert g.gully_count > 0, "the suggested threshold found no gullies"
    # The fixture's gullies are built 14 m wide.
    assert 10.0 <= g.mean_width_m <= 22.0, (
        f"mean width {g.mean_width_m:.1f} m against a built 14 m"
    )


def test_the_top_percentiles_find_nothing_which_is_why_p99_is_wrong(gullied):
    """Guards the reasoning, not just the code.

    If some future change makes p99 work fine on this fixture, the argument in
    ``suggest_slope_std_min`` is out of date and should be rewritten rather
    than left standing as folklore.
    """
    from geolens.landscape import detect_gullies
    from geolens.raster import percentiles

    t, rg = gullied
    p99 = percentiles(rg.slope_std, [99])["p99"]
    g = detect_gullies(t, roughness=rg, slope_std_min=p99, tpi_max=-0.3,
                       max_drainage_km2=0.5, exclude_streams=True,
                       min_length_m=100.0, section_width_m=60.0,
                       out_prefix="sss_p99")
    assert g.gully_count == 0, (
        f"p99 now finds {g.gully_count} gullies on this fixture — the docstring "
        f"in suggest_slope_std_min still claims it finds none. Fix the words."
    )


@pytest.mark.parametrize("roughness", [0.15, 1.0])
def test_terrain_with_no_gullies_is_reported_as_having_no_gap(roughness):
    """Otsu always returns a split. ``bimodal`` is what says it means nothing.

    Measured separations: 1.02 and 1.20 decades on the two gullied fixtures,
    0.068 and 0.083 here. The 0.4 cutoff sits in that gap.
    """
    from geolens.landscape import build_terrain, terrain_roughness
    from geolens.landscape import suggest_slope_std_min

    dem = _flat_dem(OUT / f"flat_{roughness}.tif", roughness=roughness)
    t = build_terrain(dem, stream_threshold_km2=0.05, out_prefix=f"sss_f{roughness}")
    rg = terrain_roughness(t, window_m=40.0)
    s = suggest_slope_std_min(rg.slope_std)

    assert not s["bimodal"], (
        f"a plane with noise on it read as bimodal: sep "
        f"{s['separation_decades']:.3f} decades"
    )
    assert "not clearly bimodal" in s["note"]
    assert s["separation_decades"] < 0.4


def test_it_refuses_a_raster_with_nothing_in_it():
    """An all-flat raster has no distribution to split; say so."""
    import rasterio
    from rasterio.transform import from_origin
    from geolens.safety import GeolensError
    from geolens.landscape import suggest_slope_std_min

    p = OUT / "zeros.tif"
    prof = {"driver": "GTiff", "width": 50, "height": 50, "count": 1,
            "dtype": "float32", "crs": "EPSG:32612", "nodata": -9999.0,
            "transform": from_origin(0, 0, 1, 1)}
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(np.zeros((50, 50), "float32"), 1)
    with pytest.raises(GeolensError, match="no distribution"):
        suggest_slope_std_min(str(p))
