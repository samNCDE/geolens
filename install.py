#!/usr/bin/env python3
"""
geolens installer and doctor.

STANDARD LIBRARY ONLY. This file has to run on whatever Python you happen to
have before anything is installed, so it must not import numpy, rich, tqdm or
anything else. Do not add third-party imports here.

Usage
-----
  python install.py              install into a dedicated environment (recommended)
  python install.py --check      diagnose only; install nothing
  python install.py --smoke      run the real workflow on a synthetic valley
  python install.py --here       install into the Python that is running this file
  python install.py --conda      build a conda-forge env instead of a venv

Why this script exists
----------------------
The hard part of this package is not geolens, it is the GDAL stack underneath
it (rasterio / fiona / geopandas / pyproj). On Windows those four are the ones
that break, and they break in ways whose error messages point nowhere useful:

  * "DLL load failed while importing _rasterio"  -> conda and pip each installed
    half the GDAL stack and the two halves disagree about which libgdal to load.
  * "proj.db not found" / CRS lookups failing    -> PROJ data directory lost,
    usually the same mixed-install cause.
  * "Numba needs NumPy 2.x or less"              -> numpy was upgraded after
    numba was installed.
  * "requires a different Python"                -> ArcGIS Pro's bundled Python.

So this script does two things. It installs into a CLEAN dedicated environment
rather than into a shared one (which is what keeps the mixed-install failure
from happening at all), and if something is still wrong it tells you which of
the above it is instead of making you read a traceback.

The default is a venv with pip wheels, on every platform. rasterio, fiona,
pyproj and shapely all ship Windows wheels now with their own GDAL/PROJ/GEOS
bundled and name-mangled, so the DLL collision that used to make conda
mandatory here does not happen any more. conda is still available with --conda,
but conda has to solve against whatever channel mix your base install came
with, which is a failure mode pip does not have at all.
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ENV_NAME_DEFAULT = "geolens"
PY_VERSION_DEFAULT = "3.11"

# Installed from conda-forge when conda is available. These are the packages
# that carry compiled binaries; getting them all from one channel in one
# transaction is the whole point. numpy is deliberately NOT pinned here -- conda
# picks a numpy that numba accepts, which is exactly the coupling we want it to
# resolve rather than guess at.
CONDA_CORE = [
    "geopandas>=1.0",
    "rasterio>=1.3",
    "shapely>=2.0",
    "pyproj>=3.4",
    "fiona>=1.9",
    "pandas>=2.0",
    "matplotlib-base>=3.7",
    "tqdm>=4.65",
]
CONDA_LANDSCAPE = [
    "scikit-image>=0.21",
    "scipy>=1.10",
    "psutil>=5.9",
    "numba>=0.57,<0.62",
]
CONDA_NOTEBOOK = ["jupyterlab", "ipykernel"]

# The pip route, used when there is no conda. Same split, and the heavy ones go
# in with --only-binary so pip can never decide to compile GDAL from source --
# that "install" grinds for half an hour and then fails on a missing header.
PIP_CORE = [
    "numpy>=1.24", "pandas>=2.0", "shapely>=2.0", "pyproj>=3.4",
    "fiona>=1.9", "rasterio>=1.3", "geopandas>=1.0", "matplotlib>=3.7",
    "tqdm>=4.65",
]
PIP_LANDSCAPE = ["scipy>=1.10", "scikit-image>=0.21", "numba>=0.57,<0.62", "psutil>=5.9",
                 "topotoolbox>=0.0.10"]  # headcuts. 0.0.x is the real PyPI
                                         # series; the TopoToolbox project
                                         # version 3.x is a different number.
PIP_NOTEBOOK = ["jupyterlab", "ipykernel"]

# Installed with pip --no-deps afterwards, so pip cannot decide to "fix" the
# GDAL stack that was just installed. --no-deps means we own pysheds' own
# dependencies, so the pure-Python ones it needs are listed here explicitly.
# geojson and looseversion are the two that bit: they are imported by
# pysheds.grid, not by pysheds itself, so nothing notices they are missing
# until the first real call. Anything else still missing is picked up by
# _fill_missing_imports below.
PIP_AFTER_CONDA = ["pysheds>=0.4", "geojson", "looseversion", "affine"]

# Installed with pip AND its dependencies, after conda has settled the GDAL
# stack. Not on conda-forge, and unlike pysheds its dependency list is not
# something to hand-maintain.
PIP_WITH_DEPS = ["topotoolbox>=0.0.10"]

# The module to import when checking that a package actually works. A package's
# top-level __init__ is often nearly empty -- pysheds/__init__.py is literally
# one line, `__version__ = "0.5"` -- so `import pysheds` succeeds no matter how
# many of its dependencies are absent. Probing that proves nothing. Probe what
# the code actually imports.
IMPORT_PROBE = {"pysheds": "pysheds.grid"}

# import name -> pip name, for the cases where they differ.
PIP_NAME = {
    "skimage": "scikit-image",
    "sklearn": "scikit-learn",
    "osgeo": "gdal",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "mpl_toolkits": "matplotlib",
    "dateutil": "python-dateutil",
}

# (import name, human label, what needs it)
CORE_MODULES = [
    ("numpy", "numpy", "everything"),
    ("pandas", "pandas", "tables"),
    ("shapely", "shapely", "geometry"),
    ("pyproj", "pyproj", "coordinate systems"),
    ("fiona", "fiona", "vector I/O"),
    ("rasterio", "rasterio", "raster I/O"),
    ("geopandas", "geopandas", "vector tables"),
    ("matplotlib", "matplotlib", "plots"),
    ("tqdm", "tqdm", "progress bars"),
]

LANDSCAPE_MODULES = [
    ("scipy", "scipy", "distance transforms"),
    ("skimage", "scikit-image", "skeletons, labelling"),
    ("numba", "numba", "flow routing (the fast bit)"),
    ("psutil", "psutil", "memory planning"),
    # pysheds.grid, not pysheds -- see IMPORT_PROBE above.
    ("pysheds.grid", "pysheds", "flow-routing cross-check"),
    ("topotoolbox", "topotoolbox", "headcuts / knickpoints"),
]


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

IS_WINDOWS = platform.system() == "Windows"


def say(msg=""):
    print(msg, flush=True)


def step(n, total, msg):
    say(f"\n[{n}/{total}] {msg}")
    say("-" * (len(msg) + 8))


_CMD_META = set(' <>|&^()"%!')


def _bat_cmdline(cmd) -> str:
    """Quote a command line for a Windows .bat/.cmd target.

    Windows runs a batch file through cmd.exe, which RE-PARSES the command
    line and treats < > | & ^ as shell operators. Python only quotes list
    arguments that contain spaces, so `numba>=0.57` reached cmd.exe bare:
    cmd read `>=0.57` as "redirect stdout to a file", conda received plain
    `numba` with no version constraint, and all of conda's output -- the
    progress a user watches to know the install is alive -- went into a file
    literally named `0.57` in the install folder. Found on Sam's machine.
    Double quotes are the one thing cmd.exe leaves alone.
    """
    out = []
    for a in (str(c) for c in cmd):
        out.append(f'"{a}"' if (set(a) & _CMD_META and '"' not in a) else a)
    return " ".join(out)


def run(cmd, check=True, capture=False, env=None):
    """Run a command, streaming output unless capture=True."""
    printable = " ".join(str(c) for c in cmd)
    if len(printable) > 200:
        printable = printable[:197] + "..."
    say(f"  $ {printable}")
    target = cmd
    if (os.name == "nt" and cmd
            and str(cmd[0]).lower().endswith((".bat", ".cmd"))):
        target = _bat_cmdline(cmd)
    if capture:
        p = subprocess.run(target, capture_output=True, text=True, env=env)
    else:
        p = subprocess.run(target, text=True, env=env)
    if check and p.returncode != 0:
        if capture:
            say((p.stdout or "") + (p.stderr or ""))
        raise SystemExit(
            f"\nFAILED: {printable}\n"
            f"Exit code {p.returncode}. Nothing above this line was rolled back; "
            f"re-running this script is safe."
        )
    return p


def project_root():
    """The directory containing pyproject.toml, searched from this file upward."""
    here = Path(__file__).resolve().parent
    for cand in [here, *here.parents]:
        if (cand / "pyproject.toml").exists():
            return cand
    raise SystemExit(
        "Could not find pyproject.toml next to this script.\n"
        "Keep install.py inside the unzipped geolens folder and run it from there."
    )


def _real_exe(found: str) -> str:
    """Prefer conda.exe over the condabin\\conda.bat wrapper on Windows.

    `shutil.which("conda")` usually finds the .bat, and a .bat goes through
    cmd.exe -- see _bat_cmdline for what that did to `numba>=0.57`. The real
    executable sits in Scripts\\ beside condabin\\ under the same install.
    """
    p = Path(found)
    if os.name == "nt" and p.suffix.lower() in (".bat", ".cmd"):
        for cand in (p.parent.parent / "Scripts" / (p.stem + ".exe"),
                     p.parent / (p.stem + ".exe")):
            if cand.exists():
                return str(cand)
    return found


def find_conda():
    """Locate a conda executable. CONDA_EXE first, then PATH, then usual places."""
    exe = os.environ.get("CONDA_EXE")
    if exe and Path(exe).exists():
        return _real_exe(exe)
    for name in ("conda", "mamba", "micromamba"):
        found = shutil.which(name)
        if found:
            return _real_exe(found)
    home = Path.home()
    candidates = []
    for base in (
        home / "anaconda3",
        home / "miniconda3",
        home / "miniforge3",
        home / "mambaforge",
        Path("C:/ProgramData/anaconda3"),
        Path("C:/ProgramData/miniconda3"),
        Path("/opt/conda"),
    ):
        candidates += [base / "Scripts" / "conda.exe", base / "bin" / "conda"]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def conda_env_python(conda, name):
    """
    Absolute path to the python inside a named conda env.

    Uses --json rather than parsing the text table. The text table is
    whitespace-separated, and "C:\\Users\\Sam Smith\\anaconda3\\envs\\geolens" is a
    perfectly ordinary Windows path with a space in it -- splitting on
    whitespace silently returns the wrong thing for anyone whose username has
    a space in it.
    """
    import json

    p = run([conda, "env", "list", "--json"], capture=True, check=False)
    envs = []
    try:
        envs = json.loads(p.stdout or "{}").get("envs", [])
    except Exception:
        # Very old conda without --json. Fall back to the text table, which is
        # wrong for paths with spaces but better than nothing.
        p = run([conda, "env", "list"], capture=True, check=False)
        for line in (p.stdout or "").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                envs.append(line.split()[-1])

    for path in envs:
        root = Path(path)
        if root.name != name:
            continue
        for rel in ("python.exe", "bin/python", "bin/python3"):
            cand = root / rel
            if cand.exists():
                return str(cand)
    return None


def _fill_missing_imports(py, module, rounds=8):
    """
    Import `module` with `py`; whenever it raises ModuleNotFoundError, pip
    install exactly the module it named (with --no-deps) and try again.

    This is how pysheds gets its dependencies without letting pip's resolver
    anywhere near the GDAL stack conda just installed. It installs what is
    genuinely missing and nothing else.
    """
    for _ in range(rounds):
        p = subprocess.run([py, "-c", f"import {module}"], capture_output=True, text=True)
        if p.returncode == 0:
            return True, ""
        m = re.search(r"No module named ['\"]([A-Za-z0-9_.]+)['\"]", p.stderr or "")
        if not m:
            return False, (p.stderr or "").strip()
        missing = m.group(1).split(".")[0]
        pip_name = PIP_NAME.get(missing, missing)
        say(f"  {module} needs '{missing}' -> installing {pip_name}")
        r = subprocess.run(
            [py, "-m", "pip", "install", "--no-deps", "-q", pip_name],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            return False, f"could not install {pip_name}: {(r.stderr or '').strip()[:400]}"
    return False, f"gave up after {rounds} rounds of missing imports"


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------

def _probe(name):
    """Import a module in-process and report (status, detail)."""
    try:
        mod = __import__(name)
    except ImportError as e:
        return "MISSING", str(e).split("\n")[0]
    except Exception as e:  # numba raises non-ImportError on ABI mismatch
        return "BROKEN", f"{type(e).__name__}: {e}".split("\n")[0]
    ver = getattr(mod, "__version__", "") or ""
    where = getattr(mod, "__file__", "") or ""
    return "OK", f"{ver}  {where}"


def _install_flavour(path):
    """Guess whether a module came from conda or pip, from its path."""
    p = (path or "").replace("\\", "/").lower()
    if "/site-packages/" in p and ("/envs/" in p or "conda" in p or "/opt/conda/" in p):
        return "conda-env"
    if "/site-packages/" in p:
        return "site-packages"
    return "?"


def doctor():
    """Report on the environment that is running this function. Installs nothing."""
    problems = []
    notes = []

    say("=" * 72)
    say("geolens doctor")
    say("=" * 72)
    say(f"  python      {sys.version.split()[0]}  ({platform.system()} {platform.machine()})")
    say(f"  executable  {sys.executable}")
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    in_conda = "conda" in sys.prefix.lower() or "envs" in sys.prefix.lower() \
        or bool(os.environ.get("CONDA_PREFIX"))
    kind = "conda env" if in_conda else ("venv" if in_venv else "base/system Python")
    say(f"  environment {kind}  ->  {sys.prefix}")

    if sys.version_info < (3, 9):
        problems.append(
            f"Python {sys.version.split()[0]} is too old. geolens needs 3.9 or newer; "
            f"3.11 is what it is tested on."
        )
    elif sys.version_info < (3, 10):
        notes.append(
            "Python 3.9 works but 3.11 is what gets tested. If you have the choice, "
            "use 3.11."
        )

    if not in_venv and not in_conda:
        notes.append(
            "This is a base/system Python. Installing scientific packages here is "
            "what eventually breaks other tools. Use a dedicated environment "
            "(python install.py, no flags, does that for you)."
        )

    # ---- core stack --------------------------------------------------
    say("\nCore stack")
    flavours = {}
    for name, label, why in CORE_MODULES:
        status, detail = _probe(name)
        if status == "OK":
            ver, _, where = detail.partition("  ")
            flavours[name] = _install_flavour(where)
            say(f"  [ok]      {label:<14} {ver}")
        else:
            say(f"  [{status.lower()}] {label:<14} {detail}   (needed for {why})")
            problems.append(f"{label} is {status.lower()}: {detail}")

    # ---- the mixed-install check, which is the one that matters on Windows
    gdal_stack = [n for n in ("rasterio", "fiona", "geopandas", "pyproj") if n in flavours]
    distinct = {flavours[n] for n in gdal_stack}
    if len(distinct) > 1:
        detail = ", ".join(f"{n}={flavours[n]}" for n in gdal_stack)
        problems.append(
            "MIXED GDAL INSTALL. The GDAL stack is coming from more than one place "
            f"({detail}). This is the cause of 'DLL load failed' and 'proj.db not "
            "found'. Fix: build a clean environment instead of repairing this one "
            "-- python install.py --name geolens2"
        )

    # ---- PROJ data, which is where CRS operations die silently
    try:
        import pyproj
        data_dir = pyproj.datadir.get_data_dir()
        has_db = (Path(data_dir) / "proj.db").exists()
        say(f"\n  PROJ data   {data_dir}  {'(proj.db found)' if has_db else '(NO proj.db)'}")
        if not has_db:
            problems.append(
                "proj.db is missing from the PROJ data directory. Every reprojection "
                "will fail. Usually means a mixed conda/pip install, or a stale "
                "PROJ_LIB environment variable pointing at an old install."
            )
        if os.environ.get("PROJ_LIB") and os.environ["PROJ_LIB"] != data_dir:
            notes.append(
                f"PROJ_LIB is set to {os.environ['PROJ_LIB']} but pyproj wants "
                f"{data_dir}. A stale PROJ_LIB (often left by an ArcGIS install) "
                f"overrides pyproj and breaks CRS lookups. Unset it."
            )
    except Exception:
        pass

    # ---- numpy / numba ABI, the other silent killer
    try:
        import numpy
        say(f"  numpy       {numpy.__version__}")
        try:
            import numba  # noqa: F401
            say(f"  numba       {numba.__version__}  (numpy ABI ok)")
        except Exception as e:
            msg = f"{type(e).__name__}: {e}".split("\n")[0]
            if "numpy" in msg.lower():
                problems.append(
                    f"numba and numpy disagree: {msg}. numpy was upgraded after "
                    f"numba was installed. Fix: pip install -U numba, or pin numpy "
                    f"to the version numba names."
                )
            else:
                problems.append(f"numba is broken: {msg}")
    except ImportError:
        pass

    # ---- landscape extras -------------------------------------------
    say("\nLandscape stack (flow routing, valleys, gullies)")
    for name, label, why in LANDSCAPE_MODULES:
        status, detail = _probe(name)
        if status == "OK":
            ver = detail.split("  ")[0]
            say(f"  [ok]      {label:<14} {ver}")
        else:
            say(f"  [{status.lower()}] {label:<14} {detail}   ({why})")
            problems.append(f"{label} is {status.lower()} -- landscape workflows will not run")

    # ---- geolens itself ---------------------------------------------
    say("\ngeolens")
    status, detail = _probe("geolens")
    if status == "OK":
        import geolens
        say(f"  [ok]      geolens        {geolens.__version__}  {Path(geolens.__file__).parent}")
        try:
            from geolens import api  # noqa: F401
            fns = [f for f in dir(api) if not f.startswith("_")]
            say(f"  [ok]      geolens.api    {len(fns)} functions")
        except Exception as e:
            problems.append(f"geolens imports but geolens.api does not: {type(e).__name__}: {e}")
    else:
        say(f"  [{status.lower()}] geolens        {detail}")
        if len(problems) <= 1:
            # Everything else is here, so this is almost certainly a Jupyter
            # kernel pointing at a different environment than the one geolens
            # went into -- not a failed install.
            problems.append(
                "geolens is not in THIS environment, but everything it depends on "
                "is. That pattern means you are running a Jupyter kernel from a "
                "different environment than the one geolens was installed into. "
                'Fix: Kernel -> Change Kernel -> "Python (geolens)". If that is '
                "not offered, run:  python install.py --here"
            )
        else:
            problems.append(
                "geolens itself is not installed in this environment. "
                "Fix: python install.py"
            )

    # ---- can we write output ----------------------------------------
    out = os.environ.get("GEOLENS_OUT") or str(Path.home() / "geolens_out")
    try:
        Path(out).mkdir(parents=True, exist_ok=True)
        probe = Path(out) / ".geolens_write_test"
        probe.write_text("ok")
        probe.unlink()
        say(f"\n  output dir  {out}  (writable)")
    except Exception as e:
        say(f"\n  output dir  {out}  NOT WRITABLE: {e}")
        problems.append(f"Cannot write to {out}. Set GEOLENS_OUT to a folder you own.")

    # ---- verdict -----------------------------------------------------
    # An empty environment produces one problem per package, which is fifteen
    # lines of noise around a single fact. Collapse it.
    missing_count = sum(1 for p in problems if "missing" in p)
    total_modules = len(CORE_MODULES) + len(LANDSCAPE_MODULES)
    if missing_count >= total_modules - 2 and status != "OK":
        say("\n" + "=" * 72)
        say("VERDICT: this environment is empty -- nothing is installed in it yet.")
        say("")
        say("  Fix:  python install.py")
        say("")
        say("  (That builds a separate environment and installs everything into it.")
        say("   It does not touch the Python you just ran this with.)")
        say("=" * 72)
        return 1

    say("\n" + "=" * 72)
    if not problems:
        say("VERDICT: ready. Everything geolens needs is here and importable.")
        if notes:
            say("\nWorth knowing:")
            for n in notes:
                say(f"  - {n}")
        say("\nNext:  jupyter lab notebooks/geolens_start.ipynb")
        say("=" * 72)
        return 0
    say(f"VERDICT: {len(problems)} problem(s) found.\n")
    for i, p in enumerate(problems, 1):
        say(f"  {i}. {p}\n")
    if notes:
        say("Also worth knowing:")
        for n in notes:
            say(f"  - {n}")
    say("=" * 72)
    return 1




def register_kernel(py, label):
    """
    Make this environment selectable as a Jupyter kernel.

    Without this you get the most confusing failure of the lot: the install
    succeeds, and then the notebook reports geolens missing -- because Jupyter
    launched a kernel from a different Python than the one we installed into.
    Registering it means "Python (geolens)" shows up in the kernel picker.
    """
    r = subprocess.run(
        [py, "-m", "ipykernel", "install", "--user",
         "--name", "geolens", "--display-name", f"Python ({label})"],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        say(f'  registered Jupyter kernel "Python ({label})"')
    else:
        say("  could not register a Jupyter kernel (not fatal). If the notebook")
        say("  reports geolens missing, pick the right kernel with Kernel ->")
        say("  Change Kernel, or start Jupyter from this environment directly.")


# --------------------------------------------------------------------------
# smoke test
# --------------------------------------------------------------------------

def smoke():
    """
    Build a small synthetic valley and run the real workflow over it.

    This is the difference between "everything imports" and "everything works".
    Imports passing while the workflow fails is a real state to be in: a numba
    that imports but cannot compile, a rasterio that opens files but has no
    write driver, a GDAL that reads GeoTIFF but not shapefile. Those all look
    healthy to the doctor and die on the first real run.

    It also pays for itself by triggering numba's JIT once, so the first
    genuine run does not spend its first thirty seconds compiling.
    """
    import tempfile
    import time

    say("=" * 72)
    say("geolens smoke test")
    say("=" * 72)

    t0 = time.monotonic()
    failures = []
    tmp = Path(tempfile.mkdtemp(prefix="geolens_smoke_"))
    os.environ["GEOLENS_OUT"] = str(tmp)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"

    try:
        # ---- 1. build a fixture ---------------------------------------
        say("\n  [1/6] building a synthetic valley ...")
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin
        import geopandas as gpd
        from shapely.geometry import LineString

        W, H, CELL = 300, 200, 1.0
        X0, Y0, EPSG = 500_000.0, 3_900_000.0, 32612
        HALF_W, DEPTH = 8.0, 2.5      # known truth: 16 m wide, 2.5 m deep

        xs = np.arange(W) * CELL
        cy = H / 2.0 + 25.0 * np.sin(2 * np.pi * xs / 200.0)
        XX, YY = np.meshgrid(xs, np.arange(H) * CELL)
        dist = np.abs(YY - np.broadcast_to(cy, (H, W)))
        z = 1500.0 - 0.004 * XX + 0.06 * dist
        inner = dist <= HALF_W
        z = np.where(inner, z - DEPTH * (1 - (dist / HALF_W) ** 2), z)
        z += np.random.default_rng(0).normal(0, 0.03, z.shape)
        z = z[::-1, :].astype("float32")

        dem = tmp / "smoke_dem.tif"
        with rasterio.open(
            dem, "w", driver="GTiff", height=H, width=W, count=1, dtype="float32",
            crs=f"EPSG:{EPSG}", transform=from_origin(X0, Y0 + H * CELL, CELL, CELL),
            nodata=-9999.0, compress="deflate",
        ) as dst:
            dst.write(z, 1)

        line_x = np.arange(0, W, 5.0)
        line_y = H / 2.0 + 25.0 * np.sin(2 * np.pi * line_x / 200.0)
        gpd.GeoDataFrame(
            {"reach": ["R1"]},
            geometry=[LineString([(X0 + float(a), Y0 + float(b))
                                  for a, b in zip(line_x, line_y)])],
            crs=f"EPSG:{EPSG}",
        ).to_file(tmp / "smoke_line.shp")
        say(f"        {W}x{H} DEM + centerline written  (GeoTIFF write ok, shapefile write ok)")

        # ---- 2. read it back ------------------------------------------
        say("  [2/6] reading the raster ...")
        from geolens import api
        info = api.describe_raster(str(dem))
        assert info["crs"]["epsg"] == EPSG, f"CRS came back as {info['crs']['epsg']}"
        say(f"        EPSG {info['crs']['epsg']}, {info['grid']['width']}x{info['grid']['height']} cells")

        # ---- 3. cross-sections ----------------------------------------
        say("  [3/6] cutting cross-sections ...")
        xsec = api.cross_sections(str(dem), str(tmp / "smoke_line.shp"),
                                  spacing=10, width=120)
        n = len(xsec["sections"])
        assert n > 5, f"only {n} sections cut"
        say(f"        {n} sections")

        # ---- 4. channel / floodplain delineation ----------------------
        say("  [4/6] delineating banks ...")
        from geolens.morphology import delineate
        from geolens.sections import extract_cross_sections
        secs = extract_cross_sections(str(dem), str(tmp / "smoke_line.shp"),
                                      spacing=5, width=160)
        d = delineate(secs)
        widths = [r["bankfull"]["width"] for r in d["sections"]
                  if r.get("status") == "ok" and r.get("bankfull")]
        if not widths:
            failures.append("delineation produced no usable bankfull widths")
        else:
            import statistics as st
            med = st.median(widths)
            err = abs(med - 2 * HALF_W) / (2 * HALF_W) * 100
            say(f"        bankfull width {med:.1f} m vs known truth {2*HALF_W:.0f} m  ({err:.0f}% off)")
            if err > 35:
                failures.append(
                    f"bankfull width came out {med:.1f} m against a known 16 m. "
                    f"Imports are fine, so this is a numerical problem, not an install one."
                )

        # ---- 5. numba flow routing ------------------------------------
        say("  [5/6] flow routing (this compiles numba the first time) ...")
        try:
            from geolens.landscape.flow import flow_routing
            fr = flow_routing(str(dem), out_prefix="smoke")
            f = fr.as_dict() if hasattr(fr, "as_dict") else {}
            say(f"        ok  {f.get('cells', '?')} cells, "
                f"{f.get('depressions_filled', '?')} depressions filled, "
                f"max contributing area {f.get('max_accumulation_km2', 0):.3f} km2, "
                f"{f.get('seconds', 0):.1f}s")
        except ImportError as e:
            say(f"        skipped -- landscape stack not installed ({e})")
        except Exception as e:
            failures.append(f"flow routing failed: {type(e).__name__}: {e}")

        # ---- 6. the pysheds path ---------------------------------------
        # This step exists because of a real bug. pysheds/__init__.py is one
        # line and imports nothing, so `import pysheds` succeeds with none of
        # its dependencies present. The installer's dependency check probed
        # that, saw success, and shipped an environment where the first real
        # terrain call died on a missing geojson. An install check that only
        # tests imports would still pass today -- so run the thing.
        say("  [6/6] terrain + HAND (the pysheds path) ...")
        try:
            from geolens.landscape.terrain import available_backends, build_terrain

            backends = available_backends()
            if not backends.get("pysheds"):
                say(f"        pysheds backend unavailable {backends} -- skipping")
            else:
                tr = build_terrain(str(dem), stream_threshold_km2=0.01,
                                   backend="pysheds", out_prefix="smoke_t")
                d = tr.as_dict() if hasattr(tr, "as_dict") else {}
                say(f"        ok  backend={d.get('backend', '?')}, "
                    f"HAND and slope written")
        except ImportError as e:
            say(f"        skipped -- landscape stack not installed ({e})")
        except Exception as e:
            failures.append(
                f"terrain/HAND failed: {type(e).__name__}: {e}. This is the step "
                f"that catches a pysheds installed without its dependencies."
            )

    except Exception as e:
        import traceback
        failures.append(f"{type(e).__name__}: {e}")
        say("\n" + traceback.format_exc())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    say("\n" + "=" * 72)
    if failures:
        say(f"SMOKE TEST FAILED ({len(failures)} problem(s)):")
        for f in failures:
            say(f"  - {f}")
        say("=" * 72)
        return 1
    say(f"SMOKE TEST PASSED in {time.monotonic() - t0:.0f}s -- "
        f"the install works end to end, not just on imports.")
    say("=" * 72)
    return 0


# --------------------------------------------------------------------------
# install routes
# --------------------------------------------------------------------------

def remember_python(py):
    """Write the environment's interpreter to ~/.geolens/python.txt.

    The ArcGIS Pro toolbox and the QGIS plugin read it to find the
    environment, wherever conda or the venv route put it. Best effort: an
    install never fails over this.
    """
    try:
        target = Path.home() / ".geolens" / "python.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(Path(py).resolve()) + "\n", encoding="utf-8")
        say(f"  ArcGIS Pro and QGIS tools will use: {Path(py).resolve()}")
        try:
            say(f"  ArcGIS Pro toolbox: "
                f"{project_root() / 'arcgis_toolbox' / 'geolens.pyt'}"
                f"  (Catalog > Toolboxes > Add Toolbox)")
        except SystemExit:
            pass
    except Exception as exc:                                  # noqa: BLE001
        say(f"  (could not record the environment for the QGIS plugin: {exc})")


def install_conda(conda, name, py_version, root, extras=True, smoke_test=True,
                  reuse_env=False):
    """
    Build the environment in ONE conda-forge transaction.

    The earlier version of this function did something that looked reasonable
    and was wrong: if the env already existed it reused it and ran `conda
    install --override-channels -c conda-forge` on top. That is the mixed-
    channel situation this whole script exists to avoid, just arrived at from
    the other direction. An Anaconda-made env already contains packages from
    `defaults` -- Python 3.13, and `jpeg 9f`. Modern conda-forge libgdal wants
    `libjpeg-turbo`, which declares `jpeg <0.0.0a` to mean "this package no
    longer exists". Those two cannot coexist, so the solve fails with a
    hundred lines about fiona 1.6.3 and Python 2.7 that have nothing to do with
    anything. Conda also auto-pins the env's existing Python minor version, so
    the error ends with "Currently pinned specs: python=3.13" even though we
    asked for 3.11.

    One transaction, one channel, into an env that did not exist a moment ago.
    Nothing to conflict with.
    """
    total = 4
    step(1, total, f"Creating conda environment '{name}' (Python {py_version})")

    existing = conda_env_python(conda, name)
    if existing and not reuse_env:
        say(f"  An environment named '{name}' already exists.")
        say("  Rebuilding it from scratch rather than installing on top of it:")
        say("  installing conda-forge packages into an env that already holds")
        say("  packages from another channel is what produces unsolvable")
        say("  dependency conflicts. Pass --reuse-env to override this.")
        r = run([conda, "env", "remove", "-y", "-n", name], check=False)
        if r.returncode != 0:
            say(f"  Could not remove it. Using the name '{name}_new' instead.")
            name = f"{name}_new"
    elif existing:
        say(f"  Reusing '{name}' as asked (--reuse-env). If the solve fails with")
        say("  'Currently pinned specs', that is why -- re-run without the flag.")

    say("\n  Everything in one transaction, from conda-forge only. This")
    say("  downloads a few hundred MB the first time; 5-15 minutes is normal.")
    pkgs = CONDA_CORE + (CONDA_LANDSCAPE if extras else []) + CONDA_NOTEBOOK
    create = [conda, "create", "-y", "-n", name, "-c", "conda-forge",
              "--override-channels", f"python={py_version}", *pkgs]

    # libmamba turns a 20-minute solve into a 20-second one. Current conda uses
    # it by default; older Anaconda installs have never heard of the flag.
    r = run(create + ["--solver=libmamba"], check=False)
    if r.returncode != 0:
        say("\n  (retrying without the libmamba flag -- this solve is slower)")
        r = run(create, check=False)
    if r.returncode != 0:
        raise SystemExit(
            "\n" + "=" * 72 + "\n"
            "conda could not solve this environment.\n"
            "=" * 72 + "\n\n"
            "If the error above mentions 'Currently pinned specs' or names\n"
            "packages from before 2020 (fiona 1.6, gdal 1.11, Python 2.7), it is\n"
            "a channel conflict, not a real dependency problem.\n\n"
            "The pip route does not go through conda at all and is what this\n"
            "package is tested against. Use it:\n\n"
            "    python install.py --venv\n"
        )

    py = conda_env_python(conda, name)
    if not py:
        raise SystemExit(
            f"Created env '{name}' but could not find its python.\n"
            f"Run:  conda activate {name}   then   python install.py --here"
        )
    say(f"\n  environment python: {py}")

    if extras:
        step(2, total, "Installing pysheds")
        say("  pip --no-deps, so pip cannot revisit the GDAL stack conda just")
        say("  installed. Anything genuinely missing gets added one at a time.")
        for pkg in PIP_AFTER_CONDA:
            run([py, "-m", "pip", "install", "--no-deps", pkg], check=False)
        for pkg in PIP_WITH_DEPS:
            run([py, "-m", "pip", "install", pkg], check=False)
        ok, err = _fill_missing_imports(py, IMPORT_PROBE["pysheds"])
        if not ok:
            say(f"  pysheds is not importable: {err}")
            say("  Not fatal -- pysheds is only a cross-check against our own flow")
            say("  routing. Everything else still runs.")
    else:
        step(2, total, "Skipping optional extras (--no-extras)")

    step(3, total, "Installing geolens")
    run([py, "-m", "pip", "install", "--no-deps", "-e", str(root)])
    register_kernel(py, name)

    step(4, total, "Checking the result")
    p = subprocess.run([py, str(Path(__file__).resolve()), "--check"], text=True)
    if p.returncode == 0 and smoke_test:
        say("")
        p = subprocess.run([py, str(Path(__file__).resolve()), "--smoke"], text=True)
    if p.returncode == 0:
        remember_python(py)

    say("\n" + "=" * 72)
    say("Done. To use it:")
    say("=" * 72)
    say(f"  conda activate {name}")
    say("  jupyter lab notebooks/geolens_start.ipynb")
    say("")
    say("  ...or from any terminal without activating:")
    say(f'  "{py}" -m jupyter lab')
    say("=" * 72)
    return p.returncode


def _pick_venv_base():
    """
    Which interpreter to build the venv from.

    Normally the one running this script. The exception is a very new Python:
    the compiled packages here (numba first, usually) publish wheels for a new
    minor version months after it ships, and Anaconda tracks new Pythons
    quickly. If we are on something newer than what this package is tested
    against and an older one is installed, use that instead -- it costs
    nothing and avoids a wheels-only install failing on the last package.
    """
    if sys.version_info[:2] <= (3, 12):
        return sys.executable, None
    for want in ("3.12", "3.11"):
        if IS_WINDOWS:
            cmd = ["py", f"-{want}", "-c", "import sys;print(sys.executable)"]
        else:
            exe = shutil.which(f"python{want}")
            if not exe:
                continue
            cmd = [exe, "-c", "import sys;print(sys.executable)"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        except Exception:
            continue
        if r.returncode == 0 and r.stdout.strip():
            found = r.stdout.strip()
            cur = ".".join(str(v) for v in sys.version_info[:2])
            return found, (
                f"this script is running on Python {cur}; using {want} for the "
                f"environment instead, because not every compiled package "
                f"publishes wheels for {cur} yet"
            )
    return sys.executable, None


def install_venv(root, extras=True, smoke_test=True):
    total = 5
    venv_dir = root / ".venv"
    base, why = _pick_venv_base()
    step(1, total, f"Creating virtual environment at {venv_dir}")
    if why:
        say(f"  Note: {why}.")
    if venv_dir.exists():
        say("  Already exists -> reusing it.")
    else:
        run([base, "-m", "venv", str(venv_dir)])
    py = str(venv_dir / ("Scripts/python.exe" if IS_WINDOWS else "bin/python"))
    if not Path(py).exists():
        raise SystemExit(f"venv created but {py} is missing.")

    step(2, total, "Updating pip")
    run([py, "-m", "pip", "install", "-q", "-U", "pip", "setuptools", "wheel"])

    step(3, total, "Installing the GDAL stack (wheels only)")
    say("  --only-binary means pip refuses to compile these from source. If a")
    say("  wheel is genuinely unavailable for your platform it is better to fail")
    say("  here with a clear message than to grind for half an hour first.")
    pkgs = PIP_CORE + (PIP_LANDSCAPE if extras else [])
    r = run([py, "-m", "pip", "install", "--only-binary", ":all:",
             "--retries", "5", "--timeout", "60", *pkgs], check=False)
    if r.returncode != 0:
        pv = subprocess.run(
            [py, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
            capture_output=True, text=True).stdout.strip()
        raise SystemExit(
            "\n" + "=" * 72 + "\n"
            "pip could not find a wheel for one of these packages.\n"
            + "=" * 72 + "\n\n"
            f"This venv is Python {pv}. If that is very new, some of the compiled\n"
            "packages (numba is usually first) may not have published wheels for\n"
            "it yet. The error above names which one.\n\n"
            "Two ways out:\n\n"
            "  1. Build the venv from an older Python:\n"
            "       py -3.11 install.py\n\n"
            "  2. Use conda-forge, which builds for more versions:\n"
            "       python install.py --conda\n"
        )
    run([py, "-m", "pip", "install", "-q", "--retries", "5", *PIP_NOTEBOOK], check=False)

    if extras:
        step(4, total, "Installing pysheds and geolens")
        run([py, "-m", "pip", "install", "--no-deps", "--retries", "5", *PIP_AFTER_CONDA],
            check=False)
        ok, err = _fill_missing_imports(py, IMPORT_PROBE["pysheds"])
        if not ok:
            say(f"  pysheds is not importable: {err}")
            say("  Not fatal -- it is only a cross-check against our own flow routing.")
    else:
        step(4, total, "Installing geolens")

    # --no-deps because step 3 already put every dependency in place, and we do
    # not want the resolver revisiting the GDAL stack now that it is working.
    run([py, "-m", "pip", "install", "--no-deps", "-e", str(root)])

    register_kernel(py, "geolens")

    step(5, total, "Checking the result")
    p = subprocess.run([py, str(Path(__file__).resolve()), "--check"], text=True)
    if p.returncode == 0 and smoke_test:
        say("")
        p = subprocess.run([py, str(Path(__file__).resolve()), "--smoke"], text=True)
    if p.returncode == 0:
        remember_python(py)

    say("\n" + "=" * 72)
    say("Done. To use it:")
    say("=" * 72)
    if IS_WINDOWS:
        say(f"  {venv_dir}\\Scripts\\activate")
    else:
        say(f"  source {venv_dir}/bin/activate")
    say("  jupyter lab notebooks/geolens_start.ipynb")
    say("")
    say("  ...or without activating:")
    say(f'  "{py}" -m jupyter lab')
    say("=" * 72)
    return p.returncode


def _existing_editable_root():
    """Where does this Python already import geolens from, if anywhere?

    An editable install is a pointer, and ``--here`` silently repoints it. That
    is a real trap and it cost a working session: a second copy of the source
    was unzipped for a clean-install check, ``--here`` was run inside it, and
    from that moment the original working tree was no longer what `import
    geolens` loaded. Tests kept passing -- against the frozen copy -- while
    edits to the real tree did nothing at all. Nothing in the output said so.
    """
    try:
        import importlib.util

        spec = importlib.util.find_spec("geolens")
        if spec is None or not spec.origin:
            return None
        # .../src/geolens/__init__.py -> the project root
        return Path(spec.origin).resolve().parent.parent.parent
    except Exception:
        return None


def install_here(root, extras=True, smoke_test=True):
    step(1, 2, f"Installing into {sys.executable}")
    say("  No new environment. If this is a shared or base environment, this is")
    say("  the route most likely to disturb something else you have installed.")

    prior = _existing_editable_root()
    if prior is not None and prior != Path(root).resolve():
        say("")
        say("  NOTE: this Python currently imports geolens from")
        say(f"    {prior}")
        say("  and after this it will import it from")
        say(f"    {Path(root).resolve()}")
        say("  Editable installs are pointers. If you are editing the first")
        say("  location, your edits will stop taking effect -- silently, with")
        say("  tests still passing against the copy you are not editing.")
        say("")

    target = f"{root}[all]" if extras else str(root)
    run([sys.executable, "-m", "pip", "install", "--retries", "5", "--timeout", "60",
         "-e", target])
    step(2, 2, "Checking the result")
    rc = doctor()
    if rc == 0 and smoke_test:
        say("")
        rc = smoke()
    if rc == 0:
        remember_python(sys.executable)
    return rc


# --------------------------------------------------------------------------

def _working_install(conda, name):
    """An environment that already has geolens working, if there is one.

    Returns ``(python, geolens_root)`` or ``(None, None)``.

    Checked before anything is built, because the habit is to run the
    installer for every release and the installer's job on the second release
    is not the same as on the first. With --conda it was actively destructive:
    an existing env was DELETED and rebuilt from scratch, hundreds of
    megabytes redownloaded, to deliver a few hundred kilobytes of new Python.
    """
    candidates = []
    if conda and name:
        py = conda_env_python(conda, name)
        if py:
            candidates.append(py)
    candidates.append(sys.executable)
    # The venv route puts its interpreter in <root>/.venv, so a previous
    # release's folder is where to look. Without this, someone who installed
    # into a venv and then ran the installer from the NEXT release folder
    # would not be recognised as having an install at all, and would get a
    # second venv beside the first -- which is how you end up with four of
    # them and no idea which one Jupyter is using.
    here = project_root()
    for sibling in sorted(here.parent.glob("geolens*"), reverse=True):
        for rel in (".venv/bin/python", ".venv/Scripts/python.exe"):
            cand = sibling / rel
            if cand.exists():
                candidates.append(str(cand))

    probe = ("import geolens, json, sys;"
             "print('GEOLENS_AT' + json.dumps("
             "[geolens.__version__, geolens.__file__, sys.executable]))")
    for py in candidates:
        try:
            r = subprocess.run([py, "-c", probe], capture_output=True,
                               text=True, timeout=120)
        except Exception:
            continue
        for line in (r.stdout or "").splitlines():
            if line.startswith("GEOLENS_AT"):
                try:
                    ver, origin, exe = json.loads(line[len("GEOLENS_AT"):])
                except Exception:
                    continue
                # .../<root>/src/geolens/__init__.py
                return exe, Path(origin).resolve().parent.parent.parent
    return None, None


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Install geolens, or diagnose an install that is not working.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--check", action="store_true",
                    help="diagnose the current environment; install nothing")
    ap.add_argument("--smoke", action="store_true",
                    help="run the workflow on a synthetic DEM; install nothing")
    ap.add_argument("--no-smoke", action="store_true",
                    help="skip the smoke test at the end of an install")
    ap.add_argument("--here", action="store_true",
                    help="install into the Python running this script")
    ap.add_argument("--venv", action="store_true",
                    help="build a throwaway venv + pip wheels instead of the "
                         "named conda environment — for testing a release "
                         "without touching the env you work in")
    ap.add_argument("--conda", action="store_true",
                    help="build a conda-forge environment (the default "
                         "wherever conda is found; kept for explicitness)")
    ap.add_argument("--conda-exe", metavar="PATH", default=None,
                    dest="conda_exe",
                    help="path to conda.exe, if it is not found automatically")
    ap.add_argument("--reuse-env", action="store_true",
                    help="with --conda: install into an existing env instead of "
                         "rebuilding it (usually a bad idea -- see install_conda)")
    ap.add_argument("--name", default=ENV_NAME_DEFAULT,
                    help=f"conda environment name (default: {ENV_NAME_DEFAULT})")
    ap.add_argument("--python", default=PY_VERSION_DEFAULT,
                    help=f"Python version for the new env (default: {PY_VERSION_DEFAULT})")
    ap.add_argument("--no-extras", action="store_true",
                    help="core only: skip the landscape/flow-routing stack")
    ap.add_argument("--force-rebuild", action="store_true",
                    help="build the environment again even though a working "
                         "geolens install was found (the default is to update "
                         "it in place, which is far faster and cannot break a "
                         "working environment)")
    ap.add_argument("--no-update", action="store_true",
                    help="alias for --force-rebuild")
    args = ap.parse_args(argv)

    if args.check:
        return doctor()
    if args.smoke:
        return smoke()

    root = project_root()
    extras = not args.no_extras
    smoke_test = not args.no_smoke
    say("=" * 72)
    say("geolens installer")
    say("=" * 72)
    say(f"  package    {root}")
    say(f"  platform   {platform.system()} {platform.machine()}")
    say(f"  python     {sys.version.split()[0]}  {sys.executable}")

    # -- is this an install, or an update? ---------------------------------
    #
    # Running the installer for a new release is the habit, and it was the
    # wrong thing to do: with --conda it deleted a working environment and
    # rebuilt it, and with a venv it built another one beside the last. The
    # environment is the expensive, fragile part; the code is not. So when a
    # working install is already here, update it and leave the environment
    # completely alone.
    if not (args.force_rebuild or args.no_update or args.check or args.smoke):
        # Look for the conda env whatever route the command line asked for.
        # It is the PERMANENT one, and an update has to find it even when
        # nothing on the command line mentions conda — otherwise running the
        # installer for a new release quietly builds a venv beside a perfectly
        # good environment, which is the thing this short-circuit exists to
        # stop.
        _conda = args.conda_exe or (None if args.venv else find_conda())
        existing_py, existing_root = _working_install(_conda, args.name)
        if existing_root is not None:
            say("")
            say("  A working geolens install is already here:")
            say(f"    {existing_root}")
            say(f"    using {existing_py}")
            say("")
            say("  Updating it in place. The environment is untouched -- no")
            say("  conda solve, no downloads, nothing to break. Pass")
            say("  --force-rebuild if you actually want a new environment.")
            say("")
            sys.path.insert(0, str(root))
            try:
                import update as _update
            except Exception as exc:                          # noqa: BLE001
                say(f"  Could not load update.py ({exc}); building instead.")
            else:
                rc = _update.update(root, existing_root)
                if rc == 0:
                    remember_python(existing_py)
                    say("")
                    say("Done. Start the notebook with:")
                    say(f"  jupyter lab {root / 'notebooks' / 'geolens_start.ipynb'}")
                return rc

    if args.here:
        return install_here(root, extras, smoke_test)

    # -- which environment ---------------------------------------------------
    #
    # conda is the default wherever conda exists, and the reason is that the
    # environment is meant to be PERMANENT: one named env, and later releases
    # update the code inside it rather than building a new environment each
    # time. The venv default produced four .venv folders beside four unzipped
    # releases with no way to tell which one Jupyter was using.
    #
    # --venv is still here and still works. It builds a throwaway environment
    # and is the right thing for testing a release against a clean stack
    # without touching the env you actually work in.
    conda = None
    if not args.venv:
        conda = args.conda_exe or find_conda()
        if (args.conda or args.conda_exe) and not conda:
            raise SystemExit(
                "--conda was asked for but no conda executable was found.\n"
                "Pass its path with --conda-exe, or use --venv instead."
            )
    if conda:
        say(f"  route      conda -> {conda}")
        say(f"             permanent environment '{args.name}'; later releases"
            f" update the code inside it")
        return install_conda(conda, args.name, args.python, root, extras,
                             smoke_test, args.reuse_env)

    # pip is the default, including on Windows. rasterio, fiona, pyproj and
    # shapely all ship Windows wheels with their own GDAL/PROJ/GEOS bundled and
    # name-mangled, so the DLL collision that used to make conda mandatory here
    # does not happen any more. conda is still available via --conda, but it
    # has to solve against whatever channel mix your base install came with,
    # and that is a failure mode pip simply does not have.
    if args.venv:
        say("  route      venv + pip wheels (asked for with --venv)")
        say("             throwaway environment; your conda env is untouched")
    else:
        say("  route      venv + pip wheels")
        say("             no conda found — install Miniforge and re-run for a")
        say("             permanent environment: https://conda-forge.org/download/")
    return install_venv(root, extras, smoke_test)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("\nInterrupted. Re-running this script is safe.")
        sys.exit(130)
