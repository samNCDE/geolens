"""Running geolens from inside QGIS, whichever Python QGIS happens to have.

Groundwork for a QGIS plugin, deliberately free of PyQGIS so it can be tested
without QGIS — which is the whole point, because QGIS cannot be installed in
the environment this was written in. The PyQGIS layer, when it exists, is a
thin adapter over this; everything with logic to get wrong lives here and is
covered.

The decision this makes is real: QGIS ships GDAL, numpy, shapely and matplotlib
on every platform, but ``geopandas``, ``rasterio``, ``scipy``,
``scikit-image``, ``numba`` and ``pysheds`` may or may not be there and on a
managed Windows install may not be installable either. So the mode is detected
rather than assumed — in-process when QGIS's Python can import geolens,
subprocess against a separate environment when it cannot, and an explicit
"unavailable" with the missing imports named when neither works.
"""
from __future__ import annotations

import sys

import pytest

from geolens import qgis_bridge as qb


def test_it_finds_this_interpreter_can_run_geolens():
    """This environment has everything, so the answer must be in-process."""
    st = qb.detect()
    assert st.mode == "in-process", st.summary()
    assert st.ok
    assert st.geolens_version
    assert not st.missing


def test_the_report_is_fit_to_paste_into_a_qgis_console():
    d = qb.doctor()
    assert d["ok"] is True
    assert "summary" in d and d["summary"]
    assert d["interpreter"] == sys.executable
    assert "rasterio" in d["requirements"]


def test_a_missing_landscape_stack_is_distinguished_from_a_broken_install(
        monkeypatch):
    """Losing scipy is not the same as losing rasterio.

    Cross-sections, profiles and bank delineation work without the landscape
    stack. Reporting "geolens is unavailable" because numba is absent would
    send someone chasing an install problem instead of running the half of the
    tool that works.
    """
    monkeypatch.setattr(qb, "_probe_imports",
                        lambda names=None: ["scipy", "numba"])
    st = qb.detect()
    assert st.mode == "in-process"
    assert st.missing_landscape_only is True
    assert "Cross-sections" in " ".join(st.notes)
    assert "scipy" in st.summary()


def test_losing_a_core_package_is_not_called_landscape_only(monkeypatch):
    monkeypatch.setattr(qb, "_probe_imports",
                        lambda names=None: ["rasterio", "scipy"])
    st = qb.detect()
    assert st.missing_landscape_only is False


def test_it_falls_back_to_another_interpreter(monkeypatch):
    """The case that matters on a locked-down QGIS install."""
    import builtins

    real_import = builtins.__import__

    def _no_geolens(name, *a, **k):
        if name == "geolens" or name.startswith("geolens."):
            raise ImportError("no geolens here")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_geolens)
    monkeypatch.setattr(qb, "_probe_imports", lambda names=None: ["rasterio"])
    monkeypatch.setattr(qb, "candidate_pythons", lambda: ["/somewhere/python"])
    monkeypatch.setattr(qb, "_probe_external",
                        lambda py, timeout=90.0: ("9.9.9", []))

    st = qb.detect()
    assert st.mode == "subprocess"
    assert st.python == "/somewhere/python"
    assert st.geolens_version == "9.9.9"
    assert "runs in a separate environment" in st.summary()


def test_it_says_so_when_there_is_nothing_to_run(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _no_geolens(name, *a, **k):
        if name == "geolens" or name.startswith("geolens."):
            raise ImportError("no geolens here")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_geolens)
    monkeypatch.setattr(qb, "_probe_imports",
                        lambda names=None: ["rasterio", "geopandas"])
    monkeypatch.setattr(qb, "candidate_pythons", lambda: [])

    st = qb.detect()
    assert st.mode == "unavailable"
    assert not st.ok
    assert "rasterio" in st.summary()
    assert qb.ENV_OVERRIDE in st.summary(), (
        "it did not say how to point the plugin at an existing environment"
    )
    with pytest.raises(RuntimeError, match="not available"):
        qb.run("workspace", {}, status=st)


def test_candidates_never_propose_the_interpreter_we_are_already_in():
    """Proposing it as an 'external' environment would be a pointless
    subprocess of ourselves, and it would mask the real answer."""
    here = str(qb.Path(sys.executable).resolve())
    for c in qb.candidate_pythons():
        assert str(qb.Path(c).resolve()) != here


def test_an_explicit_override_is_tried_first(monkeypatch):
    monkeypatch.setenv(qb.ENV_OVERRIDE, "/opt/chosen/python")
    assert qb.candidate_pythons()[0] == "/opt/chosen/python"


def test_running_in_process_calls_the_api(monkeypatch, tmp_path):
    """The signature has to be identical in both modes — that is the point."""
    import os

    monkeypatch.setenv("GEOLENS_OUT", str(tmp_path))
    monkeypatch.setenv("GEOLENS_NO_PROGRESS", "1")
    st = qb.detect()
    out = qb.run("workspace", {}, status=st)
    assert isinstance(out, dict)
    assert "output_directory" in out


def test_an_unknown_function_is_refused_rather_than_silently_nothing():
    st = qb.detect()
    with pytest.raises(RuntimeError, match="no function"):
        qb.run("not_a_real_function", {}, status=st)


def test_every_requirement_says_what_it_is_for():
    """'scipy is missing' means nothing; 'gully detection needs scipy' does."""
    for name, why in qb.REQUIREMENTS.items():
        assert why and len(why) > 8, f"{name} has no explanation"
    assert qb.LANDSCAPE_ONLY <= set(qb.REQUIREMENTS)
