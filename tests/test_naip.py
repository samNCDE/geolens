"""NAIP streaming, NDVI and caching.

The network path cannot run here, so the tests cover what is below it: the NDVI
arithmetic, band order, the newest-first mosaic, the cache, and the vintage
bookkeeping. ``naip_selftest`` covers the live service.

The band-order test is the one worth keeping. If NAIP's band order ever changed,
NDVI would keep computing and silently mean nothing — no error, just a wrong
answer that looks like vegetation.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "naip_test_out"

W = H = 120
CELL = 1.0
CRS = "EPSG:32612"
TRANSFORM = from_origin(500000, 4000000, CELL, CELL)


@pytest.fixture(autouse=True)
def _env(tmp_path):
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ["GEOLENS_OUT"] = str(OUT)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ.pop("GEOLENS_NAIP_CACHE", None)
    yield
    os.environ.pop("GEOLENS_NAIP_CACHE", None)


def _template():
    p = OUT / "dem.tif"
    prof = {"driver": "GTiff", "width": W, "height": H, "count": 1,
            "dtype": "float32", "crs": CRS, "transform": TRANSFORM,
            "nodata": -9999.0}
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(np.zeros((H, W), "float32"), 1)
    return str(p)


def _scene(path, red, green, blue, nir):
    """A 4-band NAIP-like COG on the template grid."""
    prof = {"driver": "GTiff", "width": W, "height": H, "count": 4,
            "dtype": "uint8", "crs": CRS, "transform": TRANSFORM, "nodata": 0}
    with rasterio.open(str(path), "w", **prof) as dst:
        for i, b in enumerate((red, green, blue, nir), start=1):
            dst.write(np.asarray(b, dtype="uint8"), i)
    return str(path)


# -- NDVI arithmetic ----------------------------------------------------------

def test_ndvi_matches_the_definition():
    from geolens.naip import _ndvi

    red = np.array([[50, 100, 200]], dtype="uint8")
    nir = np.array([[200, 100, 50]], dtype="uint8")
    got = _ndvi(red, nir)
    expect = np.array([[150 / 250, 0.0, -150 / 250]], dtype="float32")
    assert np.allclose(got, expect, atol=1e-6)


def test_zero_over_zero_is_nan_not_a_number():
    """NAIP is 8-bit; the black collar around a reprojected scene is 0/0.

    Letting that become a value puts fake NDVI in the shadow and the collar,
    and it composites as if it were data.
    """
    from geolens.naip import _ndvi

    out = _ndvi(np.zeros((2, 2), "uint8"), np.zeros((2, 2), "uint8"))
    assert np.isnan(out).all()


def test_ndvi_is_clipped_to_the_valid_range():
    from geolens.naip import _ndvi

    out = _ndvi(np.array([[0]], "uint8"), np.array([[255]], "uint8"))
    assert -1.0 <= float(out[0, 0]) <= 1.0


def test_band_order_is_red_first_nir_fourth():
    """The assumption the whole module rests on.

    NAIP is R, G, B, NIR. If that ever changes, NDVI keeps computing and means
    nothing — this pins the constants so the failure is a test, not a result.
    """
    from geolens import naip

    assert (naip.BAND_RED, naip.BAND_GREEN, naip.BAND_BLUE, naip.BAND_NIR) == \
        (1, 2, 3, 4)


# -- streaming and mosaicking -------------------------------------------------

def _patch_search(monkeypatch, items):
    from geolens import naip
    monkeypatch.setattr(naip, "search_naip",
                        lambda *a, **k: items)
    monkeypatch.setattr(naip, "sign", lambda href, timeout=30: href)


def _item(idx, path, dt):
    return {"id": f"item{idx}", "properties": {"datetime": dt},
            "assets": {"image": {"href": path}}}


def test_builds_ndvi_from_a_scene(monkeypatch):
    from geolens.naip import naip_ndvi

    tpl = _template()
    veg = np.full((H, W), 40, "uint8")          # low red
    nir = np.full((H, W), 200, "uint8")         # high NIR -> green
    scene = _scene(OUT / "s1.tif", veg, veg, veg, nir)
    _patch_search(monkeypatch, [_item(1, scene, "2021-07-01T00:00:00Z")])

    r = naip_ndvi(tpl, out_prefix="n1")
    assert r.n_used == 1
    assert r.years == [2021]
    with rasterio.open(r.ndvi) as src:
        nd = src.read(1)
    assert nd.mean() == pytest.approx((200 - 40) / 240, abs=0.02)
    assert r.coverage > 0.99


def test_mosaic_is_newest_first_not_averaged(monkeypatch):
    """Averaging NAIP vintages makes an image of no particular date.

    Each pixel must come from one flight, and the newer flight wins.
    """
    from geolens.naip import naip_ndvi

    tpl = _template()
    old = _scene(OUT / "old.tif", np.full((H, W), 200, "uint8"),
                 np.zeros((H, W), "uint8"), np.zeros((H, W), "uint8"),
                 np.full((H, W), 50, "uint8"))      # brown: NDVI < 0
    new = _scene(OUT / "new.tif", np.full((H, W), 40, "uint8"),
                 np.zeros((H, W), "uint8"), np.zeros((H, W), "uint8"),
                 np.full((H, W), 200, "uint8"))     # green: NDVI > 0

    # search_naip sorts newest-first; emulate that ordering here.
    _patch_search(monkeypatch, [_item(2, new, "2023-06-01T00:00:00Z"),
                                _item(1, old, "2018-06-01T00:00:00Z")])
    r = naip_ndvi(tpl, out_prefix="n2")
    with rasterio.open(r.ndvi) as src:
        nd = src.read(1)
    assert nd.mean() > 0.5, "the older scene overwrote the newer one"
    # An average of the two would land near zero.
    assert abs(nd.mean()) > 0.3


def test_mixed_vintages_are_reported(monkeypatch):
    from geolens.naip import naip_ndvi

    tpl = _template()
    a = np.full((H, W), 40, "uint8")
    n = np.full((H, W), 200, "uint8")
    # Real NAIP nodata is zero in ALL bands — the black collar of a reprojected
    # scene. Zeroing only NIR leaves red valid, so the pixel still reads as
    # data and the older scene never gets a chance to fill it.
    a_half = a.copy(); a_half[:, W // 2:] = 0
    n_half = n.copy(); n_half[:, W // 2:] = 0

    s_new = _scene(OUT / "half.tif", a_half, a_half, a_half, n_half)
    s_old = _scene(OUT / "full.tif", a, a, a, n)
    _patch_search(monkeypatch, [_item(2, s_new, "2023-06-01T00:00:00Z"),
                                _item(1, s_old, "2019-06-01T00:00:00Z")])
    r = naip_ndvi(tpl, out_prefix="n3")
    assert len(r.years) == 2
    assert any("vintages" in w for w in r.warnings)


def test_partial_coverage_is_flagged(monkeypatch):
    from geolens.naip import naip_ndvi

    tpl = _template()
    red = np.full((H, W), 40, "uint8")
    nir = np.zeros((H, W), "uint8")
    nir[:, : W // 3] = 200                      # a third of the extent
    red[:, W // 3:] = 0                         # the rest reads as nodata
    scene = _scene(OUT / "partial.tif", red, red, red, nir)
    _patch_search(monkeypatch, [_item(1, scene, "2021-07-01T00:00:00Z")])

    r = naip_ndvi(tpl, out_prefix="n4")
    assert r.coverage < 0.5
    assert any("covers" in w for w in r.warnings)


# -- the cache ----------------------------------------------------------------

def test_windows_are_cached_and_reused(monkeypatch):
    """Sam's requirement: keep the imagery for truthing.

    What is cached is the CLIPPED WINDOW, not the 500 MB source quad — the
    window is what is reusable, is already on the project grid, and is two
    orders of magnitude smaller.
    """
    from geolens import naip
    from geolens.naip import naip_ndvi

    tpl = _template()
    a = np.full((H, W), 40, "uint8")
    n = np.full((H, W), 200, "uint8")
    scene = _scene(OUT / "c.tif", a, a, a, n)
    _patch_search(monkeypatch, [_item(7, scene, "2022-06-01T00:00:00Z")])

    first = naip_ndvi(tpl, out_prefix="n5")
    assert first.cached_files, "nothing was cached"
    assert Path(first.cached_files[0]).exists()
    assert "2022" in Path(first.cached_files[0]).name, (
        "the vintage must be in the filename — a cache that hides the year "
        "invites comparing flights years apart and calling it change"
    )

    # Second run must not stream again.
    calls = {"n": 0}
    real = naip._read_window_onto

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(naip, "_read_window_onto", counting)
    second = naip_ndvi(tpl, out_prefix="n6")
    assert calls["n"] == 0, "the cache was ignored and the scene re-streamed"
    assert second.n_used == 1


def test_cache_dir_is_overridable(tmp_path):
    from geolens.naip import naip_cache_dir

    os.environ["GEOLENS_NAIP_CACHE"] = str(tmp_path / "my_naip")
    assert naip_cache_dir() == (tmp_path / "my_naip")
    assert (tmp_path / "my_naip").is_dir()


def test_use_cache_false_skips_it(monkeypatch):
    from geolens.naip import naip_ndvi

    tpl = _template()
    a = np.full((H, W), 40, "uint8")
    n = np.full((H, W), 200, "uint8")
    scene = _scene(OUT / "nc.tif", a, a, a, n)
    _patch_search(monkeypatch, [_item(9, scene, "2022-06-01T00:00:00Z")])

    r = naip_ndvi(tpl, use_cache=False, out_prefix="n7")
    assert r.cached_files == []


# -- failure modes ------------------------------------------------------------

def test_one_bad_scene_does_not_stop_the_run(monkeypatch):
    from geolens.naip import naip_ndvi

    tpl = _template()
    a = np.full((H, W), 40, "uint8")
    n = np.full((H, W), 200, "uint8")
    good = _scene(OUT / "good.tif", a, a, a, n)
    _patch_search(monkeypatch, [
        _item(1, "/vsicurl/http://nowhere.invalid/missing.tif",
              "2023-06-01T00:00:00Z"),
        _item(2, good, "2021-06-01T00:00:00Z"),
    ])
    r = naip_ndvi(tpl, out_prefix="n8")
    assert r.n_used == 1
    assert any("skipped" in w for w in r.warnings)


def test_no_scenes_is_not_a_crash(monkeypatch):
    from geolens.naip import naip_ndvi

    tpl = _template()
    _patch_search(monkeypatch, [])
    r = naip_ndvi(tpl, out_prefix="n9")
    assert r.n_used == 0
    assert r.ndvi is None
    assert any("No NAIP scenes" in w for w in r.warnings)


def test_a_three_band_asset_is_a_clear_error(monkeypatch):
    """RGB-only NAIP exists for older years. NDVI is impossible; say so."""
    from geolens.naip import _read_window_onto, BAND_NIR
    from geolens.safety import GeolensError

    tpl = _template()
    p = OUT / "rgb_only.tif"
    prof = {"driver": "GTiff", "width": W, "height": H, "count": 3,
            "dtype": "uint8", "crs": CRS, "transform": TRANSFORM, "nodata": 0}
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(np.zeros((3, H, W), "uint8"))

    with rasterio.open(tpl) as t:
        grid = ((t.height, t.width), t.transform, t.crs)

    with pytest.raises(GeolensError, match="4-band"):
        _read_window_onto(str(p), [1, 2, 3, BAND_NIR], *grid)


# -- the point of it all ------------------------------------------------------

def test_naip_ndvi_feeds_the_riparian_detector(monkeypatch):
    """The reason NAIP matters: 1 m NDVI into the riparian band detector."""
    from geolens.naip import naip_ndvi
    from geolens.riparian import riparian_band

    tpl = _template()
    red = np.full((H, W), 120, "uint8")
    nir = np.full((H, W), 130, "uint8")          # dry upland
    red[55:65, :] = 30
    nir[55:65, :] = 220                          # a green corridor
    scene = _scene(OUT / "rip.tif", red, red, red, nir)
    _patch_search(monkeypatch, [_item(1, scene, "2022-06-01T00:00:00Z")])

    n = naip_ndvi(tpl, out_prefix="n10")
    r = riparian_band(n.ndvi, background_window_m=60.0, min_excess=0.15,
                      max_hand_m=None, out_prefix="n10_rip")
    assert r.riparian_km2 > 0, "the corridor was not picked up from NAIP NDVI"


# -- resolution: the whole reason to stream NAIP ------------------------------
#
# NDVI is built on a grid of its own, not the DEM's. A 10 m DEM averaging a
# hundred NAIP pixels into one cell defeats the module: a 20 m riparian corridor
# is two cells at 10 m and twenty at 1 m, and two cells is an edge effect.

def _coarse_template(cell=10.0, n=12):
    p = OUT / f"dem_{cell:g}m.tif"
    prof = {"driver": "GTiff", "width": n, "height": n, "count": 1,
            "dtype": "float32", "crs": CRS, "nodata": -9999.0,
            "transform": from_origin(500000, 4000000, cell, cell)}
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(np.zeros((n, n), "float32"), 1)
    return str(p)


def test_ndvi_is_built_at_naip_resolution_not_the_dems(monkeypatch):
    from geolens.naip import naip_ndvi

    tpl = _coarse_template()                     # 12 x 12 at 10 m = 120 m square
    veg = np.full((H, W), 40, "uint8")
    nir = np.full((H, W), 200, "uint8")
    scene = _scene(OUT / "sr1.tif", veg, veg, veg, nir)
    _patch_search(monkeypatch, [_item(1, scene, "2021-07-01T00:00:00Z")])

    r = naip_ndvi(tpl, out_prefix="nr1")         # resolution_m defaults to 1.0
    assert r.cell_size_m == 1.0
    with rasterio.open(r.ndvi) as src:
        assert (src.height, src.width) == (120, 120), (
            "NDVI was built on the DEM grid, discarding 99% of the NAIP pixels"
        )
        assert abs(src.transform.a - 1.0) < 1e-9


def test_resolution_none_uses_the_aoi_grid_and_says_what_it_cost(monkeypatch):
    """Sometimes you do want the DEM's grid. It must not be silent."""
    from geolens.naip import naip_ndvi

    tpl = _coarse_template()
    veg = np.full((H, W), 40, "uint8")
    nir = np.full((H, W), 200, "uint8")
    scene = _scene(OUT / "sr2.tif", veg, veg, veg, nir)
    _patch_search(monkeypatch, [_item(1, scene, "2021-07-01T00:00:00Z")])

    r = naip_ndvi(tpl, resolution_m=None, out_prefix="nr2")
    assert r.cell_size_m == 10.0
    with rasterio.open(r.ndvi) as src:
        assert (src.height, src.width) == (12, 12)
    assert any("NAIP is 0.6-1 m" in w for w in r.warnings), (
        "a 10 m NDVI from 1 m imagery passed without comment"
    )


