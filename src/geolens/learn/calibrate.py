"""Fit thresholds to human corrections, and decide what to label next.

Two things live here, and they are the loop:

1. **Fit.** Given labels, find the thresholds that best reproduce them. This is
   the "learn the parameters, keep the physics" approach — the algorithm stays
   the interpretable slope-break method it was, only its constants move. It
   needs tens of labels, not thousands, and the result is a defensible number
   you can put in a report.
2. **Suggest.** Given a fit, say which unlabelled features would be most
   informative to label next. Labelling is the expensive step, and most
   features teach you nothing — a segment far from any decision boundary is
   already obviously classified. The ones near a break are where the labels pay.

Run them alternately and the loop closes: fit, suggest, label, refit.

A note on the objective. Classification accuracy is a step function of the
thresholds, so gradient methods are useless on it. For **ordered** thresholds —
confined < partly confined < unconfined by a single ratio — the optimum can be
found exactly by scanning candidate cuts, which is what :func:`fit_ordered_breaks`
does. For everything else :func:`fit_parameters` runs coarse-to-fine grid search,
which is slower but makes no assumptions.
"""

from __future__ import annotations

import itertools
import math
from typing import Any, Callable, Sequence

import numpy as np

from ..safety import GeolensError, note, progress

CONFIDENCE_WEIGHT = {"certain": 1.0, "likely": 0.6, "unsure": 0.3}


def _weights(labels: Sequence[dict[str, Any]]) -> np.ndarray:
    return np.array(
        [CONFIDENCE_WEIGHT.get(r.get("confidence", "certain"), 1.0) for r in labels],
        dtype="float64",
    )


# -- exact fit for ordered thresholds ----------------------------------------


