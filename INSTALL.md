# Installing geolens

## The short version

Unzip this folder somewhere, then:

**Windows** — double-click `install_windows.bat`

**macOS / Linux** — `bash install.sh`

That's it. It takes 5–15 minutes the first time, mostly downloading. When it
finishes it checks itself and tells you the command to start the notebook.

If it already went wrong, skip to [When it doesn't work](#when-it-doesnt-work).

---

## Updating: the installer now knows it is an update

**Just run the installer again.** It looks for a working geolens install
first, and when it finds one it updates the code in place and leaves the
environment completely alone — no conda solve, no downloads, nothing that can
break an environment that currently works.

That was not true before v0.13. Running the installer for a new release was
the habit and it was the wrong thing to do: with `--conda` it **deleted** the
existing environment and rebuilt it from scratch, hundreds of megabytes
redownloaded to deliver a few hundred kilobytes of new Python; with a venv it
built another one beside the last, which is how you end up with four and no
idea which one Jupyter is using.

Pass `--force-rebuild` when you actually do want a new environment.

The environment is the slow, fragile part — the GDAL / rasterio / numba stack
— and new code does not need any of it rebuilt.

The shape that avoids all of it is to install **once** into a folder that never
moves, and treat later releases as update sources:

```
Tools\geolens\current       <- installed once, never moves
Tools\geolens\geolens_15    <- unzip here, apply it, delete
```

Unzip the new release anywhere, then from inside it run the installer as
usual — or `update.py` directly, which is what the installer calls:

```
python install.py     # detects the existing install, updates in place
python update.py      # the same thing, without the installer's checks
```

It finds whichever install your Python actually imports `geolens` from — an
editable install is a pointer, and following it is more reliable than
remembering where you put it — copies the code in, and touches nothing else.
No pip, no venv, no dependency solve. It keeps a backup of the code it
replaced under `_backup/`, prints the old and new version numbers, and says so
explicitly if the new release needs a package you do not have (it will not
install it behind your back).

```
python update.py --check    say what would happen, change nothing
python update.py --to PATH  update a specific install
```

Once a release has been applied, its unzipped folder is disposable.

If you have been unzipping to `geolens_13`, `geolens_14`, … and installing each
one, you are already fine — pick the one you want to keep, rename it to
`current`, run `install.py` against it once more, and update in place from then
on.

---

## What the installer actually does

It builds **one permanent conda environment** called `geolens` — the default
wherever conda is found — and installs into that. Every later release updates
the code *inside* that environment instead of building a new one. It does not
touch your Anaconda base environment, and it does not touch ArcGIS Pro's
Python.

Until v21 the default was a venv, one per release, and in practice that meant
four `.venv` folders beside four unzipped releases and no way to tell which
one Jupyter was using. A permanent named env is the thing you point Jupyter at
once and forget.

**`--venv` is still there, on purpose** — use it to try a release against a
clean, throwaway stack without touching the environment you work in:

```
python install.py --venv      # throwaway test environment
python install.py             # permanent conda env (or update it in place)
```

With no conda on the machine the installer falls back to a venv and says so;
installing [Miniforge](https://conda-forge.org/download/) and re-running gets
you the permanent environment.

The hard part of this package isn't geolens, it's the GDAL stack underneath it
(rasterio, fiona, geopandas, pyproj). Those four carry compiled binaries that
all have to agree about which `libgdal` they're loading. When half come from
conda and half from pip they disagree, and you get
`ImportError: DLL load failed while importing _rasterio`, or reprojections
failing with `proj.db not found`. Neither message tells you what's wrong.

The conda route builds from conda-forge in **one transaction into a fresh
env**. When it has to build one it will rebuild an existing `geolens` env
rather than installing on top of it — see
[the conda conflict](#conda-cant-solve-the-environment) for why that matters.
It only gets that far on a *first* install: with a working install already
present the installer updates the code and never touches the environment.

The venv route (`--venv`, or automatically when there is no conda) uses pip
wheels. rasterio, fiona, pyproj and shapely all ship Windows wheels with their
own GDAL/PROJ/GEOS bundled, so it works — it just is not permanent.

| | |
|---|---|
| `install_windows.bat` | double-click; finds a Python and runs `install.py` |
| `python install.py` | the installer; permanent conda env `geolens` (or update it in place) |
| `python install.py --check` | **diagnose only** — installs nothing, tells you what's wrong |
| `python install.py --smoke` | run the real workflow on a synthetic valley |
| `python install.py --venv` | a throwaway venv, for testing a release without touching your env |
| `python install.py --conda` | force the conda route (the default when conda exists) |
| `python install.py --here` | install into the Python that's already running |
| `python install.py --name X` | name the conda env something other than `geolens` |
| `python install.py --no-extras` | core only, skip the flow-routing stack |
| `python install.py --force-rebuild` | build the environment again even though a working install was found |

Arguments pass through the `.bat`, so `install_windows.bat --conda` works too.

---

## When it doesn't work

Run this first. It installs nothing and it's specific about what's wrong:

```
python install.py --check
```

It checks the things that actually break, not just whether imports succeed:
whether your GDAL stack came from one place or two, whether `proj.db` exists,
whether a stale `PROJ_LIB` left over from an ArcGIS install is shadowing
pyproj, and whether numpy and numba still agree with each other.

Then:

```
python install.py --smoke
```

builds a small synthetic valley, cuts cross-sections, delineates the banks and
runs flow routing. A DEM with a known 16 m channel should come back at 16 m. If
imports pass and this fails, the problem is numerical, not an install problem —
send me the output.

### Specific failures

**`ERROR: Package 'geolens' requires a different Python`**
Something is on Python 3.8 or older. The installer builds its own environment,
so this only happens with `--here` against an old interpreter.

**`DLL load failed while importing _rasterio`** or **`proj.db not found`**
Mixed conda/pip GDAL stack. Don't repair it — build a fresh one, it's faster.
Delete the `.venv` folder (or `conda env remove -n geolens`) and re-run the
installer.

**conda can't solve the environment**

Symptom: a wall of text about `fiona 1.6.3`, `gdal 1.11.4`, Python 2.7, ending
with `Currently pinned specs: python=3.13`.

None of that is a real dependency problem. It means conda is trying to install
conda-forge packages into an environment that already holds packages from the
`defaults` channel. Anaconda-made envs contain `jpeg 9f`; current conda-forge
`libgdal` wants `libjpeg-turbo`, which declares `jpeg <0.0.0a` — meaning "this
package no longer exists". The two can't coexist, and the solver reports it by
walking back through every fiona ever published. Conda also auto-pins whatever
Python the env already has, which is where `python=3.13` comes from even though
the installer asked for 3.11.

The fix is not to repair that env:

```
conda env remove -n geolens   # bin the broken env
python install.py             # builds a fresh one from conda-forge only
```

The installer rebuilds an existing env from scratch rather than installing on
top of one, so this shouldn't recur — and it only reaches that code path on a
first install, because a working install is updated in place instead. If conda
itself is the problem, `python install.py --venv` doesn't involve it at all.

**`Numba needs NumPy 2.x or less`**
numpy was upgraded after numba was installed. In the geolens env:
`pip install -U numba`

**Conda sits on "Solving environment" for 20+ minutes**
The installer asks for the libmamba solver, but an old Anaconda may not have
it. `conda update -n base conda` and re-run, or drop `--conda`.

**`Could not find a wheel` during the pip install**
Your Python is probably too new — compiled packages (numba first, usually)
publish wheels for a new minor version months after it ships. The installer
already prefers 3.12 or 3.11 if one is installed. Otherwise: `py -3.11
install.py`, or `python install.py --conda`.

**The `.bat` can't find Python**
Install [Miniforge](https://conda-forge.org/download/) — small, and it's the
conda that works best with this stack — then double-click again.

---

## About ArcGIS Pro's Python

The installer deliberately won't install into it. Pro's environment is managed
by Esri, is read-only by default, and pins versions to match arcpy — adding
this stack to it is how you end up with a Pro that won't start.

geolens doesn't need arcpy or an ArcGIS licence for anything. It reads File
Geodatabases through GDAL on its own. Run it in its own environment and open
the outputs in Pro.

If you do want them in one place, clone Pro's environment first
(Project → Package Manager → clone), activate the clone, and run
`python install.py --here` — but the separate environment is the safer default.

---

## After it's installed

```
conda activate geolens
jupyter lab notebooks/geolens_start.ipynb
```

Only cell 1 needs editing — the paths to your DEM and centerline. Everything
below it runs top to bottom.

`RUNNING.md` has the workflows and what each output means.
