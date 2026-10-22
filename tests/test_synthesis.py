"""Stream power, stream tracing, major streams and the HUC12 sensitivity
table, on a drawn network.

The network (metres, EPSG:32612, 10 m cells), outlet at the right:

    T0 (0..500)  ->  T1 (500..1000)  ->  T2 (1000..1500, drawn uphill)
                      ^ A at the node     ^ D at the node, with the river's
                                            drainage inherited at the
                                            junction cell; B onto T2's line
                                            at x = 1200
    C ends 250 m above T0: a gap in the traced network, bridged.
    P is a parallel thread of T1 (a second skeleton path of the wash).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

gpd = pytest.importorskip("geopandas")
from shapely.geometry import LineString  # noqa: E402

from geolens.synthesis import (COMPONENTS, DEFAULT_WEIGHTS, huc12_power,  # noqa: E402
                               huc12_sensitivity, major_streams, name_streams,
                               stream_power, synthesize, trace_streams)


def _net():
    rows = [
        # seg_id, branch, pos, drainage, line
        (0, 0, 0, 30.0, [(0, 0), (500, 0)]),
        (1, 0, 1, 60.0, [(500, 0), (1000, 0)]),
        (2, 1, 0, 100.0, [(1500, 0), (1000, 0)]),             # drawn uphill
        (3, 2, 0, 25.0, [(500, 400), (500, 0)]),               # A, at the node
        (4, 3, 0, 10.0, [(1200, 300), (1200, 0)]),             # B, onto the line
        (5, 4, 0, 5.0, [(200, 500), (200, 250)]),              # C, a gap
        (6, 5, 0, 100.0, [(1000, 400), (1000, 0)]),            # D0, inherited
        (7, 5, 1, 8.0, [(1000, 800), (1000, 400)]),            # D1
        (8, 6, 0, 59.0, [(500, 0), (750, 60), (1000, 0)]),     # P, parallel
        (9, 7, 0, 20.0, [(-500, 0), (0, 0)]),                  # above T0
    ]
    recs = []
    for sid, br, pos, dr, pts in rows:
        line = LineString(pts)
        z0 = 100.0 - pts[0][0] * 0.01 + pts[0][1] * 0.02
        z1 = 100.0 - pts[-1][0] * 0.01 + pts[-1][1] * 0.02
        recs.append({"seg_id": sid, "branch": br, "pos": pos,
                     "drainage_km2": dr, "length_m": line.length,
                     "z_start": z0, "z_end": z1, "drop_m": z0 - z1,
                     "gradient": abs(z0 - z1) / line.length,
                     "confinement": "unconfined" if sid in (1, 2, 8) else "confined",
                     "landscape_type": "U-AL-AD" if sid in (1, 2, 8) else
                     ("U-AL-IF" if sid == 0 else "C-RE-FD"),
                     "huc12": "150100010101" if sid < 4 else "150100010102",
                     "dem_cell_m": 10.0, "geometry": line})
    return gpd.GeoDataFrame(recs, crs="EPSG:32612")


def test_the_main_stem_is_one_stream_and_the_tributaries_their_own():
    seg, info = trace_streams(_net())
    s = seg.set_index("seg_id")
    assert s.loc[[0, 1, 2], "stream_id"].nunique() == 1, "the trunk"
    trunk = s.loc[2, "stream_id"]
    assert trunk == 1, "the biggest outlet is stream 1"
    assert s.loc[2, "dist_to_mouth_km"] == 0.0
    assert s.loc[1, "dist_to_mouth_km"] == pytest.approx(0.5, abs=0.01)
    assert s.loc[0, "dist_to_mouth_km"] == pytest.approx(1.0, abs=0.01)
    assert s.loc[9, "stream_id"] == trunk and s.loc[9, "dist_to_mouth_km"] == 1.5
    assert bool(s.loc[2, "flow_reversed"]) and not bool(s.loc[1, "flow_reversed"])
    # ... and the uphill line is turned round, its fall with it
    assert list(s.loc[2, "geometry"].coords) == [(1000.0, 0.0), (1500.0, 0.0)]
    assert s.loc[2, "z_start"] == 90.0 and s.loc[2, "z_end"] == 85.0
    assert s.loc[2, "drop_m"] == 5.0 and bool(s.loc[2, "flow_oriented"])
    # A, B, C and D are tributaries of the trunk; D1 is on D's stream.
    for k in (3, 4, 5, 6):
        assert s.loc[k, "stream_id"] != trunk
        assert s.loc[k, "flows_into"] == trunk
    assert s.loc[7, "stream_id"] == s.loc[6, "stream_id"]
    assert info["outlets"] == 1
    assert info["joined_on_line"] >= 1 and info["bridged"] == 1
    assert s.loc[5, "flows_into"] == trunk, "the gap below C was bridged"


def test_a_mouth_that_inherited_the_rivers_drainage_is_corrected():
    seg, info = trace_streams(_net())
    s = seg.set_index("seg_id")
    assert bool(s.loc[6, "drainage_inherited"])
    assert s.loc[6, "drainage_km2_used"] == pytest.approx(8.0)
    assert s.loc[6, "stream_km2_at_mouth"] == pytest.approx(8.0)
    assert s.loc[6, "drainage_km2"] == 100.0, "the measured column is kept"
    assert info["inherited_drainage"] == 1
    # and the trunk went on along T1, not up the spur
    assert s.loc[1, "stream_id"] == s.loc[2, "stream_id"]


def test_a_parallel_thread_belongs_to_the_river():
    seg, info = trace_streams(_net())
    s = seg.set_index("seg_id")
    assert s.loc[8, "stream_id"] == s.loc[1, "stream_id"]
    assert bool(s.loc[8, "parallel_thread"])
    assert info["parallel_threads"] == 1


def test_stream_power_from_the_curve():
    seg = stream_power(_net(), "arid_sw_az")
    s = seg.set_index("seg_id")
    q = 1.60476 * 100.0 ** 0.4711
    assert s.loc[2, "q_bankfull_m3s"] == pytest.approx(q, rel=1e-3)
    omega = 1000 * 9.81 * q * s.loc[2, "gradient"]
    assert s.loc[2, "stream_power_w_m"] == pytest.approx(omega, rel=1e-2)
    w = 3.55369 * 100.0 ** 0.3169
    assert s.loc[2, "unit_stream_power_w_m2"] == pytest.approx(omega / w, rel=1e-2)
    assert "Moody" in s.loc[2, "power_basis"]
    generic = stream_power(_net(), "bieger_us")
    assert generic["q_bankfull_m3s"].isna().all()
    assert "no bankfull discharge" in generic["power_basis"].iloc[0]


def test_stream_power_uses_the_corrected_drainage_after_tracing():
    seg, _ = trace_streams(_net())
    seg = stream_power(seg, "arid_sw_az")
    s = seg.set_index("seg_id")
    assert s.loc[6, "q_bankfull_m3s"] == pytest.approx(
        1.60476 * 8.0 ** 0.4711, rel=1e-3)


def test_major_streams_are_dissolved_with_the_drainage_at_the_mouth():
    seg, _ = trace_streams(_net())
    seg = stream_power(seg, "arid_sw_az")
    ms = major_streams(seg, min_drainage_km2=20.0)
    assert list(ms["drainage_km2"]) == [100.0, 25.0]
    trunk = ms.iloc[0]
    assert trunk["length_km"] == pytest.approx(2.0 + 0.507, abs=0.02), \
        "the trunk and its parallel thread"
    assert trunk["n_reaches"] == 5
    assert trunk["landscape_main"] == "U-AL-AD"
    assert trunk["fan_share"] == pytest.approx(0.5 / 2.507, abs=0.01)
    assert trunk["huc12_mouth"] == "150100010101"
    assert pd.isna(trunk["flows_into"])
    assert ms.iloc[1]["flows_into"] == 1
    assert ms.geometry.geom_type.eq("MultiLineString").all()
    # the inherited spur is not a 100 km2 stream
    assert 8.0 not in list(ms["drainage_km2"]) and len(ms) == 2
    assert major_streams(seg, min_drainage_km2=1000.0).empty


def test_huc12_power_is_length_weighted():
    seg, _ = trace_streams(_net())
    seg = stream_power(seg, "arid_sw_az")
    p = huc12_power(seg)
    assert set(p.index) == {"150100010101", "150100010102"}
    a = seg[seg["huc12"] == "150100010101"]
    w = a["length_m"]
    assert p.loc["150100010101", "unit_power_mean_w_m2"] == pytest.approx(
        float((a["unit_stream_power_w_m2"] * w).sum() / w.sum()), abs=0.02)


def _summary():
    rng = np.random.default_rng(3)
    n = 12
    t = pd.DataFrame({
        "huc12": [f"1501000101{i + 1:02d}" for i in range(n)],
        "name": [f"Wash {i}" for i in range(n)],
        "km2": rng.uniform(30, 120, n).round(1),
        "soil_k_mean": np.linspace(0.1, 0.4, n),
        "hsg_D_share": rng.uniform(0, 1, n).round(2),
        "slope_mean_pct": rng.uniform(2, 30, n).round(1),
        "alluvial_landscape_share": np.linspace(0.9, 0.0, n),
        "form_stable_km": [5.0, 2.0, np.nan] + [np.nan] * (n - 3),
        "form_unstable_km": [1.0, 6.0, np.nan] + [np.nan] * (n - 3),
        "rap_bgr_per_decade": rng.uniform(-2, 4, n).round(2),
        "rap_bgr_recent": rng.uniform(10, 60, n).round(1),
        "fire_burned_share_10yr": [0.0] * n,                # constant: dropped
        "atlas14_24h_100yr_mm": rng.uniform(60, 120, n).round(1),
    })
    return t


def test_sensitivity_ranks_and_weights():
    t = _summary()
    out, info = huc12_sensitivity(t)
    assert len(out) == 12
    assert "fire_burned_share_10yr" in info["dropped"]
    assert "unit_power_mean_w_m2" in info["dropped"], "no power table given"
    assert abs(sum(info["weights"].values()) - 1.0) < 0.01, "rounded to 3 places"
    # a rank is a percentile among the HUC12s
    assert out["rank_soil_k_mean"].max() == 1.0
    assert out["rank_soil_k_mean"].min() == pytest.approx(1 / 12, abs=1e-3)
    # unstable form: only two HUC12s have it, under 3 -> dropped
    assert "form_unstable_share" in info["dropped"]
    assert set(out["sensitivity_class"]) <= {"high", "moderate", "low"}
    assert info["classes"]["high"] == 4
    hi = out[out["sensitivity_class"] == "high"]["sensitivity_score"].min()
    lo = out[out["sensitivity_class"] == "low"]["sensitivity_score"].max()
    assert hi > lo
    assert out["weight_measured"].eq(1.0).all()
    assert out["sensitivity_drivers"].notna().any()


def test_sensitivity_with_the_power_table_and_custom_weights(tmp_path):
    t = _summary()
    power = pd.DataFrame({"unit_power_mean_w_m2": np.linspace(50, 400, 12)},
                         index=t["huc12"])
    out, info = huc12_sensitivity(t, {"soil_k_mean": 1, "unit_power_mean_w_m2": 1},
                                  power=power)
    assert info["weights"] == {"soil_k_mean": 0.5, "unit_power_mean_w_m2": 0.5}
    # both rise together here, so the last HUC12 is the most sensitive
    assert out["sensitivity_score"].idxmax() == t["huc12"].iloc[-1]
    assert out.loc[t["huc12"].iloc[-1], "sensitivity_score"] == 1.0
    wfile = tmp_path / "w.json"
    wfile.write_text(json.dumps({"soil_k_mean": 2, "hsg_D_share": 0}))
    out2, info2 = huc12_sensitivity(t, wfile)
    assert info2["weights"] == {"soil_k_mean": 1.0}
    with pytest.raises(Exception):
        huc12_sensitivity(t, {"soil_k_mean": -1})
    with pytest.raises(Exception):
        huc12_sensitivity(t, {"nothing_here": 1})


def test_a_huc12_missing_most_components_is_insufficient_data():
    t = _summary()
    t.loc[t.index[-1], ["soil_k_mean", "hsg_D_share", "slope_mean_pct",
                        "alluvial_landscape_share", "rap_bgr_per_decade",
                        "rap_bgr_recent"]] = np.nan
    out, info = huc12_sensitivity(t)
    last = out.loc[t["huc12"].iloc[-1]]
    assert last["sensitivity_class"] == "insufficient data"
    assert last["weight_measured"] < 0.5
    assert info["classes"]["insufficient data"] == 1


def test_the_default_weights_sum_to_one():
    assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9
    assert len(COMPONENTS) == len(DEFAULT_WEIGHTS)


def test_synthesize_writes_the_layers_and_the_weights(tmp_path):
    net = _net()
    full = tmp_path / "valley_types.gpkg"
    net.to_file(full, driver="GPKG", layer="valley_types")
    merged = tmp_path / "merged"
    merged.mkdir()
    clipped = merged / "valley_types.gpkg"
    net[net["seg_id"] != 5].to_file(clipped, driver="GPKG", layer="valley_types")
    t = _summary().iloc[:2].copy()
    t["huc12"] = ["150100010101", "150100010102"]
    t.to_csv(merged / "huc12_summary.csv", index=False)
    hucs = gpd.GeoDataFrame(
        {"huc12": ["150100010101", "150100010102"]},
        geometry=[LineString([(0, 0), (1000, 0)]).buffer(600),
                  LineString([(1000, 0), (1500, 0)]).buffer(600)],
        crs="EPSG:32612")
    r = synthesize(merged, valley_types=full, curve="arid_sw_az",
                   merged=clipped, summary_csv=merged / "huc12_summary.csv",
                   hucs=hucs, min_drainage_km2=20.0)
    assert r["major_streams_n"] == 2
    assert (merged / "major_streams.gpkg").exists()
    back = gpd.read_file(clipped)
    assert "stream_id" in back.columns and "unit_stream_power_w_m2" in back.columns
    assert len(back) == 9, "the clipped layer keeps its rows"
    t2 = back.set_index("seg_id").loc[2]
    assert list(t2.geometry.coords) == [(1000.0, 0.0), (1500.0, 0.0)], \
        "the merged layer's lines run downstream too"
    assert t2["drop_m"] == 5.0
    assert r["largest"][0]["drainage_km2"] == 100.0
    # two HUC12s cannot be ranked against each other: said, not failed
    assert "fewer than 3" in r["sensitivity"]["skipped"]
    assert not (merged / "huc12_sensitivity.csv").exists()

    # twelve can
    t = _summary()                      # its first two HUC12s are the net's
    t.to_csv(merged / "huc12_summary.csv", index=False)
    r = synthesize(merged, valley_types=full, curve="arid_sw_az",
                   merged=clipped, summary_csv=merged / "huc12_summary.csv",
                   hucs=hucs, min_drainage_km2=20.0)
    assert (merged / "huc12_sensitivity.csv").exists()
    assert (merged / "huc12_sensitivity.gpkg").exists()
    w = json.loads((merged / "sensitivity_weights.json").read_text())
    assert "weights" in w and "labels" in w
    assert r["sensitivity"]["huc12s"] == 12
    assert "unit_power_mean_w_m2" in r["sensitivity"]["dropped"], \
        "only two HUC12s have reaches"
    layer = gpd.read_file(merged / "huc12_sensitivity.gpkg")
    assert layer.geometry.notna().sum() == 2, "polygons for the two HUC12s given"


def _flowlines():
    """NHD-like flowlines: a named one along the trunk, offset 40 m; a
    second name on the trunk's upper part; one unnamed line with the NHD's
    drainage at the mouth; and a named line far away."""
    return gpd.GeoDataFrame({
        "gnis_name": ["Dry Wash", "Upper Dry Wash", None, "Elsewhere Creek"],
        "gnis_id": ["100", "101", None, "200"],
        "totdasqkm": [95.0, 30.0, 110.0, 500.0],
        "streamorde": [4, 3, 4, 5],
        "qema": [12.5, 3.0, 13.0, 90.0],
        "geometry": [LineString([(300, 40), (1500, 40)]),
                     LineString([(-500, 40), (300, 40)]),
                     LineString([(1500, 0), (1600, -50)]),
                     LineString([(0, 5000), (1500, 5000)])]},
        crs="EPSG:32612")


def test_major_streams_take_their_names_and_the_nhd_drainage():
    seg, _ = trace_streams(_net())
    seg = stream_power(seg, "arid_sw_az")
    ms = name_streams(major_streams(seg, min_drainage_km2=20.0), _flowlines())
    trunk = ms.set_index("stream_id").loc[1]
    assert trunk["name"] == "Dry Wash" and trunk["gnis_id"] == "100"
    assert 0.4 <= trunk["name_share"] <= 1.0
    assert trunk["other_names"] == "Upper Dry Wash"
    assert trunk["nhd_totdasqkm"] == 110.0, "the unnamed line at the mouth"
    assert trunk["nhd_streamorde"] == 4 and trunk["nhd_qema_cfs"] == 13.0
    assert trunk["drainage_ratio"] == pytest.approx(100.0 / 110.0, abs=0.01)
    trib = ms.set_index("stream_id").loc[ms["stream_id"].max()]
    assert pd.isna(trib["name"]), "the tributary runs beside no named flowline"
    # no flowlines at all: the columns exist, empty
    bare = name_streams(major_streams(seg, 20.0), _flowlines().iloc[0:0])
    assert bare["name"].isna().all() and "drainage_ratio" in bare.columns


def test_synthesize_names_the_streams_when_given_flowlines(tmp_path):
    net = _net()
    full = tmp_path / "valley_types.gpkg"
    net.to_file(full, driver="GPKG", layer="valley_types")
    fl = tmp_path / "nhd_flowlines.gpkg"
    _flowlines().to_file(fl, driver="GPKG", layer="nhd_flowlines")
    merged = tmp_path / "merged"
    merged.mkdir()
    r = synthesize(merged, valley_types=full, curve="arid_sw_az",
                   min_drainage_km2=20.0, flowlines=fl)
    assert r["named"] == 1 and r["largest"][0]["name"] == "Dry Wash"
    assert r["largest"][0]["nhd_totdasqkm"] == 110.0
    ms = gpd.read_file(merged / "major_streams.gpkg")
    assert "name" in ms.columns and ms["name"].notna().sum() == 1
