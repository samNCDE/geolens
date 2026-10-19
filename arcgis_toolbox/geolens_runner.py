"""Run geolens in its own environment, as a subprocess, from QGIS or ArcGIS.

The host's own Python (OSGeo4W for QGIS, arcgispro-py3 for ArcGIS Pro) has
GDAL and numpy but not pysheds, numba or geopandas, and a landscape pass can
use tens of gigabytes: run inside the GIS, one out-of-memory kill takes the
whole project down. So every tool hands its work to the Python environment
geolens's installer built, streams its progress back into the tool dialog,
and can be cancelled.

No PyQGIS or arcpy here, only callbacks passed in, so it can be tested
without either. This file is both the QGIS plugin's ``runner.py`` and the
ArcGIS toolbox's ``geolens_runner.py``; a test keeps the two identical. The
callbacks are only ever called from the thread that called ``run_api`` (the
tool's own thread): neither QGIS feedback objects nor arcpy's progressor are
meant to be touched from a thread they know nothing about.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

SETTINGS_KEY = "geolens/python"

# install.py writes the interpreter it installed into (or updated) here, so
# the plugin finds the environment wherever conda or the venv put it.
POINTER = Path.home() / ".geolens" / "python.txt"

# QGIS and ArcGIS Pro point these at THEIR OWN Python, GDAL and PROJ. Handed
# to a different Python they make it import the wrong site-packages or read a
# PROJ database of another version ("DATABASE.LAYOUT.VERSION mismatch").
_STRIP_ENV = ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE",
              "GDAL_DATA", "GDAL_DRIVER_PATH", "PROJ_LIB", "PROJ_DATA",
              "GEOTIFF_CSV", "QT_PLUGIN_PATH", "CONDA_PREFIX",
              "CONDA_DEFAULT_ENV", "CONDA_SHLVL", "CONDA_PROMPT_MODIFIER",
              "CONDA_PYTHON_EXE", "CONDA_EXE")
# PATH entries from the host application: their DLLs (GDAL above all) must
# not be found before the geolens environment's own.
_HOST_PATH_MARKERS = ("qgis", "osgeo4w", "arcgis", "esri")

# Interpreters already probed OK in this QGIS session: probing imports numba
# and pysheds, which takes seconds on Windows, and a landscape pass makes two
# calls.
_GOOD: dict[str, dict] = {}


def _pointer() -> str | None:
    try:
        txt = POINTER.read_text(encoding="utf-8").strip()
        return txt or None
    except OSError:
        return None


def candidate_pythons(setting: str | None = None) -> list[str]:
    """Where the geolens environment usually is, most likely first.

    A portable release's own environment is the only candidate: it must
    never fall back to (or be shadowed by) an environment on the computer.
    """
    own = portable_python()
    if own:
        return [own]
    out: list[str] = []
    for v in (setting, os.environ.get("GEOLENS_PYTHON"), _pointer()):
        if v:
            out.append(v)
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    bases = [home / "anaconda3", home / "miniconda3", home / "miniforge3",
             home / "mambaforge", local / "anaconda3", local / "miniconda3",
             local / "miniforge3", local / "mambaforge",
             Path("C:/ProgramData/anaconda3"), Path("C:/ProgramData/miniconda3"),
             Path("C:/ProgramData/miniforge3"), Path("C:/ProgramData/mambaforge")]
    envs = [b / "envs" / "geolens" for b in bases]
    envs += [home / ".conda" / "envs" / "geolens",
             local / "ESRI" / "conda" / "envs" / "geolens"]
    for e in envs:
        out += [str(e / "python.exe"), str(e / "bin" / "python")]
    return [p for p in dict.fromkeys(out) if p and Path(p).exists()]


def release_src() -> Path | None:
    """The ``src`` folder of the release this file came with, if any.

    The ArcGIS toolbox sits in a release folder beside ``src``; the geolens
    it runs is that one, so a toolbox and its geolens can never be different
    versions, and a new release runs from wherever it was unzipped without
    re-pointing the environment (which lives on another drive). The QGIS
    plugin, installed into QGIS's profile, has no ``src`` beside it and uses
    the environment's own geolens.
    """
    here = Path(__file__).resolve().parent
    for cand in (here.parent / "src", here.parent.parent / "src"):
        if (cand / "geolens" / "__init__.py").is_file():
            return cand
    return None


def portable_root() -> Path | None:
    """The release folder, when it carries its own environment (``env``).

    A portable release (built by ``setup_portable.bat``) runs entirely from
    its drive: its own Python, and its temporary files, caches and settings
    in ``home`` and ``tmp`` beside it, so it can be handed to someone else on
    a hard drive and nothing is written to the computer it is plugged into.
    """
    src = release_src()
    if src is None:
        return None
    root = src.parent
    for py in (root / "env" / "python.exe", root / "env" / "bin" / "python"):
        if py.is_file():
            return root
    return None


def portable_python() -> str | None:
    root = portable_root()
    if root is None:
        return None
    for py in (root / "env" / "python.exe", root / "env" / "bin" / "python"):
        if py.is_file():
            return str(py)
    return None


def _portable_env(root: Path) -> dict[str, str]:
    """Where a portable run keeps what would otherwise go to the computer:
    temporary files, caches (routing, matplotlib, numba, PROJ grids) and
    the Earthdata login."""
    home, tmp = root / "home", root / "tmp"
    for d in (home, tmp):
        d.mkdir(parents=True, exist_ok=True)
    return {"TMP": str(tmp), "TEMP": str(tmp), "TMPDIR": str(tmp),
            "LOCALAPPDATA": str(home / "cache"),
            "XDG_CACHE_HOME": str(home / "cache"),
            "MPLCONFIGDIR": str(home / "matplotlib"),
            "NUMBA_CACHE_DIR": str(home / "numba"),
            "PYTHONNOUSERSITE": "1", "GEOLENS_HOME": str(home),
            "GEOLENS_NETRC": str(home / "_netrc")}


def clean_env(python: str, extra: dict[str, str] | None = None
              ) -> dict[str, str]:
    """The environment for the geolens Python: QGIS's settings removed, the
    conda environment's own DLL folders first on PATH."""
    env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}
    root = Path(python).resolve().parent
    if root.name.lower() in ("bin", "scripts"):
        root = root.parent
    first = [root, root / "Library" / "mingw-w64" / "bin",
             root / "Library" / "usr" / "bin", root / "Library" / "bin",
             root / "Scripts", root / "bin"]
    rest = [p for p in env.get("PATH", "").split(os.pathsep)
            if p and not any(m in p.lower() for m in _HOST_PATH_MARKERS)]
    env["PATH"] = os.pathsep.join([str(p) for p in first if p.exists()]
                                  + rest)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    src = release_src()
    if src is not None:
        env["GEOLENS_SRC"] = str(src)
    root = portable_root()
    if root is not None:
        env.update(_portable_env(root))
    if extra:
        env.update(extra)
    return env


