"""naip_tiles without the internet: the catalog is stubbed and the scenes
are local 4-band GeoTIFFs, so this checks the tiling, the newest-first
mosaic, skipping covered scenes, reuse of tiles on disk, and where things
are written. The live catalog is naip_selftest's job."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")


def _scene(path, left, top, w, h, red, nir, crs="EPSG:32613"):
    from rasterio.transform import from_origin

    arr = np.zeros((4, h, w), dtype="uint8")
    arr[0], arr[1], arr[2], arr[3] = red, 90, 60, nir
    with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=4,
                       dtype="uint8", crs=crs, nodata=0,
                       transform=from_origin(left, top, 0.6, 0.6)) as dst:
        dst.write(arr)
    return str(path)


def _item(iid, dt, path, left, top, w, h):
    import pyproj
    from shapely.geometry import box, mapping
    from shapely.ops import transform

    tf = pyproj.Transformer.from_crs("EPSG:32613", "EPSG:4326", always_xy=True)
    foot = transform(tf.transform, box(left, top - h * 0.6, left + w * 0.6, top))
    return {"id": iid, "geometry": mapping(foot),
            "properties": {"datetime": dt},
            "assets": {"image": {"href": path}}}


@pytest.fixture
def area(tmp_path, monkeypatch):
    from geolens import naip

    # Three scenes: a 2022 scene over the west half, a 2022 scene over the
    # east half, and a 2018 scene over everything, which newer imagery covers.
    s = tmp_path / "scenes"
    s.mkdir()
    west = _scene(s / "w.tif", 500000, 3701000, 900, 1700, red=40, nir=160)
    east = _scene(s / "e.tif", 500500, 3701000, 900, 1700, red=40, nir=120)
    old = _scene(s / "o.tif", 499900, 3701100, 2000, 2000, red=80, nir=80)
    items = [_item("m_west_2022", "2022-06-01T00:00:00Z", west, 500000, 3701000, 900, 1700),
             _item("m_east_2022", "2022-06-02T00:00:00Z", east, 500500, 3701000, 900, 1700),
             _item("m_all_2018", "2018-06-01T00:00:00Z", old, 499900, 3701100, 2000, 2000)]
    calls = {"search": 0}

    def fake_search(bbox, start=None, end=None, limit=100, timeout=60):
        calls["search"] += 1
        return sorted(items, key=lambda i: i["properties"]["datetime"],
                      reverse=True)

    monkeypatch.setattr(naip, "search_naip", fake_search)
    monkeypatch.setattr(naip, "sign", lambda href, timeout=30: href)
    # The area: a polygon inside the two 2022 scenes.
    import geopandas as gpd
    from shapely.geometry import box
    aoi = tmp_path / "aoi.gpkg"
    gpd.GeoDataFrame(geometry=[box(500100, 3700200, 500900, 3700900)],
                     crs="EPSG:32613").to_file(aoi, driver="GPKG")
    out = tmp_path / "my_naip_folder"
    monkeypatch.setenv("GEOLENS_OUT", str(out))
    return aoi, out, calls


def test_tiles_and_mosaics_land_in_the_chosen_folder(area):
    from geolens.naip import naip_tiles

    aoi, out, _ = area
    r = naip_tiles(str(aoi), resolution_m=1.0)
    assert r["n_used"] == 2, "the 2018 scene should be skipped as covered"
    assert Path(r["ndvi"]) == out / "ndvi_mosaic.tif"
    assert Path(r["rgb"]) == out / "true_colour.tif"
    assert len(list((out / "ndvi_tiles").glob("*_ndvi.tif"))) == 2
    assert len(list((out / "imagery_tiles").glob("*_rgbn.tif"))) == 2
    src = json.loads((out / "naip_sources.json").read_text())
    assert any(s.get("why") == "covered by newer imagery" for s in src["scenes"])
    with rasterio.open(r["ndvi"]) as m:
        a = m.read(1, masked=True)
        assert abs(m.res[0] - 1.0) < 1e-9
    # West scene NDVI (160-40)/200 = 0.6, east (120-40)/160 = 0.5; where they
    # overlap, the newer (east, listed first) wins.
    vals = sorted({round(float(v), 2) for v in np.unique(a.compressed())})
    assert vals == [0.5, 0.6], vals
    assert r["coverage"] > 0.95


def test_a_second_run_reads_the_tiles_from_disk(area, monkeypatch):
    from geolens import naip
    from geolens.naip import naip_tiles

    aoi, out, _ = area
    naip_tiles(str(aoi), resolution_m=1.0)

    def no_network(*a, **k):
        raise AssertionError("streamed again")
    monkeypatch.setattr(naip, "sign", no_network)
    r = naip_tiles(str(aoi), resolution_m=1.0)
    src = json.loads(Path(r["sources"]).read_text())
    assert all(s["how"] == "disk" for s in src["scenes"] if s.get("used"))


def test_lon_lat_area_is_refused(tmp_path, monkeypatch):
    import geopandas as gpd
    from shapely.geometry import box

    from geolens.naip import naip_tiles
    from geolens.safety import GeolensError

    monkeypatch.setenv("GEOLENS_OUT", str(tmp_path / "o"))
    aoi = tmp_path / "ll.gpkg"
    gpd.GeoDataFrame(geometry=[box(-105.7, 33.3, -105.6, 33.4)],
                     crs="EPSG:4326").to_file(aoi, driver="GPKG")
    with pytest.raises(GeolensError, match="projected"):
        naip_tiles(str(aoi))
