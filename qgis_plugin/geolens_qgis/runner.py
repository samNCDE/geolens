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


# -- a run in a window of its own --------------------------------------------------
# A project run of days should not tie up the GIS, nor end when it is closed.
# The call is written into the output folder (geolens_run.json, read by
# ``python -m geolens.job``) with a launcher beside it, and the launcher is
# started in a console window of its own. After a stop, a crash or a reboot,
# double-clicking the launcher carries on (a project run skips what is
# done); it also tries again by itself, a minute apart, up to five times.

SETTINGS_FILE = "geolens_run.json"
LAUNCHER_BAT = "run_geolens.bat"
LAUNCHER_SH = "run_geolens.sh"
_LAUNCH_TRIES = 5
_ENV_DIRS = ("", "Library\\mingw-w64\\bin", "Library\\usr\\bin", "Library\\bin",
             "Scripts", "bin")


def running_pid(out_dir: str) -> int:
    """The process of a geolens run working in ``out_dir`` now (its
    run.lock), or 0."""
    try:
        pid = int((Path(out_dir) / "run.lock").read_text(
            encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return 0
    if pid <= 0 or pid == os.getpid():
        return 0
    if os.name == "nt":
        try:
            import ctypes

            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, pid)    # QUERY_LIMITED_INFORMATION
            if not h:
                return 0
            code = ctypes.c_ulong()
            ok = k.GetExitCodeProcess(h, ctypes.byref(code))
            k.CloseHandle(h)
            return pid if ok and code.value == 259 else 0   # STILL_ACTIVE
        except Exception:                                    # noqa: BLE001
            return 0
    try:
        os.kill(pid, 0)
        return pid
    except OSError:
        return 0


def _bat(s: str) -> str:
    """A value inside a .bat line: % doubled."""
    return str(s).replace("%", "%%")


def _bat_echo(s: str) -> str:
    """Text for an ``echo`` or ``title`` line: cmd's special characters
    escaped."""
    out = _bat(s)
    for c in "^&|<>":
        out = out.replace(c, "^" + c)
    return out


def launcher_bat(python: str, settings: str, out_dir: str, title: str,
                 function: str, src: str | None = None,
                 extra: dict[str, str] | None = None, by: str = "") -> str:
    """The Windows launcher's text (CRLF line ends are added on writing)."""
    sets = [f'set "{k}={_bat(v)}"' for k, v in (extra or {}).items()]
    if src:
        sets = [f'set "PYTHONPATH={_bat(src)}"',
                f'set "GEOLENS_SRC={_bat(src)}"'] + sets
    dirs = ";".join("%ENVDIR%" + ("\\" + d if d else "") for d in _ENV_DIRS)
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "rem " + "=" * 75,
        f"rem  geolens: {_bat(title)}",
        f"rem  Written by {_bat(by or 'geolens')} on {time.strftime('%Y-%m-%d %H:%M')}; "
        f"the call it runs is {SETTINGS_FILE} beside it.",
        "rem",
        "rem  Leave the window open: closing it stops the run. After a stop, a",
        "rem  crash, a reboot or a power cut, double-click this file: it carries on",
        "rem  where it stopped (finished steps are kept). It tries again by itself,",
        f"rem  a minute apart, up to {_LAUNCH_TRIES} times if geolens stops with an error.",
        "rem " + "=" * 75,
        "setlocal EnableExtensions",
        f"title geolens {_bat_echo(title)} - leave open (close to stop; run again "
        f"to carry on)",
        f'cd /d "{_bat(out_dir)}"',
        f'set "PY={_bat(python)}"',
        'if not exist "%PY%" (',
        "  echo.",
        "  echo  The geolens Python environment is not where it was: %PY%",
        "  echo  Start the run again from the toolbox, which finds it.",
        "  pause",
        "  exit /b 2",
        ")",
        "rem the environment's own DLLs first; no other program's GDAL, PROJ or",
        "rem Python settings (those of ArcGIS Pro or QGIS among them)",
        'for %%P in ("%PY%") do set "ENVDIR=%%~dpP"',
        'set "ENVDIR=%ENVDIR:~0,-1%"',
        f'set "PATH={dirs};%PATH%"',
        "for %%V in (" + " ".join(_STRIP_ENV) + " GEOLENS_PROGRESS "
        "GEOLENS_NO_PROGRESS) do set \"%%V=\"",
        *sets,
        'set "PYTHONIOENCODING=utf-8"',
        'set "PYTHONUNBUFFERED=1"',
        "",
        "set /a TRIES=0",
        ":run",
        "set /a TRIES+=1",
        "echo.",
        f"echo  geolens {_bat_echo(function)}, attempt %TRIES% of {_LAUNCH_TRIES}, "
        f"started %DATE% %TIME%",
        "echo.",
        f'"%PY%" -u -m geolens.job "{_bat(settings)}"',
        'set "RC=%ERRORLEVEL%"',
        'if "%RC%"=="0" goto done',
        'if "%RC%"=="3" goto busy',
        'if "%RC%"=="2" goto failed',
        f"if %TRIES% GEQ {_LAUNCH_TRIES} goto failed",
        "echo.",
        "echo  geolens stopped with an error (code %RC%). Trying again in 60 seconds...",
        "timeout /t 60 /nobreak >nul",
        "goto run",
        "",
        ":busy",
        "echo.",
        "echo  Another geolens run is working in this folder: nothing was started.",
        "echo.",
        "pause",
        "exit /b 3",
        "",
        ":done",
        "echo.",
        "echo  Finished %DATE% %TIME%.",
        f'if exist "RESULTS.md" echo  Results: {_bat_echo(out_dir)}\\RESULTS.md',
        "echo.",
        "pause",
        "exit /b 0",
        "",
        ":failed",
        "echo.",
        "echo  geolens stopped with an error (last code %RC%). What finished is kept:",
        "echo  double-click this file to carry on, or see the messages above.",
        "echo.",
        "pause",
        "exit /b 1",
    ]
    return "\n".join(lines) + "\n"


