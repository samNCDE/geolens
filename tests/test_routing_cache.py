"""Condition once per DEM, not once per run.

Filling depressions, resolving flats and routing D8 is the expensive part of a
landscape pass, and it is *deterministic*: the same DEM through the same
backend gives the same filled surface every time. It was being paid again on
every fresh session — open the notebook the next morning and the first cell
re-fills every depression it filled yesterday. On a 1.94-billion-cell DEM that
is hours.

Reusing a ``TerrainResult`` in memory already avoided it within one session.
This survives closing the notebook, which is the case that actually hurt.

Measured on the landscape fixture: 2.01 s cold, 0.09 s warm at the same stream
threshold, 0.24 s warm at a different one — and bit-for-bit identical output in
all three cases, which is the only thing that makes a cache acceptable.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "cache_test_out"
DEM = ROOT / "landscape" / "landscape_dem.tif"


@pytest.fixture(scope="module", autouse=True)
def _env():
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"
    if not DEM.exists():
        pytest.skip("landscape fixture not built")


def _read(t, keys=("conditioned", "flow_dir", "accumulation", "streams", "hand")):
    import rasterio
    out = {}
    for k in keys:
        with rasterio.open(getattr(t, k)) as src:
            out[k] = np.nan_to_num(src.read(1), nan=-1.0)
    return out


def _differ(a, b) -> list[str]:
    return [k for k in a if not np.array_equal(a[k], b[k])]


def test_a_warm_cache_gives_bit_identical_output():
    """The only property that makes a cache acceptable."""
    from geolens.landscape import build_terrain

    cold = build_terrain(str(DEM), stream_threshold_km2=1.0,
                         out_prefix="rc_cold", reuse_routing=False)
    build_terrain(str(DEM), stream_threshold_km2=1.0, out_prefix="rc_warm")
    again = build_terrain(str(DEM), stream_threshold_km2=1.0,
                          out_prefix="rc_again")

    differ = _differ(_read(cold), _read(again))
    assert not differ, f"the cached run differs from a cold one in: {differ}"


def test_changing_the_threshold_reuses_routing_but_not_the_network():
    """The whole point: the threshold does not affect routing."""
    from geolens.landscape import build_terrain

    a = build_terrain(str(DEM), stream_threshold_km2=1.0, out_prefix="rc_t1")
    b = build_terrain(str(DEM), stream_threshold_km2=0.3, out_prefix="rc_t2")

    ra, rb = _read(a), _read(b)
    for k in ("conditioned", "flow_dir", "accumulation"):
        assert np.array_equal(ra[k], rb[k]), (
            f"{k} changed when only the stream threshold changed — routing is "
            f"being redone, or the cache is serving the wrong DEM"
        )
    assert not np.array_equal(ra["streams"], rb["streams"]), (
        "the stream network did not change when the threshold did"
    )


def test_editing_the_dem_misses_the_cache():
    """A stale cache is worse than no cache."""
    import rasterio
    from geolens.landscape import build_terrain

    copy = OUT / "editable.tif"
    shutil.copy(str(DEM), str(copy))
    first = build_terrain(str(copy), stream_threshold_km2=1.0,
                          out_prefix="rc_e1")
    before = _read(first, ("conditioned",))["conditioned"].copy()

    with rasterio.open(str(copy)) as src:
        z = src.read(1)
        prof = src.profile.copy()
    z[100:200, 100:200] += 50.0          # a real change to the surface
    with rasterio.open(str(copy), "w", **prof) as dst:
        dst.write(z, 1)

    second = build_terrain(str(copy), stream_threshold_km2=1.0,
                           out_prefix="rc_e2")
    after = _read(second, ("conditioned",))["conditioned"]
    assert not np.array_equal(before, after), (
        "the cache served a conditioned DEM from before the edit"
    )


def test_a_half_written_cache_is_ignored_rather_than_trusted():
    """Cleaning up, or a sync client moving files, must cost a recompute."""
    import json
    from geolens.landscape import build_terrain
    from geolens.landscape.terrain import (_routing_cache_dir,
                                           _read_routing_manifest)
    from geolens.safety import Guard

    t = build_terrain(str(DEM), stream_threshold_km2=1.0, out_prefix="rc_h")
    cache = _routing_cache_dir(Guard.from_env(), Path(str(DEM)), t.backend)
    assert _read_routing_manifest(cache) is not None, "nothing was cached"

    meta = json.loads((cache / "manifest.json").read_text(encoding="utf-8"))
    missing = Path(meta["accumulation"])
    moved = missing.with_suffix(".moved")
    missing.rename(moved)
    try:
        assert _read_routing_manifest(cache) is None, (
            "the manifest was trusted with one of its rasters gone"
        )
    finally:
        moved.rename(missing)


def test_the_cache_is_on_by_default_and_can_be_turned_off():
    import inspect
    from geolens.landscape import build_terrain

    sig = inspect.signature(build_terrain)
    assert sig.parameters["reuse_routing"].default is True
    t = build_terrain(str(DEM), stream_threshold_km2=1.0, out_prefix="rc_off",
                      reuse_routing=False)
    assert Path(t.conditioned).exists()


def test_a_warm_run_is_actually_faster():
    """If it is not faster there is no reason to accept the complexity."""
    import time
    from geolens.landscape import build_terrain

    build_terrain(str(DEM), stream_threshold_km2=1.0, out_prefix="rc_p0")

    t0 = time.monotonic()
    build_terrain(str(DEM), stream_threshold_km2=1.0, out_prefix="rc_p1",
                  reuse_routing=False)
    cold = time.monotonic() - t0

    t0 = time.monotonic()
    build_terrain(str(DEM), stream_threshold_km2=1.0, out_prefix="rc_p2")
    warm = time.monotonic() - t0

    assert warm < cold * 0.6, (
        f"warm {warm:.2f}s against cold {cold:.2f}s — the cache is not "
        f"saving anything"
    )


def test_the_cache_survives_a_different_output_directory(tmp_path):
    """The failure that made the cache useless in practice.

    It lived under the output directory, so it was thrown away by the most
    ordinary things anyone does: a new results folder, a new session with a
    different OUT_DIR, unzipping the next release beside the last. The point
    is to pay for conditioning once per DEM; keying it to the output folder
    meant paying once per run *configuration*.

    Measured before the fix: 4.89 s cold, 0.07 s warm in the same OUT_DIR,
    and 1.90 s again from a different one.
    """
    import time
    import shutil as _sh
    import rasterio  # noqa: F401
    from geolens.landscape import build_terrain

    data = tmp_path / "data"
    data.mkdir()
    dem = data / "d.tif"
    _sh.copy(str(DEM), str(dem))
    os.environ.pop("GEOLENS_CACHE", None)

    timings = []
    for run in ("A", "B", "C"):
        out = tmp_path / f"out{run}"
        out.mkdir()
        os.environ["GEOLENS_OUT"] = str(out)
        t0 = time.monotonic()
        build_terrain(str(dem), stream_threshold_km2=1.0, out_prefix=f"c{run}")
        timings.append(time.monotonic() - t0)
    os.environ["GEOLENS_OUT"] = str(OUT)

    cold, warm_b, warm_c = timings
    assert warm_b < cold * 0.5, (
        f"a different OUT_DIR paid {warm_b:.2f}s against a cold {cold:.2f}s — "
        f"the cache is keyed to the output folder again"
    )
    assert warm_c < cold * 0.5
    assert (data / ".geolens_cache").is_dir(), (
        "the cache is not beside the DEM it belongs to"
    )


def test_an_explicit_cache_location_is_honoured(tmp_path):
    """Research data very often sits on a read-only or network mount."""
    import shutil as _sh
    from geolens.landscape import build_terrain

    data = tmp_path / "ro_data"
    data.mkdir()
    dem = data / "d.tif"
    _sh.copy(str(DEM), str(dem))
    cache = tmp_path / "fast_local_cache"
    os.environ["GEOLENS_CACHE"] = str(cache)
    os.environ["GEOLENS_OUT"] = str(tmp_path / "out")
    (tmp_path / "out").mkdir()
    try:
        build_terrain(str(dem), stream_threshold_km2=1.0, out_prefix="cenv")
        assert any(cache.iterdir()), "GEOLENS_CACHE was ignored"
        assert not (data / ".geolens_cache").exists(), (
            "it cached beside the DEM despite an explicit location"
        )
    finally:
        os.environ.pop("GEOLENS_CACHE", None)
        os.environ["GEOLENS_OUT"] = str(OUT)


def test_a_touched_dem_still_hits(tmp_path):
    """The failure that made the cache look like it did not exist at all.

    Sam's DEM lives under ``Documents``, which on Windows is very often
    OneDrive-synced. A sync client touches files; so does copying a DEM to
    another folder, re-downloading it, or restoring it from a backup. With
    the modification time in the cache key every one of those silently threw
    away hours of conditioning, and nothing in the output said why.

    Timestamps are now advisory: a moved one prompts a content check, not a
    miss. The check has to be *trusted*, so this asserts the reused surface
    is bit-for-bit what the cold run produced, not merely that it was fast.
    """
    import shutil as _sh
    import numpy as _np
    from geolens.landscape import build_terrain

    data = tmp_path / "synced"
    data.mkdir()
    dem = data / "d.tif"
    _sh.copy(str(DEM), str(dem))
    os.environ.pop("GEOLENS_CACHE", None)
    os.environ["GEOLENS_OUT"] = str(tmp_path / "out")
    (tmp_path / "out").mkdir()
    try:
        cold = build_terrain(str(dem), stream_threshold_km2=1.0,
                             out_prefix="tc_cold")
        before = _read(cold, ("conditioned", "flow_dir", "accumulation"))

        st = dem.stat()                       # what a sync client does
        os.utime(dem, ns=(st.st_atime_ns, st.st_mtime_ns + 5 * 10**9))

        warm = build_terrain(str(dem), stream_threshold_km2=1.0,
                             out_prefix="tc_warm")
        assert warm.routing_cache["hit"] is True, (
            f"a touched DEM missed the cache: "
            f"{warm.routing_cache.get('reason')}"
        )
        after = _read(warm, ("conditioned", "flow_dir", "accumulation"))
        assert not _differ(before, after), "the reused routing is not identical"

        # And it must stay cheap: the verified timestamp is written back, so
        # a DEM the sync client keeps touching does not pay a full hash on
        # every run for the rest of its life.
        import json
        from geolens.landscape.terrain import _routing_cache_dir
        from geolens.safety import Guard
        cache = _routing_cache_dir(Guard.from_env(), dem, cold.backend)
        meta = json.loads((cache / "manifest.json").read_text(encoding="utf-8"))
        assert meta["dem_mtime_ns"] == dem.stat().st_mtime_ns, (
            "the manifest still holds the old timestamp — the next run will "
            "hash the whole DEM again"
        )
    finally:
        os.environ["GEOLENS_OUT"] = str(OUT)


def test_an_in_place_edit_of_the_same_size_misses(tmp_path):
    """A stale cache here is hours of analysis against the wrong surface.

    Rewriting an uncompressed GeoTIFF in place leaves the byte count
    identical, so size cannot catch it and the timestamp alone must not be
    allowed to wave it through. This is the case that makes the content check
    mandatory rather than an optimisation.
    """
    import numpy as _np
    import rasterio
    from geolens.landscape import build_terrain

    data = tmp_path / "edited"
    data.mkdir()
    dem = data / "d.tif"
    with rasterio.open(str(DEM)) as src:
        z, prof = src.read(1), src.profile.copy()
    prof.update(compress=None, tiled=False)       # so the size cannot change
    with rasterio.open(str(dem), "w", **prof) as dst:
        dst.write(z, 1)
    size_before = dem.stat().st_size

    os.environ.pop("GEOLENS_CACHE", None)
    os.environ["GEOLENS_OUT"] = str(tmp_path / "out")
    (tmp_path / "out").mkdir()
    try:
        first = build_terrain(str(dem), stream_threshold_km2=1.0,
                              out_prefix="ie_1")
        before = _read(first, ("conditioned",))["conditioned"].copy()

        z[100:200, 100:200] += 50.0
        with rasterio.open(str(dem), "w", **prof) as dst:
            dst.write(z, 1)
        assert dem.stat().st_size == size_before, (
            "the fixture no longer exercises the same-size case"
        )

        second = build_terrain(str(dem), stream_threshold_km2=1.0,
                               out_prefix="ie_2")
        assert second.routing_cache["hit"] is False, (
            "the cache was served for a DEM whose elevations changed"
        )
        after = _read(second, ("conditioned",))["conditioned"]
        assert not _np.array_equal(before, after)
    finally:
        os.environ["GEOLENS_OUT"] = str(OUT)


def test_the_sampled_hash_is_never_trusted_on_its_own(tmp_path):
    """Why there is a full hash at all.

    Eight megabytes sampled from a 7.7 GB DEM is 0.1% of it. Burning a
    channel into a corner of that DEM changes bytes the sample never reads,
    and a sampled-hash-only check hits. Measured on a 200 MB file with 4 KB
    edited between the chunks: the sampled hash is unchanged, the full hash
    is not.
    """
    import json
    from geolens.landscape.terrain import (_dem_full_hash, _dem_sample_hash,
                                           _read_routing_manifest)

    big = tmp_path / "big.bin"
    n = 200 << 20
    with big.open("wb") as fh:
        fh.write(b"\0" * n)
    sample_before, full_before = _dem_sample_hash(big), _dem_full_hash(big)

    with big.open("r+b") as fh:
        fh.seek(int(n * 0.37) + 12345)
        fh.write(b"\xff" * 4096)

    assert _dem_sample_hash(big) == sample_before, (
        "the fixture no longer edits a region the sample misses"
    )
    assert _dem_full_hash(big) != full_before

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "manifest.json").write_text(json.dumps({
        "conditioned": str(big), "flow_dir": str(big),
        "accumulation": str(big), "dem_size": big.stat().st_size,
        "dem_mtime_ns": 0, "dem_sample_sha": sample_before,
        "dem_sha256": full_before}), encoding="utf-8")

    why: list[str] = []
    assert _read_routing_manifest(cache, big, why) is None, (
        "the sampled hash alone was allowed to validate the cache"
    )
    assert "contents have changed" in why[0]


def test_a_miss_says_why_in_the_result():
    """'I don't see how it picks up the cache' is a fair thing to say about a
    feature whose only evidence is a note() on stderr."""
    from geolens.landscape import build_terrain

    t = build_terrain(str(DEM), stream_threshold_km2=1.0, out_prefix="rc_why",
                      reuse_routing=False)
    rc = t.routing_cache
    assert rc["enabled"] is False
    assert "directory" in rc

    warm = build_terrain(str(DEM), stream_threshold_km2=1.0, out_prefix="rc_w2")
    assert warm.routing_cache["hit"] is True
    assert warm.routing_cache["reused"]


def test_a_no_reuse_run_does_not_touch_the_cache(tmp_path):
    """"Do not use the cache" must not mean "overwrite the cache".

    The backend wrote straight into the cache directory whatever
    ``reuse_routing`` said, so a no-reuse run at one threshold replaced
    streams.tif and hand.tif while the manifest went on describing the
    threshold they used to hold. The next run at that threshold got a clean
    hit and a stream network built at a different one — no error, no warning,
    nothing newer than the manifest to notice.

    Found only by running the suite from a clean unzip. In a working tree an
    older cache happened to line up and hide it.
    """
    import shutil as _sh

    import numpy as _np
    import rasterio
    from geolens.landscape import build_terrain

    data = tmp_path / "d"
    data.mkdir()
    dem = data / "d.tif"
    _sh.copy(str(DEM), str(dem))
    os.environ.pop("GEOLENS_CACHE", None)
    os.environ["GEOLENS_OUT"] = str(tmp_path / "o")
    (tmp_path / "o").mkdir()
    try:
        warm = build_terrain(str(dem), stream_threshold_km2=1.0,
                             out_prefix="nr_warm")
        with rasterio.open(warm.streams) as src:
            before = src.read(1).copy()
        cached_streams = Path(warm.streams)
        assert ".geolens_cache" in str(cached_streams)

        # A no-reuse run at a DIFFERENT threshold. Nothing it writes may land
        # in the cache.
        cold = build_terrain(str(dem), stream_threshold_km2=0.2,
                             out_prefix="nr_cold", reuse_routing=False)
        assert ".geolens_cache" not in str(cold.streams), (
            f"a reuse_routing=False run wrote into the cache: {cold.streams}"
        )
        with rasterio.open(cached_streams) as src:
            after = src.read(1)
        assert _np.array_equal(before, after), (
            "the cached stream network was overwritten by a no-reuse run"
        )

        # And the next warm run at the original threshold still gets the
        # network that threshold actually produces.
        again = build_terrain(str(dem), stream_threshold_km2=1.0,
                              out_prefix="nr_again")
        with rasterio.open(again.streams) as src:
            assert _np.array_equal(before, src.read(1))
    finally:
        os.environ["GEOLENS_OUT"] = str(OUT)


def test_the_default_stream_threshold_fits_the_dem(tmp_path):
    """Eagle Creek: 40 km2, and the fixed 1 km2 default kept only the main
    stems -- 13 segments on 2 branches, smallest draining 2 km2. Every
    tributary was missing from the network and so from every classification.
    """
    import rasterio
    from geolens.landscape import build_terrain

    os.environ["GEOLENS_OUT"] = str(tmp_path)
    try:
        t = build_terrain(str(DEM), stream_threshold_km2=None,
                          out_prefix="auto_thr", reuse_routing=False)
        with rasterio.open(str(DEM)) as src:
            u = t.units
            extent_km2 = src.width * src.height * u["cell_area_m2"] / 1e6
        want = min(1.0, max(extent_km2 / 500.0, 0.01))
        assert t.stream_threshold_km2 == pytest.approx(want), (
            t.stream_threshold_km2, want)
        assert t.stream_threshold_km2 <= 1.0, (
            "a large DEM must never get a coarser network than the old default"
        )
    finally:
        os.environ["GEOLENS_OUT"] = str(OUT)
