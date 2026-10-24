"""The Windows batch files: read as cmd.exe reads them, and what they do.

The tests run on Linux, where cmd.exe is not, so they check what can be
checked from the text: every ``goto`` and ``call`` label exists once; no
``echo`` inside a parenthesised block carries a bare ``)`` (cmd.exe ends the
block there, the classic way a batch file breaks only on the path that
prints a message); the files are plain ASCII; and the installers do what a
computer without Python needs: they build geolens's own Python rather than
stopping (v0.45.2, found on a test computer with an external drive).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BATS = ["install_windows.bat", "setup_portable.bat", "run_project.bat",
        "start_notebook_portable.bat", "push_to_github.bat"]


def _text(name: str) -> str:
    return (ROOT / name).read_bytes().decode("ascii").replace("\r\n", "\n")


def _code_lines(text: str):
    for n, line in enumerate(text.split("\n"), 1):
        s = line.strip()
        if not s or s.lower().startswith(("rem ", "rem\t", "::")) \
                or s.lower() == "rem":
            continue
        yield n, s


def _bare(s: str) -> str:
    """The line without ^-escaped characters and "quoted" parts."""
    s = re.sub(r"\^.", "", s)
    return re.sub(r'"[^"]*"', '""', s)


def _check_blocks(name: str, text: str) -> list[str]:
    problems, depth = [], 0
    for n, s in _code_lines(text):
        b = _bare(s)
        m = re.search(r"(?i)\becho[ .]", b)
        code, words = (b[:m.start()], b[m.start():]) if m else (b, "")
        if words and depth > 0 and ")" in words:
            problems.append(f"{name}:{n}: ')' in an echo inside a block: {s}")
        if words and depth > 0 and "(" in words:
            problems.append(f"{name}:{n}: '(' in an echo inside a block: {s}")
        # only lines whose structure opens or closes blocks change the depth:
        # a ( that ends a line, a ) that starts one
        if code.startswith(")"):
            depth -= 1
        if code.rstrip().endswith("(") and not words:
            depth += 1
        if depth < 0:
            problems.append(f"{name}:{n}: ')' closes a block never opened")
            depth = 0
    if depth:
        problems.append(f"{name}: {depth} block(s) never closed")
    return problems


def test_batch_files_are_plain_ascii_and_their_blocks_close():
    problems = []
    for name in BATS:
        if (ROOT / name).exists():
            problems += _check_blocks(name, _text(name))
    assert not problems, "\n".join(problems)


def test_every_label_a_batch_file_jumps_to_exists_once():
    problems = []
    for name in BATS:
        if not (ROOT / name).exists():
            continue
        text = _text(name)
        labels = re.findall(r"(?m)^:(\w+)", text)
        dupes = {x for x in labels if labels.count(x) > 1}
        if dupes:
            problems.append(f"{name}: labels twice: {sorted(dupes)}")
        for n, s in _code_lines(text):
            for target in re.findall(r"(?i)\b(?:goto\s+:?|call\s+:)(\w+)", s):
                if target.lower() != "eof" and target not in labels:
                    problems.append(f"{name}:{n}: no label :{target}")
    assert not problems, "\n".join(problems)


def test_the_block_check_catches_the_mistake():
    bad = 'if exist x (\n  echo  could not (see above)\n)\n'
    good = 'if exist x (\n  echo  could not ^(see above^)\n)\n'
    assert _check_blocks("t", bad) and not _check_blocks("t", good)
    assert not _check_blocks("t", 'echo  done (all of it)\n')   # not in a block
    assert _check_blocks("t", 'if x (\n  echo a\n')             # never closed


def test_install_windows_builds_its_own_python_without_conda():
    t = _text("install_windows.bat")
    # the Microsoft Store's python.exe shortcut is never taken for a Python
    assert r"\WindowsApps\=" in t
    # only a conda Python hands over to install.py
    assert "conda-meta" in t and '"%PY%" "%~dp0install.py" %*' in t
    # without one: geolens's own Python, by setup_portable.bat, not a dead end
    assert 'call "%ROOT%\\setup_portable.bat"' in t
    assert "Install Miniforge" not in t
    assert "--portable" in t and "--which" in t
    # a folder with its own env is updated, not given a second environment
    assert t.index(r'if exist "%ROOT%\env\python.exe"') < t.index("anaconda3")
    # its own pause, once, unless whoever called it said not to
    assert 'set "NOPAUSE=%GEOLENS_NO_PAUSE%"' in t
    assert "if not defined NOPAUSE pause" in t


def test_portable_setup_downloads_by_more_than_one_route():
    t = _text("setup_portable.bat")
    assert r'"%SystemRoot%\System32\curl.exe"' in t or "%CURL%" in t
    assert "Invoke-WebRequest" in t and "micro.mamba.pm" in t
    assert r'"%SystemRoot%\System32\tar.exe"' in t      # not Git's GNU tar
    # what micromamba writes to a user profile stays on the drive
    assert r'set "USERPROFILE=%ROOT%\home"' in t
    # every pause can be skipped by a caller (install_windows, run_project)
    pauses = [s for _, s in _code_lines(t) if re.search(r"(?i)\bpause\b", s)]
    assert pauses and all("if not defined GEOLENS_NO_PAUSE" in s for s in pauses)
    assert "exit /b 0" in t and "exit /b 1" in t


def test_portable_setup_keeps_inside_the_windows_path_limit():
    """micromamba 2.9 unpacks packages under
    pkgs\\https\\conda.anaconda.org\\conda-forge\\<subdir>\\<package>\\, and
    debugpy's and jupyterlab's deepest files are 180-188 characters below
    pkgs: under <release>\\.portable\\mamba\\pkgs that passed 260 and the setup
    stopped ("critical libmamba Package cache error", 23 Oct 2026, on S:)."""
    t = _text("setup_portable.bat")
    # the cache in a short folder at the top of the drive
    assert r'set "PKGS=%~d0\geolens_pkgs"' in t
    assert 'set "CONDA_PKGS_DIRS=%PKGS%"' in t
    assert r'set "CONDA_PKGS_DIRS=%PORT%\mamba\pkgs"' not in t
    drive_cache = len(r"E:\geolens_pkgs")
    assert drive_cache + len(r"\https\conda.anaconda.org\conda-forge") \
        + len(r"\noarch\jupyterlab-4.6.4-pyhd8ed1ab_0\\") + 151 < 260
    # and removed once the environment is built, by its \\?\\ path (a cache
    # that passed 260 characters cannot be removed by its plain path)
    assert t.index('if exist "%PKGS%\\" rmdir /s /q "\\\\?\\%PKGS%"') \
        > t.index("install.py\" --smoke")
    # a release folder too deep for the environment's own files is refused
    # before anything is downloaded (160 characters below env)
    assert 'if not "%ROOT:~90,1%"=="" goto :too_deep' in t
    assert t.index(":~90,1%") < t.index("call :get_micromamba")
    # an env a stopped setup left behind is built again, not "updated"
    assert r"conda-meta\history" in t and "%%~zH EQU 0" in t
    assert t.index("call :remove_incomplete") < t.index('set "ACTION=install"')


def test_scripts_give_the_environment_its_own_gdal_and_proj_data():
    """Not just cleared: GDAL warned "GDAL_DATA is not defined" and pyogrio
    "Could not detect GDAL data files" on the portable env (23 Oct 2026)."""
    for name in ("setup_portable.bat", "run_project.bat",
                 "start_notebook_portable.bat"):
        t = _text(name)
        assert r'set "GDAL_DATA=%ENVDIR%\Library\share\gdal"' in t, name
        assert r'set "PROJ_DATA=%ENVDIR%\Library\share\proj"' in t, name
        # set after the clearing loop, not cleared again by it
        assert t.index('set "GDAL_DATA=%ENVDIR%') > t.index("GDAL_DATA GDAL_DRIVER_PATH") \
            or name == "setup_portable.bat"


def test_the_runner_gives_a_conda_env_its_data_dirs(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "geolens_runner_t", ROOT / "arcgis_toolbox" / "geolens_runner.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    env = tmp_path / "env"
    (env / "Library" / "share" / "gdal").mkdir(parents=True)
    (env / "Library" / "share" / "proj").mkdir(parents=True)
    (env / "Library" / "share" / "proj" / "proj.db").write_bytes(b"")
    (env / "python.exe").write_bytes(b"")
    got = runner.env_data_dirs(env)
    assert got["GDAL_DATA"].endswith("gdal") and got["PROJ_LIB"] == got["PROJ_DATA"]
    e = runner.clean_env(str(env / "python.exe"))
    assert e["GDAL_DATA"] == got["GDAL_DATA"] and e["PROJ_DATA"] == got["PROJ_DATA"]
    venv = tmp_path / "venv" / "Scripts"
    venv.mkdir(parents=True)
    assert runner.env_data_dirs(venv.parent) == {}            # wheels: nothing set
    bat = runner.launcher_bat(str(env / "python.exe"), "s.json", str(tmp_path),
                              "t", "run_project")
    assert bat.index('set "GDAL_DATA=') > bat.index("GDAL_DATA GDAL_DRIVER_PATH")


def test_run_project_mends_a_half_built_env_once():
    t = _text("run_project.bat")
    assert "if defined MENDED goto :broken_python" in t
    assert t.index('set "MENDED=1"') < t.rindex('call "%ROOT%\\setup_portable.bat"')


def test_run_project_finds_or_builds_a_python():
    t = _text("run_project.bat")
    assert '"%PY%" -m geolens.cli project "%BOUNDARY%"' in t
    order = [r"%ROOT%\env\python.exe", r"%ROOT%\.venv\Scripts\python.exe",
             r".geolens\python.txt", r"envs\geolens"]
    at = [t.index(x) for x in order]
    assert at == sorted(at)
    assert 'call "%ROOT%\\setup_portable.bat"' in t
    assert r'set "PYTHONPATH=%ROOT%\src"' in t        # this release's geolens
    # no other program's PROJ or GDAL, and the env's own DLLs first
    assert "PROJ_LIB" in t and "GDAL_DATA" in t
    assert r"%ENVDIR%\Library\bin" in t
    # a boundary that cannot be read is not tried five times
    assert r'if not exist "%PROJ_DIR%\project.json" goto :stopped' in t
    assert 'if "%RC%"=="3" goto :busy' in t
    assert "exit /b %RC%" in t and "pause" in t


def test_the_cli_says_busy_with_its_own_exit_code(tmp_path, monkeypatch):
    import geolens.cli as cli
    from geolens import api
    from geolens.project import ProjectBusy

    def busy(*a, **k):
        raise ProjectBusy("another run")

    monkeypatch.setattr(api, "run_project", busy)
    monkeypatch.setenv("GEOLENS_OUT", str(tmp_path))     # put back afterwards
    b = tmp_path / "b.geojson"
    b.write_text('{"type": "FeatureCollection", "features": []}')
    assert cli.main(["project", str(b), "--out", str(tmp_path / "p")]) == 3
