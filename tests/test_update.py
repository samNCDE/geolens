"""Updating an install without rebuilding the environment.

``install.py`` resolves the GDAL/rasterio/numba stack, which is the slow and
fragile part. New code does not need any of that redone — but unzipping each
release to its own ``geolens_14``, ``geolens_15`` folder puts every release at
a new path, so the editable pointer has to move, and the easiest way to move it
looks like running the installer again.

``update.py`` copies the code into an install that already works and touches
nothing else. These tests run it against throwaway trees, because the one thing
it must never do is damage a working install.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def trees(tmp_path):
    """A fake 'installed' tree and a fake 'release' tree."""
    def make(where: Path, version: str, extra: dict[str, str] | None = None):
        pkg = where / "src" / "geolens"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(
            f'__version__ = "{version}"\n', encoding="utf-8")
        (where / "notebooks").mkdir()
        (where / "notebooks" / "geolens_start.ipynb").write_text(
            f'{{"v": "{version}"}}', encoding="utf-8")
        (where / "pyproject.toml").write_text(
            'dependencies = ["numpy", "rasterio"]\n', encoding="utf-8")
        (where / "README.md").write_text(f"# geolens {version}\n",
                                         encoding="utf-8")
        for rel, body in (extra or {}).items():
            p = where / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        return where

    installed = make(tmp_path / "current", "0.9.0",
                     {"src/geolens/old_module.py": "GONE = True\n"})
    release = make(tmp_path / "geolens_15", "0.11.0",
                   {"src/geolens/new_module.py": "ADDED = True\n"})
    return installed, release


def test_it_reads_the_version_without_importing(trees):
    import update

    installed, release = trees
    assert update._version_of(installed) == "0.9.0"
    assert update._version_of(release) == "0.11.0"
    assert "geolens" not in [m for m in sys.modules
                             if m == "geolens" and "old_module" in str(m)]


def test_check_changes_nothing(trees, capsys):
    import update

    installed, release = trees
    before = sorted(p.name for p in (installed / "src" / "geolens").iterdir())
    rc = update.update(release, installed, check=True)
    after = sorted(p.name for p in (installed / "src" / "geolens").iterdir())
    assert rc == 0
    assert before == after
    assert update._version_of(installed) == "0.9.0"


def test_it_replaces_rather_than_merges(trees):
    """A module deleted in the new release has to actually disappear.

    Merging would leave it importable, so a bug that was removed comes back
    with nothing to show where it came from.
    """
    import update

    installed, release = trees
    update.update(release, installed, backup=False)

    pkg = installed / "src" / "geolens"
    assert (pkg / "new_module.py").exists(), "the new module was not copied"
    assert not (pkg / "old_module.py").exists(), (
        "a module removed in the new release survived the update"
    )
    assert update._version_of(installed) == "0.11.0"


def test_it_keeps_a_backup_by_default(trees):
    import update

    installed, release = trees
    update.update(release, installed)

    backups = list((installed / "_backup").glob("0.9.0_*"))
    assert backups, "no backup of the replaced code"
    assert (backups[0] / "src" / "geolens" / "old_module.py").exists(), (
        "the backup does not contain the code it replaced"
    )


def test_updating_a_folder_with_itself_is_a_no_op(trees, capsys):
    import update

    installed, _ = trees
    rc = update.update(installed, installed)
    assert rc == 0
    assert "Nothing to do" in capsys.readouterr().out
    assert update._version_of(installed) == "0.9.0"


def test_it_refuses_a_target_that_is_not_an_install(tmp_path, trees):
    import update

    _, release = trees
    empty = tmp_path / "somewhere_else"
    empty.mkdir()
    assert update.update(release, empty) == 2


def test_it_refuses_a_source_that_is_not_a_release(tmp_path, trees):
    import update

    installed, _ = trees
    empty = tmp_path / "not_a_release"
    empty.mkdir()
    assert update.update(empty, installed) == 2


def test_it_reads_dependencies_out_of_pyproject(trees):
    """So it can report what a new release needs, without installing it."""
    import update

    _, release = trees
    deps = update._declared_dependencies(release)
    assert "numpy" in deps and "rasterio" in deps


def test_the_real_pyproject_parses():
    """Guards the regex against a reformat of the actual file.

    The extras matter as much as the core list: scikit-image, scipy, numba and
    pysheds are what gully detection and flow routing actually import, so a
    check that only read ``dependencies`` would miss a release that started
    using a new one. This is also the list that matters when targeting a Python
    that is not ours, such as the one QGIS ships.
    """
    import update

    deps = update._declared_dependencies(ROOT)
    assert deps, "no dependencies parsed out of the real pyproject.toml"
    assert "rasterio" in deps and "numpy" in deps
    assert "skimage" in deps, "scikit-image should map to its import name"
    assert "numba" in deps and "pysheds" in deps, "the extras were not read"
    # A comment containing "someone else's dependency tree" used to contribute
    # a package called `s`, via the apostrophe.
    assert not [d for d in deps if len(d) < 2], f"junk parsed out: {deps}"


def test_it_verifies_the_tree_it_updated_not_whichever_one_is_importable(
        trees, capsys):
    """The first version of this printed a false pass.

    It ran ``import geolens`` in a subprocess and reported "Verified: 0.11.0"
    — from a completely different install, because the interpreter imported
    geolens from somewhere else entirely. A verification that can pass while
    checking the wrong thing is worse than none; it is the same
    editable-pointer trap ``install.py`` already warns about.
    """
    import update

    installed, release = trees
    rc = update.update(release, installed, backup=False)
    out = capsys.readouterr().out
    assert rc == 0, out

    assert "Verified: geolens 0.11.0" in out
    assert str(installed) in out, (
        "the verification did not name the folder it checked"
    )
    # These throwaway trees are never the tree this Python imports from, so
    # the mismatch note has to appear — that is the whole point of it.
    assert "NOT the folder just updated" in out, (
        "it updated a folder this Python does not import from and said nothing"
    )


def test_a_target_whose_package_is_broken_fails_rather_than_claiming_success(
        trees, capsys):
    import update

    installed, release = trees
    (release / "src" / "geolens" / "__init__.py").write_text(
        '__version__ = "0.11.0"\nraise RuntimeError("broken release")\n',
        encoding="utf-8")
    rc = update.update(release, installed, backup=False)
    assert rc == 1, "a release that cannot be imported reported success"
    assert "Could not import geolens" in capsys.readouterr().out