def test_the_cache_key_carries_the_resolution(monkeypatch):
    """Otherwise a 10 m window cached yesterday answers a 1 m request today.

    That failure is invisible: the output is a 1 m grid, the right shape, and it
    carries 10 m information.
    """
    from geolens.naip import naip_ndvi

    tpl = _coarse_template()
    veg = np.full((H, W), 40, "uint8")
    nir = np.full((H, W), 200, "uint8")
    scene = _scene(OUT / "sr3.tif", veg, veg, veg, nir)
    _patch_search(monkeypatch, [_item(1, scene, "2021-07-01T00:00:00Z")])

    coarse = naip_ndvi(tpl, resolution_m=None, out_prefix="nr3")
    fine = naip_ndvi(tpl, resolution_m=1.0, out_prefix="nr4")

    names = {Path(c).name for c in coarse.cached_files} | \
            {Path(c).name for c in fine.cached_files}
    assert len(names) == 2, f"both resolutions shared one cache entry: {names}"
    with rasterio.open(fine.ndvi) as src:
        assert (src.height, src.width) == (120, 120)


def test_an_impossible_grid_is_refused_before_allocating(monkeypatch):
    """A county at 1 m is hundreds of gigabytes. Say so; do not get OOM-killed."""
    from geolens.naip import naip_ndvi
    from geolens.safety import GeolensError

    p = OUT / "huge.tif"
    prof = {"driver": "GTiff", "width": 100, "height": 100, "count": 1,
            "dtype": "float32", "crs": CRS, "nodata": -9999.0,
            "transform": from_origin(500000, 4000000, 3000.0, 3000.0)}
    with rasterio.open(str(p), "w", **prof) as dst:
        dst.write(np.zeros((100, 100), "float32"), 1)

    _patch_search(monkeypatch, [])
    with pytest.raises(GeolensError, match="million cells"):
        naip_ndvi(str(p), resolution_m=1.0, out_prefix="nr5")


