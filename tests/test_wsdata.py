"""The watershed data package, offline: each source stood in for by a small
file or function with the shape the real service answers in, and the per
HUC12 tables and the HUC12 summary checked by hand.

The live services are tried by ``geolens services`` (``check_services``);
RAP's cover rasters were read live once (October 2026, 13 s a year over an
area the size of a BLM field office).
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

gpd = pytest.importorskip("geopandas")
rasterio = pytest.importorskip("rasterio")

import pandas as pd  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from shapely.geometry import LineString, box  # noqa: E402

from geolens import wsdata  # noqa: E402
from geolens.safety import GeolensError  # noqa: E402

UTM = "EPSG:32612"


def _hucs():
    """Two HUC12s side by side, 10 km square each, in UTM 12N (Arizona)."""
    return gpd.GeoDataFrame(
        {"huc12": ["150100000101", "150100000102"], "name": ["West", "East"],
         "areasqkm": [100.0, 100.0], "in_boundary": [1.0, 0.6],
         "lidar_frac": [1.0, 0.0]},
        geometry=[box(400000, 3900000, 410000, 3910000),
                  box(410000, 3900000, 420000, 3910000)], crs=UTM)


def _raster(path: Path, bands, bounds=(399000, 3899000, 421000, 3911000),
            cell=1000.0, crs="EPSG:32612", dtype="float32", nodata=None,
            descriptions=None):
    """A small raster whose band values are constant, or a callable of the
    column index (so the two HUC12s differ)."""
    w, s, e, n = bounds
    cols, rows = int((e - w) / cell), int((n - s) / cell)
    data = np.zeros((len(bands), rows, cols), dtype=dtype)
    for i, b in enumerate(bands):
        if callable(b):
            data[i] = np.fromfunction(lambda r, c: b(c), (rows, cols))
        else:
            data[i] = b
    with rasterio.open(path, "w", driver="GTiff", width=cols, height=rows,
                       count=len(bands), dtype=dtype, crs=crs,
                       transform=from_origin(w, n, cell, cell),
                       nodata=nodata) as dst:
        dst.write(data)
        if descriptions:
            for i, d in enumerate(descriptions):
                dst.set_band_description(i + 1, d)
    return str(path)


# -- fire ---------------------------------------------------------------------------

def test_fire_history_per_huc12(tmp_path):
    h = _hucs()
    big = box(405000, 3905000, 415000, 3910000)       # half of each, 2015
    old = box(401000, 3901000, 403000, 3903000)       # 4 km2 of West, 1998
    fires = gpd.GeoDataFrame(
        {"FIRE_YEAR_INT": [2015, 1998, 2015], "INCIDENT": ["Big", "Old", "big "]},
        geometry=[big, old, big], crs=UTM).to_crs(4326)   # one fire twice
    seen = {}

    def query(url, where, fields, bbox=None, max_offset=None, timeout=None):
        seen.update(url=url, bbox=bbox)
        return fires

    r = wsdata.fire_history(h, tmp_path / "data", query=query)
    assert seen["url"] == wsdata.FIRE_URLS[0] and len(seen["bbox"]) == 4
    assert r["perimeters"] == 3 and Path(r["path"]).exists()
    t = r["table"]
    assert t.loc["150100000101", "fire_burned_share"] == pytest.approx(0.29, abs=0.01)
    assert t.loc["150100000101", "fire_burned_share_2000"] == pytest.approx(0.25, abs=0.01)
    assert t.loc["150100000102", "fire_burned_share"] == pytest.approx(0.25, abs=0.01)
    assert t.loc["150100000102", "fire_burned_share_10yr"] == 0.0     # 2015 is older
    assert list(t["fire_count"]) == [2, 1]           # the same fire counted once
    assert list(t["fire_last_year"]) == [2015, 2015]
    kept = gpd.read_file(r["path"])
    assert set(kept.columns) >= {"fire_year", "incident", "geometry"}


def test_no_fires_is_unburned_everywhere(tmp_path):
    empty = gpd.GeoDataFrame(geometry=[], crs=4326)
    r = wsdata.fire_history(_hucs(), tmp_path / "data", query=lambda *a, **k: empty)
    assert r["path"] is None and r["perimeters"] == 0
    assert (r["table"]["fire_burned_share"] == 0).all()
    assert r["table"]["fire_last_year"].isna().all()


def test_every_fire_service_failing_is_an_error(tmp_path):
    def query(*a, **k):
        raise OSError("timed out")

    with pytest.raises(GeolensError, match="No fire perimeter service"):
        wsdata.fire_history(_hucs(), tmp_path / "data", query=query)


# -- ecological sites ----------------------------------------------------------------

def test_ecological_sites_take_the_largest_major_component(tmp_path):
    soils = tmp_path / "soils.gpkg"
    gpd.GeoDataFrame(
        {"mukey": [11, 22, 33], "material_class": ["alluvial"] * 3},
        geometry=[box(400000, 3900000, 408000, 3910000),      # 80% of West
                  box(408000, 3900000, 420000, 3910000),      # rest, all East
                  box(395000, 3895000, 399000, 3899000)],     # outside
        crs=UTM).to_file(soils, driver="GPKG", layer="soils")
    asked = []

    def ask(q):
        asked.append(q)
        return [
            {"mukey": "11", "cokey": "1", "comppct_r": "60", "majcompflag": "Yes",
             "ecoclassid": "R035XB223AZ", "ecoclassname": "Loamy Upland 10-13\" p.z.",
             "ecoclasstypename": "NRCS Rangeland Site"},
            {"mukey": "11", "cokey": "2", "comppct_r": "70", "majcompflag": "No ",
             "ecoclassid": "R035XB999AZ", "ecoclassname": "Minor",
             "ecoclasstypename": "NRCS Rangeland Site"},
            {"mukey": "22", "cokey": "3", "comppct_r": "85", "majcompflag": "Yes",
             "ecoclassid": "R040XA118AZ", "ecoclassname": "Sandy Wash",
             "ecoclasstypename": "NRCS Rangeland Site"},
        ]

    r = wsdata.ecological_sites(str(soils), tmp_path / "data", _hucs(), ask=ask)
    assert len(asked) == 1 and "coecoclass" in asked[0] and "11,22,33" in asked[0]
    assert r["map_units"] == 3 and r["with_site"] == 2
    out = gpd.read_file(r["path"]).set_index("mukey")
    assert out.loc[11, "ecoclassid"] == "R035XB223AZ"       # major beats larger
    assert out.loc[11, "ecosite_pct"] == 60
    assert pd.isna(out.loc[33, "ecoclassid"])
    t = r["table"]
    assert t.loc["150100000101", "ecosite_dominant"].startswith("R035XB223AZ Loamy")
    assert t.loc["150100000101", "ecosite_dominant_share"] == pytest.approx(0.8)
    assert t.loc["150100000101", "ecosite_count"] == 2
    assert t.loc["150100000102", "ecosite_dominant"].startswith("R040XA118AZ")


# -- PRISM ---------------------------------------------------------------------------

def _prism_zip(value_of_col, name):
    """A PRISM normals zip, as downloaded: one GeoTIFF inside."""
    buf = io.BytesIO()
    tmp = Path(name).with_suffix(".tif")
    _raster(tmp, [value_of_col], crs="EPSG:4326",
            bounds=(-113.5, 35.0, -112.9, 35.3), cell=0.01, nodata=-9999)
    with zipfile.ZipFile(buf, "w") as z:
        z.write(tmp, tmp.name)
    tmp.unlink()
    return buf.getvalue()


def test_prism_normals_per_huc12(tmp_path, monkeypatch):
    # The HUC12s in lon/lat, west and east halves of the little raster.
    h = gpd.GeoDataFrame({"huc12": ["a", "b"]},
                         geometry=[box(-113.45, 35.05, -113.25, 35.25),
                                   box(-113.15, 35.05, -112.95, 35.25)], crs=4326)
    got = []

    def get(url):
        got.append(url)
        name = url.rsplit("/", 1)[-1]
        if "/annual/" in url:
            raise OSError("404")                       # only the monthly folder
        if "ppt_us_30s_2020_" in name:                 # annual: 300 west, 100 east
            return _prism_zip(lambda c: np.where(c < 30, 300.0, 100.0), tmp_path / name)
        if "ppt_us_30s_202007" in name or "ppt_us_30s_202008" in name:
            return _prism_zip(lambda c: np.where(c < 30, 50.0, 10.0), tmp_path / name)
        if "ppt_us_30s_202009" in name:
            return _prism_zip(lambda c: np.where(c < 30, 20.0, 10.0), tmp_path / name)
        if "tmean" in name:
            return _prism_zip(lambda c: np.where(c < 30, 12.5, 18.0), tmp_path / name)
        raise AssertionError(url)

    r = wsdata.prism_normals(h, tmp_path / "prism", get=get)
    t = r["table"]
    assert t.loc["a", "prism_ppt_mm"] == 300.0 and t.loc["b", "prism_ppt_mm"] == 100.0
    assert t.loc["a", "prism_ppt_jas_share"] == pytest.approx(120 / 300, abs=0.001)
    assert t.loc["b", "prism_ppt_jas_share"] == pytest.approx(0.3, abs=0.001)
    assert t.loc["a", "prism_tmean_c"] == 12.5 and t.loc["b", "prism_tmean_c"] == 18.0
    assert "1991-2020" in r["source"]
    # The annual folder was tried first, then the monthly one; the zips are
    # kept, so a second run downloads nothing.
    assert got[0].endswith("/ppt/annual/prism_ppt_us_30s_2020_avg_30y.zip")
    assert got[1].endswith("/ppt/monthly/prism_ppt_us_30s_2020_avg_30y.zip")
    n = len(got)
    wsdata.prism_normals(h, tmp_path / "prism", get=get)
    assert len(got) == n


def test_prism_temperature_is_optional(tmp_path):
    h = gpd.GeoDataFrame({"huc12": ["a"]},
                         geometry=[box(-113.45, 35.05, -113.25, 35.25)], crs=4326)

    def get(url):
        if "tmean" in url:
            raise OSError("no")
        annual = "_2020_avg" in url
        return _prism_zip(lambda c: 200.0 if annual else 20.0,
                          tmp_path / url.rsplit("/", 1)[-1])

    t = wsdata.prism_normals(h, tmp_path / "prism", get=get)["table"]
    assert t.loc["a", "prism_ppt_mm"] == 200.0
    assert t.loc["a", "prism_ppt_jas_share"] == pytest.approx(0.3)
    assert "prism_tmean_c" not in t.columns


# -- NOAA Atlas 14 -------------------------------------------------------------------

PFDS = """
result = 1;
quantiles = [['0.26', '0.34', '0.45', '0.54', '0.66', '0.76', '0.86', '0.97', '1.12', '1.24'],
 ['0.39', '0.52', '0.69', '0.82', '1.01', '1.16', '1.31', '1.47', '1.70', '1.89'],
 ['0.49', '0.64', '0.85', '1.02', '1.25', '1.44', '1.63', '1.83', '2.11', '2.34'],
 ['0.65', '0.86', '1.15', '1.37', '1.68', '1.93', '2.19', '2.46', '2.84', '3.15'],
 ['0.81', '1.07', '1.42', '1.70', '2.08', '2.39', '2.72', '3.05', '3.52', '3.90'],
 ['0.92', '1.19', '1.56', '1.86', '2.28', '2.63', '2.99', '3.38', '3.92', '4.36'],
 ['0.98', '1.25', '1.62', '1.93', '2.37', '2.73', '3.12', '3.53', '4.12', '4.60'],
 ['1.11', '1.40', '1.78', '2.11', '2.57', '2.95', '3.36', '3.80', '4.43', '4.96'],
 ['1.26', '1.57', '1.97', '2.31', '2.77', '3.16', '3.57', '4.01', '4.62', '5.12'],
 ['1.45', '1.80', '2.25', '2.63', '3.15', '3.57', '4.02', '4.50', '5.17', '5.71'],
 ['1.58', '1.96', '2.44', '2.83', '3.38', '3.82', '4.28', '4.76', '5.42', '5.95'],
 ['1.72', '2.14', '2.65', '3.07', '3.65', '4.11', '4.59', '5.09', '5.78', '6.33'],
 ['1.84', '2.28', '2.82', '3.26', '3.86', '4.34', '4.83', '5.34', '6.04', '6.58'],
 ['2.14', '2.65', '3.26', '3.75', '4.41', '4.93', '5.46', '6.00', '6.73', '7.29'],
 ['2.42', '2.99', '3.65', '4.18', '4.89', '5.44', '6.00', '6.56', '7.32', '7.90'],
 ['3.10', '3.80', '4.59', '5.20', '6.01', '6.62', '7.23', '7.84', '8.64', '9.24'],
 ['3.67', '4.47', '5.35', '6.02', '6.90', '7.55', '8.19', '8.82', '9.63', '10.2'],
 ['4.37', '5.29', '6.27', '7.01', '7.96', '8.65', '9.32', '9.97', '10.8', '11.4'],
 ['4.93', '5.95', '7.01', '7.80', '8.80', '9.52', '10.2', '10.9', '11.7', '12.3']];