def fit_ordered_breaks(
    values: Sequence[float],
    truth: Sequence[str],
    classes: Sequence[str],
    weights: Sequence[float] | None = None,
    min_gap: float = 0.0,
    balanced: bool = True,
) -> dict[str, Any]:
    """Find the break points that best reproduce ``truth`` from ``values``.

    ``classes`` must be in increasing order of ``values`` — for confinement that
    is ``["confined", "partly confined", "unconfined"]``, because the
    confinement ratio rises as a valley opens out.

    With k classes there are k-1 breaks. Candidate breaks only ever fall between
    two adjacent sorted values, so the search is exact rather than approximate:
    every distinct classification of the data is examined.

    ``balanced=True`` (the default) scores by **mean per-class recall** rather
    than plain accuracy. This matters more than it sounds: real landscapes are
    heavily imbalanced — 90 % of segments unconfined is normal — and plain
    accuracy rewards a classifier that calls everything the majority class and
    never finds the confined reaches you actually care about. Balanced accuracy
    weights each class equally, so getting the rare class right counts.

    Returns the breaks, the score they achieve, and the confusion matrix — which
    is what tells you *how* it is wrong, not just how often.
    """
    v = np.asarray(values, dtype="float64")
    t = np.asarray([str(x) for x in truth])
    if v.size != t.size:
        raise GeolensError(f"values ({v.size}) and truth ({t.size}) differ in length")
    if v.size == 0:
        raise GeolensError("no labelled values to fit")
    k = len(classes)
    if k < 2:
        raise GeolensError("need at least two classes")

    ok = np.isfinite(v)
    v, t = v[ok], t[ok]
    w = (np.asarray(weights, dtype="float64")[ok] if weights is not None
         else np.ones_like(v))
    if v.size == 0:
        raise GeolensError("all labelled values are non-finite")

    unknown = set(t) - set(classes)
    if unknown:
        raise GeolensError(
            f"labels contain classes not in `classes`: {sorted(unknown)}. "
            f"Expected some of {list(classes)}."
        )

    order = np.argsort(v)
    vs = v[order]
    # Candidate cuts: midpoints between distinct adjacent values, plus the ends.
    mids = [(vs[i] + vs[i + 1]) / 2.0 for i in range(len(vs) - 1)
            if vs[i + 1] > vs[i]]
    cands = np.array([vs[0] - 1e-9] + mids + [vs[-1] + 1e-9])
    if cands.size > 400:            # keep the k-1 combinatorics tractable
        cands = np.quantile(cands, np.linspace(0, 1, 400))
    cands = np.unique(cands)

    best = {"score": -1.0, "breaks": None}
    total_w = float(w.sum())
    present = [c for c in classes if (t == c).any()]
    class_masks = {c: (t == c) for c in present}
    class_w = {c: float(w[m].sum()) for c, m in class_masks.items()}

    def classify(breaks):
        idx = np.searchsorted(np.asarray(breaks), v, side="right")
        return np.asarray(classes)[idx]

    def score_of(pred):
        if not balanced:
            return float(w[pred == t].sum()) / total_w if total_w else 0.0
        # Mean per-class recall: each class counts equally regardless of size.
        recalls = []
        for c in present:
            m = class_masks[c]
            denom = class_w[c]
            if denom <= 0:
                continue
            recalls.append(float(w[m & (pred == c)].sum()) / denom)
        return float(np.mean(recalls)) if recalls else 0.0

    combos = itertools.combinations(range(len(cands)), k - 1)
    for combo in combos:
        breaks = cands[list(combo)]
        if min_gap > 0 and len(breaks) > 1:
            if np.min(np.diff(breaks)) < min_gap:
                continue
        pred = classify(breaks)
        score = score_of(pred)
        if score > best["score"]:
            best = {"score": score, "breaks": [float(b) for b in breaks]}

    if best["breaks"] is None:
        raise GeolensError("no valid break combination found — check min_gap")

    pred = classify(best["breaks"])
    confusion: dict[str, dict[str, int]] = {
        a: {b: 0 for b in classes} for a in classes
    }
    for true_c, pred_c in zip(t, pred):
        confusion[true_c][pred_c] += 1

    plain = float(w[pred == t].sum()) / total_w if total_w else 0.0
    class_counts = {c: int((t == c).sum()) for c in classes}
    biggest = max(class_counts.values()) if class_counts else 0
    imbalance = biggest / max(1, int(t.size))

    out = {
        "breaks": best["breaks"],
        "classes": list(classes),
        "score": round(best["score"], 4),
        "metric": "balanced_accuracy" if balanced else "weighted_accuracy",
        "plain_accuracy": round(plain, 4),
        "n_labels": int(v.size),
        "label_counts": class_counts,
        "confusion": confusion,
        "exact": True,
        "note": (
            "Breaks are exact optima over the labelled data. With few labels "
            "they will overfit — check n_labels and the confusion matrix before "
            "adopting them."
        ),
    }
    # --- separability verdict ----------------------------------------------
    #
    # best["score"] is the BEST ACHIEVABLE by any thresholds on this feature —
    # the search was exact. If that ceiling is barely above chance, no amount of
    # tuning will help: the feature cannot tell these classes apart, and the
    # problem is the feature, not the breaks. Saying so is far more useful than
    # returning a fitted number that will not generalize.
    n_present = max(1, len(present))
    chance = 1.0 / n_present
    ceiling = best["score"]
    if ceiling < chance + 0.10:
        verdict = "not separable"
    elif ceiling < 0.70:
        verdict = "weakly separable"
    else:
        verdict = "separable"

    out["separability"] = {
        "verdict": verdict,
        "best_achievable": round(ceiling, 4),
        "chance_level": round(chance, 4),
        "classes_present": present,
    }
    if verdict == "not separable":
        out["separability"]["advice"] = (
            "No threshold on this feature separates these classes — the best "
            "possible score is near chance. Tuning the breaks cannot fix it. "
            "Either the feature is wrong for the question, or the labels encode "
            "something it does not measure. For confinement a common cause is "
            "that the ratio divides by a channel width that varies with drainage "
            "area, conflating valley size with channel size: try fitting on "
            "valley_width_m directly, or add a second feature."
        )
    elif verdict == "weakly separable":
        out["separability"]["advice"] = (
            "This feature only partly separates the classes. Expect a modest "
            "ceiling however the breaks are set — consider a second feature "
            "before investing in more labels."
        )

    missing = [c for c in classes if class_counts.get(c, 0) == 0]
    if missing:
        out["warning"] = (
            f"No labels for class(es) {missing}. Their breaks are unconstrained "
            "and essentially arbitrary — label some before using these."
        )
    elif imbalance > 0.8:
        out["warning"] = (
            f"{imbalance:.0%} of labels are one class. Random sampling wastes "
            "effort on obvious cases — use suggest_next() to pick boundary "
            "features and label those instead."
        )
    return out


# -- generic grid fit ---------------------------------------------------------


