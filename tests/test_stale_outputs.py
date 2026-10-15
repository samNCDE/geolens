"""Outputs that are stale, colliding, or claimed but never written.

All three of these return a path or a row that looks right and points at the
wrong thing. There is no error in any of them.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "stale_test_out"


@pytest.fixture(autouse=True)
def _env():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ.pop("GEOLENS_PERSIST", None)


# -- the run log --------------------------------------------------------------

def test_runs_in_the_same_second_do_not_overwrite_each_other():
    """A calibration sweep is a loop of fast calls with different arguments.

    The JSON was written with write_text (overwrite) while the manifest was
    appended, so N manifest rows pointed at ONE file holding the last result.
    """
    from geolens.persist import save_result, history

    for i in range(5):
        save_result("fake_call", {"run": i, "score": i / 10.0},
                    args={"threshold": i}, seconds=0.01)

    rows = history(limit=50, function="fake_call")
    assert len(rows) == 5, f"{len(rows)} manifest rows for 5 runs"

    records = {r["record"] for r in rows}
    assert len(records) == 5, (
        f"5 runs collapsed to {len(records)} file(s) — later runs overwrote "
        f"earlier ones inside the same second"
    )

    seen = set()
    for r in rows:
        payload = json.loads(Path(r["record"]).read_text())
        seen.add(payload["result"]["run"])
    assert seen == {0, 1, 2, 3, 4}, f"recovered runs {sorted(seen)}"


def test_each_manifest_row_points_at_a_file_that_exists():
    from geolens.persist import save_result, history

    for i in range(3):
        save_result("fake_call", {"run": i}, args={"i": i}, seconds=0.01)
    for r in history(limit=10, function="fake_call"):
        assert Path(r["record"]).exists(), f"missing record {r['record']}"


# -- headcuts -----------------------------------------------------------------

def test_a_headcut_run_that_finds_nothing_does_not_return_the_last_run():
    """The result returns its path unconditionally; the write was conditional.

    Guard reuses the output directory across runs, so a run that matched
    nothing handed back the PREVIOUS run's features with headcut_count=0
    beside them.
    """
    import geopandas as gpd
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    from geolens.landscape import detect_headcuts

    topo = pytest.importorskip("topotoolbox")  # noqa: F841

    fix = ROOT / "gullies" / "gully_dem.tif"
    if not fix.exists():
        pytest.skip("gully fixture not built")

    first = detect_headcuts(str(fix), stream_threshold_km2=0.005,
                            min_excess_drop_m=0.5, out_prefix="stale_hc")
    assert first.headcut_count > 0, "fixture found nothing; test proves nothing"

    # Same output prefix, a threshold nothing can pass.
    second = detect_headcuts(str(fix), stream_threshold_km2=0.005,
                             min_excess_drop_m=10_000.0, out_prefix="stale_hc")
    assert second.headcut_count == 0

    gdf = gpd.read_file(second.headcuts)
    assert len(gdf) == 0, (
        f"a run reporting 0 headcuts handed back a layer with {len(gdf)} "
        f"features — the previous run's"
    )


def test_a_first_run_with_no_headcuts_still_writes_a_readable_layer():
    import geopandas as gpd
    from geolens.landscape import detect_headcuts

    pytest.importorskip("topotoolbox")
    fix = ROOT / "gullies" / "gully_dem.tif"
    if not fix.exists():
        pytest.skip("gully fixture not built")

    r = detect_headcuts(str(fix), stream_threshold_km2=0.005,
                        min_excess_drop_m=10_000.0, out_prefix="stale_hc_new")
    assert r.headcut_count == 0
    assert Path(r.headcuts).exists(), (
        "the returned path does not exist, so reading the result raises"
    )
    assert len(gpd.read_file(r.headcuts)) == 0
