#!/usr/bin/env python3
"""Update an existing geolens install in place. No new environment.

    python update.py            find the install and update it
    python update.py --check    say what would happen, change nothing
    python update.py --to PATH  update a specific install

**Why this exists.** ``install.py`` builds an environment: it resolves the
GDAL/rasterio/numba stack, which is the slow and fragile part, and then points
an editable install at the source folder. That environment does not need to be
rebuilt to get new code — the code is the cheap part. But unzipping each
release to its own ``geolens_14``, ``geolens_15`` folder means every release
lives at a new path, so the editable pointer has to be moved, and the easiest
way to move it looks like running the installer again.

So the recommended shape is: **install once into a stable folder, then update
that folder.** The versioned folders you unzip afterwards are update *sources*,
not installs, and can be deleted once they have been applied.

    Tools\\geolens\\current      <- installed once, never moves
    Tools\\geolens\\geolens_15   <- unzip here, run update.py, delete

This script copies the code and leaves the environment completely alone. It
touches no pip, no venv, and no dependencies unless you ask it to.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# What actually makes up a release. Everything else in the zip (tests,
# build/, the session logs) is not part of a working install.
PAYLOAD_DIRS = ("src", "notebooks", "skill", "docs", "qgis_plugin",
                "arcgis_toolbox")
PAYLOAD_FILES = ("START_HERE.md", "START_HERE.html",
                 "README.md", "RUNNING.md", "OVERVIEW.md", "INSTALL.md",
                 "NEXT.md", "REVIEW.md", "pyproject.toml", "LICENSE",
                 "quickstart.py", "install.py", "update.py")


def say(msg: str = "") -> None:
    print(msg, flush=True)


def _version_of(root: Path) -> str:
    """Read the version from a source tree WITHOUT importing it.

    Importing would give whichever copy is already on sys.path, which during
    an update is exactly the one being replaced — so the "before" and "after"
    would be the same number and the report would be a lie.
    """
    init = root / "src" / "geolens" / "__init__.py"
    try:
        m = re.search(r'^__version__\s*=\s*["\']([^"\']+)',
                      init.read_text(encoding="utf-8"), re.M)
        return m.group(1) if m else "unknown"
    except OSError:
        return "not found"


def _installed_root() -> Path | None:
    """Where does this Python import geolens from?

    An editable install is a pointer. Following it is the only reliable way to
    find the folder that is actually in use — asking the user where they think
    it is has been wrong before.
    """
    try:
        spec = importlib.util.find_spec("geolens")
    except Exception:                                        # noqa: BLE001
        return None
    if spec is None or not spec.origin:
        return None
    # .../<root>/src/geolens/__init__.py
    return Path(spec.origin).resolve().parent.parent.parent


def _missing_imports(python: str, root: Path) -> list[str]:
    """Does the new code import anything the environment does not have?

    A release can add a dependency. Silently installing it would defeat the
    point of a script that promises not to touch the environment, so this
    reports and lets the user decide.
    """
    probe = (
        "import importlib, json, sys\n"
        "names = %r\n"
        "missing = []\n"
        "for n in names:\n"
        "    try:\n"
        "        importlib.import_module(n)\n"
        "    except Exception:\n"
        "        missing.append(n)\n"
        "print(json.dumps(missing))\n"
    )
    wanted = _declared_dependencies(root)
    if not wanted:
        return []
    try:
        out = subprocess.run([python, "-c", probe % wanted],
                             capture_output=True, text=True, timeout=180)
        import json
        return json.loads(out.stdout.strip() or "[]")
    except Exception:                                        # noqa: BLE001
        return []


def _declared_dependencies(root: Path) -> list[str]:
    """Import names from pyproject's dependency list, best effort."""
    try:
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return []
    # Core dependencies AND the optional extras. The extras are "optional" to
    # pip, not to the user: scikit-image, scipy, numba and pysheds are what
    # gully detection and flow routing actually import, and a release that
    # starts using a new one would sail past a check that only read the core
    # list. This is also the list that matters when targeting a Python you do
    # not control, such as the one QGIS ships.
    # Strip comments first. "someone else's dependency tree" in a comment
    # otherwise contributes a package called `s`, via the apostrophe.
    text = re.sub(r"^\s*#.*$", "", text, flags=re.M)
    dep_block = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.S | re.M)
    extras = re.search(r"^\[project\.optional-dependencies\](.*?)(?=^\[|\Z)",
                       text, re.S | re.M)
    chunks = []
    if dep_block:
        chunks.append(dep_block.group(1))
    if extras:
        chunks.append(extras.group(1))
    if not chunks:
        return []
    # A real requirement starts with a letter and is at least two characters.
    names = re.findall(r'["\']([A-Za-z][A-Za-z0-9_.\-]+)', "\n".join(chunks))
    # pip name -> import name, for the ones that differ.
    alias = {"scikit-image": "skimage", "pillow": "PIL", "gdal": "osgeo",
             "python-dateutil": "dateutil", "pyyaml": "yaml",
             "scikit-learn": "sklearn", "opencv-python": "cv2"}
    # Version specifiers and extras markers come through the same regex.
    skip = {"all", "dev", "test", "docs", "mcp", "extra"}
    out = []
    for n in names:
        n = n.strip().lower()
        if not n or n in skip:
            continue
        out.append(alias.get(n, n.replace("-", "_")))
    return sorted(set(out))