def fit_parameters(
    predict: Callable[[dict[str, float]], Sequence[Any]],
    truth: Sequence[Any],
    space: dict[str, Sequence[float]],
    weights: Sequence[float] | None = None,
    numeric: bool = False,
    refine_rounds: int = 2,
    refine_factor: float = 0.25,
) -> dict[str, Any]:
    """Coarse-to-fine grid search over an arbitrary parameter space.

    ``predict(params)`` must return one prediction per label, in the same order
    as ``truth``. Use this when the relationship between a parameter and the
    output is not monotone — bank-slope thresholds, for instance, where raising
    the value can both remove false banks and lose real ones.

    ``numeric=True`` scores by negative weighted mean absolute error instead of
    accuracy, for continuous corrections like a bank-top offset.

    Each refine round re-centres a tighter grid on the current best point, which
    buys resolution without the full-grid cost. Total evaluations are reported
    so the cost is visible.
    """
    t = np.asarray(truth)
    w = (np.asarray(weights, dtype="float64") if weights is not None
         else np.ones(len(t), dtype="float64"))
    total_w = float(w.sum())
    if total_w <= 0:
        raise GeolensError("all label weights are zero")

    def score_of(params: dict[str, float]) -> float:
        pred = np.asarray(predict(params))
        if pred.shape[0] != t.shape[0]:
            raise GeolensError(
                f"predict returned {pred.shape[0]} values for {t.shape[0]} labels"
            )
        if numeric:
            p = pred.astype("float64")
            tt = t.astype("float64")
            ok = np.isfinite(p) & np.isfinite(tt)
            if not ok.any():
                return -math.inf
            return -float((w[ok] * np.abs(p[ok] - tt[ok])).sum() / w[ok].sum())
        return float(w[pred.astype(str) == t.astype(str)].sum()) / total_w

    grid = {k: list(map(float, v)) for k, v in space.items()}
    names = list(grid)
    best_params: dict[str, float] = {}
    best_score = -math.inf
    evaluations = 0
    history: list[dict[str, Any]] = []

    for rnd in range(refine_rounds + 1):
        combos = list(itertools.product(*(grid[n] for n in names)))
        note(f"round {rnd}: {len(combos):,} parameter combinations")
        for combo in progress(combos, label=f"fitting (round {rnd})"):
            params = dict(zip(names, combo))
            s = score_of(params)
            evaluations += 1
            if s > best_score:
                best_score = s
                best_params = params
        history.append({"round": rnd, "best_score": best_score,
                        "best_params": dict(best_params)})

        if rnd == refine_rounds:
            break
        # Re-centre a tighter grid on the winner.
        new_grid = {}
        for n in names:
            vals = grid[n]
            if len(vals) < 2:
                new_grid[n] = vals
                continue
            span = (max(vals) - min(vals)) * refine_factor
            c = best_params[n]
            new_grid[n] = list(np.linspace(c - span / 2, c + span / 2, len(vals)))
        grid = new_grid

    return {
        "best_params": best_params,
        "best_score": round(best_score, 5),
        "metric": "neg_weighted_mae" if numeric else "weighted_accuracy",
        "evaluations": evaluations,
        "n_labels": int(t.shape[0]),
        "history": history,
    }


def sensitivity(
    predict: Callable[[dict[str, float]], Sequence[Any]],
    truth: Sequence[Any],
    best_params: dict[str, float],
    space: dict[str, Sequence[float]],
    weights: Sequence[float] | None = None,
    numeric: bool = False,
) -> dict[str, Any]:
    """How much does each parameter actually matter?

    Sweeps one parameter at a time with the others held at their fitted values.
    With few labels this is often more useful than the fit itself: a parameter
    whose score barely moves is one you can stop agonising over, and a parameter
    that swings the score is the one worth labelling more data for.
    """
    t = np.asarray(truth)
    w = (np.asarray(weights, dtype="float64") if weights is not None
         else np.ones(len(t), dtype="float64"))
    total_w = float(w.sum())

    def score_of(params):
        pred = np.asarray(predict(params))
        if numeric:
            p = pred.astype("float64")
            tt = t.astype("float64")
            ok = np.isfinite(p) & np.isfinite(tt)
            if not ok.any():
                return float("-inf")
            return -float((w[ok] * np.abs(p[ok] - tt[ok])).sum() / w[ok].sum())
        return float(w[pred.astype(str) == t.astype(str)].sum()) / total_w

    out: dict[str, Any] = {}
    for name, values in space.items():
        curve = []
        for v in values:
            params = dict(best_params)
            params[name] = float(v)
            curve.append({"value": float(v), "score": round(score_of(params), 5)})
        scores = [c["score"] for c in curve if math.isfinite(c["score"])]
        out[name] = {
            "curve": curve,
            "range": round(max(scores) - min(scores), 5) if scores else None,
        }

    ranked = sorted(
        (k for k in out if out[k]["range"] is not None),
        key=lambda k: -out[k]["range"],
    )
    return {
        "per_parameter": out,
        "ranked_by_influence": ranked,
        "note": (
            "Parameters at the top move the score most — label more data to pin "
            "those down. Ones at the bottom are not worth tuning."
        ),
    }


# -- active learning ----------------------------------------------------------


