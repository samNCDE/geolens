"""Save every meaningful result to disk, automatically.

The rule: **if it was expensive to compute, it lands on disk.** Flow routing
over a 2 m DEM is tens of minutes and tens of gigabytes of RAM. A geology fetch
depends on a USGS service with no SLA being up at that moment. Knickpoint
finding is a minute per run. None of that should live only in a Python variable
that a kernel restart destroys.

Rasters and vectors were already written by the functions that produce them.
What was not written was everything else: the numbers, the summaries, the
parameters that produced them, and any record that a run happened at all. So
after a session you had a folder of GeoTIFFs and no idea which threshold made
them, and re-deriving that meant re-running.

What this adds
--------------
Every ``geolens.api`` call writes, under ``<output>/runs/``:

``<timestamp>_<function>.json``   the full result, plus the arguments it was
                                  called with and how long it took
``<timestamp>_<function>_<table>.csv``  any list-of-records in the result, as a
                                  table you can open in Excel or join in GIS
``manifest.jsonl``                one line per run: what ran, when, with what,
                                  and where its outputs went

The manifest is the useful part. ``geolens.persist.history()`` reads it back,
so "what did I run on this DEM and what came out" is answerable later.

Turning it off
--------------
``GEOLENS_PERSIST=0`` disables it. Persisting is cheap — JSON and CSV, never
rasters, which the producing functions already wrote — so the main reason to
turn it off is a tight loop where the run log is noise rather than a record.
"""

from __future__ import annotations

import functools
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .safety import Guard

RUNS_DIRNAME = "runs"
MANIFEST = "manifest.jsonl"

# Keys whose values are paths that already exist on disk; recorded in the
# manifest as outputs so the run log points at the real products.
_PATH_HINTS = (
    "path", "paths", "file", "files", "gpkg", "csv", "png", "tif", "shp",
    "lines", "polygons", "sections", "headcuts", "gullies", "segments",
    "accumulation", "flow_dir", "filled", "conditioned", "slope", "hand",
    "streams", "evidence", "valley_bottom", "valley_core", "units_csv",
)


def enabled() -> bool:
    return os.environ.get("GEOLENS_PERSIST", "1") not in ("0", "false", "False")


def _runs_dir(guard: Guard) -> Path:
    d = Path(guard.out_dir) / RUNS_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _jsonable(obj: Any) -> Any:
    """Make a result JSON-serialisable without losing information silently.

    numpy scalars, Paths and sets all appear in these results. Anything that
    genuinely cannot be represented becomes its repr rather than raising --
    failing to save a run because one field was exotic would defeat the point.
    """
    import numpy as np

    if obj is None or isinstance(obj, (bool, str)):
        return obj
    # Floats FIRST, and before the int check, because bool is an int subclass
    # and np.float64 is a float subclass -- an isinstance(obj, float) test that
    # returns obj unchanged lets NaN straight through, and json.dumps then
    # writes a bare `NaN`, which is not valid JSON and breaks every strict
    # parser that reads the file back.
    if isinstance(obj, (float, np.floating)):
        v = float(obj)
        return v if v == v and abs(v) != float("inf") else None   # NaN/inf -> null
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist() if obj.size <= 1000 else f"<ndarray shape={obj.shape}>"
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "as_dict"):
        return _jsonable(obj.as_dict())
    return repr(obj)


def _tables(result: Any) -> dict[str, list[dict]]:
    """Find list-of-records in a result — the parts worth having as CSV."""
    out: dict[str, list[dict]] = {}
    if not isinstance(result, dict):
        return out
    for key, value in result.items():
        if (isinstance(value, list) and len(value) > 1
                and all(isinstance(v, dict) for v in value)):
            out[key] = value
    return out