def update(source: Path, target: Path, check: bool = False,
           backup: bool = True) -> int:
    source, target = source.resolve(), target.resolve()
    if source == target:
        say(f"Source and target are the same folder:\n  {target}")
        say("Nothing to do — this install already IS this release.")
        return 0

    old_v, new_v = _version_of(target), _version_of(source)
    say(f"  installed at  {target}   (geolens {old_v})")
    say(f"  updating from {source}   (geolens {new_v})")
    say()

    if not (source / "src" / "geolens" / "__init__.py").exists():
        say("This folder does not look like a geolens release: no")
        say("src/geolens/__init__.py. Run update.py from inside the unzipped")
        say("release folder.")
        return 2
    if not (target / "src" / "geolens").exists():
        say(f"{target} does not look like a geolens install (no src/geolens).")
        say("If this is a fresh machine, run install.py instead — that builds")
        say("the environment, which this script deliberately does not touch.")
        return 2

    items = [d for d in PAYLOAD_DIRS if (source / d).is_dir()]
    files = [f for f in PAYLOAD_FILES if (source / f).is_file()]
    say(f"Would replace: {', '.join(items)}")
    say(f"Would refresh: {len(files)} top-level files")

    if check:
        say()
        say("--check: nothing was changed.")
        return 0

    if backup:
        stamp = time.strftime("%Y%m%dT%H%M%S")
        bdir = target / "_backup" / f"{old_v}_{stamp}"
        bdir.mkdir(parents=True, exist_ok=True)
        for d in items:
            if (target / d).is_dir():
                shutil.copytree(target / d, bdir / d, dirs_exist_ok=True)
        say(f"Backed up the previous code to {bdir}")

    for d in items:
        dst = target / d
        # Replace rather than merge: a file deleted in the new release has to
        # disappear, or a stale module keeps being importable and the bug it
        # carried comes back with no trace of where from.
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(source / d, dst)
    for f in files:
        shutil.copy2(source / f, target / f)

    say(f"Updated {target} to geolens {new_v}.")

    missing = _missing_imports(sys.executable, source)
    if missing:
        say()
        say("This release imports packages the environment does not have:")
        for m in missing:
            say(f"  - {m}")
        say()
        say("The environment was NOT changed. Install them with:")
        say(f"  {sys.executable} -m pip install " + " ".join(missing))
    else:
        say("Every declared dependency is already present — nothing to install.")

    # Verify THE TREE THAT WAS UPDATED, not whatever this interpreter happens
    # to import. The first version of this ran `import geolens` in a
    # subprocess and printed "Verified: 0.11.0" — from a completely different
    # install, because this Python imported geolens from somewhere else
    # entirely. A verification that can pass while checking the wrong thing is
    # worse than none: it is the exact shape of the editable-pointer trap that
    # install.py already warns about.
    #
    # A fresh interpreter is still needed — this one imported geolens before
    # the files moved, so its idea of the version is stale.
    probe = (
        "import sys; sys.path.insert(0, %r)\n"
        "import geolens; print(geolens.__version__, geolens.__file__)\n"
        % str(target / "src")
    )
    try:
        out = subprocess.run([sys.executable, "-c", probe],
                             capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            say("Could not import geolens from the updated folder:")
            say(out.stderr.strip()[-600:])
            return 1
        ver, where = (out.stdout.strip().split(None, 1) + [""])[:2]
        if Path(where).resolve().is_relative_to(target):
            say(f"Verified: geolens {ver} at {where}")
        else:
            say(f"Could not verify: imported geolens {ver} from {where},")
            say(f"which is not inside {target}.")
            return 1
        if ver != new_v:
            say(f"The updated folder reports {ver}, expected {new_v}.")
            return 1
    except Exception as exc:                                 # noqa: BLE001
        say(f"Could not verify the update: {exc}")
        return 1

    installed = _installed_root()
    if installed is not None and installed.resolve() != target:
        say()
        say("NOTE: this Python imports geolens from")
        say(f"  {installed}")
        say("which is NOT the folder just updated. Nothing you run will pick")
        say("up this update until the two agree — either update that folder")
        say("instead, or point the install at this one.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Update an existing geolens install in place.")
    ap.add_argument("--to", metavar="PATH", default=None,
                    help="the install to update (default: wherever this "
                         "Python imports geolens from)")
    ap.add_argument("--check", action="store_true",
                    help="say what would happen and change nothing")
    ap.add_argument("--no-backup", action="store_true",
                    help="do not keep a copy of the replaced code")
    args = ap.parse_args(argv)

    source = Path(__file__).resolve().parent
    say("geolens update")
    say("=" * 60)

    if args.to:
        target = Path(args.to).expanduser()
    else:
        found = _installed_root()
        if found is None:
            say("This Python does not have geolens installed, so there is")
            say("nothing to update. Run install.py to build an environment,")
            say("or pass --to PATH to update a different install.")
            return 2
        target = found

    return update(source, target, check=args.check, backup=not args.no_backup)


if __name__ == "__main__":
    raise SystemExit(main())
