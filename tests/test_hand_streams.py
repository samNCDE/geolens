"""HAND's two silent failures, both found by asking where the numbers came from.

The report was "the hand output doesn't look like it worked" — on a real run
HAND came back with a maximum of 20,628 on a DEM whose entire relief is 5,908,
and 17,741,388 cells were above that relief. ``_sanitise_hand`` caught and
masked them, but catching a wrong number downstream is not the same as not
producing it, and the cause was never fixed.

**Cause.** A nodata cell still collects flow from its valid neighbours. On a
clipped watershed — which is what most real DEMs are — the collar around the
data is exactly where that flow ends up, so those cells sail past the stream
threshold and get marked as streams with the nodata value for an elevation.
HAND then measures height above them, and every cell draining to the collar
reports ``elevation + 9999``.

Measured on the fixture below before the fix: **794 of 1,053 stream cells
(75%) were on nodata**, and 26,547 cells came back above the DEM's 96.2 m
relief, the worst 11,997.

**Cost of the fix: none.** HAND coverage is identical before and after, on both
a real catchment (85.1%) and one whose ground mostly drains off the edge
(16.5%) — because those cells never had usable HAND, they had a fabricated one
that ``_sanitise_hand`` then threw away. ``test_the_fix_does_not_cost_coverage``
holds that, so a future over-eager mask cannot quietly delete real data.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "hs_test_out"


@pytest.fixture(scope="module", autouse=True)
def _env():
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"


def _clipped_basin(path: Path, kind: str = "bowl", n: int = 240,
                   cell: float = 10.0) -> str:
    """A catchment clipped to an irregular boundary, like any real DEM.

    ``bowl`` converges hard on a central channel and has one real outlet
    through the collar. ``flank`` drains mostly straight off the edge, which is
    the harder case for HAND and the one that shows the coverage question
    honestly.
    """
    import rasterio
    from rasterio.transform import from_origin

    yy, xx = np.mgrid[0:n, 0:n].astype("float64")
    X, Y = xx * cell, yy * cell
    cx = n * cell / 2
    if kind == "bowl":
        z = 2000.0 + 0.030 * np.abs(X - cx) - 0.015 * Y
        z -= np.clip(25.0 - 0.5 * np.abs(X - cx), 0, None)
    else:
        z = 2000.0 - 0.020 * Y + 0.006 * np.abs(X - cx)
        z -= np.clip(40.0 - 0.25 * np.abs(X - cx), 0, None)
    z = z.astype("float32")

    rr = ((xx - n / 2) / (0.42 * n)) ** 2 + ((yy - n / 2) / (0.46 * n)) ** 2
    nod = rr > 1.0
    if kind == "bowl":
        nod &= ~((np.abs(X - cx) < 40) & (Y > n * cell * 0.5))
    z[nod] = -9999.0

    prof = {"driver": "GTiff", "width": n, "height": n, "count": 1,
            "dtype": "float32", "crs": "EPSG:32612", "nodata": -9999.0,
            "transform": from_origin(500000, 4000000, cell, cell)}
    with rasterio.open(str(path), "w", **prof) as dst:
        dst.write(z, 1)
    return str(path)


def _coverage(t):
    """Fraction of real ground that ended up with a usable HAND value."""
    import rasterio
    with rasterio.open(t.dem) as s:
        dz = s.read(1, masked=True)
        valid = ~np.ma.getmaskarray(dz)
    with rasterio.open(t.hand) as s:
        hv = s.read(1)
        nod = s.nodata
    has = np.isfinite(hv) & (hv != nod) & valid
    return int(has.sum()), int(valid.sum())


@pytest.fixture(scope="module")
def basin():
    from geolens.landscape import build_terrain
    dem = _clipped_basin(OUT / "basin.tif", "bowl")
    return build_terrain(dem, stream_threshold_km2=0.05, out_prefix="hs_b")


# -- the defect ---------------------------------------------------------------

def test_no_stream_cell_sits_on_nodata(basin):
    """The whole bug in one assertion."""
    import rasterio
    with rasterio.open(basin.streams) as s:
        st = s.read(1).astype(bool)
    with rasterio.open(basin.dem) as s:
        valid = ~np.ma.getmaskarray(s.read(1, masked=True))
    on_nodata = int((st & ~valid).sum())
    assert on_nodata == 0, (
        f"{on_nodata:,} stream cells are on nodata — HAND will be measured "
        f"above the nodata value for everything draining to them"
    )


def test_hand_never_exceeds_the_dems_own_relief(basin):
    """The invariant, checked on the written raster rather than in passing."""
    import rasterio
    with rasterio.open(basin.dem) as s:
        dz = s.read(1, masked=True)
    relief = float(dz.max()) - float(dz.min())
    with rasterio.open(basin.hand) as s:
        hv = s.read(1)
        nod = s.nodata
    real = hv[np.isfinite(hv) & (hv != nod)]
    assert real.size, "no HAND values at all"
    assert float(real.max()) <= relief * 1.001 + 1e-6, (
        f"HAND max {float(real.max()):,.1f} exceeds relief {relief:,.1f}"
    )


def test_the_impossible_hand_warning_no_longer_fires(basin):
    """It used to, on this exact fixture: 26,547 cells, worst 11,997.

    That warning is a backstop. Once the cause is fixed it should be silent —
    if it starts firing again the cause is back, not the backstop.
    """
    bad = [w for w in basin.warnings if "above the DEM's own relief" in w]
    assert not bad, f"the invariant backstop is firing again: {bad}"


def test_it_reports_the_collar_rather_than_staying_quiet(basin):
    """Dropping 75% of the raw stream mask is not something to do silently."""
    assert any("sitting on nodata" in w for w in basin.warnings), (
        f"stream cells were dropped with no explanation: {basin.warnings}"
    )


# -- the fix must not cost anything -------------------------------------------

@pytest.mark.parametrize("kind,floor", [("bowl", 0.80), ("flank", 0.15)])
def test_the_fix_does_not_cost_coverage(kind, floor):
    """Measured identical before and after: 85.1% and 16.5%.

    A mask that removed real drainage would show up here as lost coverage. The
    floors are set below the measured values, not at them, so ordinary
    conditioning noise does not make this flaky — but a mask that started
    eating real streams would fall far below.
    """
    from geolens.landscape import build_terrain

    dem = _clipped_basin(OUT / f"cov_{kind}.tif", kind)
    t = build_terrain(dem, stream_threshold_km2=0.05, out_prefix=f"hs_c{kind}")
    has, valid = _coverage(t)
    assert has / valid >= floor, (
        f"{kind}: HAND on only {has:,}/{valid:,} ground cells "
        f"({100 * has / valid:.1f}%), below the {floor:.0%} floor"
    )


# -- the sentinel -------------------------------------------------------------

def test_hand_works_below_sea_level():
    """``found >= 0.0`` used to double as "nothing found yet".

    `found` is an elevation, so on ground below sea level a perfectly good
    negative base was rejected and the whole upstream path dropped to NaN with
    nothing reported. Measured on this fixture: 39,473 of 40,000 cells before
    the fix, 40,000 after — a silent 1.3% loss.

    The arid Southwest has plenty of ground below sea level (Salton Sea −69 m,
    Death Valley −86 m), so this is not hypothetical for the intended use.
    """
    import rasterio
    from rasterio.transform import from_origin
    from geolens.landscape import build_terrain

    n, cell = 200, 10.0
    yy, xx = np.mgrid[0:n, 0:n].astype("float64")
    X, Y = xx * cell, yy * cell
    cx = n * cell / 2
    z = (-60.0 + 0.030 * np.abs(X - cx) - 0.015 * Y)
    z -= np.clip(25.0 - 0.5 * np.abs(X - cx), 0, None)
    z = z.astype("float32")
    assert z.max() < 0, "fixture is meant to be entirely below sea level"

    p = OUT / "below_sea.tif"
    prof = {"driver": "GTiff", "width": n, "height": n, "count": 1,
            "dtype": "float32", "crs": "EPSG:32612", "nodata": -32768.0,
            "transform": from_origin(500000, 4000000, cell, cell)}
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(z, 1)

    t = build_terrain(str(p), stream_threshold_km2=0.02, out_prefix="hs_sea")
    has, valid = _coverage(t)
    assert has == valid, (
        f"{valid - has:,} of {valid:,} cells lost their HAND on a DEM that is "
        f"entirely below sea level"
    )


# -- one HAND implementation, not one per backend -----------------------------

def test_the_path_cap_does_not_change_the_answer():
    """``path`` used to be allocated H*W: 15.5 GB of int64 on a 1.94-billion
    cell DEM, on top of the 7.7 GB output, requested up front on every call.

    It only ever holds the walk in progress. Capping it is safe because every
    cell is the first entry of its own walk, so a truncated walk delays other
    cells rather than losing them — they are resolved when the scan reaches
    them. That claim is the whole justification, so test it directly: a cap of
    3 must give bit-identical output to no cap at all.
    """
    import rasterio
    from geolens.landscape import build_terrain
    from geolens.landscape.terrain import _hand_from_flow

    dem = _clipped_basin(OUT / "cap.tif", "bowl", n=160)
    t = build_terrain(dem, stream_threshold_km2=0.05, out_prefix="hs_cap")
    with rasterio.open(t.conditioned) as s:
        cond = s.read(1)
    with rasterio.open(t.streams) as s:
        st = s.read(1).astype(bool)

    ref = _hand_from_flow(cond, st, t.flow_dir, path_cap=10 ** 9)
    for cap in (1, 3, 17, 1000):
        got = _hand_from_flow(cond, st, t.flow_dir, path_cap=cap)
        assert np.array_equal(np.nan_to_num(ref, nan=-1.0),
                              np.nan_to_num(got, nan=-1.0)), (
            f"path_cap={cap} changed the result"
        )


def test_build_and_restream_resolve_the_same_cells(basin):
    """They used to not, and nothing said so.

    ``build_terrain`` called pysheds' ``compute_hand``; ``restream`` called
    geolens' tracer. The two agree to 1e-4 m on values but not on which cells
    get one — 89.8% against 95.3% of ground on the compound fixture, and 76%
    against 100% on a convergent basin. Same routing, same thresholds, two
    different answers depending on which entry point you came through.
    """
    import rasterio
    from geolens.landscape import restream

    r = restream(basin, stream_threshold_km2=basin.stream_threshold_km2,
                 out_prefix="hs_rs")
    def _n(path):
        with rasterio.open(path) as s:
            v = s.read(1)
            return int((np.isfinite(v) & (v != s.nodata)).sum())
    a, b = _n(basin.hand), _n(r.hand)
    assert a == b, (
        f"build_terrain resolved {a:,} cells and restream {b:,} from identical "
        f"routing — the two are not using the same HAND implementation"
    )


# -- the out-of-core backend --------------------------------------------------
#
# WhiteboxTools cannot be exercised here: its binary download is blocked in
# this environment. The two guards it needs are therefore written as plain
# functions over rasters and tested directly, so the logic is covered even
# though the wiring that calls it is not. That is the honest split -- the
# helpers below are verified; `_run_whitebox` calling them is read, not run.

def _tiny(path: Path, arr, nodata=-9999.0, dtype="float32"):
    import rasterio
    from rasterio.transform import from_origin
    prof = {"driver": "GTiff", "width": arr.shape[1], "height": arr.shape[0],
            "count": 1, "dtype": dtype, "crs": "EPSG:32612", "nodata": nodata,
            "transform": from_origin(0, 0, 1, 1)}
    with rasterio.open(str(path), "w", **prof) as dst:
        dst.write(arr.astype(dtype), 1)
    return path


def test_windowed_masking_drops_exactly_the_collar_cells():
    import rasterio
    from geolens.landscape.terrain import _mask_streams_to_valid_file

    z = np.full((40, 40), 100.0, dtype="float32")
    z[:5, :] = -9999.0                       # a nodata band along the top
    st = np.zeros((40, 40), dtype="uint8")
    st[3:10, 20] = 1                         # a "stream" running into it
    dem = _tiny(OUT / "wb_dem.tif", z)
    sp = _tiny(OUT / "wb_streams.tif", st, nodata=0, dtype="uint8")

    warns: list[str] = []
    dropped = _mask_streams_to_valid_file(sp, dem, warns, block=8)

    assert dropped == 2, f"expected the 2 cells on nodata, got {dropped}"
    with rasterio.open(str(sp)) as s:
        out = s.read(1)
    assert out[:5, :].sum() == 0, "stream cells left on nodata"
    assert out[5:10, 20].sum() == 5, "it ate stream cells on real ground"
    assert any("sitting on nodata" in w for w in warns)


def test_windowed_masking_is_silent_when_there_is_nothing_to_drop():
    from geolens.landscape.terrain import _mask_streams_to_valid_file

    z = np.full((30, 30), 100.0, dtype="float32")
    st = np.zeros((30, 30), dtype="uint8")
    st[10:20, 15] = 1
    dem = _tiny(OUT / "wb_dem2.tif", z)
    sp = _tiny(OUT / "wb_streams2.tif", st, nodata=0, dtype="uint8")

    warns: list[str] = []
    assert _mask_streams_to_valid_file(sp, dem, warns, block=7) == 0
    assert not warns


def test_the_windowed_invariant_check_finds_the_plus_9999_signature():
    """The exact shape of the original failure: elevation + 9999."""
    from geolens.landscape.terrain import _check_hand_file

    z = np.linspace(1000, 1100, 900).reshape(30, 30).astype("float32")
    h = np.full((30, 30), 5.0, dtype="float32")
    h[10, 10] = 1050.0 + 9999.0
    dem = _tiny(OUT / "wb_dem3.tif", z)
    hp = _tiny(OUT / "wb_hand3.tif", h)

    warns: list[str] = []
    n = _check_hand_file(hp, dem, warns, block=9)
    assert n == 1, f"expected 1 impossible cell, got {n}"
    assert any("above the DEM's own relief" in w for w in warns)


def test_the_windowed_invariant_check_passes_a_good_raster():
    from geolens.landscape.terrain import _check_hand_file

    z = np.linspace(1000, 1100, 900).reshape(30, 30).astype("float32")
    h = np.full((30, 30), 5.0, dtype="float32")
    dem = _tiny(OUT / "wb_dem4.tif", z)
    hp = _tiny(OUT / "wb_hand4.tif", h)
    warns: list[str] = []
    assert _check_hand_file(hp, dem, warns, block=9) == 0
    assert not warns


def test_the_whitebox_backend_actually_calls_both_guards(monkeypatch):
    """The wiring, with the binary stubbed.

    WhiteboxTools' binary cannot be downloaded in CI here, so ``_run_whitebox``
    had no coverage of any kind — the two guards above could have been wired in
    wrongly and nothing would have said so. This replaces the tool with a stub
    that writes the rasters it would write, including a stream raster that runs
    into the nodata collar, and then checks what came back out.
    """
    import sys
    import types
    import rasterio
    from geolens.landscape.terrain import _run_whitebox

    n = 60
    z = np.full((n, n), 100.0, dtype="float32")
    z += np.linspace(0, 40, n)[:, None].astype("float32")
    z[:8, :] = -9999.0                                  # nodata collar
    dem = _tiny(OUT / "wbx_dem.tif", z)
    out_dir = OUT / "wbx"
    out_dir.mkdir(exist_ok=True)

    def _like(path, arr, dtype="float32", nodata=-9999.0):
        with rasterio.open(str(dem)) as src:
            prof = src.profile.copy()
        prof.update(dtype=dtype, nodata=nodata, count=1, compress="lzw")
        with rasterio.open(str(path), "w", **prof) as dst:
            dst.write(arr.astype(dtype), 1)

    class _FakeWBT:
        verbose = False
        def set_working_dir(self, d): pass
        def breach_depressions_least_cost(self, src, dst, dist=0):
            _like(dst, z)
        def d8_pointer(self, src, dst, esri_pntr=False):
            _like(dst, np.full((n, n), 4.0), dtype="int32", nodata=0)   # due S
        def d8_flow_accumulation(self, src, dst, out_type="cells"):
            _like(dst, np.tile(np.arange(n, dtype="float32")[:, None], (1, n)))
        def extract_streams(self, acc, dst, threshold=0):
            st = np.zeros((n, n), dtype="uint8")
            st[:, 30] = 1              # a column of stream, collar included
            _like(dst, st, dtype="uint8", nodata=0)
        def elevation_above_stream(self, cond, streams, dst):
            # Whatever this writes, the point of the test is the stream raster
            # it was handed: the guard has to run BEFORE this, because once
            # HAND has been measured above a nodata cell there is nothing left
            # to repair. `_check_hand_file` itself is tested on its own fixture
            # above; this only needs to produce a well-formed raster.
            with rasterio.open(str(streams)) as s:
                st = s.read(1) > 0
            _like(dst, np.where(st, 0.0, 5.0).astype("float32"))

    fake = types.ModuleType("whitebox")
    fake.WhiteboxTools = _FakeWBT
    monkeypatch.setitem(sys.modules, "whitebox", fake)

    warns: list[str] = []
    paths = _run_whitebox(dem, out_dir, 5, warns)

    with rasterio.open(paths["streams"]) as s:
        st = s.read(1)
    assert st[:8].sum() == 0, "collar stream cells survived into the network"
    assert st[8:, 30].sum() == n - 8, "it ate stream cells on real ground"
    assert any("sitting on nodata" in w for w in warns), (
        f"the mask ran but said nothing: {warns}"
    )