def _popen_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


# Both snippets put the release's own src first on the path (GEOLENS_SRC,
# set by clean_env), ahead of whatever geolens the environment has.
_SRC_FIRST = (
    "import os,sys\n"
    "_s=os.environ.get('GEOLENS_SRC')\n"
    "if _s and _s not in sys.path: sys.path.insert(0,_s)\n"
)

_PROBE = _SRC_FIRST + (
    "import json,importlib\n"
    "out={'version':'','missing':[],'python':sys.executable,'code':''}\n"
    "try:\n"
    "    import geolens; out['version']=geolens.__version__\n"
    "    out['code']=os.path.dirname(os.path.dirname(geolens.__file__))\n"
    "except Exception as e: out['error']=repr(e)\n"
    "for n in ('rasterio','geopandas','shapely','pyproj','numpy','pandas',\n"
    "          'scipy','skimage','numba','pysheds','matplotlib'):\n"
    "    try: m=importlib.import_module(n)\n"
    "    except Exception: out['missing'].append(n); continue\n"
    "    if n=='numba': out['numba']=str(getattr(m,'__version__',''))\n"
    "print('GEOLENS_PROBE'+json.dumps(out))\n"
)

# pysheds' flow routing is numba-compiled, and numba 0.68 cannot compile one
# of its generators: every landscape pass dies in a TypingError about pysheds
# internals. The installers pin numba below this; an environment upgraded
# afterwards (conda update --all, pip install -U numba) gets this warning.
NUMBA_BELOW = (0, 62)


