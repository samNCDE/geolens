"""Results land on disk automatically.

The requirement: anything expensive to reproduce gets written. Rasters and
vectors were already written by the functions that make them; what was missing
was the numbers, the parameters that produced them, and any record that a run
happened at all.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
COMPOUND = ROOT / "compound"
OUT = ROOT / "persist_test_out"


@pytest.fixture(scope="module", autouse=True)
def _env():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ.pop("GEOLENS_PERSIST", None)
    if not (COMPOUND / "compound_dem.tif").exists():
        subprocess.run([sys.executable, str(ROOT / "make_compound.py")], check=True)


def _profile():
    from geolens import api
    return api.longitudinal_profile(str(COMPOUND / "compound_dem.tif"),
                                    str(COMPOUND / "centerline.shp"), spacing=10)


def test_a_run_is_recorded(_env):
    from geolens import api

    r = _profile()
    assert "_run_record" in r, "the result should say where it was saved"
    rec = Path(r["_run_record"])
    assert rec.exists()

    saved = json.loads(rec.read_text())
    assert saved["function"] == "longitudinal_profile"
    assert saved["seconds"] is not None
    # The arguments matter as much as the result: without them you cannot tell
    # which settings produced the numbers.
    assert saved["arguments"]["spacing"] == 10
    assert "reach_slope" in saved["result"]


def test_tables_come_out_as_csv(_env):
    import pandas as pd

    r = _profile()
    rec = Path(r["_run_record"])
    csvs = list(rec.parent.glob(rec.stem + "_*.csv"))
    assert csvs, "the profile samples should be written as a table"
    df = pd.read_csv(csvs[0])
    assert len(df) > 10


def test_manifest_indexes_every_run(_env):
    from geolens import api

    before = len(api.run_history()["runs"])
    _profile()
    _profile()
    after = api.run_history()["runs"]
    assert len(after) == before + 2
    assert after[0]["function"] == "longitudinal_profile"
    # Newest first, so a session can be read backwards from what just happened.
    assert after[0]["utc"] >= after[-1]["utc"]


def test_history_filters_by_function(_env):
    from geolens import api

    _profile()
    runs = api.run_history(function="longitudinal_profile")["runs"]
    assert runs and all(r["function"] == "longitudinal_profile" for r in runs)
    assert api.run_history(function="nothing_by_this_name")["runs"] == []


def test_a_saved_run_loads_back(_env):
    from geolens import api

    r = _profile()
    again = api.load_run(r["_run_record"])
    assert again["result"]["reach_slope"] == pytest.approx(r["reach_slope"])


def test_cheap_reads_are_not_logged(_env):
    """describe_raster runs constantly and produces nothing expensive.

    Logging it would bury the runs that matter.
    """
    from geolens import api

    before = len(api.run_history()["runs"])
    api.describe_raster(str(COMPOUND / "compound_dem.tif"))
    api.workspace()
    assert len(api.run_history()["runs"]) == before


def test_persistence_can_be_switched_off(_env, monkeypatch):
    from geolens import api

    before = len(api.run_history()["runs"])
    monkeypatch.setenv("GEOLENS_PERSIST", "0")
    r = _profile()
    assert "_run_record" not in r
    monkeypatch.delenv("GEOLENS_PERSIST")
    assert len(api.run_history()["runs"]) == before


def test_logging_failure_never_fails_the_run(_env, monkeypatch):
    """Losing the record is not a reason to lose the result."""
    from geolens import persist

    def boom(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(persist, "_runs_dir", boom)
    r = _profile()
    assert "reach_slope" in r, "the analysis must still return"
    assert "_run_record" not in r


def test_nan_survives_serialisation(_env):
    """NaN is not valid JSON; it must become null rather than corrupt the file."""
    from geolens.persist import _jsonable
    import numpy as np

    out = _jsonable({"a": np.float64("nan"), "b": np.int64(3),
                     "c": np.array([1.0, 2.0])})
    assert out["a"] is None
    assert out["b"] == 3
    assert out["c"] == [1.0, 2.0]
    json.dumps(out)  # must not raise