def _output_paths(result: Any) -> list[str]:
    """Collect values that look like paths to files that exist."""
    found: list[str] = []

    def walk(o: Any, key: str = "") -> None:
        if isinstance(o, str):
            k = key.lower()
            if any(h in k for h in _PATH_HINTS) or any(
                    o.lower().endswith(e) for e in
                    (".tif", ".gpkg", ".shp", ".csv", ".png", ".json", ".jsonl")):
                try:
                    if Path(o).exists():
                        found.append(o)
                except OSError:
                    pass
        elif isinstance(o, dict):
            for k, v in o.items():
                walk(v, str(k))
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v, key)

    walk(result)
    # de-duplicate, keep order
    seen, uniq = set(), []
    for p in found:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def save_result(
    name: str,
    result: Any,
    args: dict[str, Any] | None = None,
    seconds: float | None = None,
    guard: Guard | None = None,
) -> dict[str, str]:
    """Write one result to the run log. Returns the paths written.

    Never raises: a failure to record a run must not fail the run. If the
    output directory is unwritable the caller already has bigger problems, and
    losing the log is not the thing to surface first.
    """
    written: dict[str, str] = {}
    if not enabled():
        return written
    try:
        guard = guard or Guard.from_env()
        runs = _runs_dir(guard)
        now = datetime.now(timezone.utc)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")

        # A one-second stamp is not unique. The JSON is written with
        # `write_text` (overwrite) while the manifest is opened "a" (append), so
        # several runs inside one second produced N manifest lines all pointing
        # at ONE file holding only the last result. A calibration sweep is
        # exactly that pattern: a loop of fast calls with different arguments,
        # which is precisely when the log is worth having.
        #
        # Sub-second precision plus a collision check: the clock can repeat
        # across processes and on coarse-resolution filesystems.
        base = f"{stamp}_{name}"
        if (runs / f"{base}.json").exists():
            base = f"{now.strftime('%Y%m%dT%H%M%S')}_{now.microsecond:06d}Z_{name}"
            n = 1
            while (runs / f"{base}.json").exists():
                base = (f"{now.strftime('%Y%m%dT%H%M%S')}_"
                        f"{now.microsecond:06d}Z_{name}_{n}")
                n += 1

        payload = {
            "function": name,
            "utc": stamp,
            "seconds": seconds,
            "arguments": _jsonable(args or {}),
            "result": _jsonable(result),
        }
        jpath = runs / f"{base}.json"
        jpath.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        written["json"] = str(jpath)

        for table_name, rows in _tables(result).items():
            try:
                import pandas as pd

                cpath = runs / f"{base}_{table_name}.csv"
                pd.DataFrame(rows).to_csv(cpath, index=False)
                written[f"csv:{table_name}"] = str(cpath)
            except Exception:
                continue

        entry = {
            "utc": stamp,
            "function": name,
            "seconds": seconds,
            "arguments": _jsonable(args or {}),
            "record": str(jpath),
            "outputs": _output_paths(result),
        }
        with (runs / MANIFEST).open("a") as fh:
            fh.write(json.dumps(entry) + "\n")
        written["manifest"] = str(runs / MANIFEST)
    except Exception:
        return written
    return written


def persisted(name: str | None = None) -> Callable:
    """Decorator: run the function, then save its result to the run log.

    Applied across ``geolens.api`` so persistence is the default rather than
    something the caller has to remember. The wrapped function's own return
    value is unchanged, so nothing downstream has to know this is here.
    """
    def deco(fn: Callable) -> Callable:
        label = name or fn.__name__

        @functools.wraps(fn)
        def wrapper(*a, **kw):
            t0 = time.monotonic()
            result = fn(*a, **kw)
            elapsed = time.monotonic() - t0
            try:
                import inspect

                bound = inspect.signature(fn).bind_partial(*a, **kw)
                bound.apply_defaults()
                call_args = dict(bound.arguments)
            except Exception:
                call_args = {"args": list(a), "kwargs": kw}
            saved = save_result(label, result, call_args, elapsed)
            if isinstance(result, dict) and saved.get("json"):
                # Tell the caller where the record went, without disturbing the
                # rest of the payload.
                result.setdefault("_run_record", saved["json"])
            return result

        wrapper.__wrapped__ = fn
        return wrapper

    return deco


def history(limit: int = 50, function: str | None = None,
            guard: Guard | None = None) -> list[dict[str, Any]]:
    """Read the run log back, newest first.

    Answers "what did I already run, with what settings, and where did it go" —
    which is the question that otherwise gets answered by running it again.
    """
    guard = guard or Guard.from_env()
    path = Path(guard.out_dir) / RUNS_DIRNAME / MANIFEST
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if function and entry.get("function") != function:
            continue
        rows.append(entry)
    rows.reverse()
    return rows[:limit]


def load_run(record: str | Path) -> dict[str, Any]:
    """Load a saved result back, by the path the manifest recorded."""
    return json.loads(Path(record).read_text(encoding="utf-8"))
