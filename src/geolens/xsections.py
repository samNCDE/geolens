"""Cross-sections as GIS features: lines, a lookup table, and per-section plots.

The stacked overlay plot is for eyeballing a reach in one glance. It is not a
working product — you cannot click a section in it, you cannot join it to
anything, and past about thirty sections it is a blue smear.

What is useful in GIS is the section *as a feature*: a line you can see on the
map, attributed with its own geometry metrics, carrying the path to its own
plot. Click a section, read its number, open its figure.

Two things reduce the pile:

* **Per-section plots on demand.** Nothing is rendered unless asked for, and a
  section's plot path is stored whether or not the file exists yet.
* **Representative sections.** Fifty sections through a uniform reach carry
  about as much information as three. :func:`select_representative` picks the
  section closest to the middle of its reach's *distribution* — a medoid, not a
  mean — so the one you keep is a real surveyed section rather than an average
  of several that resembles none of them.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .safety import Guard, GeolensError, note, progress

# Metrics used to judge how alike two sections are. Each is scaled to its own
# spread across the reach before comparison, so a width in metres and a
# dimensionless ratio carry equal weight.
SHAPE_METRICS = (
    "width_m", "mean_depth_m", "max_depth_m", "width_depth_ratio",
    "entrenchment_ratio", "relief_m",
)


@dataclass
class SectionExport:
    lines: str
    table: str
    plot_dir: str
    section_count: int
    plotted: int
    representative: str | None
    seconds: float
    warnings: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _section_line(sec: dict[str, Any], width: float):
    """The section's trace on the ground, from its centre and bearing."""
    from shapely.geometry import LineString

    b = math.radians(sec["bearing_deg"])
    nx, ny = math.sin(b), math.cos(b)
    cx, cy = sec["center_x"], sec["center_y"]
    half = width / 2.0
    return LineString([(cx - nx * half, cy - ny * half),
                       (cx + nx * half, cy + ny * half)])