def suggest_next(
    values: Sequence[float],
    breaks: Sequence[float],
    feature_ids: Sequence[Any],
    n: int = 20,
    already_labelled: Sequence[Any] = (),
    spread: bool = True,
    strategy: str = "auto",
    cold_start_below: int = 25,
) -> dict[str, Any]:
    """Pick the most informative unlabelled features to label next.

    Uncertainty sampling: a feature sitting right on a break could fall either
    way, so a human's answer there is worth many answers from the obvious middle
    of a class. This is the step that makes the loop converge quickly rather
    than requiring you to label everything.

    ``spread=True`` balances the picks across breaks, so a single crowded
    boundary cannot consume the whole batch.

    **Cold start.** Uncertainty sampling only works once the breaks are roughly
    right. Early on they are not — a fit from a handful of labels can put a
    break in the middle of the majority class, and then every "informative"
    suggestion is drawn from that same majority, which teaches nothing. So
    below ``cold_start_below`` labels the strategy switches to ``diverse``: a
    spread across quantiles of the value range, which guarantees the rare
    classes at both ends get seen. Once there are enough labels for the breaks
    to mean something, it switches to boundary sampling automatically.

    ``strategy`` is ``auto`` (switch by label count), ``uncertainty`` (always
    boundary) or ``diverse`` (always spread).
    """
    v = np.asarray(values, dtype="float64")
    ids = np.asarray(feature_ids, dtype=object)
    if v.size != ids.size:
        raise GeolensError("values and feature_ids differ in length")
    brk = np.asarray(breaks, dtype="float64")
    if brk.size == 0:
        raise GeolensError("no breaks given — fit them first")

    done = {str(x) for x in already_labelled}
    keep = np.array([str(i) not in done for i in ids]) & np.isfinite(v)
    if not keep.any():
        return {"suggested": [], "note": "Everything is already labelled."}

    v_k, ids_k = v[keep], ids[keep]

    if strategy not in ("auto", "uncertainty", "diverse"):
        raise GeolensError(
            f"strategy must be auto/uncertainty/diverse, got {strategy!r}"
        )
    chosen_strategy = strategy
    if strategy == "auto":
        chosen_strategy = ("diverse" if len(done) < cold_start_below
                           else "uncertainty")

    if chosen_strategy == "diverse":
        # Spread across quantiles of the value range so both tails — where the
        # rare classes live — are represented from the first batch.
        qs = np.linspace(0.0, 1.0, min(n, v_k.size))
        targets = np.quantile(v_k, qs)
        picked: list[int] = []
        used: set[int] = set()
        for tgt in targets:
            order = np.argsort(np.abs(v_k - tgt))
            for i in order:
                if int(i) not in used:
                    used.add(int(i))
                    picked.append(int(i))
                    break
        return {
            "suggested": [
                {
                    "feature_id": (ids_k[i].item() if hasattr(ids_k[i], "item")
                                   else ids_k[i]),
                    "value": float(v_k[i]),
                    "quantile": float((v_k < v_k[i]).mean()),
                }
                for i in picked[:n]
            ],
            "strategy": "diverse",
            "n_candidates": int(keep.sum()),
            "n_already_labelled": len(done),
            "note": (
                f"Cold start: fewer than {cold_start_below} labels, so these are "
                "spread across the value range rather than clustered at a "
                "boundary the fit cannot yet place. Both tails are included so "
                "the rare classes get seen. Switches to boundary sampling "
                "automatically once there are enough labels."
            ),
        }
    dist = np.min(np.abs(v_k[:, None] - brk[None, :]), axis=1)
    nearest = np.argmin(np.abs(v_k[:, None] - brk[None, :]), axis=1)

    picks: list[int] = []
    if spread and brk.size > 1:
        per = max(1, n // brk.size)
        for b in range(brk.size):
            idx = np.flatnonzero(nearest == b)
            idx = idx[np.argsort(dist[idx])][:per]
            picks.extend(idx.tolist())
        remaining = [i for i in np.argsort(dist) if i not in set(picks)]
        picks.extend(remaining[: max(0, n - len(picks))])
    else:
        picks = np.argsort(dist)[:n].tolist()

    picks = picks[:n]
    return {
        "suggested": [
            {
                "feature_id": ids_k[i] if not hasattr(ids_k[i], "item") else ids_k[i].item(),
                "value": float(v_k[i]),
                "distance_to_break": float(dist[i]),
                "nearest_break": float(brk[nearest[i]]),
            }
            for i in picks
        ],
        "strategy": "uncertainty",
        "n_candidates": int(keep.sum()),
        "n_already_labelled": len(done),
        "note": (
            "These sit closest to a class boundary, so a human answer on them "
            "moves the fit most. Features far from a break are already "
            "unambiguous — labelling them teaches nothing."
        ),
    }
