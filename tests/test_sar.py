"""Sentinel-1 statistics from OPERA RTC-S1, offline.

NASA's catalog and ASF's files are replaced by local stand-ins: bursts on a
30 m UTM grid in the OPERA layout (VV, VH, mask), two tracks, a woody half
whose VH is steady and a field half whose VH jumps from pass to pass. What
is checked is everything geolens does with them: the login file, the
catalog's paging and duplicates, mask handling, tiles per pass, per-track
and combined statistics, statistics per polygon, and reuse on a rerun.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")
gpd = pytest.importorskip("geopandas")

from geolens import sar  # noqa: E402
from geolens.safety import Guard, GeolensError  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CRS = "EPSG:32613"
AOI = (430000.0, 3690000.0, 433000.0, 3693000.0)       # 3 km, 100 x 100 cells
BURST = (429000.0, 3689000.0, 434010.0, 3694000.0)     # wider than the area


# -- the login file ---------------------------------------------------------------

def _runner():
    spec = importlib.util.spec_from_file_location(
        "geolens_runner_sar", ROOT / "arcgis_toolbox" / "geolens_runner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_login_is_saved_beside_other_logins(tmp_path):
    f = tmp_path / "_netrc"
    f.write_text("# mine\nmachine example.org login me password pw1\n"
                 "machine urs.earthdata.nasa.gov login old password stale\n")
    sar.save_login("sam_ncde", "n3w-Pa$$#word", path=f)
    assert sar.earthdata_login(f) == ("sam_ncde", "n3w-Pa$$#word")
    assert "machine example.org login me password pw1" in f.read_text()
    assert "stale" not in f.read_text()
    with pytest.raises(GeolensError, match="spaces or quotes"):
        sar.save_login("sam", "has space", path=f)


def test_the_toolbox_and_geolens_agree_on_the_file(tmp_path):
    # The toolbox writes the file (the password never passes through the
    # geolens subprocess); geolens reads it. And the other way round.
    runner = _runner()
    f = tmp_path / "_netrc"
    f.write_text("machine example.org login me password machine\n")
    runner.save_earthdata_login("sam", "pw-1", path=str(f))
    assert sar.earthdata_login(f) == ("sam", "pw-1")
    runner.save_earthdata_login("sam", "pw-2", path=str(f))
    assert sar.earthdata_login(f) == ("sam", "pw-2")
    assert "machine example.org login me password machine" in f.read_text()
    sar.save_login("sam", "pw-3", path=f)
    runner.save_earthdata_login("sam", "pw-4", path=str(f))
    assert sar.earthdata_login(f) == ("sam", "pw-4")
    assert f.read_text().count("urs.earthdata.nasa.gov") == 1
    with pytest.raises(ValueError):
        runner.save_earthdata_login("sam", "", path=str(f))


def test_no_login_says_how_to_save_one(tmp_path, monkeypatch):
    monkeypatch.setattr(sar, "netrc_path", lambda: tmp_path / "none")
    with pytest.raises(GeolensError, match="Save Earthdata login"):
        sar.sentinel1_stats(str(tmp_path / "x.gpkg"),
                            guard=Guard(out_dir=tmp_path / "out"))


def test_a_401_is_a_refused_login_and_a_date_is_not():
    assert sar._refused("HTTP response code: 401")
    assert sar._refused("401 Unauthorized")
    assert not sar._refused("'/vsicurl/https://x/OPERA_L2_RTC-S1_T071-151230-"
                            "IW3_20240401T125237Z_20240402T000000Z_S1A_30_"
                            "v1.0_VV.tif' not recognized")


# -- the catalog --------------------------------------------------------------------

def _entry(track, day, made="20240102T000000Z", burst="151230-IW3",
           base="https://datapool.asf.alaska.edu/RTC/OPERA-S1"):
    name = (f"OPERA_L2_RTC-S1_T{track:03d}-{burst}_{day}T125237Z_{made}_S1A_"
            f"30_v1.0")
    return {"title": name, "polygons": [["33.2 -105.8 33.2 -105.5 33.5 -105.5 "
                                         "33.5 -105.8 33.2 -105.8"]],
            "links": [{"href": f"{base}/{name}_VV.tif"},
                      {"href": f"{base}/{name}_VH.tif"},
                      {"href": f"{base}/{name}_mask.tif"},
                      {"href": f"s3://bucket/{name}_VV.tif"},
                      {"href": f"{base}/{name}.h5"}]}


def test_a_catalog_entry_is_read():
    g = sar.parse_granule(_entry(71, "20240401"))
    assert (g.track, g.burst, g.sensor, g.date) == (71, "151230-IW3", "S1A",
                                                    "20240401")
    assert set(g.urls) == {"VV", "VH", "mask"}
    assert all(u.startswith("https://") for u in g.urls.values())
    assert g.footprint.contains(__import__("shapely").geometry.Point(-105.6, 33.3))
    assert sar.parse_granule({"title": "something else"}) is None


def test_search_pages_and_keeps_the_newest_version(monkeypatch):
    pages = [[_entry(71, "20240101"), _entry(71, "20240113")],
             [_entry(71, "20240101", made="20240301T000000Z"),
              _entry(143, "20240105")],
             [_entry(143, "20240117")]]
    seen = []

    def page(params, after, timeout):
        seen.append(after)
        i = len(seen) - 1
        return pages[i], (f"cursor{i}" if i < len(pages) - 1 else None)

    monkeypatch.setattr(sar, "PAGE_SIZE", 2)
    monkeypatch.setattr(sar, "_cmr_page", page)
    got = sar.search((-105.8, 33.2, -105.5, 33.5), "2024-01-01", "2024-01-31")
    assert seen == [None, "cursor0", "cursor1"]
    assert [(g.track, g.date) for g in got] == [
        (71, "20240101"), (71, "20240113"), (143, "20240105"),
        (143, "20240117")]
    assert got[0].made == "20240301T000000"


# -- statistics from stand-in bursts ---------------------------------------------------

def _write(path, arr, nodata):
    from rasterio.transform import from_origin

    h, w = arr.shape
    with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=1,
                       dtype=arr.dtype, crs=CRS, nodata=nodata,
                       transform=from_origin(BURST[0], BURST[3], 30, 30)) as d:
        d.write(arr, 1)
    return str(path)


def _bursts(tmp_path, tracks=(41, 143), n=10):
    """Two tracks of n passes each. West of x = 431500 is woody (VH steady),
    east is a field (VH alternates 0.01 / 0.06). A band of layover runs along
    the north edge of the area."""
    w = int(round((BURST[2] - BURST[0]) / 30))
    h = int(round((BURST[3] - BURST[1]) / 30))
    xs = BURST[0] + 15 + 30 * np.arange(w)
    ys = BURST[3] - 15 - 30 * np.arange(h)
    east = np.broadcast_to(xs[None, :] > 431500, (h, w))
    mask = np.zeros((h, w), dtype="uint8")
    mask[(ys > 3692700)[:, None] & np.ones((1, w), bool)] = 2
    rng = np.random.default_rng(1)
    granules = []
    day0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for t, track in enumerate(tracks):
        for k in range(n):
            start = day0 + timedelta(days=12 * k + t)
            vh = np.where(east, 0.06 if k % 2 else 0.01,
                          0.05 * (1 + 0.03 * rng.standard_normal((h, w))))
            vh = vh.astype("float32")
            name = (f"OPERA_L2_RTC-S1_T{track:03d}-000001-IW1_"
                    f"{start:%Y%m%dT%H%M%S}Z_20250101T000000Z_S1A_30_v1.0")
            d = tmp_path / "asf"
            d.mkdir(exist_ok=True)
            urls = {"VV": _write(d / f"{name}_VV.tif", vh * 5, np.nan),
                    "VH": _write(d / f"{name}_VH.tif", vh, np.nan),
                    "mask": _write(d / f"{name}_mask.tif", mask, 255)}
            granules.append(sar.Granule(id=name, track=track, burst="000001-IW1",
                                        sensor="S1A", start=start,
                                        made="20250101T000000", urls=urls))
    return granules


def _polygons(tmp_path):
    from shapely.geometry import box

    p = tmp_path / "polys.gpkg"
    gpd.GeoDataFrame({"kind": ["woody", "field"]},
                     geometry=[box(430300, 3690300, 431200, 3692000),
                               box(431800, 3690300, 432700, 3692000)],
                     crs=CRS).to_file(p, driver="GPKG")
    a = tmp_path / "aoi.gpkg"
    gpd.GeoDataFrame(geometry=[box(*AOI)], crs=CRS).to_file(a, driver="GPKG")
    return str(a), str(p)


def test_statistics_tell_a_steady_canopy_from_a_field(tmp_path, monkeypatch):
    login = tmp_path / "_netrc"
    sar.save_login("someone", "secret", path=login)
    monkeypatch.setattr(sar, "netrc_path", lambda: login)
    granules = _bursts(tmp_path)
    monkeypatch.setattr(sar, "search", lambda *a, **k: list(granules))
    aoi, polys = _polygons(tmp_path)
    guard = Guard(out_dir=tmp_path / "out")

    res = sar.sentinel1_stats(aoi, "2024-01-01", "2024-12-31", polygons=polys,
                              min_passes=8, guard=guard)
    assert sorted(res["track_stats"]) == ["T041", "T143"]
    assert res["passes"] == {41: 10, 143: 10} and res["reused"] == 0
    assert len(list((tmp_path / "out" / "sar_tiles" / "T041").glob("*.tif"))) == 10

    with rasterio.open(res["stats"]) as src:
        assert [src.descriptions[i] for i in range(5)] == sar.STAT_BANDS
        assert src.res == (30.0, 30.0) and src.crs.to_epsg() == 32613
        st = src.read()
        woody = st[:, 50, 20]
        field = st[:, 50, 80]
        layover = st[:, 2, 50]
    assert woody[3] < 0.1 < 0.5 < field[3]                 # CV of VH
    assert woody[0] == pytest.approx(10 * np.log10(0.05), abs=0.2)
    assert woody[2] == pytest.approx(-10 * np.log10(5), abs=0.01)   # VH/VV
    assert woody[4] == 20                                  # both tracks
    assert np.isnan(layover[3]) and layover[4] == 0        # masked out

    z = gpd.read_file(res["zonal"]).set_index("kind")
    assert z.loc["woody", "sar_vh_cv"] < 0.1 < 0.5 < z.loc["field", "sar_vh_cv"]
    assert z.loc["woody", "sar_cells"] > 0

    # A rerun reads nothing remote: every pass is a tile on disk.
    def no_reading(*a, **k):
        raise AssertionError("read a burst again")

    monkeypatch.setattr(sar._Reader, "read", no_reading)
    again = sar.sentinel1_stats(aoi, "2024-01-01", "2024-12-31", guard=guard)
    assert again["reused"] == 20 and again["zonal"] is None


def test_a_track_with_too_few_passes_is_left_out(tmp_path, monkeypatch):
    login = tmp_path / "_netrc"
    sar.save_login("someone", "secret", path=login)
    monkeypatch.setattr(sar, "netrc_path", lambda: login)
    granules = _bursts(tmp_path, tracks=(41,), n=10) + \
        _bursts(tmp_path, tracks=(143,), n=3)
    monkeypatch.setattr(sar, "search", lambda *a, **k: list(granules))
    aoi, _ = _polygons(tmp_path)
    res = sar.sentinel1_stats(aoi, "2024-01-01", "2024-12-31",
                              guard=Guard(out_dir=tmp_path / "out"))
    assert list(res["track_stats"]) == ["T041"]
    assert any("T143" in w and "3 passes" in w for w in res["warnings"])


def test_streaming_that_cannot_get_through_falls_back_to_downloads(
        tmp_path, monkeypatch):
    granules = _bursts(tmp_path, tracks=(41,), n=1)
    local = granules[0].urls["VH"]
    reader = sar._Reader(("u", "p"), tmp_path / "dl")
    calls = []
    real = sar.read_onto

    def read_onto(path, *a, **k):
        calls.append(path)
        if str(path).startswith("https://"):
            raise rasterio.errors.RasterioIOError(
                "CURL error: SSL certificate problem: self-signed certificate")
        return real(path, *a, **k)

    monkeypatch.setattr(sar, "read_onto", read_onto)
    monkeypatch.setattr(reader, "_download", lambda url: Path(local))
    grid = sar.grid_over(AOI, CRS, 30.0)
    arr = reader.read("https://example.org/x_VH.tif", grid,
                      (0, 0, grid.height, grid.width), "bilinear")
    assert reader.mode == "download" and np.isfinite(arr).all()

    def refused(path, *a, **k):
        raise rasterio.errors.RasterioIOError("HTTP response code: 401")

    monkeypatch.setattr(sar, "read_onto", refused)
    with pytest.raises(GeolensError, match="refused the saved login"):
        sar._Reader(("u", "p"), tmp_path / "dl").read(
            "https://example.org/y_VH.tif", grid,
            (0, 0, grid.height, grid.width), "bilinear")