def export_sections(
    sections_result: dict[str, Any],
    delineation: dict[str, Any] | None = None,
    crs: Any = None,
    name: str = "cross_sections",
    plot: str | bool = "representative",
    reach_field: str | None = None,
    max_plots: int = 200,
    guard: Guard | None = None,
) -> SectionExport:
    """Write cross-sections as GIS lines plus a lookup table.

    Parameters
    ----------
    sections_result
        Output of ``extract_cross_sections``.
    delineation
        Optional output of ``morphology.delineate``, joined on station so the
        table carries bankfull width, depth, W/D and entrenchment.
    plot
        ``"representative"`` (default) renders a figure only for the section
        chosen to represent each reach; ``True`` renders every section;
        ``False`` renders none. Every row still gets a ``plot_path``, so the
        figure can be produced later without changing the table.
    max_plots
        With ``plot=True``, the most sections to render (~0.2 s each): the
        representative ones plus an even spread of the rest. A long line cut
        every 25 ft has thousands of sections.

    The folder also gets ``<name>.qml``, which QGIS applies to both the
    GeoPackage and the shapefile: hover over a section (View > Show Map Tips)
    to see its plot, or click it with Run Feature Action to open it.
    reach_field
        Column in the delineation to group by when choosing representatives.
        Defaults to a single reach covering all sections.

    The ``xs_id`` field is what to type in GIS: it is stable, sequential, and
    matches the plot filename.
    """
    import geopandas as gpd
    import pandas as pd

    guard = guard or Guard.from_env()
    t0 = time.monotonic()
    warnings: list[str] = []

    sections = sections_result.get("sections")
    if not sections:
        raise GeolensError("No sections in the result — run cross_sections first.")

    width = float(sections_result.get("width", 0.0))
    if width <= 0:
        raise GeolensError("Section width missing from the result.")

    by_station: dict[float, dict[str, Any]] = {}
    if delineation:
        for r in delineation.get("sections", []):
            by_station[round(float(r["station"]), 4)] = r

    out_dir = Path(guard.write_path(f"{name}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    plot_dir = out_dir / "plots"
    plot_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for i, sec in enumerate(sections):
        st = round(float(sec["station"]), 4)
        d = by_station.get(st, {})
        bf = d.get("bankfull") or {}
        fp = d.get("floodplain") or {}
        rec = {
            "xs_id": i + 1,
            "station": st,
            "center_x": sec["center_x"],
            "center_y": sec["center_y"],
            "bearing_deg": sec["bearing_deg"],
            "valid_points": sec["valid_points"],
            "thalweg_offset": sec.get("thalweg_offset"),
            "thalweg_elev": sec.get("thalweg_elevation"),
            "min_elev": sec.get("min_elevation"),
            "max_elev": sec.get("max_elevation"),
            "relief_m": sec.get("relief"),
            "width_m": bf.get("width"),
            "bankfull_elev": bf.get("elevation"),
            "mean_depth_m": bf.get("mean_depth"),
            "max_depth_m": bf.get("max_depth"),
            "width_depth_ratio": bf.get("width_depth_ratio"),
            "entrenchment_ratio": fp.get("entrenchment_ratio"),
            "confined_left": fp.get("confined_left"),
            "confined_right": fp.get("confined_right"),
            "status": d.get("status", "no_delineation"),
            "plot_file": f"plots/xs_{i + 1:04d}.png",
            "plot_path": str((plot_dir / f"xs_{i + 1:04d}.png").resolve()),
            "geometry": _section_line(sec, width),
        }
        rows.append(rec)

    gdf = gpd.GeoDataFrame(rows, crs=crs)

    # --- representative selection -------------------------------------------
    rep_path: str | None = None
    if reach_field and reach_field in gdf.columns:
        groups = gdf.groupby(reach_field).groups
    else:
        groups = {"all": gdf.index}

    gdf["is_representative"] = False
    gdf["reach"] = ""
    reps: list[int] = []
    for key, idx in groups.items():
        sub = gdf.loc[idx]
        gdf.loc[idx, "reach"] = str(key)
        pick = _medoid(sub)
        if pick is not None:
            gdf.loc[pick, "is_representative"] = True
            reps.append(int(gdf.loc[pick, "xs_id"]))

    # --- plots ---------------------------------------------------------------
    plotted = 0
    if plot:
        want = (gdf[gdf["is_representative"]] if plot == "representative"
                else gdf)
        if plot != "representative" and len(want) > max_plots > 0:
            keep = set(np.linspace(0, len(gdf) - 1, max_plots).round()
                       .astype(int).tolist())
            want = gdf[gdf.index.isin(keep) | gdf["is_representative"]]
            warnings.append(
                f"{len(gdf)} sections: rendered {len(want)} evenly spaced "
                f"plots (max_plots={max_plots}). The rest are "
                f"plot_cross_section_by_id(result, xs_id=N) away.")
        note(f"rendering {len(want)} section plot(s)")
        for _, row in progress(list(want.iterrows()), label="plotting sections"):
            sec = sections[int(row["xs_id"]) - 1]
            d = by_station.get(round(float(sec["station"]), 4))
            try:
                _plot_one(sec, d, Path(row["plot_path"]),
                          representative=bool(row["is_representative"]))
                plotted += 1
            except Exception as exc:
                warnings.append(f"xs {row['xs_id']}: plot failed ({exc})")

    lines_path = out_dir / f"{name}.gpkg"
    gdf.to_file(str(lines_path), driver="GPKG")
    shp_path = out_dir / f"{name}.shp"
    try:
        # Shapefile field names cap at 10 characters; write a copy with short
        # names so it opens cleanly in older GIS without silent truncation.
        short = gdf.rename(columns={
            "width_depth_ratio": "wd_ratio",
            "entrenchment_ratio": "entrench",
            "is_representative": "is_rep",
            "thalweg_offset": "thal_off",
            "thalweg_elev": "thal_elev",
            "bankfull_elev": "bf_elev",
            "confined_left": "conf_L",
            "confined_right": "conf_R",
            "valid_points": "n_points",
            "mean_depth_m": "mean_dep",
            "max_depth_m": "max_dep",
        })
        short.to_file(str(shp_path), driver="ESRI Shapefile")
    except Exception as exc:
        warnings.append(f"shapefile write failed ({exc}); use the GeoPackage.")
        shp_path = lines_path

    table_path = out_dir / f"{name}.csv"
    gdf.drop(columns="geometry").to_csv(table_path, index=False)

    try:
        from .gis_style import plot_link_qml, write_qgis_style
        write_qgis_style(lines_path, name, plot_link_qml(
            display_expression="'xs ' || \"xs_id\" || ' - station ' || "
                               "round(\"station\", 1)",
            action_name="Open section plot",
            missing_note="No plot rendered for this section. In Python: "
                         "geolens.api.plot_cross_section_by_id(result, "
                         "xs_id=N)."))
    except Exception as exc:                                 # noqa: BLE001
        warnings.append(f"QGIS style not written ({exc}).")

    if reps:
        rep_gdf = gdf[gdf["is_representative"]]
        rep_path = str(out_dir / f"{name}_representative.gpkg")
        rep_gdf.to_file(rep_path, driver="GPKG")
        qml = Path(lines_path).with_suffix(".qml")
        if qml.exists():
            Path(rep_path).with_suffix(".qml").write_text(
                qml.read_text(encoding="utf-8"), encoding="utf-8")

    elapsed = time.monotonic() - t0
    note(f"{len(gdf)} sections exported, {plotted} plotted, in {elapsed:.1f}s")
    if plot == "representative" and len(gdf) > plotted:
        note(f"  plot=True would render all {len(gdf)}; paths are already in "
             "the table either way")

    return SectionExport(
        lines=str(shp_path), table=str(table_path), plot_dir=str(plot_dir),
        section_count=len(gdf), plotted=plotted, representative=rep_path,
        seconds=elapsed, warnings=warnings,
    )


def _medoid(sub) -> Any:
    """Index of the section most like the others — the medoid of the group.

    A medoid rather than a mean, because the point is to name a section that
    actually exists. A mean cross-section is an average of several shapes and
    typically resembles none of them, which is exactly what you do not want to
    hand someone as "the representative section for this reach".

    Each metric is standardised by its own spread first, so a width in metres
    and a dimensionless ratio count equally rather than the larger number
    dominating.
    """
    cols = [c for c in SHAPE_METRICS if c in sub.columns]
    if not cols or len(sub) == 0:
        return None
    X = sub[cols].apply(lambda s: s.astype("float64"))
    X = X.dropna(axis=1, how="all")
    if X.empty or X.shape[1] == 0:
        return sub.index[len(sub) // 2]

    # A section can only represent a reach if its CHANNEL geometry was actually
    # measured. Requiring merely "some metric present" is not enough: relief_m
    # comes from the raw section and survives a failed delineation, so a section
    # with no bankfull at all still qualified, had its remaining NaNs filled
    # with the column median, and landed exactly at the centre of the
    # distribution — scoring as the most typical section in the reach. Observed
    # picking xs_id 1 (every channel metric NaN) over six good sections.
    core = [c for c in ("width_m", "mean_depth_m") if c in X.columns]
    usable = X[core].notna().all(axis=1) if core else X.notna().any(axis=1)
    if not usable.any():
        # Nothing in this reach has measurable channel geometry. Returning the
        # middle section would be inventing a representative — say nothing.
        return None
    X = X[usable]
    sub = sub[usable]

    X = X.fillna(X.median())
    sd = X.std(ddof=0).replace(0, np.nan)
    if sd.isna().all():
        return sub.index[len(sub) // 2]
    Z = ((X - X.mean()) / sd).fillna(0.0).to_numpy()

    # Sum of distances to every other section; smallest wins.
    d = np.sqrt(((Z[:, None, :] - Z[None, :, :]) ** 2).sum(axis=2)).sum(axis=1)
    return sub.index[int(np.argmin(d))]


def _plot_one(sec: dict[str, Any], deline: dict[str, Any] | None, out: Path,
              representative: bool = False) -> None:
    """One section, plotted on its own, with its channel marked when known."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from .render import (SURFACE, INK, INK_2, AXIS, SERIES_1, SERIES_2,
                         SERIES_3, _style_axes, _finish)

    pts = [(p["offset"], p["elevation"]) for p in sec["points"]
           if p["elevation"] is not None]
    if len(pts) < 2:
        raise GeolensError("too few valid samples")
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]

    fig, ax = plt.subplots(figsize=(9, 4.2))
    bits = []
    bf = (deline or {}).get("bankfull") or {}
    fp = (deline or {}).get("floodplain") or {}
    if bf.get("width"):
        bits.append(f"bankfull W {bf['width']:,.1f}")
    if bf.get("width_depth_ratio"):
        bits.append(f"W/D {bf['width_depth_ratio']:,.1f}")
    if fp.get("entrenchment_ratio"):
        bits.append(f"ER {fp['entrenchment_ratio']:,.2f}")
    sub = "   ·   ".join(bits) if bits else f"relief {sec.get('relief', 0):,.2f}"

    title = f"XS {sec['station']:,.1f}"
    if representative:
        title += "  —  representative section"
    _style_axes(ax, "Offset from centerline  (− left / + right)", "Elevation",
                title, sub)

    lb = ((deline or {}).get("left") or {}).get("bank_top_offset")
    rb = ((deline or {}).get("right") or {}).get("bank_top_offset")
    le, re_ = fp.get("left_edge_offset"), fp.get("right_edge_offset")

    if lb is not None and rb is not None:
        ax.axvspan(lb, rb, color=SERIES_1, alpha=0.15, zorder=1, label="channel")
    if le is not None and lb is not None:
        ax.axvspan(le, lb, color=SERIES_3, alpha=0.12, zorder=1, label="floodplain")
    if re_ is not None and rb is not None:
        ax.axvspan(rb, re_, color=SERIES_3, alpha=0.12, zorder=1)

    ax.plot(xs, ys, color=INK_2, linewidth=2.0, zorder=4, solid_capstyle="round")
    ax.axvline(0, color=AXIS, linewidth=1.1, linestyle=(0, (4, 4)), zorder=2)

    if sec.get("thalweg_offset") is not None:
        ax.plot([sec["thalweg_offset"]], [sec["thalweg_elevation"]], "v",
                color=SERIES_1, markersize=9, markeredgecolor=SURFACE,
                markeredgewidth=1.6, zorder=6, label="thalweg")
    for off in (lb, rb):
        if off is None:
            continue
        z = next((e for o, e in pts if abs(o - off) < 1e-9), None)
        if z is not None:
            ax.plot([off], [z], "o", color=SERIES_2, markersize=8,
                    markeredgecolor=SURFACE, markeredgewidth=2, zorder=6)

    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(frameon=False, loc="upper center", ncol=4, fontsize=9,
                  labelcolor=INK_2, bbox_to_anchor=(0.5, -0.17))
    out.parent.mkdir(parents=True, exist_ok=True)
    _finish(fig, out)


def plot_section_by_id(
    sections_result: dict[str, Any], xs_id: int,
    delineation: dict[str, Any] | None = None,
    name: str | None = None, guard: Guard | None = None,
) -> dict[str, Any]:
    """Render one section by the ``xs_id`` shown in the GIS attribute table.

    This is the other half of the lookup: read an id off the map, get its
    figure. Ids are 1-based and match the exported table.
    """
    guard = guard or Guard.from_env()
    sections = sections_result.get("sections") or []
    if not 1 <= xs_id <= len(sections):
        raise GeolensError(
            f"xs_id {xs_id} out of range — this result has {len(sections)} "
            "sections, numbered from 1."
        )
    sec = sections[xs_id - 1]
    d = None
    if delineation:
        st = round(float(sec["station"]), 4)
        d = next((r for r in delineation.get("sections", [])
                  if round(float(r["station"]), 4) == st), None)
    out = Path(guard.write_path(name or f"xs_{xs_id:04d}.png"))
    _plot_one(sec, d, out)
    return {"path": str(out), "xs_id": xs_id, "station": sec["station"]}


def select_representative(
    sections_result: dict[str, Any],
    delineation: dict[str, Any],
    n_reaches: int = 1,
    by: str = "station",
) -> dict[str, Any]:
    """Split the sections into reaches and name the medoid of each.

    ``by="station"`` cuts the reach into ``n_reaches`` equal lengths — the
    simple case. ``by="confinement"`` groups consecutive sections sharing a
    confinement class instead, which follows the geomorphology rather than an
    arbitrary interval and usually gives more defensible reaches.

    Cost is O(k²) in the number of sections *per reach*, not overall, so more
    reaches is cheaper rather than more expensive. A 500-section run splits into
    10 reaches in well under a second.
    """
    import pandas as pd

    sections = sections_result.get("sections") or []
    dl = {round(float(r["station"]), 4): r for r in delineation.get("sections", [])}
    if not sections:
        raise GeolensError("No sections to choose from.")

    rows = []
    for i, sec in enumerate(sections):
        st = round(float(sec["station"]), 4)
        d = dl.get(st, {})
        bf = d.get("bankfull") or {}
        fp = d.get("floodplain") or {}
        rows.append({
            "xs_id": i + 1, "station": st, "relief_m": sec.get("relief"),
            "width_m": bf.get("width"), "mean_depth_m": bf.get("mean_depth"),
            "max_depth_m": bf.get("max_depth"),
            "width_depth_ratio": bf.get("width_depth_ratio"),
            "entrenchment_ratio": fp.get("entrenchment_ratio"),
            "confinement": _conf_of(fp),
        })
    df = pd.DataFrame(rows)

    if by == "confinement":
        # Consecutive runs of the same class become reaches.
        grp = (df["confinement"] != df["confinement"].shift()).cumsum()
        df["reach"] = grp
    else:
        edges = np.linspace(df["station"].min(), df["station"].max() + 1e-6,
                            n_reaches + 1)
        df["reach"] = np.digitize(df["station"], edges[1:-1])

    picks = []
    for reach, sub in df.groupby("reach"):
        idx = _medoid(sub)
        if idx is None:
            continue
        row = df.loc[idx]
        picks.append({
            "reach": str(reach),
            "xs_id": int(row["xs_id"]),
            "station": float(row["station"]),
            "n_sections_in_reach": int(len(sub)),
            "width_m": _f(row.get("width_m")),
            "width_depth_ratio": _f(row.get("width_depth_ratio")),
            "entrenchment_ratio": _f(row.get("entrenchment_ratio")),
            "confinement": row.get("confinement"),
        })

    return {
        "representatives": picks,
        "reaches": len(picks),
        "total_sections": len(df),
        "grouped_by": by,
        "reduction": f"{len(df)} sections -> {len(picks)} representative",
        "note": (
            "Each pick is a medoid: the real section closest to the middle of "
            "its reach's distribution. Not an average — an average section is a "
            "shape that does not exist anywhere on the ground."
        ),
    }


def _conf_of(fp: dict[str, Any]) -> str:
    if not fp:
        return "unknown"
    l, r = fp.get("confined_left"), fp.get("confined_right")
    if l and r:
        return "confined"
    if l or r:
        return "partly confined"
    return "unconfined"


def _f(v):
    try:
        f = float(v)
        return None if not np.isfinite(f) else round(f, 3)
    except (TypeError, ValueError):
        return None