# -- what the first real run found -------------------------------------------
#
# Eagle Creek, v20, EPSG:6529. The notebook printed "NAIP fetch failed" and
# suggested checking the network. The network was never reached: resolution_m
# was divided into an extent in US survey feet, so a "1 m" grid was built at
# 1 ft, 434 million cells, and the size guard raised before any request. And
# nothing was written to say so.

FT_US = 1200.0 / 3937.0


def _template_ft(path, size_m=800.0, cell_ft=2.0):
    """A DEM on EPSG:6529 — the grid this package is actually used on."""
    n = int(round(size_m / (cell_ft * FT_US)))
    prof = {"driver": "GTiff", "width": n, "height": n, "count": 1,
            "dtype": "float32", "crs": "EPSG:6529",
            "transform": from_origin(1_600_000.0, 1_400_000.0, cell_ft, cell_ft),
            "nodata": -9999.0}
    with rasterio.open(str(path), "w", **prof) as dst:
        dst.write(np.zeros((n, n), "float32"), 1)
    return str(path)


def _scene_like(path, template, red=40, nir=200):
    with rasterio.open(template) as t:
        prof = t.profile.copy()
    prof.update(count=4, dtype="uint8", nodata=0)
    with rasterio.open(str(path), "w", **prof) as dst:
        for i, v in enumerate((red, 60, 50, nir), start=1):
            dst.write(np.full((prof["height"], prof["width"]), v, "uint8"), i)
    return str(path)


