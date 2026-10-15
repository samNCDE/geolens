"""Durable store for human corrections.

This is the piece everything else depends on. Without a record of "the model
said *confined*, a human who looked at it says *partly confined*", there is
nothing to learn from and no way to tell whether a parameter change helped.

Design choices, and why:

* **JSONL, append-only.** One label per line. Diffable in git, readable without
  a database, survives a crashed process mid-write, and two people can append
  concurrently without corrupting each other's rows. A schema change adds a key
  rather than migrating a table.
* **Every label records the run that produced the value it corrects.** A
  correction is only meaningful against the parameters that generated the
  prediction — the same segment under different thresholds is a different
  prediction. ``params_hash`` ties them together, and the parameters themselves
  are stored so a run can be reproduced from the label alone.
* **Nothing is ever overwritten.** A re-labelled feature appends a new row.
  ``load_labels`` takes the latest per (dataset, feature, field) by default, so
  history is kept but does not confuse the fit.
* **Corrections are opinions, not truth.** ``author`` is required so
  inter-operator disagreement can be measured — which is the first thing worth
  measuring, before trusting any of it (see ``disagreement``).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

from ..safety import Guard, GeolensError

SCHEMA_VERSION = 1


def params_hash(params: dict[str, Any] | None) -> str:
    """Stable short hash of a parameter dict, for tying labels to runs."""
    if not params:
        return "none"
    norm = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(norm.encode()).hexdigest()[:12]


def dataset_key(path: str | Path) -> str:
    """Identify a dataset by name + size + mtime, not by full path.

    Paths move between machines; this keeps labels attached to the data even
    when the project folder is somewhere else.
    """
    p = Path(path)
    try:
        st = p.stat()
        raw = f"{p.name}:{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        raw = p.name
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


@dataclass
class Label:
    """One human correction of one predicted value."""

    dataset: str                 # dataset_key of the source DEM / layer
    feature_id: str              # segment id, gully id, station, ...
    field: str                   # what is being corrected, e.g. "confinement"
    human_value: Any             # what the person says it is
    model_value: Any = None      # what the tool said (may be None if unlabelled)
    author: str = ""             # who corrected it
    kind: str = "categorical"    # "categorical" or "numeric"
    x: float | None = None       # location, for finding it again on a map
    y: float | None = None
    params: dict[str, Any] = field(default_factory=dict)
    params_hash: str = ""
    notes: str = ""
    confidence: str = "certain"  # certain | likely | unsure — weight the fit
    created_at: float = 0.0
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        if not self.author:
            self.author = os.environ.get("GEOLENS_AUTHOR", "") or "unknown"
        if not self.created_at:
            self.created_at = time.time()
        if not self.params_hash:
            self.params_hash = params_hash(self.params)
        if self.kind not in ("categorical", "numeric"):
            raise GeolensError(
                f"kind must be 'categorical' or 'numeric', got {self.kind!r}"
            )
        if self.confidence not in ("certain", "likely", "unsure"):
            raise GeolensError(
                f"confidence must be certain/likely/unsure, got {self.confidence!r}"
            )


def store_path(guard: Guard | None = None, name: str = "labels.jsonl") -> Path:
    """Where labels live. Override with GEOLENS_LABELS."""
    env = os.environ.get("GEOLENS_LABELS", "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    guard = guard or Guard.from_env()
    return Path(guard.write_path(name))


def add_labels(labels: Iterable[Label], guard: Guard | None = None,
               name: str = "labels.jsonl") -> dict[str, Any]:
    """Append labels to the store. Never overwrites."""
    path = store_path(guard, name)
    n = 0
    with path.open("a", encoding="utf-8") as fh:
        for lab in labels:
            fh.write(json.dumps(asdict(lab), default=str) + "\n")
            n += 1
    return {"path": str(path), "added": n}


def add_label(**kwargs: Any) -> dict[str, Any]:
    """Convenience: build and append one label."""
    guard = kwargs.pop("guard", None)
    name = kwargs.pop("name", "labels.jsonl")
    return add_labels([Label(**kwargs)], guard=guard, name=name)


def load_labels(
    guard: Guard | None = None,
    name: str = "labels.jsonl",
    dataset: str | None = None,
    field_name: str | None = None,
    author: str | None = None,
    latest_only: bool = True,
) -> list[dict[str, Any]]:
    """Read labels back, newest-per-feature by default.

    ``latest_only`` keeps one row per (dataset, feature_id, field, author) —
    the most recent. Set it False to see the full history, which is what
    :func:`disagreement` needs.
    """
    path = store_path(guard, name)
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # A partial final line from an interrupted write — skip it
                # rather than failing the whole read.
                continue

    if dataset:
        rows = [r for r in rows if r.get("dataset") == dataset]
    if field_name:
        rows = [r for r in rows if r.get("field") == field_name]
    if author:
        rows = [r for r in rows if r.get("author") == author]

    if latest_only:
        best: dict[tuple, dict] = {}
        for r in rows:
            k = (r.get("dataset"), str(r.get("feature_id")), r.get("field"),
                 r.get("author"))
            if k not in best or r.get("created_at", 0) > best[k].get("created_at", 0):
                best[k] = r
        rows = list(best.values())

    rows.sort(key=lambda r: r.get("created_at", 0))
    return rows


def label_summary(guard: Guard | None = None, name: str = "labels.jsonl") -> dict[str, Any]:
    """What is in the store — counts by field, author, dataset and value."""
    rows = load_labels(guard, name, latest_only=False)
    if not rows:
        return {"path": str(store_path(guard, name)), "total": 0,
                "note": "No labels yet. Correct some predictions first."}

    def tally(key):
        out: dict[str, int] = {}
        for r in rows:
            out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    latest = load_labels(guard, name, latest_only=True)
    agree = sum(
        1 for r in latest
        if r.get("model_value") is not None
        and str(r.get("model_value")) == str(r.get("human_value"))
    )
    scored = sum(1 for r in latest if r.get("model_value") is not None)

    return {
        "path": str(store_path(guard, name)),
        "total": len(rows),
        "unique": len(latest),
        "by_field": tally("field"),
        "by_author": tally("author"),
        "by_dataset": tally("dataset"),
        "by_confidence": tally("confidence"),
        "model_agreement": (
            {"scored": scored, "agreed": agree,
             "rate": round(agree / scored, 3) if scored else None}
        ),
        "distinct_param_sets": len({r.get("params_hash") for r in rows}),
    }


def disagreement(guard: Guard | None = None, name: str = "labels.jsonl",
                 field_name: str | None = None) -> dict[str, Any]:
    """Measure inter-operator disagreement on features labelled by 2+ people.

    **Run this before fitting anything.** If two people disagree with each other
    more than the algorithm disagrees with either, the algorithm is not the
    thing to fix, and a model trained on those labels will fit the noise.
    """
    rows = load_labels(guard, name, field_name=field_name, latest_only=True)
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        k = (r.get("dataset"), str(r.get("feature_id")), r.get("field"))
        groups.setdefault(k, []).append(r)

    multi = {k: v for k, v in groups.items() if len({r["author"] for r in v}) > 1}
    if not multi:
        return {
            "features_with_multiple_authors": 0,
            "note": (
                "No feature has been labelled by more than one person, so "
                "operator variability is unknown. Have a second person label "
                "20-30 of the same features before trusting a fit."
            ),
        }

    cat_total = cat_agree = 0
    num_diffs: list[float] = []
    for _k, v in multi.items():
        vals = [r["human_value"] for r in v]
        if v[0].get("kind") == "numeric":
            try:
                nums = [float(x) for x in vals]
                num_diffs.append(max(nums) - min(nums))
            except (TypeError, ValueError):
                pass
        else:
            cat_total += 1
            if len({str(x) for x in vals}) == 1:
                cat_agree += 1

    out: dict[str, Any] = {"features_with_multiple_authors": len(multi)}
    if cat_total:
        out["categorical_agreement_rate"] = round(cat_agree / cat_total, 3)
    if num_diffs:
        srt = sorted(num_diffs)
        out["numeric_spread"] = {
            "n": len(srt), "median": srt[len(srt) // 2],
            "mean": sum(srt) / len(srt), "max": srt[-1],
        }
        out["note"] = (
            "Compare numeric_spread.median against the algorithm's own error. "
            "If operators differ by more, stop and agree on a definition first."
        )
    return out
