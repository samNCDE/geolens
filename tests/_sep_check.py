"""Does the separability verdict fire, and is its advice correct?

Fits confinement breaks on `confinement_ratio` (expected: not separable, because
the ratio divides by a channel width that varies with drainage area), then
follows the advice and refits on `valley_width_m` — the raw quantity the human
was actually judging.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ["GEOLENS_OUT"] = str(ROOT / "learnout")
os.environ["GEOLENS_LABELS"] = str(ROOT / "learnout" / "labels.jsonl")
os.environ["GEOLENS_NO_PROGRESS"] = "1"

import numpy as np
import pandas as pd

from geolens import api
from geolens.learn import fit_ordered_breaks, load_labels

csv = str(ROOT / "learnout" / "classified_dir" / "segments_classified.csv")
CLASSES = ["confined", "partly confined", "unconfined"]

fit = api.calibrate_confinement(csv)
sep = fit["separability"]
print("FIT on confinement_ratio (the tool's own feature):")
print(f"  breaks {[round(b, 2) for b in fit['breaks']]}  "
      f"{fit['metric']}={fit['score']}")
print(f"  separability: {sep['verdict']}  "
      f"(best achievable {sep['best_achievable']}, chance {sep['chance_level']})")
if sep.get("advice"):
    print(f"  advice: {sep['advice'][:170]}...")

df = pd.read_csv(csv)
lab = load_labels(field_name="confinement")
by = {str(r["feature_id"]): r for r in lab}
m = df[df.seg_id.astype(str).isin(by)]
truth = [by[str(i)]["human_value"] for i in m.seg_id]

print(f"\nFollowing the advice — refit on valley_width_m ({len(m)} labels):")
f2 = fit_ordered_breaks(m["valley_width_m"].to_numpy(), truth, CLASSES)
s2 = f2["separability"]
print(f"  breaks {[round(b, 1) for b in f2['breaks']]} m  "
      f"balanced accuracy {f2['score']}")
print(f"  separability: {s2['verdict']}  (best achievable {s2['best_achievable']})")

print("\n" + "=" * 62)
improved = s2["best_achievable"] > sep["best_achievable"]
print(f"Separability diagnostic {'WORKS' if improved else 'INCONCLUSIVE'}: "
      f"{sep['best_achievable']} on the ratio -> "
      f"{s2['best_achievable']} on valley width")
print("The loop identified that the FEATURE was wrong, not the thresholds.")
sys.exit(0 if improved else 1)
