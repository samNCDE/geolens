"""Run every CLI subcommand that can be reached, on real fixtures.

The CLI is a documented entry point with 28 subcommands. Five of them were
exercised anywhere in this suite. The rest were argparse definitions and a
handler branch that nothing had ever taken — including ``valleyform``,
``valleytypes``, ``breaks``, ``reclassify``, ``fans`` and ``headcuts``, all of
which are listed in RUNNING.md as things to run.

``--help`` on all of them is the cheap half: it catches a duplicate option, a
bad default, a `type=` that cannot parse its own default. Running them is the
half that finds real breakage, so both are here. The ones needing the network
or the WhiteboxTools binary are named in ``_UNREACHABLE`` rather than quietly
left out, so the gap is visible in the file.
"""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "cli_test_out"

# Reachable only with network access or the WhiteboxTools binary, neither of
# which is available where this suite runs. Each has its own --selftest.
_UNREACHABLE = {"naip", "geology", "inundation", "validate-floodplain",
                "compare-backends", "riparian", "ingest-labels",
                "calibrate-cycle"}


@pytest.fixture(scope="module", autouse=True)
def _env():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"


def _subcommands() -> list[str]:
    import re
    import inspect
    from geolens import cli
    return sorted(set(re.findall(r'add_parser\(\s*["\']([a-z0-9_-]+)',
                                 inspect.getsource(cli))))


def _run(argv: list[str]) -> tuple[int, str]:
    from geolens.cli import main
    buf, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = main(argv) or 0
    except SystemExit as exc:
        rc = exc.code or 0
    return rc, buf.getvalue() + err.getvalue()


def test_every_subcommand_has_a_valid_parser():
    subs = _subcommands()
    assert len(subs) >= 25, f"only found {len(subs)} subcommands"
    broken = []
    for s in subs:
        rc, out = _run([s, "--help"])
        if rc != 0 or "usage:" not in out:
            broken.append(f"{s}: rc={rc} {out[:120]!r}")
    assert not broken, "subcommands with a broken parser:\n  " + "\n  ".join(broken)


def test_gullies_has_a_subcommand_like_every_other_feature():
    """It did not, while headcuts, fans and riparian all did."""
    assert "gullies" in _subcommands()


def test_the_watershed_command_exists():
    """The unit the analysis should happen in had no entry point at all.

    ``catchment()`` -- whose own docstring says a complete watershed carries no
    truncation -- has been in flow.py the whole time, reachable from neither
    the api nor the CLI, while ``plan`` pointed users at the approximate
    coarsened basins instead.
    """
    assert "watershed" in _subcommands()


@pytest.fixture(scope="module")
def paths():
    p = {
        "lnd": ROOT / "landscape" / "landscape_dem.tif",
        "cmp": ROOT / "compound" / "compound_dem.tif",
        "cl": ROOT / "compound" / "centerline.shp",
        "nhd": ROOT / "landscape" / "nhd_flowline.shp",
        "gul": ROOT / "gullies" / "gully_dem.tif",
        "fan": ROOT / "fans" / "fan_dem.tif",
    }
    missing = [k for k, v in p.items() if not v.exists()]
    if missing:
        pytest.skip(f"fixtures not built: {missing}")
    return {k: str(v) for k, v in p.items()}


@pytest.fixture(scope="module")
def segments(paths):
    rc, _ = _run(["landscape", paths["lnd"], "--segment-length", "500"])
    assert rc == 0, "the landscape pass failed, so nothing downstream is testable"
    found = sorted(OUT.rglob("segments_classified.gpkg"))
    assert found, "the landscape pass produced no segments"
    return str(found[0])


@pytest.mark.parametrize("label,argv_key", [
    ("workspace", ["workspace"]),
    ("describe", ["describe", "lnd"]),
    ("attrs", ["attrs", "cl"]),
    ("sample", ["sample", "cl"]),
    ("profile", ["profile", "cmp", "cl"]),
    ("sections", ["sections", "cmp", "cl", "--csv"]),
    ("delineate", ["delineate", "cmp", "cl", "--summary"]),
    ("valleyform", ["valleyform", "lnd", "nhd"]),
    ("fans", ["fans", "fan"]),
    ("headcuts", ["headcuts", "gul"]),
    ("history", ["history"]),
    ("backends", ["backends"]),
    ("calibration-history", ["calibration-history"]),
    ("map", ["map", "--dem", "lnd", "--name", "cli_map"]),
    ("gullies", ["gullies", "gul", "--max-drainage-km2", "0.5"]),
    ("watershed", ["watershed", "lnd", "--pour-point", "429985,4510035"]),
])
def test_the_subcommand_runs(label, argv_key, paths):
    argv = [paths.get(a, a) for a in argv_key]
    rc, out = _run(argv)
    assert rc == 0, f"geolens {label} exited {rc}:\n{out[-800:]}"
    assert out.strip(), f"geolens {label} printed nothing at all"


@pytest.mark.parametrize("label,extra", [
    ("breaks", []),
    ("reclassify", ["--confined-max", "2.0", "--partly-confined-max", "5.0"]),
    ("valleytypes", []),
])
def test_the_segment_subcommands_run(label, extra, segments):
    rc, out = _run([label, segments] + extra)
    assert rc == 0, f"geolens {label} exited {rc}:\n{out[-800:]}"
    assert out.strip(), f"geolens {label} printed nothing at all"


def test_the_unreachable_list_is_still_accurate():
    """If one of these becomes runnable here, move it into the tests above.

    A skip list that quietly grows stale is how untested code stays untested.
    """
    subs = set(_subcommands())
    stale = _UNREACHABLE - subs
    assert not stale, (
        f"these are listed as unreachable but no longer exist as "
        f"subcommands: {sorted(stale)}"
    )


def test_every_cli_command_shown_in_the_docs_exists():
    """``geolens plan dem.tif`` was in OVERVIEW.md and was not a command.

    ``api.plan_landscape_run`` existed, the notebook's step 2 is built on it,
    and the docs told people to run it from the shell — where it exited 2 with
    an argparse error. A documented command that does not exist is worse than
    an undocumented one, and nothing in the suite compared the two lists.
    """
    import re

    docs = ROOT.parent
    shown: dict[str, set[str]] = {}
    for name in ("OVERVIEW.md", "RUNNING.md", "README.md", "INSTALL.md"):
        f = docs / name
        if not f.exists():
            continue
        # Only a line that starts a shell command, and not prose like
        # "geolens doesn't ..." -- the apostrophe lookahead is what keeps the
        # contraction out.
        for m in re.finditer(
                r"^\s*(?:\$ )?geolens\s+([a-z0-9][a-z0-9_-]*)(?![\w'\u2019])",
                f.read_text(encoding="utf-8"), re.M):
            shown.setdefault(m.group(1), set()).add(name)

    known = set(_subcommands())
    # Words that follow `geolens` in prose or in a pip/install line rather than
    # being subcommands.
    not_commands = {"is", "needs", "will", "uses", "has", "can", "does"}
    missing = {c: sorted(v) for c, v in shown.items()
               if c not in known and c not in not_commands}
    assert not missing, (
        "the docs show CLI commands that do not exist: "
        + ", ".join(f"`geolens {c}` ({', '.join(v)})" for c, v in sorted(missing.items()))
    )


def test_plan_runs_because_it_is_the_one_the_docs_say_to_run_first(paths):
    rc, out = _run(["plan", paths["lnd"]])
    assert rc == 0, f"geolens plan exited {rc}:\n{out[-500:]}"
    assert '"fits"' in out and '"peak_gb"' in out
