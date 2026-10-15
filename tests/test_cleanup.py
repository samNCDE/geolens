"""What the gully pass leaves behind — files and open handles.

None of this is visible on Linux in normal use, which is why it survived. It
is not invisible on Windows, and Windows is where this is run.

* **Files.** ``_xsect`` takes a path, so each gully's centreline has to exist
  on disk for one call. They were written to a directory that was never
  cleaned, so the run that found 10,739 gullies left 10,739 GeoPackages — in
  the user's output folder, which on Windows is typically under Documents and
  therefore cloud-synced.

* **Handles.** Two rasters were closed by two ``close()`` calls at the end of
  the function, so any exception skipped them. In Jupyter that is worse than a
  leak: ``sys.last_traceback`` keeps the failed frame alive, which keeps the
  datasets open, and on Windows an open handle blocks rewriting the file. One
  failed gully run locked ``slope_std`` and ``accumulation``, and the obvious
  next move — re-run the step that writes them — was exactly what could not
  work until the kernel was restarted.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "cleanup_test_out"
FIX = ROOT / "gullies" / "gully_dem.tif"


@pytest.fixture(scope="module", autouse=True)
def _env():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"


@pytest.fixture(scope="module")
def prepared():
    if not FIX.exists():
        pytest.skip("gully fixture not built")
    from geolens.landscape import build_terrain, terrain_roughness
    t = build_terrain(str(FIX), stream_threshold_km2=0.2, out_prefix="cl_t")
    return t, terrain_roughness(t, window_m=40.0)


def _strays() -> list[Path]:
    return sorted(OUT.rglob("_gully_*.gpkg"))


def _tmp_dirs() -> list[Path]:
    return sorted(p for p in OUT.rglob("gully_tmp_dir*") if p.is_dir())


def test_a_successful_run_leaves_no_centreline_files(prepared):
    from geolens.landscape import detect_gullies

    t, rg = prepared
    g = detect_gullies(t, roughness=rg, slope_std_min=0.04, tpi_max=-0.3,
                       min_length_m=100.0, section_width_m=60.0,
                       out_prefix="cl_ok")
    assert g.gully_count > 0, "nothing was measured, so nothing was cleaned up"
    assert not _strays(), (
        f"{len(_strays())} per-gully GeoPackages left behind — at the scale "
        f"this runs at that is thousands of files in the output folder"
    )
    assert not _tmp_dirs(), f"temp directories left: {_tmp_dirs()}"


def test_a_failed_run_still_cleans_up_and_closes_its_rasters(prepared,
                                                             monkeypatch):
    """The case the two trailing close() calls could never handle."""
    import rasterio
    from geolens.landscape import detect_gullies, features

    t, rg = prepared

    opened = []
    real_open = rasterio.open

    def _tracking_open(*args, **kwargs):
        ds = real_open(*args, **kwargs)
        opened.append(ds)
        return ds

    monkeypatch.setattr(rasterio, "open", _tracking_open)

    def _boom(*args, **kwargs):
        raise RuntimeError("induced failure partway through the pass")

    monkeypatch.setattr(features, "progress", _boom)

    with pytest.raises(RuntimeError, match="induced failure"):
        detect_gullies(t, roughness=rg, slope_std_min=0.04, tpi_max=-0.3,
                       min_length_m=100.0, section_width_m=60.0,
                       out_prefix="cl_boom")

    still_open = [ds for ds in opened if not ds.closed]
    assert not still_open, (
        f"{len(still_open)} rasterio datasets left open after a failure: "
        f"{[getattr(ds, 'name', '?') for ds in still_open]} — on Windows "
        f"those files cannot be rewritten until the process exits"
    )
    assert not _strays(), "centreline files survived a failed run"
    assert not _tmp_dirs(), "the temp directory survived a failed run"


def test_the_peak_file_count_is_one_not_one_per_gully(prepared, monkeypatch):
    """Deleting at the end would still mean thousands of files at once.

    They are deleted as they go, so the directory never holds more than the
    one being measured. This watches the directory from inside the loop rather
    than trusting the end state.
    """
    from geolens import sections
    from geolens.landscape import detect_gullies

    t, rg = prepared
    peak = 0
    # features.py imports it inside the function as
    # `from ..sections import extract_cross_sections as _xsect`, so the name to
    # patch is the one in geolens.sections, not a module-level alias.
    real_xsect = sections.extract_cross_sections

    def _watching_xsect(dem, line_path, **kwargs):
        nonlocal peak
        d = Path(line_path).parent
        peak = max(peak, len(list(d.glob("_gully_*.gpkg"))))
        return real_xsect(dem, line_path, **kwargs)

    monkeypatch.setattr(sections, "extract_cross_sections", _watching_xsect)
    g = detect_gullies(t, roughness=rg, slope_std_min=0.04, tpi_max=-0.3,
                       min_length_m=100.0, section_width_m=60.0,
                       out_prefix="cl_peak")
    assert g.gully_count > 1, "need several gullies for this to mean anything"
    assert peak == 1, (
        f"{peak} centreline files existed at once; they should be deleted as "
        f"they go, so the peak is 1 however many gullies there are"
    )
