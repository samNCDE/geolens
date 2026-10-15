"""The calibration loop.

The tests that carry weight here are the negative ones. A loop that always
recommends adopting what it just fitted launders overfitting as improvement,
and that already happened once in this package — an earlier fit raised training
accuracy while lowering held-out accuracy from 0.922 to 0.885 on imbalanced
data, and nothing caught it. So most of what follows checks that the loop says
"keep what you have", "label more", or "this feature cannot do it" when those
are the true answers.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "loop_test_out"
CLASSES = ("confined", "partly confined", "unconfined")


@pytest.fixture(autouse=True)
def _env(tmp_path):
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_LABELS"] = str(tmp_path / "labels.jsonl")
    os.environ["GEOLENS_AUTHOR"] = "tester"
    yield
    os.environ.pop("GEOLENS_LABELS", None)


def _segments(tmp_path, ratios, ids=None, name="segs.gpkg"):
    """A segment layer with a confinement ratio and a geometry."""
    import geopandas as gpd
    from shapely.geometry import LineString

    ids = ids if ids is not None else list(range(len(ratios)))
    geoms = [LineString([(i * 100.0, 0.0), (i * 100.0 + 90.0, 0.0)])
             for i in range(len(ratios))]
    gdf = gpd.GeoDataFrame(
        {"seg_id": ids,
         "confinement_ratio": ratios,
         "confinement": ["unconfined"] * len(ratios),
         "valley_width_m": [r * 10.0 for r in ratios],
         "gradient": [0.01] * len(ratios)},
        geometry=geoms, crs="EPSG:32612",
    )
    p = tmp_path / name
    gdf.to_file(str(p), driver="GPKG")
    return str(p)


def _label(dataset, ids, truths, field_name="confinement"):
    from geolens.learn.labels import Label, add_labels, dataset_key
    add_labels([
        Label(dataset=dataset_key(dataset), feature_id=str(i),
              field=field_name, human_value=t, kind="categorical")
        for i, t in zip(ids, truths)
    ])


def _clean_split(n_per=12):
    """Ratios that a break can separate perfectly, with truth."""
    ratios = ([0.5 + 0.1 * i for i in range(n_per)] +
              [3.0 + 0.1 * i for i in range(n_per)] +
              [8.0 + 0.2 * i for i in range(n_per)])
    truth = (["confined"] * n_per + ["partly confined"] * n_per +
             ["unconfined"] * n_per)
    return ratios, truth


# -- refusing its own output --------------------------------------------------

def test_keeps_current_breaks_when_the_fit_is_no_better(tmp_path):
    """The defaults (2, 5) already split this data perfectly.

    A re-fit cannot beat them, and the loop must say so rather than adopting a
    cosmetically different set of breaks that scores the same.
    """
    from geolens.learn.loop import run_cycle

    ratios, truth = _clean_split()
    seg = _segments(tmp_path, ratios)
    _label(seg, range(len(ratios)), truth)

    r = run_cycle(seg, dataset=seg, classes=CLASSES, current_breaks=(2.0, 5.0))
    assert r.recommendation == "keep_current", r.reasons
    assert r.current_score is not None and r.current_score >= 0.99
    assert any("not an improvement" in x for x in r.reasons)


def test_adopts_when_the_fit_genuinely_beats_the_incumbent(tmp_path):
    """Same data, but the incumbent breaks are badly placed.

    Both branches have to be reachable, or "keep_current" is not a decision,
    it is a constant.
    """
    from geolens.learn.loop import run_cycle

    ratios, truth = _clean_split()
    seg = _segments(tmp_path, ratios)
    _label(seg, range(len(ratios)), truth)

    r = run_cycle(seg, dataset=seg, classes=CLASSES, current_breaks=(0.2, 0.3))
    assert r.recommendation == "adopt", r.reasons
    assert r.fitted_score > r.current_score


def test_a_feature_that_cannot_separate_says_so(tmp_path):
    """Random ratios against random classes.

    The honest answer is that the feature cannot do it — not "label more",
    which would send someone off to generate hundreds of useless labels.
    """
    from geolens.learn.loop import run_cycle

    rng = np.random.default_rng(0)
    n = 60
    ratios = rng.uniform(0.5, 12.0, n).tolist()
    truth = rng.choice(CLASSES, n).tolist()
    seg = _segments(tmp_path, ratios)
    _label(seg, range(n), truth)

    r = run_cycle(seg, dataset=seg, classes=CLASSES, current_breaks=(2.0, 5.0))
    assert r.recommendation == "feature_does_not_separate", (
        f"{r.recommendation}: {r.reasons}"
    )
    assert r.separable is False
    assert any("will not fix" in x for x in r.reasons)


def test_too_few_labels_refuses_to_report_a_score(tmp_path):
    """With six labels a held-out score is noise. Do not produce one."""
    from geolens.learn.loop import run_cycle

    ratios = [0.5, 0.8, 3.0, 3.4, 8.0, 9.0]
    truth = ["confined", "confined", "partly confined", "partly confined",
             "unconfined", "unconfined"]
    seg = _segments(tmp_path, ratios)
    _label(seg, range(len(ratios)), truth)

    r = run_cycle(seg, dataset=seg, classes=CLASSES, min_labels=12)
    assert r.recommendation == "label_more"
    assert r.fitted_score is None
    assert r.fitted_breaks is None


def test_held_out_score_is_not_the_training_score(tmp_path):
    """On noisy data the two must differ, or cross-validation is not happening."""
    from geolens.learn.loop import run_cycle

    rng = np.random.default_rng(3)
    ratios, truth = [], []
    for cls, centre in zip(CLASSES, (1.5, 3.5, 6.0)):
        vals = rng.normal(centre, 1.6, 20)      # heavily overlapping
        ratios += vals.tolist()
        truth += [cls] * 20
    seg = _segments(tmp_path, ratios)
    _label(seg, range(len(ratios)), truth)

    r = run_cycle(seg, dataset=seg, classes=CLASSES)
    assert r.train_score is not None and r.fitted_score is not None
    assert r.fitted_score < r.train_score, (
        "held-out equals in-sample — the fit is being scored on its own data"
    )


# -- folds and the curve ------------------------------------------------------

def test_folds_keep_rare_classes_present(tmp_path):
    """Imbalance is the normal case: 90% unconfined is an ordinary landscape."""
    from geolens.learn.loop import _stratified_folds

    truth = np.array(["confined"] * 5 + ["unconfined"] * 95)
    folds = _stratified_folds(truth, 5)
    assert len(folds) == 5
    for f in folds:
        assert "confined" in truth[f], "a fold has no confined segments"


def test_fold_count_drops_when_a_class_is_scarce(tmp_path):
    from geolens.learn.loop import run_cycle

    ratios = ([0.5, 0.7] + [3.0 + 0.1 * i for i in range(15)] +
              [8.0 + 0.1 * i for i in range(15)])
    truth = ["confined"] * 2 + ["partly confined"] * 15 + ["unconfined"] * 15
    seg = _segments(tmp_path, ratios)
    _label(seg, range(len(ratios)), truth)

    r = run_cycle(seg, dataset=seg, classes=CLASSES, folds=5)
    assert r.folds <= 2
    assert any("rarest class" in w for w in r.warnings)


def test_learning_curve_reports_a_stopping_signal(tmp_path):
    from geolens.learn.loop import run_cycle

    ratios, truth = _clean_split(15)
    seg = _segments(tmp_path, ratios)
    _label(seg, range(len(ratios)), truth)

    r = run_cycle(seg, dataset=seg, classes=CLASSES)
    assert len(r.learning_curve) == 4
    assert r.learning_curve[0]["n_labels"] < r.learning_curve[-1]["n_labels"]
    assert any("learning curve" in x for x in r.reasons)


# -- worksheets ---------------------------------------------------------------

def test_worksheet_has_an_empty_column_and_does_not_prefill_it(tmp_path):
    """Pre-filling the model's answer would collect agreement, not judgement.

    People confirm a default far more readily than they generate an answer, so
    the model's guess goes in its own column and the human's stays blank.
    """
    import geopandas as gpd
    from geolens.learn.loop import write_worksheet, WORKSHEET_COLUMN

    ratios, _ = _clean_split()
    seg = _segments(tmp_path, ratios)
    ws = write_worksheet(seg, [0, 1, 2, 3], out_prefix="w1")

    gdf = gpd.read_file(ws["worksheet"])
    assert len(gdf) == 4
    assert WORKSHEET_COLUMN in gdf.columns
    assert all((v is None or str(v).strip() == "") for v in gdf[WORKSHEET_COLUMN])
    assert "model_class" in gdf.columns


def test_ingest_skips_blanks_and_keeps_agreements(tmp_path):
    """A blank means "not judged", which is not the same as agreeing.

    Agreements are kept, though: a store holding only corrections would make
    the tool look worse than it is and bias every fit toward its failures.
    """
    import geopandas as gpd
    from geolens.learn.loop import (WORKSHEET_COLUMN, ingest_worksheet,
                                    write_worksheet)

    ratios, _ = _clean_split()
    seg = _segments(tmp_path, ratios)
    ws = write_worksheet(seg, [0, 1, 2, 3], out_prefix="w2")

    gdf = gpd.read_file(ws["worksheet"])
    gdf.loc[0, WORKSHEET_COLUMN] = "confined"
    gdf.loc[1, WORKSHEET_COLUMN] = "unconfined"      # differs from the model
    gdf.loc[2, WORKSHEET_COLUMN] = ""                # blank -> skipped
    gdf.loc[3, WORKSHEET_COLUMN] = "unconfined"      # matches the model
    filled = tmp_path / "filled.gpkg"
    gdf.to_file(str(filled), driver="GPKG")

    r = ingest_worksheet(str(filled), dataset=seg)
    assert r["added"] == 3
    assert r["skipped_blank"] == 1
    assert r["agreed_with_model"] >= 1
    assert r["disagreed"] >= 1


def test_round_trip_reaches_the_fit(tmp_path):
    """Suggest, fill, ingest, re-fit — the whole loop in one test."""
    import geopandas as gpd
    from geolens.learn.loop import (WORKSHEET_COLUMN, ingest_worksheet,
                                    run_cycle, write_worksheet)

    ratios, truth = _clean_split()
    seg = _segments(tmp_path, ratios)

    first = run_cycle(seg, dataset=seg, classes=CLASSES, n_suggest=30)
    assert first.recommendation == "label_more"
    assert first.worksheet, "no worksheet was produced to label"

    gdf = gpd.read_file(first.worksheet)
    by_id = {str(i): t for i, t in zip(range(len(ratios)), truth)}
    gdf[WORKSHEET_COLUMN] = [by_id[str(i)] for i in gdf["seg_id"]]
    filled = tmp_path / "filled2.gpkg"
    gdf.to_file(str(filled), driver="GPKG")

    ing = ingest_worksheet(str(filled), dataset=seg)
    assert ing["added"] == len(gdf)

    second = run_cycle(seg, dataset=seg, classes=CLASSES,
                       current_breaks=(0.2, 0.3))
    assert second.n_labels == len(gdf)
    assert second.recommendation in ("adopt", "keep_current")
    assert second.fitted_breaks is not None


def test_history_accumulates_across_cycles(tmp_path):
    """Whether it is improving is only visible across rounds."""
    from geolens.learn.loop import cycle_history, run_cycle

    ratios, truth = _clean_split()
    seg = _segments(tmp_path, ratios)
    _label(seg, range(len(ratios)), truth)

    run_cycle(seg, dataset=seg, classes=CLASSES)
    run_cycle(seg, dataset=seg, classes=CLASSES)
    h = cycle_history()
    assert len(h) >= 2
    assert {"n_labels", "recommendation", "fitted_score"} <= set(h[0])


def test_null_baseline_is_reported_both_ways(tmp_path):
    """The null must be visible, and clean data must clear it comfortably.

    Pinning both sides: a criterion that nothing ever passes is as useless as
    one that everything passes.
    """
    from geolens.learn.loop import run_cycle

    ratios, truth = _clean_split(15)
    seg = _segments(tmp_path, ratios)
    _label(seg, range(len(ratios)), truth)

    r = run_cycle(seg, dataset=seg, classes=CLASSES, current_breaks=(0.2, 0.3))
    assert r.null_score is not None
    assert r.fitted_score > r.null_score, (
        "perfectly separable data failed its own null — the bar is too high"
    )
    assert r.separable is not False
