"""The calibration loop: run, check by hand, feed back, re-fit, repeat.

The pieces for this already existed separately — a label store, an exact
break-fitter, uncertainty sampling — and nothing joined them. This is the join,
plus the two things that decide whether a loop like this helps or quietly makes
the answer worse.

THE LOOP
--------
1. ``write_worksheet`` picks the segments worth labelling and writes them as a
   GeoPackage with an empty column. Open it in QGIS or ArcGIS Pro, look at each
   one, type the class, save.
2. ``ingest_worksheet`` reads the filled file back into the label store.
3. ``run_cycle`` re-fits, evaluates **on data it did not fit**, and says whether
   to adopt the new breaks — including saying no.

THE TWO THINGS THAT MATTER
--------------------------
**It must be able to refuse its own output.** A calibration loop that always
recommends adopting what it just fitted is worse than no loop, because it
launders overfitting as improvement. This one already has the scar: an earlier
fit here raised training accuracy while *lowering* held-out accuracy from 0.922
to 0.885 on 92%-imbalanced data. Every number below is cross-validated, the
incumbent breaks are scored on exactly the same folds, and ``recommendation``
comes back as ``keep_current`` whenever the new fit does not actually beat them.

**Not separating is a real answer.** If the confinement ratio genuinely cannot
tell your classes apart — because the channel-width relation is wrong, or
because two classes are the same thing in this landscape — then no amount of
labelling fixes it, and more labels will keep looking like progress. The
separability verdict says so instead.

WHAT ABOUT SATELLITE LABELS
---------------------------
Observed flood extent (see :mod:`geolens.inundation`) can supply positive
labels, but it is a supplement, not the source. In arid and intermittent
systems it is close to useless — see that module's docstring. The primary label
source is a person looking at a reach and saying what it is, which is also the
only source that carries local knowledge the DEM does not contain.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..safety import Guard, GeolensError, note
from .calibrate import fit_ordered_breaks, suggest_next
from .labels import Label, add_labels, dataset_key, load_labels

WORKSHEET_COLUMN = "human_class"
HISTORY_FILE = "calibration_history.jsonl"


@dataclass
class CycleResult:
    n_labels: int
    classes: list[str]
    fitted_breaks: list[float] | None
    current_breaks: list[float] | None
    fitted_score: float | None          # cross-validated, held out
    current_score: float | None         # same folds, incumbent breaks
    train_score: float | None           # in-sample, for the overfitting gap
    folds: int
    separable: bool | None
    null_score: float | None            # same pipeline on shuffled labels
    learning_curve: list[dict[str, Any]]
    recommendation: str
    reasons: list[str]
    worksheet: str | None
    suggested: list[Any]
    per_class_recall: dict[str, float]
    confusion: dict[str, Any]
    seconds: float
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


# -- cross validation ---------------------------------------------------------


def _stratified_folds(truth: np.ndarray, k: int, seed: int = 0) -> list[np.ndarray]:
    """Fold assignment that keeps every class present in every fold.

    Plain random folds fail badly here: with 8 confined segments out of 300, an
    unstratified 5-fold split routinely produces a fold with no confined
    segments at all, and balanced accuracy on that fold is undefined or wildly
    noisy. Stratifying makes the held-out score mean something.
    """
    rng = np.random.default_rng(seed)
    folds = np.empty(len(truth), dtype=int)
    for cls in np.unique(truth):
        idx = np.flatnonzero(truth == cls)
        rng.shuffle(idx)
        folds[idx] = np.arange(len(idx)) % k
    return [np.flatnonzero(folds == i) for i in range(k)]


def _score_breaks(values: np.ndarray, truth: np.ndarray, breaks: Sequence[float],
                  classes: Sequence[str]) -> tuple[float, dict[str, float]]:
    """Balanced accuracy — mean per-class recall — for a given set of breaks."""
    pred = np.array(classes)[np.searchsorted(np.asarray(breaks, dtype=float),
                                             values, side="right")]
    recalls: dict[str, float] = {}
    for cls in classes:
        m = truth == cls
        recalls[cls] = float((pred[m] == cls).mean()) if m.any() else float("nan")
    present = [r for r in recalls.values() if r == r]
    return (float(np.mean(present)) if present else float("nan")), recalls


def _cv_score(values: np.ndarray, truth: np.ndarray, classes: Sequence[str],
              k: int, seed: int = 0,
              fixed_breaks: Sequence[float] | None = None) -> float:
    """Mean held-out balanced accuracy.

    With ``fixed_breaks`` the breaks are not re-fitted — that is how the
    incumbent is scored on identical folds, which is the only fair comparison.
    """
    scores = []
    for test in _stratified_folds(truth, k, seed):
        train = np.setdiff1d(np.arange(len(truth)), test)
        if len(train) < len(classes) or len(test) == 0:
            continue
        if fixed_breaks is None:
            try:
                fit = fit_ordered_breaks(values[train], truth[train], classes,
                                         balanced=True)
            except GeolensError:
                continue
            brk = fit["breaks"]
        else:
            brk = list(fixed_breaks)
        s, _ = _score_breaks(values[test], truth[test], brk, classes)
        if s == s:
            scores.append(s)
    return float(np.mean(scores)) if scores else float("nan")


def _permutation_baseline(values: np.ndarray, truth: np.ndarray,
                          classes: Sequence[str], k: int,
                          n_shuffles: int = 30) -> dict[str, float]:
    """How well this exact procedure scores on SHUFFLED labels.

    This is the null model, and it is the check that matters most. The
    separability verdict from ``fit_ordered_breaks`` is computed in-sample, so
    with enough labels an exact break search can fit noise well enough to look
    separable. Cross-validation alone does not save you either: with small
    folds the search still has room to exploit structure that is not there.

    Scoring the same pipeline against randomised labels answers the real
    question -- "how high would this number be if the feature carried no
    information at all?" -- and on 60 random points with 3 classes that answer
    is around 0.44, not the 0.33 chance level you would predict. Without this
    baseline the loop recommended adopting a fit on pure noise, because 0.45
    beat the incumbent's 0.34. It is not a hypothetical failure; it happened.
    """
    rng = np.random.default_rng(11)
    scores = []
    for _ in range(n_shuffles):
        shuffled = rng.permutation(truth)
        s = _cv_score(values, shuffled, classes, k)
        if s == s:
            scores.append(s)
    if not scores:
        return {"mean": float("nan"), "p95": float("nan"), "n": 0}
    a = np.asarray(scores)
    # The bar is the null's MEAN PLUS TWO OF ITS OWN STANDARD DEVIATIONS, which
    # self-scales with how noisy the estimate is at this label count. Two weaker
    # rules were tried first and both let noise through:
    #
    #   * mean + a fixed 0.05 -- far too lenient, half the null beats its own
    #     mean by construction.
    #   * the null's 95th percentile -- better, but on 60 random points the real
    #     score landed 0.005 above it, which is a coin flip being reported as a
    #     result.
    #
    # With few labels the null is WIDE (mean 0.34, sigma 0.06 on 60 points and
    # three classes), so the bar rises to 0.46 and marginal fits are correctly
    # refused. With plenty of labels the null tightens and the bar drops, so a
    # genuine signal is not held back. The width of the null is the information.
    mean, sd = float(a.mean()), float(a.std())
    return {"mean": mean, "sd": sd, "p95": float(np.percentile(a, 95)),
            "threshold": mean + 2.0 * sd, "n": int(a.size)}


def _learning_curve(values: np.ndarray, truth: np.ndarray,
                    classes: Sequence[str], k: int) -> list[dict[str, Any]]:
    """Held-out score against label count — the stopping criterion.

    If the curve has flattened, more labelling buys nothing and the honest
    advice is to stop. If it is still climbing, it is worth another batch. This
    is the question people actually want answered and almost never get.
    """
    out = []
    n = len(truth)
    for frac in (0.25, 0.5, 0.75, 1.0):
        m = max(len(classes) * 2, int(round(n * frac)))
        if m > n:
            m = n
        rng = np.random.default_rng(7)
        idx = rng.permutation(n)[:m]
        # Keep every class represented, or the subset is not comparable.
        for cls in np.unique(truth):
            if cls not in truth[idx]:
                extra = np.flatnonzero(truth == cls)[:2]
                idx = np.unique(np.concatenate([idx, extra]))
        s = _cv_score(values[idx], truth[idx], classes, min(k, 3))
        out.append({"n_labels": int(len(idx)), "held_out_score":
                    None if s != s else round(float(s), 4)})
    return out


# -- worksheets ---------------------------------------------------------------


def write_worksheet(
    segments: str | Path,
    suggested_ids: Sequence[Any],
    field_name: str = "confinement",
    id_field: str = "seg_id",
    out_prefix: str = "worksheet",
    guard: Guard | None = None,
) -> dict[str, Any]:
    """Write the suggested segments as a file to fill in by hand.

    A GeoPackage, because the point is to open it on the map next to the DEM and
    the imagery and look at each reach. There is one empty column,
    ``human_class``; type the class, save, and hand it to
    :func:`ingest_worksheet`.

    The model's own answer travels with each row so you can see what it thought,
    but in a column named ``model_class`` rather than pre-filled into the
    human's column. Pre-filling would collect agreement rather than judgement --
    people confirm a default far more readily than they generate an answer.
    """
    import geopandas as gpd

    guard = guard or Guard.from_env()
    gdf = gpd.read_file(str(guard.read_path(segments)))
    if id_field not in gdf.columns:
        raise GeolensError(f"{segments} has no '{id_field}' column")

    want = {str(s) for s in suggested_ids}
    sel = gdf[gdf[id_field].astype(str).isin(want)].copy()
    if sel.empty:
        raise GeolensError(
            f"None of the {len(want)} suggested ids were found in {segments}."
        )

    if field_name in sel.columns:
        sel["model_class"] = sel[field_name]
    sel[WORKSHEET_COLUMN] = ""
    sel["label_notes"] = ""
    sel["confidence"] = "certain"

    keep = [id_field, "model_class", WORKSHEET_COLUMN, "label_notes",
            "confidence", "confinement_ratio", "valley_width_m",
            "channel_width_m", "drainage_km2", "gradient", "geometry"]
    cols = [c for c in keep if c in sel.columns]
    sel = sel[cols]

    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    gpkg = out_dir / "to_label.gpkg"
    sel.to_file(str(gpkg), driver="GPKG")
    csv = out_dir / "to_label.csv"
    sel.drop(columns="geometry").to_csv(csv, index=False)

    return {
        "worksheet": str(gpkg),
        "worksheet_csv": str(csv),
        "n_features": int(len(sel)),
        "column_to_fill": WORKSHEET_COLUMN,
        "instructions": (
            f"Open {gpkg} over your DEM. For each feature, put the class in "
            f"'{WORKSHEET_COLUMN}'. Leave a row blank to skip it — blanks are "
            f"ignored, not treated as disagreement. Set 'confidence' to "
            f"certain / likely / unsure; unsure rows are down-weighted in the "
            f"fit rather than discarded. Then run ingest_worksheet."
        ),
    }


def ingest_worksheet(
    worksheet: str | Path,
    dataset: str,
    field_name: str = "confinement",
    id_field: str = "seg_id",
    author: str = "",
    guard: Guard | None = None,
) -> dict[str, Any]:
    """Read a filled worksheet into the label store.

    Blank rows are skipped rather than recorded, because a blank means "I did
    not judge this one", which is not the same as agreeing with the model. Rows
    where the human matches the model ARE recorded — agreement is evidence too,
    and a store containing only corrections would make the tool look far worse
    than it is.
    """
    import geopandas as gpd
    import pandas as pd

    guard = guard or Guard.from_env()
    p = guard.read_path(worksheet)
    try:
        df = gpd.read_file(str(p))
        geom = df.geometry
    except Exception:
        df = pd.read_csv(str(p))
        geom = None

    if WORKSHEET_COLUMN not in df.columns:
        raise GeolensError(
            f"{worksheet} has no '{WORKSHEET_COLUMN}' column. Use the file "
            f"write_worksheet produced, and fill that column in."
        )

    key = dataset_key(dataset)
    labels: list[Label] = []
    blanks = 0
    for i, row in df.iterrows():
        val = row.get(WORKSHEET_COLUMN)
        if val is None or (isinstance(val, float) and val != val) or str(val).strip() == "":
            blanks += 1
            continue
        x = y = None
        if geom is not None and i in geom.index and geom.loc[i] is not None:
            try:
                c = geom.loc[i].centroid
                x, y = float(c.x), float(c.y)
            except Exception:
                pass
        conf = str(row.get("confidence", "certain") or "certain").strip().lower()
        if conf not in ("certain", "likely", "unsure"):
            conf = "certain"
        labels.append(Label(
            dataset=key,
            feature_id=str(row.get(id_field, i)),
            field=field_name,
            human_value=str(val).strip(),
            model_value=(str(row["model_class"])
                         if "model_class" in df.columns
                         and row.get("model_class") is not None else None),
            author=author,
            kind="categorical",
            x=x, y=y,
            notes=str(row.get("label_notes", "") or ""),
            confidence=conf,
        ))

    res = add_labels(labels, guard=guard)
    agreed = sum(1 for l in labels
                 if l.model_value is not None and l.human_value == l.model_value)
    return {
        "added": res["added"],
        "store": res["path"],
        "skipped_blank": blanks,
        "agreed_with_model": agreed,
        "disagreed": len(labels) - agreed,
        "note": ("Agreements are stored too. A store holding only corrections "
                 "would make the tool look worse than it is and would bias any "
                 "fit toward the cases where it already failed."),
    }


# -- the cycle ----------------------------------------------------------------


def run_cycle(
    segments: str | Path,
    dataset: str | None = None,
    classes: Sequence[str] = ("confined", "partly confined", "unconfined"),
    field_name: str = "confinement",
    value_field: str = "confinement_ratio",
    id_field: str = "seg_id",
    current_breaks: Sequence[float] | None = (2.0, 5.0),
    n_suggest: int = 20,
    folds: int = 5,
    min_labels: int = 12,
    out_prefix: str = "calibration",
    guard: Guard | None = None,
) -> CycleResult:
    """One round: fit from labels, evaluate honestly, suggest what to label next.

    ``current_breaks`` is what you are using today — the defaults, or whatever
    the last cycle adopted. It is scored on the same held-out folds as the new
    fit, because "the fit scores 0.84" means nothing without knowing what the
    breaks you already had score on the same data.
    """
    import geopandas as gpd
    import pandas as pd

    t0 = time.monotonic()
    guard = guard or Guard.from_env()
    warnings: list[str] = []
    reasons: list[str] = []

    seg_path = guard.read_path(segments)
    try:
        seg = gpd.read_file(str(seg_path))
    except Exception:
        seg = pd.read_csv(str(seg_path))
    for f in (value_field, id_field):
        if f not in seg.columns:
            raise GeolensError(f"{segments} has no '{f}' column")

    key = dataset_key(dataset or str(seg_path))
    rows = load_labels(dataset=key, field_name=field_name, guard=guard)
    n_labels = len(rows)

    by_id = {str(r["feature_id"]): r for r in rows}
    seg["_id"] = seg[id_field].astype(str)
    lab = seg[seg["_id"].isin(by_id)].copy()
    lab["_truth"] = [by_id[i]["human_value"] for i in lab["_id"]]

    values = lab[value_field].to_numpy(dtype="float64")
    truth = lab["_truth"].to_numpy()
    ok = np.isfinite(values)
    values, truth = values[ok], truth[ok]

    fitted_breaks = None
    fitted_score = current_score = train_score = null_score = None
    per_class: dict[str, float] = {}
    confusion: dict[str, Any] = {}
    separable: bool | None = None
    separability_verdict: str | None = None
    curve: list[dict[str, Any]] = []
    k = folds

    if len(values) < min_labels:
        recommendation = "label_more"
        reasons.append(
            f"Only {len(values)} usable labels. Below about {min_labels} a "
            f"held-out score is noise — the fit would be evaluated on two or "
            f"three segments. Label the suggested batch and run this again."
        )
    else:
        present = sorted(set(truth))
        missing = [c for c in classes if c not in present]
        if missing:
            warnings.append(
                f"No labels for {missing}. Those classes cannot be fitted or "
                f"scored, and balanced accuracy is averaged over the rest."
            )
        k = max(2, min(folds, min(int(np.bincount(
            np.unique(truth, return_inverse=True)[1]).min()), folds)))
        if k < folds:
            warnings.append(
                f"Using {k}-fold instead of {folds}-fold: the rarest class has "
                f"only {k} examples, and a fold without it cannot be scored."
            )

        fit = fit_ordered_breaks(values, truth, classes, balanced=True)
        fitted_breaks = [float(b) for b in fit["breaks"]]
        train_score = float(fit.get("score", float("nan")))
        confusion = fit.get("confusion", {})
        # "not separable" and "weakly separable" are both real answers; only the
        # first is a hard stop. A weakly separable feature can still be worth
        # calibrating, it just will not get far.
        sep = (fit.get("separability") or {}).get("verdict")
        separable = None if sep is None else (sep != "not separable")
        separability_verdict = sep

        fitted_score = _cv_score(values, truth, classes, k)
        null = _permutation_baseline(values, truth, classes, k)
        null_score = null["threshold"]
        if current_breaks is not None:
            current_score = _cv_score(values, truth, classes, k,
                                      fixed_breaks=current_breaks)
        _, per_class = _score_breaks(values, truth, fitted_breaks, classes)
        curve = _learning_curve(values, truth, classes, k)

        # --- the decision -------------------------------------------------
        gap = (train_score - fitted_score) if (train_score == train_score
                                               and fitted_score == fitted_score) else 0.0
        # Held out, does the feature beat randomised labels? This overrides the
        # in-sample verdict, which is the optimistic one.
        beats_null = (fitted_score == fitted_score and null_score == null_score
                      and fitted_score > null_score)
        if null_score == null_score and not beats_null:
            separable = False
            reasons.append(
                f"Held out, these breaks score {fitted_score:.3f}. The same "
                f"procedure on SHUFFLED labels averages {null['mean']:.3f} with "
                f"a spread of {null['sd']:.3f} over {null['n']} shuffles, so "
                f"anything under {null_score:.3f} is inside the noise. The fit "
                f"does not clear its own null — at this label count the feature "
                f"is not demonstrably carrying information about these classes."
            )

        if separability_verdict == "weakly separable":
            warnings.append(
                f"'{value_field}' is only weakly separable here: the best any "
                f"breaks could achieve is "
                f"{(fit.get('separability') or {}).get('best_achievable')}. "
                f"Calibration will help a little and then stop helping."
            )
        if separable is False:
            recommendation = "feature_does_not_separate"
            reasons.append(
                f"'{value_field}' cannot separate these classes on this data, "
                f"at any breaks. More labels will not fix that. Either the "
                f"channel-width relation feeding the ratio is wrong for this "
                f"landscape, or two of these classes are not distinct here."
            )
        elif fitted_score != fitted_score:
            recommendation = "label_more"
            reasons.append("Cross-validation produced no usable folds.")
        elif current_score == current_score and fitted_score <= current_score + 0.01:
            recommendation = "keep_current"
            reasons.append(
                f"The new fit scores {fitted_score:.3f} held out against "
                f"{current_score:.3f} for the breaks you already use. That is "
                f"not an improvement, so keep what you have. Re-fitting on "
                f"every batch regardless is how a calibration loop launders "
                f"overfitting as progress."
            )
        else:
            recommendation = "adopt"
            if current_score == current_score:
                reasons.append(
                    f"Held out, the new breaks score {fitted_score:.3f} against "
                    f"{current_score:.3f} for the current ones."
                )
            else:
                reasons.append(f"Held-out score {fitted_score:.3f}.")
        if gap > 0.15:
            warnings.append(
                f"In-sample {train_score:.3f} against held-out {fitted_score:.3f} "
                f"— a {gap:.2f} gap means the breaks are partly memorising these "
                f"particular segments. Treat the held-out number as the real one."
            )

        if len(curve) >= 2:
            last, prev = curve[-1]["held_out_score"], curve[-2]["held_out_score"]
            if last is not None and prev is not None:
                if last - prev < 0.01:
                    reasons.append(
                        "The learning curve has flattened: the last quarter of "
                        "labels moved the held-out score by under 0.01. More "
                        "labelling of this field is unlikely to help."
                    )
                else:
                    reasons.append(
                        f"The learning curve is still rising "
                        f"({prev:.3f} to {last:.3f}) — another batch should help."
                    )

    # --- what to label next ------------------------------------------------
    all_values = seg[value_field].to_numpy(dtype="float64")
    all_ids = seg[id_field].tolist()
    brk = fitted_breaks or list(current_breaks or (2.0, 5.0))
    sug = suggest_next(all_values, brk, all_ids, n=n_suggest,
                       already_labelled=list(by_id.keys()), strategy="auto")
    # suggest_next returns dicts carrying the value and quantile, not bare ids.
    suggested = [
        (s_["feature_id"] if isinstance(s_, dict) else s_)
        for s_ in sug.get("suggested", [])
    ]

    worksheet = None
    if suggested:
        try:
            ws = write_worksheet(segments, suggested, field_name=field_name,
                                 id_field=id_field, out_prefix=out_prefix,
                                 guard=guard)
            worksheet = ws["worksheet"]
        except Exception as e:  # noqa: BLE001
            warnings.append(f"Could not write a worksheet: {type(e).__name__}: {e}")

    # --- history -----------------------------------------------------------
    try:
        hist = Path(guard.write_path(HISTORY_FILE))
        with hist.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "dataset": key, "field": field_name,
                "n_labels": int(len(values)),
                "fitted_breaks": fitted_breaks,
                "fitted_score": fitted_score if fitted_score == fitted_score else None,
                "current_score": current_score if current_score == current_score else None,
                "recommendation": recommendation,
            }) + "\n")
    except Exception:
        pass

    return CycleResult(
        n_labels=int(len(values)),
        classes=list(classes),
        fitted_breaks=fitted_breaks,
        current_breaks=list(current_breaks) if current_breaks else None,
        fitted_score=None if fitted_score != fitted_score else fitted_score,
        current_score=None if current_score != current_score else current_score,
        train_score=None if train_score != train_score else train_score,
        folds=int(k),
        separable=separable,
        null_score=None if (null_score is None or null_score != null_score)
        else round(float(null_score), 4),
        learning_curve=curve,
        recommendation=recommendation,
        reasons=reasons,
        worksheet=worksheet,
        suggested=suggested,
        per_class_recall={k2: (None if v != v else round(v, 4))
                          for k2, v in per_class.items()},
        confusion=confusion,
        seconds=time.monotonic() - t0,
        warnings=warnings,
    )


def cycle_history(guard: Guard | None = None) -> list[dict[str, Any]]:
    """Every cycle run in this output directory, oldest first.

    Progress across rounds is the thing you cannot see from a single cycle: a
    held-out score that is not moving after three batches means stop.
    """
    guard = guard or Guard.from_env()
    p = Path(guard.out_dir) / HISTORY_FILE
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