def test_a_one_metre_grid_on_a_foot_crs_is_one_metre(monkeypatch):
    """The Eagle Creek failure, as a regression."""
    from geolens.naip import naip_ndvi

    tpl = _template_ft(OUT / "dem_ft.tif")
    scene = _scene_like(OUT / "s_ft.tif", tpl)
    _patch_search(monkeypatch, [_item(1, scene, "2022-06-01T00:00:00Z")])

    r = naip_ndvi(tpl, resolution_m=1.0, out_prefix="nft")
    assert r.cell_size_m == pytest.approx(1.0, rel=1e-6), (
        f"a 1 m request on a foot grid built {r.cell_size_m:.4g} m cells"
    )
    with rasterio.open(r.ndvi) as src:
        cell_crs = abs(src.transform.a)
        n_cells = src.width * src.height
    assert cell_crs * FT_US == pytest.approx(1.0, rel=1e-6)
    # 800 m square at 1 m is 640,000 cells. At 1 ft it would be 6.9 million.
    assert n_cells == pytest.approx(640_000, rel=0.01), n_cells


def test_the_imagery_itself_is_kept_with_all_four_bands(monkeypatch):
    """NDVI and RGB are products. The imagery is the reference."""
    from geolens.naip import naip_ndvi

    tpl = _template()
    scene = _scene(OUT / "s4.tif", np.full((H, W), 30, "uint8"),
                   np.full((H, W), 70, "uint8"), np.full((H, W), 90, "uint8"),
                   np.full((H, W), 210, "uint8"))
    _patch_search(monkeypatch, [_item(4, scene, "2021-07-01T00:00:00Z")])

    r = naip_ndvi(tpl, out_prefix="n4")
    assert r.rgbn and Path(r.rgbn).exists(), "the 4-band imagery was not kept"
    with rasterio.open(r.rgbn) as src:
        assert src.count == 4
        assert src.descriptions == ("red", "green", "blue", "nir")
        means = [float(src.read(i).mean()) for i in (1, 2, 3, 4)]
    assert means == pytest.approx([30, 70, 90, 210], abs=1.0)


