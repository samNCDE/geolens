"""The QGIS plugin's runner, tested without QGIS.

The plugin hands every tool to the geolens environment as a subprocess and
streams its progress back. These check the parts that can break on a
colleague's machine without QGIS in the picture: finding the environment,
cleaning QGIS's variables out of the child's environment, delivering
progress on the calling thread (QGIS feedback objects must not be touched
from a foreign thread), cancelling, and a real api call round trip.
"""
import importlib.util
import os
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEM = ROOT / "tests" / "landscape" / "landscape_dem.tif"


@pytest.fixture
def runner():
    spec = importlib.util.spec_from_file_location(
        "geolens_qgis_runner",
        ROOT / "qgis_plugin" / "geolens_qgis" / "runner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_installer_pointer_is_a_candidate(runner, tmp_path, monkeypatch):
    monkeypatch.delenv("GEOLENS_PYTHON", raising=False)
    pointer = tmp_path / "python.txt"
    pointer.write_text(sys.executable + "\n", encoding="utf-8")
    monkeypatch.setattr(runner, "POINTER", pointer)
    assert runner.candidate_pythons()[0] == sys.executable
    # A setting chosen in the plugin wins over the pointer.
    assert runner.candidate_pythons(sys.executable)[0] == sys.executable
    monkeypatch.setattr(runner, "POINTER", tmp_path / "missing.txt")
    assert sys.executable not in runner.candidate_pythons()


def test_clean_env_drops_qgis_settings(runner, monkeypatch):
    monkeypatch.setenv("PYTHONHOME", "/opt/QGIS/apps/Python39")
    monkeypatch.setenv("PROJ_LIB", "/opt/QGIS/share/proj")
    monkeypatch.setenv("PATH", os.pathsep.join(["/opt/QGIS 3.34/bin",
                                                "/usr/bin"]))
    env = runner.clean_env(sys.executable, {"GEOLENS_OUT": "x"})
    assert "PYTHONHOME" not in env and "PROJ_LIB" not in env
    assert "/opt/QGIS 3.34/bin" not in env["PATH"].split(os.pathsep)
    assert "/usr/bin" in env["PATH"].split(os.pathsep)
    assert env["GEOLENS_OUT"] == "x" and env["PYTHONIOENCODING"] == "utf-8"


_FAKE = (
    "import sys,json,time\n"
    "sys.stdin.read()\n"
    "for i in range(1,4):\n"
    "    sys.stderr.write('GEOLENS_PROGRESS '+json.dumps("
    "{'label':'step','n':i,'total':3})+'\\n'); sys.stderr.flush()\n"
    "    time.sleep(0.05)\n"
    "sys.stderr.write('GEOLENS_NOTE a note\\n')\n"
    "sys.stderr.write('some other line\\n')\n"
    "print('GEOLENS_RESULT'+json.dumps({'ok':1,'path':'p'}))\n"
)


def test_progress_arrives_on_the_calling_thread(runner, tmp_path,
                                                monkeypatch):
    monkeypatch.setattr(runner, "_RUN", _FAKE)
    me = threading.get_ident()
    seen = {"progress": [], "info": [], "debug": [], "threads": set()}

    def on(kind):
        def f(*a):
            seen["threads"].add(threading.get_ident())
            seen[kind].append(a)
        return f

    res = runner.run_api(sys.executable, "anything", {}, str(tmp_path),
                         on_progress=on("progress"), on_info=on("info"),
                         on_debug=on("debug"))
    assert res == {"ok": 1, "path": "p"}
    assert [round(p) for p, _ in seen["progress"]] == [33, 67, 100]
    assert seen["info"] == [("a note",)]
    assert ("some other line",) in seen["debug"]
    assert seen["threads"] == {me}


def test_cancel_stops_the_run(runner, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_RUN",
                        "import sys,time\nsys.stdin.read()\ntime.sleep(60)\n")
    t0 = time.monotonic()
    with pytest.raises(runner.Cancelled):
        runner.run_api(sys.executable, "anything", {}, str(tmp_path),
                       is_cancelled=lambda: time.monotonic() - t0 > 0.5)
    assert time.monotonic() - t0 < 20


def test_failure_reports_the_traceback(runner, tmp_path):
    with pytest.raises(RuntimeError, match="no_such_function"):
        runner.run_api(sys.executable, "no_such_function", {}, str(tmp_path))


def test_real_api_round_trip(runner, tmp_path):
    res = runner.run_api(sys.executable, "plan_landscape_run",
                         {"dem": str(DEM)}, str(tmp_path))
    assert res["verdict"] and res["cells"] > 0


def test_a_callback_that_raises_stops_the_run(runner, tmp_path, monkeypatch):
    """arcpy without env.isCancelled reports a cancel by raising from the
    next progress call; the run must stop, not carry on unseen."""
    monkeypatch.setattr(runner, "_RUN", (
        "import sys,json,time\n"
        "sys.stdin.read()\n"
        "for i in range(1,600):\n"
        "    sys.stderr.write('GEOLENS_PROGRESS '+json.dumps("
        "{'label':'x','n':i,'total':600})+'\\n'); sys.stderr.flush()\n"
        "    time.sleep(0.1)\n"))

    class Stop(Exception):
        pass

    def boom(p, label):
        raise Stop()

    t0 = time.monotonic()
    with pytest.raises(Stop):
        runner.run_api(sys.executable, "anything", {}, str(tmp_path),
                       on_progress=boom)
    assert time.monotonic() - t0 < 20