def numba_warning(info: dict[str, Any]) -> str:
    """Why this environment's numba will break flow routing, or ''."""
    ver = str(info.get("numba") or "")
    try:
        major, minor = (int(x) for x in ver.split(".")[:2])
    except ValueError:
        return ""
    if (major, minor) < NUMBA_BELOW:
        return ""
    return ("numba %s is too new for pysheds' flow routing (0.61.2 is "
            "tested; 0.68 fails). In the geolens environment run:\n"
            "  pip install \"numba>=0.57,<0.62\"" % ver)


def probe(python: str, timeout: float = 180.0) -> dict[str, Any]:
    """geolens version and missing packages in that interpreter."""
    try:
        r = subprocess.run([python, "-c", _PROBE], capture_output=True,
                           text=True, timeout=timeout, env=clean_env(python),
                           **_popen_kwargs())
    except Exception as exc:                                  # noqa: BLE001
        return {"version": "", "missing": [], "error": repr(exc)}
    for line in (r.stdout or "").splitlines():
        if line.startswith("GEOLENS_PROBE"):
            return json.loads(line[len("GEOLENS_PROBE"):])
    return {"version": "", "missing": [],
            "error": (r.stderr or r.stdout or "no output")[-800:]}


def find_python(setting: str | None = None, fresh: bool = False
                ) -> tuple[str | None, dict]:
    """The first candidate with a working geolens."""
    tried = {}
    for py in candidate_pythons(setting):
        info = _GOOD.get(py) if not fresh else None
        if info is None:
            info = probe(py)
        tried[py] = info
        if info.get("version") and not info.get("missing"):
            _GOOD[py] = info
            return py, tried
    return None, tried


_RUN = _SRC_FIRST + (
    "import json,sys\n"
    "import geolens.api as api\n"
    "payload=json.loads(sys.stdin.read())\n"
    "fn=getattr(api,payload['function'])\n"
    "res=fn(**payload['kwargs'])\n"
    "if isinstance(res,dict):\n"
    "    res={k:v for k,v in res.items() if not str(k).startswith('_')}\n"
    "def plain(o):\n"
    "    try:\n"
    "        json.dumps(o); return o\n"
    "    except Exception: return str(o)\n"
    "sys.stdout.write('GEOLENS_RESULT'+json.dumps(res,default=plain)+'\\n')\n"
    "sys.stdout.flush()\n"
)


class Cancelled(Exception):
    pass


def _stop(proc: subprocess.Popen) -> None:
    """End the run and anything it started (section plots use worker
    processes, which outlive a parent killed with TerminateProcess)."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=30,
                           **_popen_kwargs())
        except Exception:                                    # noqa: BLE001
            pass
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:                                    # noqa: BLE001
            proc.kill()


def run_api(python: str, function: str, kwargs: dict[str, Any],
            out_dir: str, on_progress: Callable[[float, str], None] | None = None,
            on_info: Callable[[str], None] | None = None,
            on_debug: Callable[[str], None] | None = None,
            is_cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Call ``geolens.api.<function>(**kwargs)`` in ``python``.

    Output lands in ``out_dir`` (GEOLENS_OUT). Progress lines become
    ``on_progress(percent, label)``, status notes ``on_info(text)``, anything
    else ``on_debug(text)``, all called from this thread. ``is_cancelled`` is
    polled; when it turns true the process is stopped and ``Cancelled``
    raised.
    """
    env = clean_env(python, {"GEOLENS_OUT": str(out_dir),
                             "GEOLENS_PROGRESS": "json"})
    env.pop("GEOLENS_NO_PROGRESS", None)       # the dialog's bar needs it
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [python, "-u", "-c", _RUN], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        text=True, encoding="utf-8", errors="replace", **_popen_kwargs())
    result: dict[str, Any] = {}
    tail: list[str] = []
    events: queue.Queue = queue.Queue()

    def _read_err():
        for line in proc.stderr:
            line = line.rstrip("\n")
            if line.startswith("GEOLENS_PROGRESS "):
                try:
                    d = json.loads(line[len("GEOLENS_PROGRESS "):])
                    if d.get("total"):
                        events.put(("progress", 100.0 * d["n"] / d["total"],
                                    str(d.get("label", ""))))
                except Exception:                            # noqa: BLE001
                    pass
            elif line.startswith("GEOLENS_NOTE "):
                events.put(("info", line[len("GEOLENS_NOTE "):]))
            elif line.strip():
                tail.append(line)
                del tail[:-60]
                events.put(("debug", line))

    def _read_out():
        for line in proc.stdout:
            if line.startswith("GEOLENS_RESULT"):
                result["value"] = json.loads(line[len("GEOLENS_RESULT"):])
            elif line.strip():
                events.put(("debug", line.rstrip("\n")))

    def _drain():
        # A callback that raises stops the run: that is how arcpy reports a
        # cancel on versions without arcpy.env.isCancelled, and a run nobody
        # can see progress for should not carry on in the background.
        while True:
            try:
                ev = events.get_nowait()
            except queue.Empty:
                return
            if ev[0] == "progress" and on_progress:
                on_progress(ev[1], ev[2])
            elif ev[0] == "info" and on_info:
                on_info(ev[1])
            elif ev[0] == "debug" and on_debug:
                on_debug(ev[1])

    threads = [threading.Thread(target=_read_err, daemon=True),
               threading.Thread(target=_read_out, daemon=True)]
    for t in threads:
        t.start()

    def _close():
        for t in threads:
            t.join(timeout=5)
        for f in (proc.stdout, proc.stderr):
            try:
                f.close()
            except Exception:                                # noqa: BLE001
                pass

    proc.stdin.write(json.dumps({"function": function, "kwargs": kwargs}))
    proc.stdin.close()
    try:
        while proc.poll() is None:
            _drain()
            if is_cancelled and is_cancelled():
                raise Cancelled("cancelled")
            time.sleep(0.2)
    except BaseException:
        _stop(proc)
        _close()
        raise
    _close()
    _drain()
    if "value" not in result:
        raise RuntimeError(
            f"geolens.api.{function} failed (exit code {proc.returncode}) in "
            f"{python}:\n" + "\n".join(tail[-25:]))
    return result["value"]


