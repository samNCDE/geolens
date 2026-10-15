"""Learning from human corrections.

The loop: run the workflow, correct what it got wrong, fit the thresholds to
those corrections, re-run. Repeat on the features the fit is least sure about.

Two deliberate choices shape this subpackage:

* **Fit the parameters, keep the physics.** The slope-break method stays what
  it is; only its constants move. That needs tens of labels rather than
  thousands, and the output is a number you can defend in a design report
  instead of a model you cannot explain.
* **Corrections are opinions.** ``labels.disagreement`` exists because a
  hand-drawn bank top carries its own error, often larger than the algorithm's.
  Measure operator variability before trusting a fit.
"""

from .labels import (  # noqa: F401
    Label, add_label, add_labels, load_labels, label_summary, disagreement,
    dataset_key, params_hash, store_path,
)
from .calibrate import (  # noqa: F401
    fit_ordered_breaks, fit_parameters, sensitivity, suggest_next,
)

__all__ = [
    "Label", "add_label", "add_labels", "load_labels", "label_summary",
    "disagreement", "dataset_key", "params_hash", "store_path",
    "fit_ordered_breaks", "fit_parameters", "sensitivity", "suggest_next",
]

from .loop import (  # noqa: F401,E402
    run_cycle, write_worksheet, ingest_worksheet, cycle_history,
    CycleResult,
)
