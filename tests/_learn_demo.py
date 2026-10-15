"""Does the learning loop actually improve anything?

Uses the synthetic landscape, where valley width is known exactly, to generate
*honest* corrections: a segment whose true valley is narrow is labelled
confined, wide is labelled unconfined. Then checks that fitting breaks to a
handful of those corrections beats the uncalibrated literature defaults.

This is the loop running end to end with a simulated human.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ["GEOLENS_OUT"] = str(ROOT / "learnout")
os.environ["GEOLENS_LABELS"] = str(ROOT / "learnout" / "labels.jsonl")
os.environ["GEOLENS_NO_PROGRESS"] = "1"
Path(os.environ["GEOLENS_OUT"]).mkdir(parents=True, exist_ok=True)
Path(os.environ["GEOLENS_LABELS"]).unlink(missing_ok=True)

import numpy as np
import pandas as pd

if not (ROOT / "landscape" / "landscape_dem.tif").exists():
    subprocess.run([sys.executable, str(ROOT / "make_landscape.py")], check=True,
                   capture_output=True)

from geolens import api
from geolens.learn import dataset_key, load_labels, sensitivity, fit_parameters

DEM = str(ROOT / "landscape" / "landscape_dem.tif")

print("1. run the landscape pass with UNCALIBRATED defaults")
r = api.landscape_pass(DEM, stream_threshold_km2=1.0, segment_length_m=500)
csv = r["classified"]["table"]
params = r["classified"]["parameters"]
df = pd.read_csv(csv)
print(f"   {len(df)} segments")
print(f"   default classes: {r['classified']['summary']['confinement_counts']}")
pct = r["classified"]["summary"]["confinement_ratio_percentiles"]
print(f"   ratio percentiles: p25={pct['p25']} p50={pct['p50']} p75={pct['p75']}")

# --- the simulated human ----------------------------------------------------
# Fixture truth: the mainstem valley is 300/1800/480/2800 m wide by segment.
# A human looking at the map would call the narrow ones confined. We express
# that as a rule on TRUE valley width, independent of the tool's own ratio.
TRUTH_SEGMENTS = [(0, 7500, 300.0), (7500, 15000, 1800.0),
                  (15000, 21000, 480.0), (21000, 30000, 2800.0)]
X0 = 400_000.0


def true_width_at(x_world):
    x = x_world - X0
    for a, b, w in TRUTH_SEGMENTS:
        if a <= x < b:
            return w
    return TRUTH_SEGMENTS[-1][2]


def human_class(width):
    if width <= 400:
        return "confined"
    if width <= 1000:
        return "partly confined"
    return "unconfined"


import geopandas as gpd
seg = gpd.read_file(r["classified"]["segments"])
seg["ctr_x"] = seg.geometry.centroid.x
seg["ctr_y"] = seg.geometry.centroid.y
# Only label mainstem segments — the tributaries have their own widths.
mid_y = 4_500_000.0 + 10_000.0
main = seg[(seg["ctr_y"] - mid_y).abs() < 1200].copy()
main["true_width"] = [true_width_at(x) for x in main["ctr_x"]]
main["human"] = [human_class(w) for w in main.true_width]

print(f"\n2. simulated human labels {len(main)} mainstem segments")
print("   human class counts:", main["human"].value_counts().to_dict())

ds = dataset_key(DEM)
N_LABEL = 30


def label_these(rows, tag):
    """Record corrections for a set of segments, wiping the store first."""
    Path(os.environ["GEOLENS_LABELS"]).unlink(missing_ok=True)
    for _, row in rows.iterrows():
        api.record_correction(
            dataset=ds, feature_id=str(row["seg_id"]), field="confinement",
            human_value=row["human"], model_value=row["confinement"],
            author="field_sim", kind="categorical",
            x=float(row["ctr_x"]), y=float(row["ctr_y"]), params=params,
        )
    return len(rows)


def classify(ratios, breaks):
    out = []
    for v in ratios:
        if not np.isfinite(v):
            out.append("unknown")
        elif v <= breaks[0]:
            out.append("confined")
        elif v <= breaks[1]:
            out.append("partly confined")
        else:
            out.append("unconfined")
    return np.array(out)


def balanced_acc(pred, truth):
    """Mean per-class recall — the metric that matters on imbalanced data."""
    recs = []
    for c in np.unique(truth):
        m = truth == c
        recs.append(float((pred[m] == c).mean()))
    return float(np.mean(recs))


def score(breaks, rows):
    pred = classify(rows["confinement_ratio"].to_numpy(), breaks)
    truth = rows["human"].to_numpy()
    return balanced_acc(pred, truth), float((pred == truth).mean())


# --- strategy A: label 30 segments at random --------------------------------
print("\n3a. RANDOM sampling: label 30 segments picked at random")
rand = main.sample(N_LABEL, random_state=0)
label_these(rand, "random")
fit_rand = api.calibrate_confinement(csv)
held_rand = main[~main["seg_id"].isin(rand["seg_id"])]
print(f"    label mix: {rand['human'].value_counts().to_dict()}")
print(f"    breaks {[round(b,1) for b in fit_rand['breaks']]}")
if "warning" in fit_rand:
    print(f"    warning: {fit_rand['warning'][:76]}...")

# --- strategy B: label the 30 the active learner asks for --------------------
print("\n3b. ACTIVE sampling: label the 30 nearest a decision boundary")
Path(os.environ["GEOLENS_LABELS"]).unlink(missing_ok=True)
seed = main.sample(6, random_state=1)          # small seed so a fit exists
label_these(seed, "seed")
seed_fit = api.calibrate_confinement(csv)
# Suggest from the LABELABLE pool only — suggesting segments nobody can label
# silently shrinks the batch.
from geolens.learn import suggest_next as _suggest
sug = _suggest(
    main["confinement_ratio"].to_numpy(),
    seed_fit.get("breaks") or [2.0, 5.0],
    main["seg_id"].astype(str).tolist(),
    n=N_LABEL - len(seed),
    already_labelled=[str(i) for i in seed["seg_id"]],
)
print(f"    strategy: {sug.get('strategy')}")
want = {str(x["feature_id"]) for x in sug["suggested"]}
picked = main[main["seg_id"].astype(str).isin(want)]
active = pd.concat([seed, picked]).drop_duplicates(subset="seg_id")
label_these(active, "active")
fit_act = api.calibrate_confinement(csv)
held_act = main[~main["seg_id"].isin(active["seg_id"])]
print(f"    label mix: {active['human'].value_counts().to_dict()}")
print(f"    breaks {[round(b,1) for b in fit_act['breaks']]}")

# --- compare on held-out data ------------------------------------------------
print("\n4. balanced accuracy on HELD-OUT segments (never labelled)")
d_bal, d_pl = score([2.0, 5.0], main)
r_bal, r_pl = score(fit_rand["breaks"], held_rand)
a_bal, a_pl = score(fit_act["breaks"], held_act)
print(f"    defaults [2.0, 5.0]        balanced {d_bal:.3f}   plain {d_pl:.3f}")
print(f"    fitted from RANDOM labels  balanced {r_bal:.3f}   plain {r_pl:.3f}   "
      f"({len(held_rand)} held out)")
print(f"    fitted from ACTIVE labels  balanced {a_bal:.3f}   plain {a_pl:.3f}   "
      f"({len(held_act)} held out)")
print(f"\n    active vs defaults: {a_bal - d_bal:+.3f} balanced accuracy")
print(f"    active vs random:   {a_bal - r_bal:+.3f} balanced accuracy")

# --- what to label next -----------------------------------------------------
print("\n5. what the active learner would ask for next")
sug2 = api.suggest_labels(csv, n=5)
print(f"   strategy: {sug2.get('strategy')}")
for x in sug2["suggested"]:
    extra = (f"{x['distance_to_break']:7.2f} from break {x['nearest_break']:.2f}"
             if "distance_to_break" in x else f"quantile {x.get('quantile', 0):.2f}")
    print(f"   seg {x['feature_id']:>4}  ratio {x['value']:8.2f}  {extra}")
print(f"   ({sug2['n_candidates']} candidates, {sug2['n_already_labelled']} already done)")

# --- which parameter matters ------------------------------------------------
print("\n6. sensitivity — which break matters more?")
lab = load_labels(field_name="confinement")
by_id = {str(x["feature_id"]): x for x in lab}
m = df[df["seg_id"].astype(str).isin(by_id)]
vals = m["confinement_ratio"].to_numpy()
tru = np.array([by_id[str(i)]["human_value"] for i in m["seg_id"]])
sens = sensitivity(
    lambda p: classify(vals, [p["confined_max"], p["partly_confined_max"]]),
    tru,
    best_params={"confined_max": fit_act["breaks"][0],
                 "partly_confined_max": fit_act["breaks"][1]},
    space={"confined_max": np.linspace(2, 40, 12),
           "partly_confined_max": np.linspace(20, 100, 12)},
)
for name in sens["ranked_by_influence"]:
    print(f"   {name:24s} score range {sens['per_parameter'][name]['range']}")

print("\n" + "=" * 60)
ok = a_bal > d_bal
print(f"LOOP {'WORKS' if ok else 'DID NOT IMPROVE'}: balanced accuracy "
      f"{d_bal:.3f} (defaults) -> {a_bal:.3f} (fitted from active labels)")
print(f"Random labelling would have given {r_bal:.3f} — "
      f"choosing WHAT to label matters as much as the fit.")
sys.exit(0 if ok else 1)
