"""Rendering: plan-view maps and section/profile plots.

Output is PNG so a model can *look* at the data rather than only read numbers.

Chart conventions follow one rule set throughout:
* Station is a continuous magnitude, so sections overlaid by station use a
  single-hue **sequential** ramp (never cycled categorical hues).
* Single-series plots carry no legend box — the title names the series.
* Grid and axes are recessive; text wears ink colors, never a series color.
* One y-axis. Never two.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, Normalize  # noqa: E402

from .safety import Guard, GeolensError, progress  # noqa: E402

# -- design tokens ------------------------------------------------------------

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
AXIS = "#b8b7b2"

SERIES_1 = "#2a78d6"  # blue  — primary line
SERIES_2 = "#eb6834"  # orange — thalweg / accent
SERIES_3 = "#1baf7a"  # aqua  — secondary accent

# Sequential blue ramp, ordinal-safe range (step 250 -> 700).
SEQ_STEPS = ["#86b6ef", "#5598e7", "#3987e5", "#256abf", "#184f95", "#0d366b"]
SEQ_CMAP = LinearSegmentedColormap.from_list("geolens_blue", SEQ_STEPS)

TERRAIN_STEPS = ["#0d366b", "#256abf", "#5598e7", "#cde2fb", "#eda100", "#eb6834"]
TERRAIN_CMAP = LinearSegmentedColormap.from_list("geolens_terrain", TERRAIN_STEPS)


def _style_axes(ax, xlabel: str, ylabel: str, title: str, subtitle: str | None = None):
    ax.set_facecolor(SURFACE)
    ax.grid(True, color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
        ax.spines[side].set_linewidth(1.0)
    ax.tick_params(colors=INK_2, labelsize=9, length=4, width=1.0)
    ax.set_xlabel(xlabel, color=INK_2, fontsize=10, labelpad=8)
    ax.set_ylabel(ylabel, color=INK_2, fontsize=10, labelpad=8)
    if subtitle:
        ax.set_title(
            f"{title}\n{subtitle}", color=INK, fontsize=13, loc="left", pad=14,
            linespacing=1.5,
        )
        ax.title.set_fontweight("medium")
    else:
        ax.set_title(title, color=INK, fontsize=13, loc="left", pad=14,
                     fontweight="medium")


def _finish(fig, out: Path) -> str:
    fig.patch.set_facecolor(SURFACE)
    fig.tight_layout()
    fig.savefig(out, dpi=140, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    return str(out)


# -- longitudinal profile -----------------------------------------------------


def plot_profile(
    result: dict[str, Any],
    name: str = "profile.png",
    guard: Guard | None = None,
    show_trend: bool = True,
) -> dict[str, Any]:
    """Plot a longitudinal profile. Single series, so no legend box."""
    guard = guard or Guard.from_env()
    out = guard.write_path(name)

    pts = [
        (p["station"], p["elevation"])
        for p in result.get("profile", [])
        if p["elevation"] is not None
    ]
    if len(pts) < 2:
        raise GeolensError("Not enough valid elevation samples to plot a profile.")

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]

    fig, ax = plt.subplots(figsize=(11, 4.4))

    slope = result.get("reach_slope")
    sub = f"{len(pts)} samples over {result.get('length', 0):,.0f} units"
    if slope is not None:
        sub += f"   ·   reach slope {slope * 100:.3f}%"

    _style_axes(ax, "Station (CRS units)", "Elevation", "Longitudinal profile", sub)

    ax.fill_between(xs, min(ys), ys, color=SERIES_1, alpha=0.08, zorder=1)
    ax.plot(xs, ys, color=SERIES_1, linewidth=2.0, zorder=3, solid_capstyle="round")

    if show_trend and slope is not None:
        y0 = ys[0]
        trend = [y0 - slope * (x - xs[0]) for x in xs]
        ax.plot(
            xs, trend, color=INK_2, linewidth=1.4, linestyle=(0, (5, 4)),
            zorder=2, alpha=0.75,
        )
        q = len(xs) // 4
        ax.annotate(
            f"best-fit slope {slope * 100:.3f}%",
            xy=(xs[q], trend[q]),
            xytext=(8, 14), textcoords="offset points",
            color=INK_2, fontsize=9, ha="left",
            bbox=dict(boxstyle="round,pad=0.3", facecolor=SURFACE,
                      edgecolor="none", alpha=0.85),
        )

    # Selective direct labels: endpoints only, never every point.
    for x, y, ha, lbl in (
        (xs[0], ys[0], "left", f"{ys[0]:,.2f}"),
        (xs[-1], ys[-1], "right", f"{ys[-1]:,.2f}"),
    ):
        ax.plot([x], [y], "o", color=SERIES_1, markersize=8,
                markeredgecolor=SURFACE, markeredgewidth=2, zorder=4)
        ax.annotate(lbl, xy=(x, y), xytext=(0, 12), textcoords="offset points",
                    color=INK, fontsize=9.5, ha=ha, fontweight="medium")

    return {"path": _finish(fig, out), "samples": len(pts)}


# -- cross-sections -----------------------------------------------------------


def plot_cross_section(
    result: dict[str, Any],
    station: float | None = None,
    index: int | None = None,
    name: str = "cross_section.png",
    guard: Guard | None = None,
) -> dict[str, Any]:
    """Plot one cross-section with its thalweg marked."""
    guard = guard or Guard.from_env()
    out = guard.write_path(name)

    sections = result.get("sections", [])
    if not sections:
        raise GeolensError("No sections in result.")

    if index is not None:
        if index < 0 or index >= len(sections):
            raise GeolensError(f"index {index} out of range (0..{len(sections) - 1}).")
        sec = sections[index]
    elif station is not None:
        sec = min(sections, key=lambda s: abs(s["station"] - station))
    else:
        sec = sections[len(sections) // 2]

    pts = [
        (p["offset"], p["elevation"])
        for p in sec["points"]
        if p["elevation"] is not None
    ]
    if len(pts) < 2:
        raise GeolensError(
            f"Section at station {sec['station']} has too few valid samples."
        )

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]

    fig, ax = plt.subplots(figsize=(9.5, 4.6))
    sub = (
        f"relief {sec['relief']:,.2f}   ·   "
        f"thalweg {sec['thalweg_elevation']:,.2f} at offset "
        f"{sec['thalweg_offset']:+,.2f}"
    )
    _style_axes(
        ax,
        "Offset from centerline  (− left / + right)",
        "Elevation",
        f"Cross-section at station {sec['station']:,.1f}",
        sub,
    )

    ax.fill_between(xs, min(ys), ys, color=SERIES_1, alpha=0.10, zorder=1)
    ax.plot(xs, ys, color=SERIES_1, linewidth=2.0, zorder=3, solid_capstyle="round")

    ax.axvline(0, color=AXIS, linewidth=1.2, linestyle=(0, (4, 4)), zorder=2)
    ax.annotate("centerline", xy=(0, max(ys)), xytext=(4, -2),
                textcoords="offset points", color=INK_2, fontsize=8.5, ha="left")

    if sec["thalweg_offset"] is not None:
        ax.plot([sec["thalweg_offset"]], [sec["thalweg_elevation"]], "o",
                color=SERIES_2, markersize=9, markeredgecolor=SURFACE,
                markeredgewidth=2, zorder=5)
        ax.annotate(
            f"thalweg  {sec['thalweg_elevation']:,.2f}",
            xy=(sec["thalweg_offset"], sec["thalweg_elevation"]),
            xytext=(0, -20), textcoords="offset points",
            color=INK, fontsize=9.5, ha="center", fontweight="medium",
        )

    return {"path": _finish(fig, out), "station": sec["station"],
            "points_plotted": len(pts)}


def plot_section_stack(
    result: dict[str, Any],
    name: str = "section_stack.png",
    max_sections: int = 40,
    guard: Guard | None = None,
) -> dict[str, Any]:
    """Overlay every cross-section, colored by station on a sequential ramp.

    Station is a continuous magnitude, so it gets a one-hue light→dark ramp and
    a colorbar — not cycled categorical hues, and not a legend with 40 entries.
    """
    guard = guard or Guard.from_env()
    out = guard.write_path(name)

    sections = [s for s in result.get("sections", []) if s["valid_points"] >= 2]
    if not sections:
        raise GeolensError("No sections with enough valid samples to plot.")

    if len(sections) > max_sections:
        stride = len(sections) / max_sections
        sections = [sections[int(i * stride)] for i in range(max_sections)]

    stations = [s["station"] for s in sections]
    norm = Normalize(vmin=min(stations), vmax=max(stations))

    segs, colors = [], []
    for sec in progress(sections, label="drawing sections"):
        pts = [
            (p["offset"], p["elevation"])
            for p in sec["points"]
            if p["elevation"] is not None
        ]
        if len(pts) >= 2:
            segs.append(pts)
            colors.append(SEQ_CMAP(norm(sec["station"])))

    fig, ax = plt.subplots(figsize=(10, 5.2))
    _style_axes(
        ax,
        "Offset from centerline  (− left / + right)",
        "Elevation",
        "Cross-sections along the reach",
        f"{len(segs)} sections, colored by station",
    )

    lc = LineCollection(segs, colors=colors, linewidths=1.6, zorder=3, alpha=0.9)
    ax.add_collection(lc)

    thal_x = [s["thalweg_offset"] for s in sections if s["thalweg_offset"] is not None]
    thal_y = [
        s["thalweg_elevation"] for s in sections if s["thalweg_elevation"] is not None
    ]
    if thal_x:
        ax.plot(thal_x, thal_y, "o", color=SERIES_2, markersize=5,
                markeredgecolor=SURFACE, markeredgewidth=1.2, zorder=5,
                label="thalweg")
        ax.legend(frameon=False, loc="upper right", fontsize=9, labelcolor=INK_2)

    ax.axvline(0, color=AXIS, linewidth=1.2, linestyle=(0, (4, 4)), zorder=2)
    ax.autoscale_view()

    sm = plt.cm.ScalarMappable(cmap=SEQ_CMAP, norm=norm)
    cbar = fig.colorbar(sm, ax=ax, pad=0.02, fraction=0.04)
    cbar.set_label("Station", color=INK_2, fontsize=9.5)
    cbar.ax.tick_params(colors=INK_2, labelsize=8.5, length=3)
    cbar.outline.set_visible(False)

    return {"path": _finish(fig, out), "sections_plotted": len(segs)}


# -- plan view ----------------------------------------------------------------


def plot_map(
    dem: str | Path | None = None,
    centerline: str | Path | None = None,
    sections_result: dict[str, Any] | None = None,
    overlays: Sequence[str | Path] | None = None,
    name: str = "map.png",
    guard: Guard | None = None,
    max_dim: int = 1400,
) -> dict[str, Any]:
    """Plan-view map: shaded DEM, centerline, and section traces.

    Gives a model something to *look at* before it reasons about the numbers.
    """
    import numpy as np

    guard = guard or Guard.from_env()
    out = guard.write_path(name)

    fig, ax = plt.subplots(figsize=(9, 8))
    ax.set_facecolor(SURFACE)
    drawn: list[str] = []
    dem_crs = None

    if dem:
        import rasterio
        from rasterio.plot import plotting_extent

        dem_p = guard.read_path(dem)
        with rasterio.open(str(dem_p)) as src:
            dem_crs = src.crs
            decim = max(1, int(max(src.width, src.height) / max_dim))
            arr = src.read(
                1,
                out_shape=(max(1, src.height // decim), max(1, src.width // decim)),
                masked=True,
            )
            extent = plotting_extent(src)

        # Hillshade for shape, elevation ramp for magnitude.
        z = arr.astype("float64").filled(np.nan)
        gy, gx = np.gradient(z)
        slope = np.pi / 2.0 - np.arctan(np.hypot(gx, gy))
        aspect = np.arctan2(-gx, gy)
        az, alt = np.radians(315.0), np.radians(45.0)
        shade = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(
            az - aspect
        )

        ax.imshow(z, extent=extent, cmap=TERRAIN_CMAP, origin="upper",
                  interpolation="bilinear", zorder=1)
        ax.imshow(shade, extent=extent, cmap="gray", origin="upper", alpha=0.35,
                  interpolation="bilinear", zorder=2)
        drawn.append("dem")

    if overlays:
        import geopandas as gpd

        for i, ov in enumerate(overlays):
            ov_p = guard.read_path(ov)
            g = gpd.read_file(str(ov_p))
            if dem_crs is not None and g.crs is not None and g.crs != dem_crs:
                g = g.to_crs(dem_crs)
            g.plot(ax=ax, facecolor="none", edgecolor=SERIES_3, linewidth=1.3,
                   zorder=3, alpha=0.9)
            drawn.append(f"overlay:{ov_p.name}")

    if sections_result:
        import numpy as np

        half = sections_result.get("width", 0) / 2.0
        segs = []
        for sec in sections_result.get("sections", []):
            b = np.radians(sec["bearing_deg"])
            nx, ny = np.sin(b), np.cos(b)
            cx, cy = sec["center_x"], sec["center_y"]
            segs.append(
                [(cx - nx * half, cy - ny * half), (cx + nx * half, cy + ny * half)]
            )
        if segs:
            ax.add_collection(
                LineCollection(segs, colors=SERIES_2, linewidths=1.1, zorder=4,
                               alpha=0.85)
            )
            drawn.append(f"{len(segs)} section traces")

    if centerline:
        import geopandas as gpd

        cl_p = guard.read_path(centerline)
        g = gpd.read_file(str(cl_p))
        if dem_crs is not None and g.crs is not None and g.crs != dem_crs:
            g = g.to_crs(dem_crs)
        g.plot(ax=ax, color=INK, linewidth=2.4, zorder=5)
        g.plot(ax=ax, color="#ffffff", linewidth=0.9, zorder=6)
        drawn.append("centerline")

    if not drawn:
        raise GeolensError("Nothing to draw — pass at least a dem or a centerline.")

    ax.set_title("Plan view", color=INK, fontsize=13, loc="left", pad=12,
                 fontweight="medium")
    ax.tick_params(colors=INK_2, labelsize=8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.set_aspect("equal", adjustable="box")
    ax.autoscale_view(tight=True)

    return {"path": _finish(fig, out), "layers": drawn}


# -- channel / floodplain delineation ----------------------------------------


def plot_delineation(
    sections_result: dict[str, Any],
    delineation: dict[str, Any],
    station: float | None = None,
    index: int | None = None,
    name: str = "delineation.png",
    guard: Guard | None = None,
) -> dict[str, Any]:
    """Plot one section with channel, banks and floodplain marked.

    Zones are shaded (not line-colored) so the ground surface stays one series;
    the legend names the zones, so identity is never carried by color alone.
    """
    guard = guard or Guard.from_env()
    out = guard.write_path(name)

    secs = sections_result.get("sections", [])
    dels = delineation.get("sections", [])
    if not secs or not dels:
        raise GeolensError("Need both a sections result and a delineation result.")

    if index is not None:
        i = index
    elif station is not None:
        i = min(range(len(dels)), key=lambda k: abs(dels[k]["station"] - station))
    else:
        ok = [k for k, r in enumerate(dels) if r["status"] == "ok"]
        i = ok[len(ok) // 2] if ok else len(dels) // 2
    if not 0 <= i < len(secs):
        raise GeolensError(f"index {i} out of range (0..{len(secs) - 1}).")

    sec, dl = secs[i], dels[i]
    pts = [
        (p["offset"], p["elevation"])
        for p in sec["points"]
        if p["elevation"] is not None
    ]
    if len(pts) < 2:
        raise GeolensError(f"Section at station {sec['station']} has too few samples.")

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    lo = min(ys)

    fig, ax = plt.subplots(figsize=(10.5, 5.0))

    bf = dl.get("bankfull") or {}
    fp = dl.get("floodplain") or {}
    bits = []
    if bf.get("width"):
        bits.append(f"bankfull W {bf['width']:,.1f}")
    if bf.get("width_depth_ratio"):
        bits.append(f"W/D {bf['width_depth_ratio']:,.1f}")
    if fp.get("entrenchment_ratio"):
        bits.append(f"ER {fp['entrenchment_ratio']:,.2f}")
    sub = "   ·   ".join(bits) if bits else dl.get("status", "")

    _style_axes(
        ax,
        "Offset from centerline  (− left / + right)",
        "Elevation",
        f"Channel & floodplain — station {sec['station']:,.1f}",
        sub,
    )

    lb = (dl.get("left") or {}).get("bank_top_offset")
    rb = (dl.get("right") or {}).get("bank_top_offset")
    le = fp.get("left_edge_offset")
    re_ = fp.get("right_edge_offset")

    if lb is not None and rb is not None:
        ax.axvspan(lb, rb, color=SERIES_1, alpha=0.16, zorder=1, label="channel")
    if le is not None and lb is not None:
        ax.axvspan(le, lb, color=SERIES_3, alpha=0.14, zorder=1, label="floodplain")
    if re_ is not None and rb is not None:
        ax.axvspan(rb, re_, color=SERIES_3, alpha=0.14, zorder=1)

    ax.plot(xs, ys, color=INK_2, linewidth=2.0, zorder=4, solid_capstyle="round")

    for off, lbl in ((lb, "bank top"), (rb, None)):
        if off is None:
            continue
        z = next((e for o, e in pts if abs(o - off) < 1e-9), None)
        if z is None:
            continue
        ax.plot([off], [z], "o", color=SERIES_2, markersize=9,
                markeredgecolor=SURFACE, markeredgewidth=2, zorder=6,
                label=lbl if lbl else None)

    for off in (le, re_):
        if off is None:
            continue
        ax.axvline(off, color=AXIS, linewidth=1.3, linestyle=(0, (3, 3)), zorder=3)

    if dl.get("thalweg_offset") is not None:
        ax.plot([dl["thalweg_offset"]], [dl["thalweg_elevation"]], "v",
                color=SERIES_1, markersize=9, markeredgecolor=SURFACE,
                markeredgewidth=1.6, zorder=6, label="thalweg")

    if fp.get("confined_left") or fp.get("confined_right"):
        ax.annotate(
            "valley confinement",
            xy=(le if fp.get("confined_left") else re_, max(ys)),
            xytext=(6 if fp.get("confined_left") else -6, -4),
            textcoords="offset points", color=INK_2, fontsize=8.5,
            ha="left" if fp.get("confined_left") else "right",
        )

    ax.set_ylim(lo - (max(ys) - lo) * 0.08, max(ys) + (max(ys) - lo) * 0.14)
    ax.legend(frameon=False, loc="upper center", ncol=4, fontsize=9,
              labelcolor=INK_2, bbox_to_anchor=(0.5, -0.16))

    return {
        "path": _finish(fig, out),
        "station": sec["station"],
        "status": dl.get("status"),
    }
