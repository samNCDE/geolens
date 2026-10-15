"""Running geolens from inside QGIS, whichever Python QGIS happens to have.

QGIS ships its own interpreter. What is in it varies by platform and install
method more than anything else about QGIS does: GDAL, numpy, shapely and
matplotlib are always there; ``geopandas``, ``rasterio``, ``scipy``,
``scikit-image``, ``numba`` and ``pysheds`` may or may not be, and on a locked
-down Windows install they may not be installable either.

So the plugin does **not** decide in advance where geolens runs. It asks:

* **in-process** — QGIS's Python can import geolens. Fastest, and results come
  back as live objects. This is the good case.
* **subprocess** — QGIS's Python cannot, but a geolens environment exists
  elsewhere on the machine. Work is handed to that interpreter and the answer
  comes back as JSON. Slower by the cost of one process start, and QGIS's own
  Python is left completely alone, which on a managed install is the whole
  point.
* **unavailable** — neither. Say so, with the specific missing imports, rather
  than failing on the first analysis the user tries.

Everything here is deliberately free of PyQGIS so it can be tested without
QGIS. The plugin's PyQGIS layer is a thin adapter over this.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# What geolens needs, and what each thing is needed FOR. The plugin reports
# missing pieces against the work the user is trying to do, because "scipy is
# missing" means nothing and "gully detection needs scipy" means something.
REQUIREMENTS: dict[str, str] = {
    "rasterio": "reading and writing rasters — everything",
    "geopandas": "vector outputs — segments, valley bottoms, gullies",
    "shapely": "geometry — everything",
    "pyproj": "coordinate systems and unit handling",
    "numpy": "everything",
    "pandas": "tables and summaries",
    "scipy": "valley bottoms, roughness, gullies",
    "skimage": "gully centrelines (scikit-image)",
    "numba": "flow routing — the landscape pass",
    "pysheds": "flow routing backend",
    "matplotlib": "plots and map images",
    "tqdm": "progress bars",
}

# Needed for the landscape pass specifically. Without these the reach-scale
# tools still work, which is worth saying rather than declaring the whole
# plugin broken.
LANDSCAPE_ONLY = {"scipy", "skimage", "numba", "pysheds"}

ENV_OVERRIDE = "GEOLENS_PYTHON"


@dataclass
class BridgeStatus:
    """What the plugin can actually do here, and why."""

    mode: str                                  # in-process | subprocess | unavailable
    python: str = ""                           # interpreter that will run the work
    geolens_version: str = ""
    missing: list[str] = field(default_factory=list)
    missing_landscape_only: bool = False
    notes: list[str] = field(default_factory=list)
    searched: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.mode in ("in-process", "subprocess")

    def as_dict(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in
             ("mode", "python", "geolens_version", "missing",
              "missing_landscape_only", "notes", "searched")}
        d["ok"] = self.ok
        return d

    def summary(self) -> str:
        if self.mode == "in-process":
            head = (f"geolens {self.geolens_version} runs inside QGIS's own "
                    f"Python.")
        elif self.mode == "subprocess":
            head = (f"geolens {self.geolens_version} runs in a separate "
                    f"environment:\n  {self.python}")
        else:
            head = "geolens is not available to QGIS."
        lines = [head]
        if self.missing:
            what = ("only the landscape pass" if self.missing_landscape_only
                    else "geolens")
            lines.append("")
            lines.append(f"Missing from QGIS's Python, needed by {what}:")
            for m in self.missing:
                lines.append(f"  - {m}  ({REQUIREMENTS.get(m, 'required')})")
        for n in self.notes:
            lines.append("")
            lines.append(n)
        return "\n".join(lines)


def _probe_imports(names: "list[str] | None" = None) -> list[str]:
    """Which of these cannot be imported HERE, in this interpreter."""
    import importlib

    missing = []
    for n in names or list(REQUIREMENTS):
        try:
            importlib.import_module(n)
        except Exception:                                    # noqa: BLE001
            missing.append(n)
    return missing


def _probe_external(python: str, timeout: float = 90.0) -> "tuple[str, list[str]]":
    """Ask another interpreter for its geolens version and missing imports.

    Returns ``(version, missing)``; an empty version means it has no geolens.
    """
    code = (
        "import json,importlib\n"
        "out={'version':'','missing':[]}\n"
        "try:\n"
        "    import geolens; out['version']=geolens.__version__\n"
        "except Exception: pass\n"
        "for n in %r:\n"
        "    try: importlib.import_module(n)\n"
        "    except Exception: out['missing'].append(n)\n"
        "print('GEOLENS_PROBE'+json.dumps(out))\n"
    ) % list(REQUIREMENTS)
    try:
        r = subprocess.run([python, "-c", code], capture_output=True,
                           text=True, timeout=timeout)
    except Exception:                                        # noqa: BLE001
        return "", list(REQUIREMENTS)
    for line in (r.stdout or "").splitlines():
        if line.startswith("GEOLENS_PROBE"):
            try:
                d = json.loads(line[len("GEOLENS_PROBE"):])
                return d.get("version", ""), list(d.get("missing", []))
            except Exception:                                # noqa: BLE001
                break
    return "", list(REQUIREMENTS)


def candidate_pythons() -> list[str]:
    """Interpreters that might have geolens, most likely first.

    Deliberately conservative: an explicit setting, then a virtualenv sitting
    next to geolens itself, then whatever is on PATH. It does not go hunting
    through the filesystem — guessing wrong here means running a user's
    analysis in an environment they did not choose.
    """
    out: list[str] = []

    env = os.environ.get(ENV_OVERRIDE)
    if env:
        out.append(env)

    # A venv built by install.py lives inside the geolens source tree. If this
    # module is importable at all we know where that tree is.
    try:
        root = Path(__file__).resolve().parent.parent.parent
        for rel in (".venv/bin/python", ".venv/Scripts/python.exe",
                    "venv/bin/python", "venv/Scripts/python.exe"):
            p = root / rel
            if p.exists():
                out.append(str(p))
    except Exception:                                        # noqa: BLE001
        pass

    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            out.append(found)

    # Dedupe, keep order, and never propose the interpreter we are already in
    # as an "external" one.
    here = str(Path(sys.executable).resolve())
    seen, uniq = set(), []
    for p in out:
        try:
            rp = str(Path(p).resolve())
        except Exception:                                    # noqa: BLE001
            rp = p
        if rp in seen or rp == here:
            continue
        seen.add(rp)
        uniq.append(p)
    return uniq


def detect(prefer: str | None = None) -> BridgeStatus:
    """Work out how geolens can run from here.

    ``prefer`` names an interpreter to try before anything else — the plugin
    passes whatever the user set in its settings.
    """
    missing_here = _probe_imports()
    try:
        import geolens                                       # noqa: PLC0415
        version = geolens.__version__
        have_geolens = True
    except Exception:                                        # noqa: BLE001
        version, have_geolens = "", False

    # The good case: this interpreter can do everything.
    if have_geolens and not missing_here:
        return BridgeStatus(mode="in-process", python=sys.executable,
                            geolens_version=version)

    # geolens imports but some analyses will fail. Worth distinguishing,
    # because the reach-scale tools are fine without the landscape stack.
    if have_geolens and missing_here:
        only_landscape = set(missing_here) <= LANDSCAPE_ONLY
        st = BridgeStatus(
            mode="in-process", python=sys.executable, geolens_version=version,
            missing=missing_here, missing_landscape_only=only_landscape)
        if only_landscape:
            st.notes.append(
                "Cross-sections, profiles and bank delineation will work. The "
                "landscape pass, valley bottoms and gullies will not until "
                "those are installed.")
        return st

    candidates = [prefer] if prefer else []
    candidates += candidate_pythons()
    searched = []
    for py in candidates:
        if not py:
            continue
        searched.append(py)
        ver, miss = _probe_external(py)
        if ver and not miss:
            return BridgeStatus(mode="subprocess", python=py,
                                geolens_version=ver, searched=searched)
        if ver and set(miss) <= LANDSCAPE_ONLY:
            return BridgeStatus(
                mode="subprocess", python=py, geolens_version=ver,
                missing=miss, missing_landscape_only=True, searched=searched,
                notes=["That environment is missing the landscape stack; "
                       "reach-scale tools will still work."])

    return BridgeStatus(
        mode="unavailable", python=sys.executable, missing=missing_here,
        searched=searched,
        notes=[
            "Install geolens into QGIS's Python (see INSTALL.md), or point the "
            f"plugin at an existing environment by setting {ENV_OVERRIDE} to "
            "its python executable.",
        ])


def run(function: str, kwargs: dict[str, Any],
        status: BridgeStatus | None = None,
        timeout: float = 86400.0) -> dict[str, Any]:
    """Call a ``geolens.api`` function, in-process or in another interpreter.

    The signature is the same either way, which is the point: the plugin's
    algorithms are written once and do not know or care which mode they are
    running under.
    """
    st = status or detect()
    if not st.ok:
        raise RuntimeError(st.summary())

    if st.mode == "in-process":
        import geolens.api as api                            # noqa: PLC0415

        fn = getattr(api, function, None)
        if fn is None:
            raise RuntimeError(f"geolens.api has no function {function!r}.")
        return fn(**kwargs)

    code = (
        "import json,sys\n"
        "import geolens.api as api\n"
        "payload=json.loads(sys.stdin.read())\n"
        "fn=getattr(api,payload['function'])\n"
        "res=fn(**payload['kwargs'])\n"
        "def plain(o):\n"
        "    try:\n"
        "        json.dumps(o); return o\n"
        "    except Exception: return str(o)\n"
        "print('GEOLENS_RESULT'+json.dumps(res,default=plain))\n"
    )
    proc = subprocess.run(
        [st.python, "-c", code],
        input=json.dumps({"function": function, "kwargs": kwargs}),
        capture_output=True, text=True, timeout=timeout)
    for line in (proc.stdout or "").splitlines():
        if line.startswith("GEOLENS_RESULT"):
            return json.loads(line[len("GEOLENS_RESULT"):])
    raise RuntimeError(
        f"geolens failed in {st.python}:\n"
        f"{(proc.stderr or proc.stdout or '').strip()[-2000:]}")


def doctor(prefer: str | None = None) -> dict[str, Any]:
    """A report fit to print in the QGIS Python console.

    Meant to be run *before* installing the plugin, from
    **Plugins -> Python Console**::

        import geolens.qgis_bridge as b; print(b.doctor()["summary"])

    which answers the only question that matters up front — will this work
    here, and if not, what exactly is missing.
    """
    st = detect(prefer=prefer)
    d = st.as_dict()
    d["summary"] = st.summary()
    d["interpreter"] = sys.executable
    d["requirements"] = REQUIREMENTS
    return d
