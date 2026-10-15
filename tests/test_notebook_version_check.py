"""A notebook newer than the installed library must stop and say so.

On Sam's machine a v23 notebook ran against a v21 library: the update had
been applied from an older unzipped folder. The notebook passed
stream_threshold_km2=None, which v21 does not accept, and the error was a
TypeError from inside build_terrain -- an error about the code, several cells
after the actual cause, which was the install.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

NB = Path(__file__).resolve().parent.parent / "notebooks" / "geolens_start.ipynb"


def _step0_source() -> str:
    nb = json.loads(NB.read_text(encoding="utf-8"))
    for c in nb["cells"]:
        s = "".join(c["source"])
        if c["cell_type"] == "code" and "NOTEBOOK_FOR" in s:
            return s
    raise AssertionError("no version check in the notebook")


def test_the_notebook_is_stamped_with_the_package_version():
    import geolens
    assert f'NOTEBOOK_FOR = "{geolens.__version__}"' in _step0_source()


def test_an_older_library_stops_with_the_fix(monkeypatch, tmp_path):
    src = _step0_source()
    fake = types.ModuleType("geolens")
    fake.__version__ = "0.21.0"
    fake.__file__ = str(tmp_path / "geolens" / "__init__.py")
    monkeypatch.setitem(sys.modules, "geolens", fake)
    with pytest.raises(RuntimeError) as ei:
        exec(compile(src, "step0", "exec"), {"__name__": "__nb__"})
    msg = str(ei.value)
    assert "0.21.0" in msg and "update.py" in msg, msg
