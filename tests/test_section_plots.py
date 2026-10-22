"""Section plots for the stream types, and the links from the map to them.

Sam: "i've never seen in the outputs a way to reference these cross section
spatial locations to an image of cross sections". Every section line now has
a PNG, the GeoPackages carry plot_file/plot_path, and a QGIS style shows the
plot on hover and opens it on click. The style itself is checked in a real
QGIS by tests/qgis/check_plot_links.py; these check everything else.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

DEM = Path(__file__).resolve().parent / "landscape" / "landscape_dem.tif"


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """One stream-type run on the landscape fixture, plots for 6 reaches.

    The run itself goes through rosgen_stream_types(section_plots=True) with
    the renderer limited to six reaches: 655 sections would take a minute
    and prove nothing more.
    """
    if not DEM.exists():
        pytest.skip("landscape fixture not built")
    from geolens.landscape import (build_terrain, extract_network,
                                   rosgen_stream_types)

    tmp = tmp_path_factory.mktemp("splots")
    os.environ["GEOLENS_OUT"] = str(tmp)
    os.environ["GEOLENS_NO_PROGRESS"] = "1"
    os.environ["GEOLENS_PERSIST"] = "0"
    # A coarse network: the default one has ~600 reaches, 3,000 sections.
    t = build_terrain(str(DEM), stream_threshold_km2=3.0, out_prefix="sp_t")
    n = extract_network(t, segment_length_m=800.0, out_prefix="sp_n")
    # The 30 m fixture cannot show a real channel's banks, so give every
    # reach a channel the grid can see (100 m, over three cells): the
    # regional stage then measures a bankfull on most sections, which is
    # what the plots draw. (Until v0.42 the test leaned on one section of
    # 680 where the bank picker found something by accident.)
    import geopandas as gpd
    segs = gpd.read_file(n.segments)
    segs["channel_width_m"] = 100.0
    segs.to_file(n.segments, driver="GPKG")
    from geolens.landscape import section_plots as sp
    real = sp.plot_stream_sections

    def few(folder, **kw):
        import geopandas as gpd
        ids = gpd.read_file(Path(folder) / "stream_type_sections.gpkg",
                            layer="stream_type_sections")["seg_id"]
        return real(folder, reach_ids=sorted(set(ids))[:6], **kw)

    sp.plot_stream_sections = few
    try:
        r = rosgen_stream_types(t.dem, n.segments, out_prefix="sp",
                                section_plots=True)
    finally:
        sp.plot_stream_sections = real
    return r


def _read(path, layer):
    import geopandas as gpd
    return gpd.read_file(str(path), layer=layer)


def test_every_section_and_reach_has_its_plot(run):
    d = Path(run.sections).parent
    secs = _read(run.sections, "stream_type_sections")
    reaches = _read(run.segments, "stream_types")
    six = sorted(set(secs["seg_id"]))[:6]
    mine = secs[secs["seg_id"].isin(six)]
    assert run.plots["section_plots"] == len(mine) > 0
    assert run.plots["reach_sheets"] == 6
    drawn = {p.name for p in (d / "section_plots").iterdir()}
    assert drawn == {Path(f).name for f in mine["plot_file"]}
    for layer in (secs, reaches):
        assert layer["plot_file"].notna().all()
        for rel, ab in zip(layer["plot_file"], layer["plot_path"]):
            assert Path(ab).is_absolute() and Path(ab) == (d / rel).resolve()
    for rel in reaches.loc[reaches["seg_id"].isin(six), "plot_file"]:
        assert (d / rel).exists(), rel
    # the two GeoPackages carry one layer each; the style is beside them
    import pyogrio
    for g in (run.sections, run.segments):
        assert len(pyogrio.list_layers(g)) == 1
        assert Path(g).with_suffix(".qml").exists()
    assert Path(run.plots["index"]).exists()


def test_the_stages_the_plots_draw_are_on_the_sections_layer(run):
    """So a plot never has to re-run the bank detector to draw the pick."""
    secs = _read(run.sections, "stream_type_sections")
    bf = secs[secs["status"] == "bankfull"]
    assert len(bf) > 0
    for col in ("thalweg_z_m", "bankfull_stage_m", "floodprone_stage_m"):
        assert bf[col].notna().all(), col
    np.testing.assert_allclose(
        bf["bankfull_stage_m"] - bf["thalweg_z_m"], bf["bankfull_max_depth_m"],
        atol=1e-9)
    np.testing.assert_allclose(
        bf["floodprone_stage_m"] - bf["thalweg_z_m"],
        2 * bf["bankfull_max_depth_m"], atol=1e-9)
    reg = secs[secs["basis"] == "regional"]
    if len(reg):
        assert reg["floodprone_stage_m"].notna().all()
        assert reg["regional_max_depth_m"].notna().all()


def test_an_earlier_output_folder_gets_the_same_pictures(run, tmp_path):
    """v0.25 folders have no stage columns and no links: derive and add them."""
    from geolens.landscape.section_plots import (_derive_stages,
                                                 plot_stream_sections)
    import pandas as pd

    src = Path(run.sections).parent
    old = tmp_path / "old"
    shutil.copytree(src, old, ignore=shutil.ignore_patterns(
        "section_plots", "reach_plots", "*.qml", "*.html"))
    new_cols = ["thalweg_offset_m", "thalweg_z_m", "bankfull_stage_m",
                "candidate_stages_m", "floodprone_stage_m",
                "regional_max_depth_m", "plot_file", "plot_path"]
    secs = _read(old / "stream_type_sections.gpkg", "stream_type_sections")
    truth = secs.copy()
    secs.drop(columns=new_cols).to_file(
        old / "stream_type_sections.gpkg", driver="GPKG",
        layer="stream_type_sections")
    reaches = _read(old / "stream_types.gpkg", "stream_types")
    reaches.drop(columns=["plot_file", "plot_path"]).to_file(
        old / "stream_types.gpkg", driver="GPKG", layer="stream_types")

    two = sorted(set(secs["seg_id"]))[:2]
    out = plot_stream_sections(old, reach_ids=two)
    assert out["section_plots"] == secs["seg_id"].isin(two).sum() > 0
    assert not out["warnings"]
    after = _read(old / "stream_type_sections.gpkg", "stream_type_sections")
    assert (after["plot_file"] == truth["plot_file"]).all()
    assert all(Path(p).exists() for p, s_ in
               zip(after["plot_path"], after["seg_id"]) if s_ in two)
    assert "plot_path" in _read(old / "stream_types.gpkg", "stream_types")

    # The derived stages match the recorded ones on measured sections. A
    # stage the regional method lowered to keep the width plausible
    # (stage_capped, v0.28) is not the regional depth above the thalweg,
    # and no v0.25 folder has one: those are left out.
    prof = pd.read_csv(old / "stream_type_profiles.csv").dropna()
    capped = truth.get("stage_capped")
    plain = truth[(truth["status"] == "bankfull")
                  & ~(capped.fillna(False).astype(bool) if capped is not None
                      else False)]
    assert len(plain) > 0
    for _, t in plain.iterrows():
        p = prof[prof["xs_id"] == t["xs_id"]].sort_values("offset_m")
        r = {k: v for k, v in t.items() if k not in new_cols}
        _derive_stages(p["offset_m"].to_numpy(float),
                       p["elevation_m"].to_numpy(float), r)
        assert r["bankfull_stage_m"] == pytest.approx(
            t["bankfull_stage_m"], abs=0.02)
        assert r["floodprone_stage_m"] == pytest.approx(
            t["floodprone_stage_m"], abs=0.02)

    # A second run changes nothing it does not have to.
    m = (old / "stream_type_sections.gpkg").stat().st_mtime_ns
    plot_stream_sections(old, sections=False, reaches=False)
    assert (old / "stream_type_sections.gpkg").stat().st_mtime_ns == m


def test_a_layer_open_elsewhere_gets_a_linked_copy(run, tmp_path, monkeypatch):
    """Windows will not replace a GeoPackage QGIS has open."""
    from geolens.landscape import section_plots as sp

    d = tmp_path / "busy"
    shutil.copytree(Path(run.sections).parent, d)
    g = d / "stream_type_sections.gpkg"
    secs = _read(g, "stream_type_sections")
    secs.drop(columns=["plot_file", "plot_path"]).to_file(
        g, driver="GPKG", layer="stream_type_sections")
    real = os.replace

    def busy(a, b):
        if Path(b) == g:
            raise PermissionError("in use")
        return real(a, b)

    monkeypatch.setattr(sp.os, "replace", busy)
    warnings: list[str] = []
    _, p = sp._rewrite_with_links(
        g, "stream_type_sections",
        [sp.section_plot_file(x) for x in secs["xs_id"]], warnings)
    assert p.name == "stream_type_sections_linked.gpkg" and p.exists()
    assert "open in another program" in warnings[0]


def test_looking_downstream_whichever_way_the_reach_was_drawn():
    """Offsets are to the LEFT of the line as digitized; river left must end
    up on the left of the plot whichever way the reach runs."""
    from shapely.geometry import LineString

    from geolens.landscape.section_plots import _direction, _ends

    # Digitized downstream (z falls): +offset is river left -> x = -offset.
    s, words = _direction({"z_start": 100.0, "z_end": 90.0})
    assert s == -1.0 and words == "looking downstream"
    # Digitized upstream: +offset is river right -> x = +offset.
    assert _direction({"z_start": 90.0, "z_end": 100.0})[0] == 1.0
    assert "digitized" in _direction({})[1]

    # A reach flowing north, digitized downstream: the section's +offset end
    # is west. Looking downstream (north), west is on the left.
    sec = LineString([(10.0, 0.0), (-10.0, 0.0)])      # min offset -> max
    assert _ends(sec, -1.0) == ("W", "E")
    # The same north-flowing reach digitized upstream (southward): its
    # left-hand normal, and so the +offset end, is east now -- and the plot
    # must still put west on the left, because the water still runs north.
    sec = LineString([(-10.0, 0.0), (10.0, 0.0)])
    assert _ends(sec, 1.0) == ("W", "E")


def test_the_style_links_the_plot_and_falls_back_when_moved():
    from geolens.gis_style import plot_link_qml

    qml = plot_link_qml("'xs ' || \"xs_id\"", category_field="status",
                        categories=[("bankfull", "#2a78d6", "used")])
    root = ET.fromstring(qml.split("\n", 1)[1])
    assert "Symbology" in root.get("styleCategories")
    tip = root.find("mapTip").text
    # absolute path first, then the layer's own folder + the relative one
    assert 'file_exists("plot_path")' in tip
    assert "layer_property(@layer, 'path')" in tip and '"plot_file"' in tip
    assert "replace(@p, '\\\\', '/')" in tip        # Windows separators
    acts = root.find("attributeactions")
    a = acts.find("actionsetting")
    assert a.get("type") == "5"                       # Open URL
    assert acts.find("defaultAction").get("value") == a.get("id")
    w = root.find("fieldConfiguration/field[@name='plot_path']/editWidget")
    assert w.get("type") == "ExternalResource"
    cats = root.findall("renderer-v2/categories/category")
    assert [c.get("type") for c in cats] == ["string", "NULL"]   # + other


def test_embedding_the_style_matches_what_qgis_writes(tmp_path):
    import geopandas as gpd
    from shapely.geometry import LineString

    from geolens.gis_style import write_qgis_style

    g = tmp_path / "x.gpkg"
    gpd.GeoDataFrame({"a": [1]}, geometry=[LineString([(0, 0), (1, 1)])],
                     crs="EPSG:32613").to_file(g, driver="GPKG", layer="x")
    for _ in range(2):                                # re-run replaces
        write_qgis_style(g, "x", "<qgis/>", embed=True)
    con = sqlite3.connect(g)
    rows = con.execute("SELECT f_table_catalog, f_table_schema, f_table_name,"
                       " f_geometry_column, useAsDefault FROM layer_styles"
                       ).fetchall()
    kind = con.execute("SELECT data_type FROM gpkg_contents WHERE "
                       "table_name = 'layer_styles'").fetchone()
    con.close()
    assert rows == [("", "", "x", "geom", 1)]
    assert kind == ("attributes",)
    assert (tmp_path / "x.qml").read_text() == "<qgis/>"
