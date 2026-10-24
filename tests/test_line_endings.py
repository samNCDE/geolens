"""Windows batch files keep CRLF line endings; shell scripts keep LF.

cmd.exe reads a batch file in blocks, and when its lines end in a bare LF it
can fail to find a ``goto`` or ``call`` label ("The system cannot find the
batch label specified"), depending on where the label happens to fall. The
portable setup jumps to a label when something fails, so an LF-only copy
breaks exactly when something has already gone wrong. Shell scripts are the
reverse: a CR at the end of a line breaks bash.

The tests run on Linux, where neither problem shows, so they look at the
bytes.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# what a portable setup or a run adds beside the release
SKIP = {"__pycache__", ".git", ".pytest_cache", "env", "tmp", ".portable",
        "home", "PortableGit"}


def _files(pattern):
    return [p for p in ROOT.rglob(pattern)
            if not set(p.relative_to(ROOT).parts) & SKIP]


def _lone_lf(data: bytes) -> int:
    return data.replace(b"\r\n", b"").count(b"\n")


def test_batch_files_have_windows_line_endings():
    bats = _files("*.bat")
    assert {"install_windows.bat", "setup_portable.bat",
            "start_notebook_portable.bat",
            "run_project.bat"} <= {p.name for p in bats}
    bad = [p.name for p in bats if _lone_lf(p.read_bytes())]
    assert not bad, f"LF-only lines in {bad}: cmd.exe can miss their labels"


def test_shell_scripts_have_unix_line_endings():
    bad = [p.name for p in _files("*.sh") if b"\r" in p.read_bytes()]
    assert not bad, f"CR in {bad}: bash fails on them"


def test_git_keeps_them_that_way():
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8").split()
    assert attrs.count("-text") >= 1 and "*.bat" in attrs
    assert "*.sh" in attrs and "eol=lf" in attrs


def test_a_lone_lf_is_caught():
    assert _lone_lf(b"@echo off\r\ngoto :x\r\n:x\r\n") == 0
    assert _lone_lf(b"@echo off\r\ngoto :x\n:x\r\n") == 1


def test_the_launcher_runs_a_project_with_the_drives_own_python():
    """run_project.bat: drag a boundary on, it runs on its own."""
    text = (ROOT / "run_project.bat").read_text(encoding="ascii")
    assert '"%PY%" -m geolens.cli project "%BOUNDARY%"' in text
    assert text.index(r"%ROOT%\env\python.exe") < text.index(r"envs\geolens")
    assert r'set "PYTHONPATH=%ROOT%\src"' in text     # this release's geolens
    assert "exit /b %RC%" in text and "pause" in text   # more: test_batch_files