upper = [['0.30']];
"""


def test_the_pfds_answer_is_parsed():
    q = wsdata.parse_pfds(PFDS)
    assert q["60-min"][1] == 0.81 and q["60-min"][2] == 1.07
    assert q["60-min"][100] == 2.72 and q["24-hr"][100] == 4.02
    assert q["5-min"][1] == 0.26 and q["60-day"][1000] == 12.3
    assert set(q) == set(wsdata.ATLAS14_DURATIONS)
    with pytest.raises(GeolensError, match="no quantiles"):
        wsdata.parse_pfds("<html>Sorry, outside the coverage</html>")


def test_atlas14_depths_at_each_centre_and_the_cache(tmp_path):
    h = _hucs()
    asked = []

    def get(url):
        asked.append(url)
        if len(asked) == 2:
            raise OSError("502")                    # East fails the first time
        return PFDS

    cache = tmp_path / "data" / "atlas14.json"
    cache.parent.mkdir()
    r = wsdata.atlas14_depths(h, cache, get=get, pause_s=0)
    assert r["failed"] == 1 and list(r["table"].index) == ["150100000101"]
    t = r["table"]
    assert t.loc["150100000101", "atlas14_1h_2yr_mm"] == pytest.approx(1.07 * 25.4, abs=0.1)
    assert t.loc["150100000101", "atlas14_24h_100yr_mm"] == pytest.approx(4.02 * 25.4, abs=0.1)
    assert "lat=35.2" in asked[0] and "lon=-11" in asked[0] and "series=pds" in asked[0]
    assert set(json.loads(cache.read_text())) == {asked[0].split("lat=")[1].split("&")[0]
                                                  + "," + asked[0].split("lon=")[1].split("&")[0]}
    # The rerun asks only for the HUC12 that failed.
    r = wsdata.atlas14_depths(h, cache, get=get, pause_s=0)
    assert len(asked) == 3 and r["failed"] == 0
    assert list(r["table"].index) == ["150100000101", "150100000102"]
    assert set(r["table"].columns) == {f"atlas14_{d}_{a}yr_mm"
                                       for d in ("1h", "24h") for a in (2, 10, 25, 100)}


def test_atlas14_answering_nowhere_is_an_error(tmp_path):
    def get(url):
        raise OSError("down")

    with pytest.raises(GeolensError, match="none of the HUC12s"):
        wsdata.atlas14_depths(_hucs(), tmp_path / "a.json", get=get, pause_s=0)


# -- RAP vegetation cover -----------------------------------------------------------------

def _rap_year(tmp_path, year, west, east):
    """A RAP cover raster: 6 bands, uint8 percent, 255 nodata, lon/lat."""
    h = _hucs().to_crs(4326)
    w, s, e, n = h.total_bounds
    bands = []
    for b in wsdata.RAP_BANDS:
        wv, ev = west[b], east[b]
        bands.append(lambda c, wv=wv, ev=ev: np.where(c < 20, wv, ev))
    return _raster(tmp_path / f"rap_{year}.tif", bands, crs="EPSG:4326",
                   bounds=(w - 0.02, s - 0.02, e + 0.02, n + 0.02),
                   cell=(e - w + 0.04) / 40, dtype="uint8", nodata=255,
                   descriptions=list(wsdata.RAP_BANDS))


def test_rap_cover_means_and_trends(tmp_path):
    flat = {"AFG": 10, "BGR": 40, "LTR": 10, "PFG": 15, "SHR": 20, "TRE": 5}
    years = list(range(2016, 2026))
    paths = {}
    for i, y in enumerate(years):
        west = dict(flat, TRE=5 + i, BGR=40 - i)          # trees up, bare down
        east = dict(flat, PFG=15)
        paths[y] = _rap_year(tmp_path, y, west, east)
    opened = []

    def open_url(year):
        opened.append(year)
        if year == 2019:
            return str(tmp_path / "missing.tif")
        return paths[year]

    out = tmp_path / "data"
    r = wsdata.rap_cover(_hucs(), out, years=years, open_url=open_url)
    assert r["missing_years"] == [2019]
    assert r["years"] == [y for y in years if y != 2019]
    long = pd.read_csv(r["path"], dtype={"huc12": str})
    assert len(long) == 2 * 9 and set(long.columns) == {"huc12", "year", *wsdata.RAP_BANDS}
    row = long[(long["huc12"] == "150100000101") & (long["year"] == 2016)].iloc[0]
    assert row["TRE"] == 5 and row["BGR"] == 40 and row["PFG"] == 15
    t = r["table"]
    assert t.loc["150100000101", "rap_tre_recent"] == pytest.approx(11.9, abs=0.2)
    assert t.loc["150100000101", "rap_tre_per_decade"] == pytest.approx(10.0, abs=0.3)
    assert t.loc["150100000101", "rap_bgr_per_decade"] == pytest.approx(-10.0, abs=0.3)
    assert t.loc["150100000102", "rap_tre_per_decade"] == pytest.approx(0.0, abs=0.01)
    assert t.loc["150100000102", "rap_pfg_recent"] == 15.0
    # Resumable: a second run reads only the years not in the CSV.
    n = len(opened)
    r2 = wsdata.rap_cover(_hucs(), out, years=years, open_url=open_url)
    assert opened[n:] == [2019] and r2["years"] == r["years"]


def test_rap_with_no_year_readable_is_an_error(tmp_path):
    with pytest.raises(GeolensError, match="No RAP cover year"):
        wsdata.rap_cover(_hucs(), tmp_path / "d", years=[2020],
                         open_url=lambda y: str(tmp_path / "none.tif"))


# -- the HUC12 summary -----------------------------------------------------------------

def test_the_huc12_summary_brings_everything_together(tmp_path):
    h = _hucs()
    vt = tmp_path / "valley_types.gpkg"
    gpd.GeoDataFrame(
        {"huc12": ["150100000101"] * 3 + ["150100000102"] * 2 + [None],
         "confinement": ["unconfined", "unconfined", "confined",
                         "partly confined", "confined", "confined"],
         "valley_type": ["VIII", "VIII", "II", "X", "II", None],
         "landscape_type": ["U-AL-AD", "U-AL-AD", "C-BR-CB", "U-AL-AF",
                            "C-BR-CB", None]},
        geometry=[LineString([(401000 + i * 1000, 3905000),
                              (401000 + i * 1000, 3906000)]) for i in range(3)]
        + [LineString([(411000, 3905000), (411000, 3907000)]),
           LineString([(413000, 3905000), (413000, 3906000)]),
           LineString([(430000, 3905000), (430000, 3906000)])],
        crs=UTM).to_file(vt, driver="GPKG", layer="valley_types")
    soils = tmp_path / "soils.gpkg"
    gpd.GeoDataFrame(
        {"mukey": [1, 2], "material_class": ["alluvial", "mass_movement"],
         "hydgrpdcd": ["B", "C/D"], "kwfact": [0.2, 0.4]},
        geometry=[box(395000, 3895000, 405000, 3915000),       # west half of West
                  box(405000, 3895000, 425000, 3915000)],
        crs=UTM).to_file(soils, driver="GPKG", layer="soils")
    stab = tmp_path / "stability.gpkg"
    gpd.GeoDataFrame(
        {"huc12": ["150100000101"] * 2, "form_status": ["stable", "check"],
         "form_status_1996": ["stable", "unstable"]},
        geometry=[LineString([(402000, 3902000), (402000, 3903000)]),
                  LineString([(403000, 3902000), (403000, 3904500)])],
        crs=UTM).to_file(stab, driver="GPKG", layer="stream_stability")
    slope = _raster(tmp_path / "slope.tif",
                    [lambda c: np.where(c < 11, 0.05, 0.30)], cell=1000.0)
    data = tmp_path / "data"
    data.mkdir()
    pd.DataFrame({"huc12": ["150100000101", "150100000102"],
                  "prism_ppt_mm": [250.0, 180.0]}).to_csv(data / "prism_huc12.csv",
                                                           index=False)
    pd.DataFrame({"huc12": ["150100000102"],
                  "fire_burned_share": [0.4]}).to_csv(data / "fire_huc12.csv",
                                                      index=False)

    r = wsdata.huc12_summary(tmp_path / "merged", h, valley_types=str(vt),
                             soils=str(soils), stability=str(stab), slope=slope,
                             data_dir=data,
                             pilot_group={"150100000101": "150100000101",
                                          "150100000102": "150100000101"})
    assert r["rows"] == 2
    t = pd.read_csv(r["csv"], dtype={"huc12": str, "pilot_group": str}
                    ).set_index("huc12")
    g = gpd.read_file(r["path"])
    assert len(g) == 2 and g.geometry.notna().all() and g.crs.to_epsg() == 32612
    w, e = t.loc["150100000101"], t.loc["150100000102"]
    assert w["name"] == "West" and w["km2"] == 100.0 and e["in_boundary_share"] == 0.6
    assert w["pilot_group"] == e["pilot_group"] == "150100000101"
    assert w["stream_km"] == 3.0 and e["stream_km"] == 3.0
    assert w["conf_unconfined_share"] == pytest.approx(2 / 3, abs=0.001)
    assert e["conf_partly_confined_share"] == pytest.approx(2 / 3, abs=0.001)
    assert w["valley_types_1996"] == "VIII 67%, II 33%"
    assert e["valley_types_2022"] == "U-AL-AF 67%, C-BR-CB 33%"
    assert w["alluvial_landscape_share"] == pytest.approx(2 / 3, abs=0.001)
    assert w["soil_alluvial_share"] == pytest.approx(0.5, abs=0.001)
    assert w["soil_colluvial_share"] == pytest.approx(0.5, abs=0.001)   # renamed
    assert e["soil_colluvial_share"] == pytest.approx(1.0, abs=0.001)
    assert w["soil_mapped_share"] == 1.0 and e["soil_mapped_share"] == 1.0
    assert w["hsg_B_share"] == pytest.approx(0.5, abs=0.001)
    assert e["hsg_D_share"] == pytest.approx(1.0, abs=0.001)       # C/D counts as D
    assert w["soil_k_mean"] == pytest.approx(0.3, abs=0.001)
    assert w["slope_mean_pct"] == pytest.approx(5.0, abs=0.1)       # 10 cols at 5%
    assert e["slope_over_20pct_share"] == pytest.approx(1.0, abs=0.01)
    assert w["form1996_stable_km"] == 1.0 and w["form1996_unstable_km"] == 2.5
    assert w["form_stable_km"] == 1.0 and w["form_check_km"] == 2.5     # the card
    assert pd.isna(e["form1996_stable_km"])                        # no level B
    assert w["prism_ppt_mm"] == 250.0 and e["fire_burned_share"] == 0.4
    assert pd.isna(w["fire_burned_share"])


def test_the_summary_with_only_the_huc12s(tmp_path):
    """Nothing else need exist: the table still has the HUC12s."""
    r = wsdata.huc12_summary(tmp_path / "m", _hucs())
    t = pd.read_csv(r["csv"], dtype={"huc12": str})
    assert list(t["huc12"]) == ["150100000101", "150100000102"]
    assert set(t.columns) == {"huc12", "name", "km2", "in_boundary_share",
                              "lidar_share"}


def test_soil_shares_are_of_the_mapped_part(tmp_path):
    """Soils are fetched over the boundary, so a HUC12 on its edge is mapped
    in part: shares are of the mapped part, and the table says how much
    that is."""
    soils = tmp_path / "soils.gpkg"
    gpd.GeoDataFrame(
        {"mukey": [1, 2], "material_class": ["alluvial", "residual"],
         "hydgrpdcd": ["A", "B"]},
        geometry=[box(400000, 3900000, 403000, 3910000),       # 30% of West
                  box(403000, 3900000, 405000, 3910000)],      # 20% of West
        crs=UTM).to_file(soils, driver="GPKG", layer="soils")
    r = wsdata.huc12_summary(tmp_path / "m", _hucs(), soils=str(soils))
    t = pd.read_csv(r["csv"], dtype={"huc12": str}).set_index("huc12")
    w, e = t.loc["150100000101"], t.loc["150100000102"]
    assert w["soil_mapped_share"] == pytest.approx(0.5, abs=0.001)
    assert w["soil_alluvial_share"] == pytest.approx(0.6, abs=0.001)
    assert w["hsg_B_share"] == pytest.approx(0.4, abs=0.001)
    assert e["soil_mapped_share"] == 0.0 and pd.isna(e["soil_alluvial_share"])


def test_the_steps_have_titles_and_sources():
    assert set(wsdata.TITLES) == set(wsdata.STEPS) == set(wsdata.SOURCES)
    assert "NIFC" in wsdata.SOURCES["fire"] and "Atlas 14" in wsdata.SOURCES["atlas14"]