def test_every_run_leaves_a_provenance_record(monkeypatch):
    """The next "the fetcher is broken" should come with a file that says
    which part broke."""
    import json

    from geolens.naip import naip_ndvi

    tpl = _template()
    good = _scene(OUT / "sg.tif", *[np.full((H, W), v, "uint8")
                                    for v in (40, 60, 50, 200)])
    _patch_search(monkeypatch, [
        _item(1, str(OUT / "does_not_exist.tif"), "2023-06-01T00:00:00Z"),
        _item(2, good, "2021-07-01T00:00:00Z"),
    ])
    r = naip_ndvi(tpl, out_prefix="nprov")
    doc = json.loads(Path(r.sources).read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in doc["scenes"]}
    assert by_id["item2"]["used"] is True
    assert by_id["item1"]["used"] is False
    assert by_id["item1"]["error"], "a failed scene's error was not recorded"
    assert "?" not in (by_id["item2"].get("href") or ""), (
        "a SAS token was written into the provenance file"
    )


def test_a_failed_catalog_search_still_leaves_a_record(monkeypatch):
    import json

    from geolens import naip
    from geolens.naip import naip_ndvi

    def boom(*a, **k):
        raise naip.GeolensError("Could not reach the Planetary Computer")
    monkeypatch.setattr(naip, "search_naip", boom)

    tpl = _template()
    with pytest.raises(naip.GeolensError):
        naip_ndvi(tpl, out_prefix="nfail")
    rec = OUT / "nfail_dir" / "naip_sources.json"
    assert rec.exists(), "a failed search left nothing on disk"
    doc = json.loads(rec.read_text(encoding="utf-8"))
    assert doc["stage"] == "catalog search"
    assert "Planetary Computer" in doc["error"]


