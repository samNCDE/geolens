"""Running the installer for a new release must not rebuild the environment.

The habit is to run the installer for every release, and the installer's job
on the second release is not the same as on the first. It was doing the wrong
thing in both routes:

* ``--conda`` **deleted** an existing environment and rebuilt it from scratch,
  hundreds of megabytes redownloaded, to deliver a few hundred kilobytes of
  new Python. That was deliberate — mixing conda channels into an existing env
  is what produces unsolvable dependency conflicts — but it is the right
  answer to "install" and the wrong answer to "update".
* The venv route built another environment beside the last one, which is how
  someone ends up with four of them and no idea which Jupyter is using.

The environment is the expensive, fragile part; the code is not.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_it_finds_the_install_this_python_already_has():
    import install

    py, root = install._working_install(None, None)
    assert py is not None, "geolens is importable here but was not found"
    assert root is not None
    assert (root / "src" / "geolens" / "__init__.py").exists(), (
        f"the root it reported, {root}, is not a geolens source tree"
    )


def test_it_reports_nothing_when_there_is_no_install(monkeypatch):
    """A genuine first install must not be diverted into an update."""
    import install

    def _fail(*a, **k):
        class R:
            stdout = ""
            returncode = 1
        return R()

    monkeypatch.setattr(install.subprocess, "run", _fail)
    monkeypatch.setattr(install, "project_root", lambda: Path("/nonexistent"))
    py, root = install._working_install(None, None)
    assert py is None and root is None


def test_running_the_installer_updates_instead_of_rebuilding():
    """The behaviour, end to end, from a real subprocess."""
    r = subprocess.run([sys.executable, str(ROOT / "install.py")],
                       capture_output=True, text=True, timeout=600)
    out = r.stdout + r.stderr
    assert "Updating it in place" in out, (
        f"the installer did not recognise an existing install:\n{out[-1500:]}"
    )
    assert "The environment is untouched" in out
    # The things a rebuild would say.
    for forbidden in ("Creating conda environment", "conda create",
                      "Creating a virtual environment"):
        assert forbidden not in out, (
            f"it rebuilt the environment anyway: {forbidden!r}"
        )
    assert r.returncode == 0, out[-1500:]


def test_there_is_a_way_to_force_a_rebuild():
    """Updating by default is only acceptable if the old behaviour is still
    reachable — an environment does genuinely need rebuilding sometimes."""
    import install
    import argparse

    src = (ROOT / "install.py").read_text(encoding="utf-8")
    assert "--force-rebuild" in src
    # And it has to actually bypass the short-circuit.
    assert "args.force_rebuild or args.no_update" in src


def test_a_version_spec_survives_a_windows_batch_file():
    """Found on Sam's machine: a file named `0.57` in the install folder.

    Windows runs conda.bat through cmd.exe, which re-parses the line and
    treats `>` as redirection. `numba>=0.57` reached it unquoted, so conda
    got plain `numba` and all of its output went into a file called 0.57 --
    the install looked frozen while it worked.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_inst", str(Path(__file__).resolve().parent.parent / "install.py"))
    inst = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inst)

    line = inst._bat_cmdline(["C:/x/condabin/conda.bat", "install", "-y",
                              "numba>=0.57", "python=3.11",
                              "C:/Program Files/x"])
    assert '"numba>=0.57"' in line, line
    assert ' python=3.11 ' in line, "plain arguments should stay bare"
    assert '"C:/Program Files/x"' in line
    for bare in line.split(" "):
        if ">" in bare or "<" in bare or "|" in bare:
            assert bare.startswith('"'), f"unquoted shell operator in {bare}"


def test_the_real_conda_exe_is_preferred_over_the_bat(tmp_path, monkeypatch):
    import importlib.util
    import types

    spec = importlib.util.spec_from_file_location(
        "_inst2", str(Path(__file__).resolve().parent.parent / "install.py"))
    inst = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inst)

    base = tmp_path / "anaconda3"
    (base / "condabin").mkdir(parents=True)
    (base / "Scripts").mkdir()
    bat = base / "condabin" / "conda.bat"
    exe = base / "Scripts" / "conda.exe"
    bat.write_text("@echo off")
    exe.write_text("")
    fake_os = types.SimpleNamespace(name="nt", environ={})
    monkeypatch.setattr(inst, "os", fake_os)
    assert inst._real_exe(str(bat)) == str(exe)
    # and with no exe beside it, the .bat is kept rather than lost
    exe.unlink()
    assert inst._real_exe(str(bat)) == str(bat)
