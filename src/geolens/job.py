"""Run one geolens call from a settings file, in a console window of its own.

The ArcGIS toolbox's "Run in its own window" (and anyone, by hand) writes
the call into the project folder as ``geolens_run.json`` with a launcher
beside it (``run_geolens.bat``), and starts the launcher::

    python -m geolens.job S:\\project\\geolens_run.json

A run of days then does not tie up ArcGIS Pro, and after a stop, a crash or
a reboot the launcher is double-clicked to carry on (a project run skips
what is done). The result goes beside the settings as
``geolens_run_result.json``; the progress bars and notes go to the window.

Exit codes, which the launcher reads: 0 finished; 1 geolens stopped with an
error (the launcher tries again, a project run carries on where it
stopped); 3 another run is already working in that folder (nothing to try
again); 2 the settings file could not be read.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

SETTINGS = "geolens_run.json"
RESULT = "geolens_run_result.json"
EXIT_FAILED = 1
EXIT_SETTINGS = 2
EXIT_BUSY = 3


def write_settings(function: str, kwargs: dict, out_dir: str | Path,
                   by: str = "") -> Path:
    """``out_dir/geolens_run.json``: the call a launcher runs (again)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / SETTINGS
    path.write_text(json.dumps(
        {"function": function, "kwargs": kwargs, "out_dir": str(out),
         "written": time.strftime("%Y-%m-%d %H:%M:%S"), "by": by},
        indent=1, default=str), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m geolens.job <folder>/geolens_run.json",
              file=sys.stderr)
        return EXIT_SETTINGS
    path = Path(args[0]).expanduser().resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        function = str(payload["function"])
        kwargs = dict(payload.get("kwargs") or {})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: cannot read the settings {path}: {exc}", file=sys.stderr)
        return EXIT_SETTINGS
    out = Path(payload.get("out_dir") or path.parent)
    os.environ["GEOLENS_OUT"] = str(out)
    # Bars in the window, not the JSON lines a tool dialog reads.
    os.environ.pop("GEOLENS_PROGRESS", None)
    os.environ.pop("GEOLENS_NO_PROGRESS", None)

    from . import __version__, api
    from .project import ProjectBusy
    from .safety import GeolensError

    fn = getattr(api, function, None)
    if not callable(fn):
        print(f"error: geolens has no function {function!r}", file=sys.stderr)
        return EXIT_SETTINGS
    print(f"geolens {__version__} ({Path(__file__).resolve().parent}): "
          f"{function} -> {out}", file=sys.stderr, flush=True)
    t0 = time.monotonic()
    try:
        res = fn(**kwargs)
    except ProjectBusy as exc:
        print(f"\n{exc}", file=sys.stderr)
        return EXIT_BUSY
    except GeolensError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return EXIT_FAILED
    except Exception:                                        # noqa: BLE001
        traceback.print_exc()
        return EXIT_FAILED
    if isinstance(res, dict):
        res = {k: v for k, v in res.items() if not str(k).startswith("_")}
    try:
        (out / RESULT).write_text(json.dumps(
            {"function": function, "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
             "seconds": round(time.monotonic() - t0, 1), "result": res},
            indent=1, default=str), encoding="utf-8")
    except OSError as exc:
        print(f"(the result could not be written beside the settings: {exc})",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
