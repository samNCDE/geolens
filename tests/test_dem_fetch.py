"""fetch_dem without the internet: The National Map search is stubbed and the
"remote" tiles are local GeoTIFFs. Checks the newest-lidar-first mosaic, the
1/3 arc-second gap fill, tiles and mosaic in the chosen folder, the output
CRS, and elevations converted to feet on a foot CRS."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")


def _tile(path, crs, left, top, res, w, h, value):
    from rasterio.transform import from_origin

    with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=1,
                       dtype="float32", crs=crs, nodata=-999999.0,
                       transform=from_origin(left, top, res, res)) as d:
        d.write(np.full((1, h, w), value, dtype="float32"))
    return str(path)


def _wgs_box(crs, left, top, right, bottom):
    import pyproj

    tf = pyproj.Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    xs, ys = tf.transform([left, right, left, right], [top, top, bottom, bottom])
    return {"minX": min(xs), "maxX": max(xs), "minY": min(ys), "maxY": max(ys)}


@pytest.fixture
def world(tmp_path, monkeypatch):
    from geolens import dem_fetch

    t = tmp_path / "remote"
    t.mkdir()
    utm = "EPSG:26913"
    # Two overlapping 1 m lidar tiles: 2020 (2000 m) and 2014 (1000 m), and a
    # coarse 1/3 arc-second stand-in (in lon/lat, 1500 m) for the gap.
    new = _tile(t / "USGS_1M_new.tif", utm, 400000, 3700000, 1.0, 1200, 1000, 2000.0)
    old = _tile(t / "USGS_1M_old.tif", utm, 400600, 3700000, 1.0, 1200, 1000, 1000.0)
    ned = _tile(t / "USGS_13_n34w106.tif", "EPSG:4269", -106.2, 33.6, 0.0001,
                3000, 3000, 1500.0)
    items = [
        {"title": "USGS one meter NEW 2020", "downloadURL": new,
         "publicationDate": "2021-01-01",
         "boundingBox": _wgs_box(utm, 400000, 3700000, 401200, 3699000)},
        {"title": "USGS one meter OLD 2014", "downloadURL": old,
         "publicationDate": "2016-01-01",
         "boundingBox": _wgs_box(utm, 400600, 3700000, 401800, 3699000)},
    ]
    monkeypatch.setattr(dem_fetch, "search_1m",
                        lambda bbox, timeout=60: list(items))
    monkeypatch.setattr(dem_fetch, "ned13_tiles", lambda bbox: [{
        "title": "USGS 1/3 arc-second n34w106", "downloadURL": ned,
        "boundingBox": {"minX": -106.2, "maxX": -105.9, "minY": 33.3,
                        "maxY": 33.6}, "source": "1/3 arc-second"}])
    import geopandas as gpd
    from shapely.geometry import box
    aoi = tmp_path / "aoi.gpkg"
    # 400100..402000 x 3699100..3699900: most on lidar, the east end is not.
    gpd.GeoDataFrame(geometry=[box(400100, 3699100, 402000, 3699900)],
                     crs=utm).to_file(aoi, driver="GPKG")
    out = tmp_path / "my_dem_folder"
    monkeypatch.setenv("GEOLENS_OUT", str(out))
    return aoi, out


def test_best_mosaics_newest_lidar_then_fills_the_gap(world):
    from geolens.dem_fetch import fetch_dem

    aoi, out = world
    r = fetch_dem(str(aoi), source="best", resolution_m=2.0)
    assert Path(r["dem"]) == out / "dem_mosaic.tif"
    assert len(list((out / "dem_tiles").glob("*.tif"))) == 3
    with rasterio.open(r["dem"]) as d:
        a = d.read(1, masked=True)
        assert "26913" in d.crs.to_string()            # the lidar's own UTM
        assert abs(d.res[0] - 2.0) < 1e-9
        # Columns: 400100-400600 only the new tile (2000); 400600-401200 both,
        # the NEWER wins (2000); 401200-401800 only the old one (1000); east
        # of 401800 no lidar, so the 1/3 arc-second fill (1500).
        row = a[200]
        assert row[10] == 2000.0 and row[400] == 2000.0
        assert row[700] == 1000.0 and row[900] == 1500.0
    assert 0.6 < r["lidar_fraction"] < 0.95
    assert any("no 3DEP 1 m lidar" in w for w in r["warnings"])
    src = json.loads(Path(r["sources"]).read_text())
    assert src["z_unit"] == "metre"


def test_a_foot_crs_gets_feet_of_elevation(world):
    from geolens.dem_fetch import fetch_dem

    aoi, out = world
    r = fetch_dem(str(aoi), source="1m", resolution_m=2.0, crs="EPSG:6529")
    with rasterio.open(r["dem"]) as d:
        a = d.read(1, masked=True)
        assert "6529" in d.crs.to_string()
    assert abs(float(a.max()) - 2000.0 / 0.3048006096) < 1.0
    assert "foot" in r["z_unit"].lower()


def test_tiles_on_disk_are_reused(world, monkeypatch):
    from geolens import dem_fetch
    from geolens.dem_fetch import fetch_dem

    aoi, out = world
    fetch_dem(str(aoi), source="1m", resolution_m=2.0)
    r = fetch_dem(str(aoi), source="1m", resolution_m=2.0)
    src = json.loads(Path(r["sources"]).read_text())
    assert all(t["how"] == "disk" for t in src["tiles"] if t.get("used"))


def test_ned13_tile_names():
    from geolens.dem_fetch import ned13_tiles

    names = sorted(t["title"].split()[-1] for t in
                   ned13_tiles((-105.8, 33.2, -104.9, 33.7)))
    assert names == ["n34w105", "n34w106"]


def _half_tile(path, crs, left, top, res, w, h, value, data_cols):
    """A tile whose footprint is all of it but whose data is only its first
    ``data_cols`` columns (a lidar project's edge inside a 10 km square)."""
    from rasterio.transform import from_origin

    a = np.full((1, h, w), -999999.0, dtype="float32")
    a[:, :, :data_cols] = value
    with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=1,
                       dtype="float32", crs=crs, nodata=-999999.0,
                       transform=from_origin(left, top, res, res)) as d:
        d.write(a)
    return str(path)


