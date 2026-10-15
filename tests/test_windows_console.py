"""The console this runs on is not always UTF-8, and that used to be fatal.

Every test in this suite runs on Linux with a UTF-8 stream. The tool is run on
Windows. A Windows *console* is UTF-8 in modern Python, but the moment output
is redirected — ``geolens landscape dem.tif > run.log``, which is what anyone
does for a job that takes hours — the stream falls back to the locale
encoding, cp1252 on a Western install.

cp1252 has the em dash and the superscript two this package uses freely. It
does **not** have U+2588 and U+2591, the block characters the progress bar is
drawn with. Writing one raises ``UnicodeEncodeError``, which is not a cosmetic
failure: it ends the run. A progress bar, whose entire job is to be reassuring,
killing an overnight job at its first redraw.

These tests write to a real cp1252 stream, so they fail on Linux if the
handling regresses rather than waiting for someone on Windows to find out.
"""
from __future__ import annotations

import io
import sys

import pytest


def _cp1252_stream():
    """A stream that behaves exactly like redirected output on Windows."""
    buf = io.BytesIO()
    return buf, io.TextIOWrapper(buf, encoding="cp1252", errors="strict",
                                 newline="")


@pytest.fixture(autouse=True)
def _progress_on(monkeypatch):
    monkeypatch.delenv("GEOLENS_NO_PROGRESS", raising=False)


def test_the_progress_bar_does_not_kill_a_redirected_run(monkeypatch):
    from geolens import safety

    buf, stream = _cp1252_stream()
    monkeypatch.setattr(sys, "stderr", stream)
    monkeypatch.setattr(safety, "_supports_progress", lambda: True)

    out = list(safety.progress(range(40), label="cp1252"))
    stream.flush()

    assert out == list(range(40)), "the bar must not disturb what it wraps"
    text = buf.getvalue().decode("cp1252")
    assert text, "nothing was drawn"
    assert "█" not in text and "░" not in text


def test_the_nice_bar_is_still_used_when_the_stream_can_take_it(monkeypatch):
    """The fallback must not become the default everywhere."""
    from geolens import safety

    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="utf-8", errors="strict", newline="")
    monkeypatch.setattr(sys, "stderr", stream)
    monkeypatch.setattr(safety, "_supports_progress", lambda: True)
    monkeypatch.setitem(sys.modules, "tqdm.auto", None)   # force the fallback

    list(safety.progress(range(20), label="utf8"))
    stream.flush()
    text = buf.getvalue().decode("utf-8")
    assert "█" in text, "the block bar was dropped on a UTF-8 stream"


def test_notes_survive_characters_the_console_cannot_show(monkeypatch):
    """A note is never worth ending a run over."""
    from geolens import safety

    buf, stream = _cp1252_stream()
    monkeypatch.setattr(sys, "stderr", stream)
    monkeypatch.setattr(safety, "_supports_progress", lambda: True)

    safety.note("gullies — 2.59 km/km² █░ → done")
    stream.flush()
    text = buf.getvalue().decode("cp1252")
    assert "2.59 km/km²" in text, "it mangled characters cp1252 does have"
    assert "gullies —" in text


def test_no_string_in_the_package_is_unprintable_on_a_western_windows_console():
    """A standing check on every user-facing literal, not just today's.

    Found when this was first written: the two progress-bar blocks, an arrow in
    the MCP tool descriptions and a typographic minus in three matplotlib axis
    labels. The bar is the one that mattered — the others do not reach a
    console — so they are allowed here by name rather than by being rewritten
    into worse typography.
    """
    import ast
    import pathlib

    allowed = {"→", "−"}          # MCP descriptions; plot axis labels
    # safety.py is the module that DOES the guarding: it holds the block
    # characters and the probe string it tests the stream with, both
    # reached only after `_stream_can_encode` says the stream can take
    # them. The three tests above are what cover it, by writing to a real
    # cp1252 stream.
    exempt = {"safety.py"}
    root = pathlib.Path(__file__).resolve().parent.parent / "src" / "geolens"
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if path.name in exempt:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            for ch in node.value:
                if ord(ch) < 128 or ch in allowed:
                    continue
                try:
                    ch.encode("cp1252")
                except UnicodeEncodeError:
                    offenders.append(
                        f"{path.name}:{node.lineno} {ch!r} U+{ord(ch):04X}")
    assert not offenders, (
        "string literals a redirected Windows console cannot encode:\n  "
        + "\n  ".join(sorted(set(offenders))[:20])
    )