# -- NASA Earthdata login --------------------------------------------------------
# Written here, by the host program's own Python, so a password typed into a
# tool's dialog goes straight into the user's netrc file: never through the
# geolens subprocess, its run records or a log. geolens.sar reads that file,
# and has the same writer for notebooks; a test keeps the two in step.

EARTHDATA_HOST = "urs.earthdata.nasa.gov"


def netrc_file() -> Path:
    """``home\\_netrc`` on a portable release's drive; otherwise
    ``%USERPROFILE%\\_netrc`` on Windows, ``~/.netrc`` elsewhere."""
    root = portable_root()
    if root is not None:
        return root / "home" / "_netrc"
    return Path.home() / ("_netrc" if os.name == "nt" else ".netrc")


def save_earthdata_login(username: str, password: str,
                         path: str | None = None) -> str:
    """Save a NASA Earthdata login in the user's netrc. Other sites' entries
    are kept. Raises ValueError with a message for the user."""
    user, pw = str(username or "").strip(), str(password or "")
    if not user or not pw:
        raise ValueError("Give both the Earthdata username and password.")
    if any(c.isspace() or c in "\"'" for c in user + pw):
        raise ValueError(
            "A netrc file cannot hold a username or password containing "
            "spaces or quotes. Change the Earthdata password at "
            "https://urs.earthdata.nasa.gov and save it again.")
    p = Path(path) if path else netrc_file()
    keep: list[list[str]] = []
    if p.exists():
        text = p.read_text(encoding="utf-8")
        toks = " ".join(line for line in text.splitlines()
                        if not line.lstrip().startswith("#")).split()
        if "macdef" in toks:
            raise ValueError(
                f"{p} has a macdef entry, which geolens will not rewrite. "
                f"Add this line to it yourself: machine {EARTHDATA_HOST} "
                f"login <username> password <password>")
        cur: list[str] | None = None
        for i, t in enumerate(toks):
            if t in ("machine", "default") and (
                    i == 0 or toks[i - 1] not in ("login", "password",
                                                  "account")):
                cur = [t]
                keep.append(cur)
            elif cur is not None:
                cur.append(t)
        keep = [e for e in keep
                if not (e[0] == "machine" and e[1:2] == [EARTHDATA_HOST])]
    lines = [f"machine {EARTHDATA_HOST} login {user} password {pw}"]
    lines += [" ".join(e) for e in keep]
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    tmp.replace(p)
    return str(p)