def test_a_newer_tile_without_data_does_not_hide_an_older_one(tmp_path,
                                                             monkeypatch):
    """The newer tile's footprint covers the older one's, but it has data in
    its west third only; the older tile, and a copy of the same project in
    the next UTM zone, fill the rest. Before v0.45 both were skipped on
    their footprints (Kingman: mosaics 14-70% filled)."""
    from geolens import dem_fetch
    from geolens.dem_fetch import FETCH_VERSION, fetch_dem

    t = tmp_path / "remote"
    t.mkdir()
    utm = "EPSG:26913"
    new = _half_tile(t / "USGS_1M_13_new.tif", utm, 400000, 3700000, 1.0,
                     1800, 1000, 2000.0, 600)
    old = _tile(t / "USGS_1M_13_old.tif", utm, 400600, 3700000, 1.0, 600,
                1000, 1000.0)
    # the same project's other half, delivered in zone 12
    import pyproj

    x12, y12 = pyproj.Transformer.from_crs(utm, "EPSG:26912", always_xy=True
                                           ).transform(401150, 3700050)
    z12 = _tile(t / "USGS_1M_12_new.tif", "EPSG:26912", x12, y12, 1.0, 800,
                1200, 1500.0)
    items = [
        {"title": "NEW zone 13", "downloadURL": new, "publicationDate": "2023-01-01",
         "boundingBox": _wgs_box(utm, 400000, 3700000, 401800, 3699000)},
        {"title": "NEW zone 12", "downloadURL": z12, "publicationDate": "2023-01-01",
         "boundingBox": _wgs_box(utm, 401100, 3700000, 401800, 3699000)},
        {"title": "OLD", "downloadURL": old, "publicationDate": "2016-01-01",
         "boundingBox": _wgs_box(utm, 400600, 3700000, 401200, 3699000)},
    ]
    monkeypatch.setattr(dem_fetch, "search_1m", lambda bbox, timeout=60: list(items))
    import geopandas as gpd
    from shapely.geometry import box

    aoi = tmp_path / "aoi.gpkg"
    gpd.GeoDataFrame(geometry=[box(400100, 3699100, 401700, 3699900)],
                     crs=utm).to_file(aoi, driver="GPKG")
    monkeypatch.setenv("GEOLENS_OUT", str(tmp_path / "out"))
    r = fetch_dem(str(aoi), source="1m", resolution_m=2.0, crs=utm)
    with rasterio.open(r["dem"]) as d:
        a = d.read(1, masked=True)
        x0 = d.transform.c
    row = a[200]
    col = lambda x: int((x - x0) / 2.0)                       # noqa: E731
    assert row[col(400300)] == 2000.0          # the newer tile's data
    assert row[col(400900)] == 1000.0          # the older tile, under its footprint
    assert abs(row[col(401500)] - 1500.0) < 1  # the zone 12 copy
    assert r["area_coverage"] > 0.99
    assert r["fetch_version"] == FETCH_VERSION
    assert not any("no 3DEP 1 m lidar" in w for w in r["warnings"])
    src = json.loads(Path(r["sources"]).read_text())
    assert all(s.get("used") for s in src["tiles"]), src["tiles"]
    assert src["fetch_version"] == FETCH_VERSION


def test_a_tile_whose_cells_are_filled_is_skipped(world):
    """A tile is still skipped when every cell it could fill has data."""
    from geolens.dem_fetch import fetch_dem

    aoi, out = world
    import geopandas as gpd
    from shapely.geometry import box

    small = aoi.parent / "small.gpkg"
    gpd.GeoDataFrame(geometry=[box(400100, 3699100, 400500, 3699900)],
                     crs="EPSG:26913").to_file(small, driver="GPKG")
    r = fetch_dem(str(small), source="1m", resolution_m=2.0)
    src = json.loads(Path(r["sources"]).read_text())
    used = [s["title"] for s in src["tiles"] if s.get("used")]
    assert used == ["USGS one meter NEW 2020"]
    assert r["area_coverage"] > 0.99