def _sh(s: str) -> str:
    return "'" + str(s).replace("'", "'\\''") + "'"


def launcher_sh(python: str, settings: str, out_dir: str, title: str,
                function: str, src: str | None = None,
                extra: dict[str, str] | None = None, by: str = "") -> str:
    env = dict(extra or {})
    if src:
        env = {"PYTHONPATH": src, "GEOLENS_SRC": src, **env}
    lines = [
        "#!/bin/sh",
        f"# geolens: {title}",
        f"# Written by {by or 'geolens'} on {time.strftime('%Y-%m-%d %H:%M')}; the "
        f"call it runs is {SETTINGS_FILE} beside it. Run it again to carry on",
        "# where it stopped (finished steps are kept).",
        f"cd {_sh(out_dir)} || exit 2",
        "unset " + " ".join(_STRIP_ENV) + " GEOLENS_PROGRESS GEOLENS_NO_PROGRESS",
        *[f"export {k}={_sh(v)}" for k, v in env.items()],
        "export PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1",
        "tries=0",
        "while :; do",
        "  tries=$((tries + 1))",
        f'  echo "geolens {function}, attempt $tries of {_LAUNCH_TRIES}, started $(date)"',
        f"  {_sh(python)} -u -m geolens.job {_sh(settings)}",
        "  rc=$?",
        '  [ "$rc" -eq 0 ] && { echo "Finished $(date)."; exit 0; }',
        '  [ "$rc" -eq 3 ] && { echo "Another geolens run is working in this '
        'folder: nothing was started."; exit 3; }',
        '  [ "$rc" -eq 2 ] && exit 2',
        f'  [ "$tries" -ge {_LAUNCH_TRIES} ] && {{ echo "geolens stopped with an '
        f'error (last code $rc): run this again to carry on."; exit 1; }}',
        '  echo "geolens stopped with an error (code $rc). Trying again in 60 '
        'seconds..."',
        "  sleep 60",
        "done",
    ]
    return "\n".join(lines) + "\n"


def write_launcher(python: str, function: str, kwargs: dict[str, Any],
                   out_dir: str, title: str = "", by: str = "",
                   windows: bool | None = None) -> tuple[Path, Path]:
    """geolens_run.json and the launcher beside it, in ``out_dir``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    settings = out / SETTINGS_FILE
    settings.write_text(json.dumps(
        {"function": function, "kwargs": kwargs, "out_dir": str(out),
         "written": time.strftime("%Y-%m-%d %H:%M:%S"), "by": by},
        indent=1, default=str), encoding="utf-8")
    src = release_src()
    root = portable_root()
    extra = _portable_env(root) if root is not None else None
    win = (os.name == "nt") if windows is None else windows
    args = (python, str(settings), str(out), title or function, function,
            str(src) if src else None, extra, by)
    if win:
        path = out / LAUNCHER_BAT
        path.write_bytes(launcher_bat(*args).replace("\n", "\r\n")
                         .encode("utf-8"))
    else:
        path = out / LAUNCHER_SH
        path.write_text(launcher_sh(*args), encoding="utf-8")
        path.chmod(0o755)
    return settings, path


def launch_detached(python: str, function: str, kwargs: dict[str, Any],
                    out_dir: str, title: str = "", by: str = "",
                    start: bool = True) -> dict[str, Any]:
    """Write the call and its launcher into ``out_dir`` and start the
    launcher in a console window of its own; returns at once. Refuses if a
    run is already working in that folder."""
    busy = running_pid(out_dir)
    if busy:
        raise RuntimeError(
            f"Another geolens run (process {busy}) is working in {out_dir}. "
            f"Let it finish, or close its window, before starting this folder "
            f"again.")
    settings, launcher = write_launcher(python, function, kwargs, out_dir,
                                        title, by)
    info: dict[str, Any] = {"settings": str(settings), "launcher": str(launcher),
                            "pid": None, "log": None}
    if not start:
        return info
    env = clean_env(python)
    if os.name == "nt":
        flags = (getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200))
        breakaway = 0x01000000          # CREATE_BREAKAWAY_FROM_JOB: outlive the GIS
        try:
            proc = subprocess.Popen(["cmd.exe", "/c", str(launcher)],
                                    cwd=str(out_dir), env=env,
                                    creationflags=flags | breakaway,
                                    close_fds=True)
        except OSError:                 # the GIS's job does not allow it
            proc = subprocess.Popen(["cmd.exe", "/c", str(launcher)],
                                    cwd=str(out_dir), env=env,
                                    creationflags=flags, close_fds=True)
    else:
        log = Path(out_dir) / "run_geolens.log"
        info["log"] = str(log)
        with open(log, "ab") as fh:
            proc = subprocess.Popen(["sh", str(launcher)], cwd=str(out_dir),
                                    env=env, stdout=fh, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL,
                                    start_new_session=True, close_fds=True)
    info["pid"] = proc.pid
    return info


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