def test_when_streaming_fails_the_tile_is_downloaded_and_kept(monkeypatch):
    """GDAL's libcurl and Python's HTTPS trust different certificate stores.

    Behind a TLS-inspecting proxy — the normal case on a work Windows machine
    — Python can reach the catalog and GDAL cannot read a tile. The fallback
    downloads with Python's stack, keeps the tile for later, and reads it
    locally.
    """
    import shutil

    from geolens import naip
    from geolens.naip import naip_ndvi

    tpl = _template()
    real = _scene(OUT / "remote_src.tif", *[np.full((H, W), v, "uint8")
                                            for v in (40, 60, 50, 200)])
    remote = "https://example.invalid/naip/tile.tif"
    _patch_search(monkeypatch, [_item(5, remote, "2022-06-01T00:00:00Z")])

    real_read = naip._read_window_onto

    def gdal_cannot_stream(href, *a, **k):
        if str(href).startswith("http"):
            raise RuntimeError("CURL error: SSL certificate problem: "
                               "unable to get local issuer certificate")
        return real_read(href, *a, **k)

    downloads = []

    def python_can_download(url, dest, timeout=120, chunk=1 << 20):
        downloads.append(url)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(real, dest)
        return Path(dest)

    monkeypatch.setattr(naip, "_read_window_onto", gdal_cannot_stream)
    monkeypatch.setattr(naip, "_download", python_can_download)

    r = naip_ndvi(tpl, out_prefix="nfall", use_cache=False)
    assert downloads == [remote]
    assert r.n_used == 1, r.warnings
    assert r.tiles and Path(r.tiles[0]).exists(), "the tile was not kept"
    with rasterio.open(r.ndvi) as src:
        assert src.read(1).mean() == pytest.approx((200 - 40) / 240, abs=0.02)


def test_the_cache_lives_beside_the_dem(monkeypatch):
    """Under the output folder it was thrown away by every new OUT_DIR, and
    the output folder changes every release."""
    from geolens.naip import naip_ndvi

    tpl = _template()
    scene = _scene(OUT / "sc.tif", *[np.full((H, W), v, "uint8")
                                     for v in (40, 60, 50, 200)])
    _patch_search(monkeypatch, [_item(6, scene, "2022-06-01T00:00:00Z")])
    r = naip_ndvi(tpl, out_prefix="ncache")
    assert Path(r.cache_dir) == Path(tpl).parent / ".geolens_cache" / "naip"
