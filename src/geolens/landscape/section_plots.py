"""Pictures of the channel sections behind the stream types, linked to the map.

``rosgen_stream_types`` measures five sections per reach and writes them as
lines (``stream_type_sections.gpkg``) and numbers (``stream_type_profiles.csv``).
Checking a call means looking at the sections, and a line on a map does not
show you a bank. So:

``section_plots/xs_<xs_id>.png``
    One per section line: the whole section, and the channel close up. Bank
    picks, the bankfull and flood-prone stages, the regional channel width,
    the thalweg and where the mapped channel crosses. Looking downstream,
    compass directions at both ends so it matches the map.
``reach_plots/reach_<id>.png``
    One per reach: every section of the reach, upstream to downstream, under
    the stream type they produced and what it rests on.
``stream_type_plots.html``
    Every reach sheet on one page, filterable, for reading in a browser.

Both GeoPackages get ``plot_file`` / ``plot_path`` columns and a default
QGIS style (``geolens.gis_style``): hover over a section to see its plot,
click it with Run Feature Action to open it.

Everything here reads the files in the output folder -- no DEM -- so it runs
on any earlier output folder too: ``plot_stream_sections(folder)``.
"""
from __future__ import annotations

import html
import math
import os
import re
import time
from pathlib import Path
from typing import Any

import numpy as np

from ..safety import GeolensError, note, progress

SECTION_DIR = "section_plots"
REACH_DIR = "reach_plots"
INDEX_NAME = "stream_type_plots.html"

# Section status -> (colour, legend label). Same colours on the map and in
# the plots' titles.
STATUS_STYLE: dict[str, tuple[str, str]] = {
    "bankfull": ("#2a78d6", "bankfull - used"),
    "one-sided": ("#eda100", "one-sided pick - not used"),
    "implausible": ("#eb6834", "implausible pick - not used"),
    "sub-resolution": ("#8e6bd1", "narrower than 3 samples - not used"),
    "no bank found": ("#8a8984", "no bank found"),
    "below resolution": ("#c9c8c3", "channel below DEM resolution"),
}
LETTER_COLOURS: list[tuple[str, str, str]] = [
    ("Aa+", "#0d366b", "Aa+"), ("A", "#256abf", "A"), ("B", "#5598e7", "B"),
    ("C", "#1baf7a", "C"), ("D", "#eda100", "D"), ("DA", "#0f8f8f", "DA"),
    ("E", "#7cc36b", "E"), ("F", "#eb6834", "F"), ("G", "#c2352b", "G"),
]

_GROUND = "#ebe7de"
_REJECT = "#9a9994"
_PICKER = "#6d4fb3"        # the bank picker's answer, drawn as a check


def _safe(v: Any) -> str:
    return re.sub(r"[^\w.-]", "_", str(v))


def section_plot_file(xs_id: Any) -> str:
    return f"{SECTION_DIR}/xs_{int(xs_id):05d}.png"


def reach_plot_file(seg_id: Any) -> str:
    return f"{REACH_DIR}/reach_{_safe(seg_id)}.png"


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _compass(az: float) -> str:
    return ("N", "NE", "E", "SE", "S", "SW", "W", "NW")[
        int(((az % 360.0) + 22.5) // 45.0) % 8]


def _derive_stages(o: np.ndarray, z: np.ndarray, r: dict) -> None:
    """Fill in the stage columns on outputs written before v0.26.

    The thalweg of a bank pick is the low point between the banks; the
    flood-prone stage is where the ground crosses it at the flood-prone
    edges (unless the section ran out there).
    """
    bl, br = _num(r.get("bank_left_offset_m")), _num(r.get("bank_right_offset_m"))
    dmax = _num(r.get("bankfull_max_depth_m"))
    if _num(r.get("thalweg_z_m")) is None:
        if bl is not None and br is not None:
            m = (o >= min(bl, br)) & (o <= max(bl, br))
        else:
            w = _num(r.get("expected_width_m")) or 3.0
            dx = float(np.median(np.diff(o))) if o.size > 1 else 1.0
            m = np.abs(o) <= max(w, 3 * dx)
        if m.any():
            i = np.flatnonzero(m)[int(np.argmin(z[m]))]
            r["thalweg_offset_m"], r["thalweg_z_m"] = float(o[i]), float(z[i])
    tz = _num(r.get("thalweg_z_m"))
    if _num(r.get("bankfull_stage_m")) is None and tz is not None and dmax:
        r["bankfull_stage_m"] = tz + dmax
    if _num(r.get("floodprone_stage_m")) is None:
        if r.get("basis") == "measured" and tz is not None and dmax:
            r["floodprone_stage_m"] = tz + 2.0 * dmax
        else:
            edges = [e for e in (_num(r.get("floodprone_left_offset_m")),
                                 _num(r.get("floodprone_right_offset_m")))
                     if e is not None and o[0] < e < o[-1]]
            if edges:
                r["floodprone_stage_m"] = float(np.mean(np.interp(edges, o, z)))


# -- drawing ------------------------------------------------------------------
#
# Speed matters: Ruidoso has ~4,300 sections. A fresh figure per PNG cost
# ~190 ms, most of it building axes, ticks and legend. The canvases below
# build the figure once and redraw only the data (~4x faster), and the PNGs
# are written as 128-colour palette images (~3x smaller, no visible loss on
# flat-colour plots).


def _style(ax, compact: bool) -> None:
    from matplotlib.ticker import MaxNLocator

    from ..render import AXIS, GRID, INK_2, SURFACE
    ax.set_facecolor(SURFACE)
    ax.grid(True, color=GRID, linewidth=0.7, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.tick_params(colors=INK_2, labelsize=7 if compact else 8, length=3)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=7 if not compact else 6))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=5 if not compact else 4))
    ax.ticklabel_format(useOffset=False, style="plain")


def _clear(ax, keep: tuple) -> None:
    """Remove the data artists, keep the axes, ticks and fixed labels."""
    for a in (list(ax.lines) + list(ax.collections) + list(ax.patches)
              + [t for t in ax.texts if t not in keep]):
        a.remove()


def _draw(ax, x: np.ndarray, z: np.ndarray, r: dict, s: float,
          zoom: bool, compact: bool, dx: float) -> None:
    """One panel: the whole section, or (``zoom``) the channel close up.

    ``x`` is already in plot coordinates (``s`` * offset, so the section is
    seen looking downstream); every stored offset is multiplied by ``s``.
    """
    from ..render import AXIS, INK, INK_2, SERIES_1, SERIES_2, SERIES_3

    used = bool(r.get("used"))
    bl, br = _num(r.get("bank_left_offset_m")), _num(r.get("bank_right_offset_m"))
    bstage = _num(r.get("bankfull_stage_m"))
    fl = _num(r.get("floodprone_left_offset_m"))
    fr = _num(r.get("floodprone_right_offset_m"))
    fstage = _num(r.get("floodprone_stage_m"))
    toff, tz = _num(r.get("thalweg_offset_m")), _num(r.get("thalweg_z_m"))
    w_reg = _num(r.get("expected_width_m"))
    w_bf = _num(r.get("bankfull_width_m"))
    banks = bl is not None and br is not None and bstage is not None
    if banks:
        a, b = sorted((s * bl, s * br))

    # -- extent
    if zoom:
        if banks:
            c = 0.5 * (a + b)
        elif toff is not None:
            c = s * toff
        else:
            c = 0.0
        half = max(1.25 * (w_bf or 0.0), 1.5 * (w_reg or 0.0), 5.0 * dx, 2.0)
        x0, x1 = c - half, c + half
        m = (x >= x0) & (x <= x1)
        if m.sum() < 2:
            m = np.ones_like(x, dtype=bool)
        zlo = float(z[m].min())
        tops = [v for v in (bstage, fstage) if v is not None]
        ztop = max(tops + [zlo + 1.0])
        h = ztop - zlo
        y0, y1 = zlo - 0.12 * h, ztop + 0.25 * h
    else:
        x0, x1 = float(x.min()), float(x.max())
        zlo = float(z.min())
        # A flood-prone stage above the whole section (it ran off the ends)
        # must still be on the plot.
        zhi = max([float(z.max())] + [v for v in (bstage, fstage)
                                      if v is not None])
        h = max(zhi - zlo, 0.5)
        y0, y1 = zlo - 0.06 * h, zhi + 0.10 * h
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)

    # -- ground
    ax.fill_between(x, z, y0, color=_GROUND, zorder=1, linewidth=0)
    ax.plot(x, z, color=INK_2, linewidth=1.3 if compact else 1.6, zorder=5,
            solid_joinstyle="round")

    # -- flood-prone: water to twice the max depth, over the whole section
    if fl is not None and fr is not None and fstage is not None:
        fa, fb = sorted((s * fl, s * fr))
        wet = (x >= fa) & (x <= fb) & (z < fstage)
        ax.fill_between(x, z, fstage, where=wet, color=SERIES_3, alpha=0.13,
                        zorder=2, linewidth=0, interpolate=True)
        ax.plot([fa, fb], [fstage, fstage], color=SERIES_3, linewidth=1.3,
                linestyle=(0, (5, 3)), zorder=6)
        if r.get("floodprone_lower_bound") in (True, 1, "True", "1"):
            # A side ran off the section: the width is at least this.
            for xe in (fa, fb):
                if xe <= float(x.min()) + dx or xe >= float(x.max()) - dx:
                    ax.annotate("", xy=(xe + (-1 if xe < 0 else 1) * 0.04
                                        * (x1 - x0), fstage),
                                xytext=(xe, fstage), zorder=7,
                                arrowprops=dict(arrowstyle="->",
                                                color=SERIES_3, lw=1.2))

    # -- bankfull
    if banks:
        col = SERIES_1 if used else _REJECT
        wet = (x >= a) & (x <= b) & (z < bstage)
        ax.fill_between(x, z, bstage, where=wet, color=col,
                        alpha=0.38 if used else 0.28, zorder=3, linewidth=0,
                        interpolate=True)
        ax.plot([a, b], [bstage, bstage], color=col, linewidth=1.5, zorder=7)
        ax.plot([a, b], [bstage, bstage], "o", color=SERIES_2 if used
                else _REJECT, markersize=4.5 if compact else 6,
                markeredgecolor="white", markeredgewidth=1.0, zorder=8)
        if zoom and tz is not None and not compact:
            # The other stages the detector considered, faintly.
            for hs in str(r.get("candidate_stages_m") or "").split(","):
                h_ = _num(hs.strip())
                if h_ is None or abs(tz + h_ - bstage) < 1e-3:
                    continue
                ax.axhline(tz + h_, color=AXIS, linewidth=0.8,
                           linestyle=(0, (1, 2)), zorder=4)

    # -- the bank picker's own answer, where it was only a check
    ps = _num(r.get("picker_stage_m"))
    pl, pr_ = (_num(r.get("picker_left_offset_m")),
               _num(r.get("picker_right_offset_m")))
    if ps is not None and pl is not None and pr_ is not None:
        pa, pb = sorted((s * pl, s * pr_))
        ax.plot([pa, pb], [ps, ps], color=_PICKER, linewidth=1.1,
                linestyle=(0, (3, 2)), zorder=7)
        ax.plot([pa, pb], [ps, ps], "o", markerfacecolor="white",
                markeredgecolor=_PICKER, markeredgewidth=1.2,
                markersize=4 if compact else 5.5, zorder=8)

    # -- regional channel width, centred on the thalweg, just under it
    if zoom and w_reg and tz is not None:
        xc = s * toff if toff is not None else 0.0
        yb = tz - 0.06 * (y1 - y0)
        tk = 0.02 * (y1 - y0)
        ax.plot([xc - w_reg / 2, xc - w_reg / 2, xc - w_reg / 2,
                 xc + w_reg / 2, xc + w_reg / 2, xc + w_reg / 2],
                [yb - tk, yb + tk, yb, yb, yb + tk, yb - tk], color=INK,
                linewidth=1.1, zorder=7)

    # -- where the mapped channel crosses, and the thalweg
    ax.axvline(0.0, color=AXIS, linewidth=1.0, linestyle=(0, (4, 3)),
               zorder=4)
    if toff is not None and tz is not None:
        ax.plot([s * toff], [tz], "v", color=SERIES_1, markersize=5 if compact
                else 6.5, markeredgecolor="white", markeredgewidth=0.8,
                zorder=9)


def _ve(ax, fig) -> str:
    """Vertical exaggeration of a panel, from its size on the page."""
    bb = ax.get_position()
    w_in, h_in = bb.width * fig.get_figwidth(), bb.height * fig.get_figheight()
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    if w_in <= 0 or h_in <= 0 or y1 <= y0:
        return ""
    ve = ((x1 - x0) / w_in) / ((y1 - y0) / h_in)
    # Below 1 the section is squashed, not exaggerated: say so.
    return f"VE {ve:.0f}x" if ve >= 9.5 else f"VE {ve:.1f}x"


def _fmt(v: float | None, spec: str, unit: str = "") -> str | None:
    return None if v is None else f"{format(v, spec)}{unit}"


def _describe(r: dict) -> str:
    """The numbers of one section, as a line of text."""
    bits = []
    st = str(r.get("status") or "")
    if _num(r.get("bankfull_width_m")) is not None:
        at = (" at regional depth" if r.get("picked_by") == "regional depth"
              else "")
        b = [f"bankfull{at} W {r['bankfull_width_m']:.1f} m",
             _fmt(_num(r.get("bankfull_max_depth_m")), ".2f", " m max depth"),
             ("W/D " + _fmt(_num(r.get("width_depth_ratio")), ".1f"))
             if _num(r.get("width_depth_ratio")) is not None else None]
        bits.append(" · ".join(x for x in b if x)
                    + ("" if r.get("used") else " (not used)"))
    fw = _num(r.get("floodprone_width_m"))
    if fw is not None:
        lb = r.get("floodprone_lower_bound") in (True, 1, "True", "1")
        er = _num(r.get("entrenchment_ratio"))
        t = f"flood-prone {'at least ' if lb else ''}{fw:.1f} m"
        if er is not None:
            t += f" · ER {er:.2f}"
            if r.get("basis") == "regional":
                t += " (vs regional W)"
        bits.append(t)
    wr = _num(r.get("expected_width_m"))
    if wr is not None:
        bits.append(f"regional W {wr:.1f} m")
    pw, pd_ = _num(r.get("picker_width_m")), _num(r.get("picker_depth_ratio"))
    if pw is not None:
        bits.append(f"picker W {pw:.1f} m" + (f", {pd_:.1f}x regional depth"
                                              if pd_ is not None else ""))
    if st.startswith("error"):
        bits.append(st)
    return "   ·   ".join(bits) if bits else "nothing measurable"


def _status_text(r: dict) -> str:
    st = str(r.get("status") or "")
    basis = str(r.get("basis") or "none")
    label = STATUS_STYLE.get(st, (None, st))[1] if st else "not measured"
    return f"{label}  [{basis}]" if basis not in ("none", "") else label


def _reach_line(rr: dict | None) -> str:
    if not rr:
        return ""
    t = rr.get("stream_type") or "untyped"
    bits = [f"Reach type {t}"]
    if rr.get("stream_basis"):
        bits.append(f"{rr['stream_basis']} basis")
    if rr.get("stream_confidence"):
        bits.append(f"{rr['stream_confidence']} confidence")
    sl = _num(rr.get("slope"))
    if sl is not None:
        bits.append(f"slope {100 * sl:.1f}%")
    sn = _num(rr.get("sinuosity"))
    if sn is not None:
        bits.append(f"sinuosity {sn:.2f}")
    if rr.get("no_type_fits") in (True, 1, "True", "1"):
        bits.append("no type fits within tolerance")
    return " · ".join(bits)


def _direction(rr: dict | None) -> tuple[float, str]:
    """(sign, words): offsets are to the LEFT of the line as digitized, so a
    reach digitized downstream is seen looking downstream with x = -offset."""
    zs, ze = (_num((rr or {}).get("z_start")), _num((rr or {}).get("z_end")))
    if zs is not None and ze is not None and zs != ze:
        return (-1.0 if zs > ze else 1.0), "looking downstream"
    return -1.0, "looking along the line as digitized"


def _ends(geom, s: float) -> tuple[str, str]:
    """Compass direction of the plot's left and right ends."""
    try:
        (xa, ya), (xb, yb) = geom.coords[0], geom.coords[-1]
    except Exception:                                        # noqa: BLE001
        return "", ""
    az_plus = math.degrees(math.atan2(xb - xa, yb - ya))  # to +offset end
    right = az_plus if s > 0 else az_plus + 180.0
    return _compass(right + 180.0), _compass(right)


def _legend(fig, y: float) -> None:
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    from ..render import AXIS, INK, INK_2, SERIES_1, SERIES_2, SERIES_3
    h = [Patch(color=SERIES_1, alpha=0.45, label="bankfull (used)"),
         Patch(color=_REJECT, alpha=0.35, label="bank pick not used"),
         Line2D([], [], marker="o", color=SERIES_2, linestyle="none",
                markeredgecolor="white", label="bank tops"),
         Line2D([], [], color=SERIES_3, linestyle=(0, (5, 3)),
                label="flood-prone stage (2 x max depth)"),
         Line2D([], [], color=INK, label="regional channel width"),
         Line2D([], [], marker="v", color=SERIES_1, linestyle="none",
                markeredgecolor="white", label="thalweg"),
         Line2D([], [], color=AXIS, linestyle=(0, (4, 3)),
                label="mapped channel line"),
         Line2D([], [], color=_PICKER, linestyle=(0, (3, 2)), marker="o",
                markerfacecolor="white", markeredgecolor=_PICKER,
                label="bank picker (check)")]
    fig.legend(handles=h, loc="lower center", bbox_to_anchor=(0.5, y),
               ncol=4, frameon=False, fontsize=7.5, labelcolor=INK_2,
               handlelength=2.2, columnspacing=1.4)


def _save(fig, out: Path) -> None:
    """Draw once, write a 128-colour PNG."""
    from PIL import Image

    fig.canvas.draw()
    rgb = np.asarray(fig.canvas.buffer_rgba())[..., :3]
    im = Image.fromarray(np.ascontiguousarray(rgb)).quantize(
        colors=128, method=Image.Quantize.FASTOCTREE,
        dither=Image.Dither.NONE)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, format="PNG", compress_level=6)


# -- one section --------------------------------------------------------------


class _SectionCanvas:
    """The single-section figure, built once and redrawn per section."""

    def __init__(self, dpi: int):
        import matplotlib.pyplot as plt

        from ..render import INK, INK_2, SURFACE
        self.fig = fig = plt.figure(figsize=(10.0, 3.9), dpi=dpi)
        fig.patch.set_facecolor(SURFACE)
        self.ax1 = fig.add_axes([0.065, 0.255, 0.575, 0.47])
        self.ax2 = fig.add_axes([0.70, 0.255, 0.285, 0.47])
        self.keep = {}
        for ax, name in ((self.ax1, "whole section"), (self.ax2, "channel")):
            _style(ax, compact=False)
            t = [ax.text(0.0, 1.02, name, transform=ax.transAxes,
                         fontsize=8.5, color=INK_2, va="bottom"),
                 ax.text(1.0, 1.02, "", transform=ax.transAxes, fontsize=7.5,
                         color=INK_2, ha="right", va="bottom"),
                 ax.text(0.0, -0.13, "", transform=ax.transAxes, fontsize=8,
                         color=INK_2, ha="left", va="top",
                         fontweight="medium"),
                 ax.text(1.0, -0.13, "", transform=ax.transAxes, fontsize=8,
                         color=INK_2, ha="right", va="top",
                         fontweight="medium")]
            self.keep[ax] = t
        self.ax1.set_ylabel("elevation, m", fontsize=8, color=INK_2)
        self.ax2.set_xlabel("metres", fontsize=8, color=INK_2, labelpad=2)
        self.t_title = fig.text(0.012, 0.955, "", fontsize=12, color=INK,
                                fontweight="medium", va="top")
        self.t_status = fig.text(0.988, 0.955, "", fontsize=10.5,
                                 fontweight="medium", va="top", ha="right")
        self.t_desc = fig.text(0.012, 0.873, "", fontsize=9.5, color=INK,
                               va="top")
        self.t_reach = fig.text(0.012, 0.81, "", fontsize=9, color=INK_2,
                                va="top")
        _legend(fig, 0.0)

    def render(self, out: Path, o: np.ndarray, z: np.ndarray, r: dict,
               rr: dict | None, k: int, n: int, id_field: str) -> None:
        from ..render import INK_2
        s, looking = _direction(rr)
        x = s * o
        order = np.argsort(x)
        x, zz = x[order], z[order]
        dx = float(np.median(np.diff(x))) if x.size > 1 else 1.0
        left, right = _ends(r.get("geometry"), s)
        for ax, zoom in ((self.ax1, False), (self.ax2, True)):
            _clear(ax, tuple(self.keep[ax]))
            _draw(ax, x, zz, r, s, zoom=zoom, compact=False, dx=dx)
            _, ve, lt, rt = self.keep[ax]
            ve.set_text(_ve(ax, self.fig))
            lt.set_text(left)
            rt.set_text(right)
        self.ax1.set_xlabel(f"metres from the mapped channel, {looking}",
                            fontsize=8, color=INK_2, labelpad=2)
        st = str(r.get("status") or "")
        self.t_title.set_text(
            f"Reach {r.get(id_field)}  ·  section {k} of {n}  ·  "
            f"xs {r.get('xs_id')}  ·  station {r.get('station_m', 0):.0f} m")
        self.t_status.set_text(_status_text(r))
        self.t_status.set_color(STATUS_STYLE.get(st, ("#d03b3b", ""))[0])
        self.t_desc.set_text(_describe(r))
        self.t_reach.set_text(_reach_line(rr))
        _save(self.fig, out)

    def close(self) -> None:
        import matplotlib.pyplot as plt
        plt.close(self.fig)


# -- one reach ----------------------------------------------------------------


class _ReachCanvas:
    """A reach sheet with ``n`` section rows, built once per row count."""

    HEAD, ROW, FOOT = 1.05, 1.62, 0.55

    def __init__(self, n: int, dpi: int):
        import matplotlib.pyplot as plt

        from ..render import INK, INK_2, SURFACE
        H = self.HEAD + n * self.ROW + self.FOOT
        self.fig = fig = plt.figure(figsize=(10.0, H), dpi=dpi)
        fig.patch.set_facecolor(SURFACE)
        self.t_title = fig.text(0.012, 1 - 0.18 / H, "", fontsize=14,
                                color=INK, fontweight="medium", va="top")
        self.t_reach = fig.text(0.012, 1 - 0.52 / H, "", fontsize=9.5,
                                color=INK_2, va="top")
        self.t_nums = fig.text(0.012, 1 - 0.80 / H, "", fontsize=8.5,
                               color=INK_2, va="top")
        self.rows = []
        for i in range(n):
            top = 1 - (self.HEAD + i * self.ROW + 0.30) / H
            hh = (self.ROW - 0.52) / H
            ax1 = fig.add_axes([0.065, top - hh, 0.575, hh])
            ax2 = fig.add_axes([0.70, top - hh, 0.285, hh])
            keep = {}
            for ax in (ax1, ax2):
                _style(ax, compact=True)
                # Compass letters inside the bottom corners: below the
                # axis they collide with the tick labels at this size.
                box = dict(facecolor=SURFACE, edgecolor="none", pad=1.0,
                           alpha=0.85)
                keep[ax] = [
                    ax.text(1.0, 0.97, "", transform=ax.transAxes,
                            fontsize=6.5, color=INK_2, ha="right", va="top"),
                    ax.text(0.01, 0.04, "", transform=ax.transAxes,
                            fontsize=7, color=INK_2, ha="left",
                            va="bottom", fontweight="medium", bbox=box,
                            zorder=20),
                    ax.text(0.99, 0.04, "", transform=ax.transAxes,
                            fontsize=7, color=INK_2, ha="right",
                            va="bottom", fontweight="medium", bbox=box,
                            zorder=20)]
            keep[ax1] += [
                ax1.text(0.0, 1.04, "", transform=ax1.transAxes, fontsize=8.5,
                         color=INK, fontweight="medium", va="bottom"),
                ax1.text(0.30, 1.04, "", transform=ax1.transAxes, fontsize=8,
                         va="bottom")]
            keep[ax2] += [ax2.text(0.0, 1.04, "", transform=ax2.transAxes,
                                   fontsize=7, color=INK_2, va="bottom")]
            self.rows.append((ax1, ax2, keep))
        _legend(fig, 0.0)

    def render(self, out: Path, secs: list, rr: dict | None, sid: Any) -> None:
        s, looking = _direction(rr)
        t = (rr or {}).get("stream_type") or "untyped"
        self.t_title.set_text(f"Reach {sid}  ·  {t}")
        self.t_reach.set_text(_reach_line(rr))
        bits = []
        for k, lab, spec in (("entrenchment_ratio", "ER", ".2f"),
                             ("width_depth_ratio", "W/D", ".1f"),
                             ("bankfull_width_m", "bankfull W", ".1f"),
                             ("regional_width_m", "regional W", ".1f"),
                             ("drainage_km2", "drainage", ".2f")):
            v = _num((rr or {}).get(k))
            if v is not None:
                unit = (" km²" if k == "drainage_km2" else
                        " m" if k.endswith("_m") else "")
                bits.append(f"{lab} {format(v, spec)}{unit}")
        alt = (rr or {}).get("stream_candidates")
        if alt:
            bits.append(f"alternatives {alt}")
        bits.append(f"sections {looking}, upstream first")
        self.t_nums.set_text("   ·   ".join(bits))
        for (ax1, ax2, keep), (o, z, r) in zip(self.rows, secs):
            x = s * o
            order = np.argsort(x)
            x, zz = x[order], z[order]
            dx = float(np.median(np.diff(x))) if x.size > 1 else 1.0
            left, right = _ends(r.get("geometry"), s)
            for ax, zoom in ((ax1, False), (ax2, True)):
                _clear(ax, tuple(keep[ax]))
                _draw(ax, x, zz, r, s, zoom=zoom, compact=True, dx=dx)
                ve, lt, rt = keep[ax][:3]
                ve.set_text(_ve(ax, self.fig))
                lt.set_text(left)
                rt.set_text(right)
            st = str(r.get("status") or "")
            head, stat = keep[ax1][3:5]
            head.set_text(f"xs {r.get('xs_id')} · station "
                          f"{r.get('station_m', 0):.0f} m")
            stat.set_text(_status_text(r))
            stat.set_color(STATUS_STYLE.get(st, ("#d03b3b", ""))[0])
            keep[ax2][3].set_text(_describe(r).split("   ·   ")[0][:60])
        _save(self.fig, out)

    def close(self) -> None:
        import matplotlib.pyplot as plt
        plt.close(self.fig)


# -- the folder ---------------------------------------------------------------


def _index_html(path: Path, reaches: list[dict], by_reach: dict,
                id_field: str) -> None:
    rows = []
    for rr in reaches:
        sid = rr[id_field]
        secs = by_reach.get(sid, [])
        t = rr.get("stream_type") or "untyped"
        key = " ".join(str(v).lower() for v in (
            sid, t, rr.get("stream_basis"), rr.get("stream_confidence"),
            "no_type_fits" if rr.get("no_type_fits") in (True, 1, "True")
            else ""))
        xs = " ".join(
            f'<a href="{html.escape(section_plot_file(r["xs_id"]))}">'
            f'xs {r["xs_id"]}</a>' for r in secs)
        rows.append(
            f'<section data-k="{html.escape(key)}" id="reach-{_safe(sid)}">'
            f'<h2>Reach {html.escape(str(sid))} · {html.escape(str(t))}</h2>'
            f'<p>{html.escape(_reach_line(rr))}</p>'
            f'<a href="{html.escape(reach_plot_file(sid))}"><img loading="lazy" '
            f'src="{html.escape(reach_plot_file(sid))}" alt="reach {sid}"></a>'
            f'<p class="xs">Sections: {xs}</p></section>')
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stream-type sections</title>
<style>
body {{ font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
  margin: 0 auto; max-width: 1100px; padding: 16px; color: #0b0b0b;
  background: #fcfcfb; }}
h1 {{ font-size: 22px; margin: 8px 0; }}
h2 {{ font-size: 17px; margin: 0 0 4px; }}
section {{ border-top: 1px solid #e4e3df; padding: 14px 0; }}
section p {{ margin: 2px 0 8px; color: #52514e; }}
img {{ width: 100%; height: auto; border: 1px solid #e4e3df; }}
.xs a {{ margin-right: 10px; }}
input {{ font: inherit; padding: 6px 10px; width: min(420px, 100%);
  border: 1px solid #b8b7b2; border-radius: 6px; }}
</style></head><body>
<h1>Stream-type sections</h1>
<p>{len(reaches)} reaches, {sum(len(v) for v in by_reach.values())} sections.
Each sheet shows a reach's sections upstream first, looking downstream. The
reach and <code>xs</code> numbers are <code>{html.escape(id_field)}</code> and
<code>xs_id</code> in stream_types.gpkg and stream_type_sections.gpkg.</p>
<p><input id="q" placeholder="Filter: reach id, type, basis, confidence, no_type_fits"></p>
{''.join(rows)}
<script>
document.getElementById('q').addEventListener('input', function () {{
  var q = this.value.trim().toLowerCase().split(/\\s+/).filter(Boolean);
  document.querySelectorAll('section').forEach(function (s) {{
    var k = s.getAttribute('data-k');
    s.style.display = q.every(function (w) {{ return k.indexOf(w) >= 0; }})
      ? '' : 'none';
  }});
}});
</script>
</body></html>
"""
    path.write_text(doc, encoding="utf-8")


def _rewrite_with_links(gpkg: Path, layer: str, files: list[str],
                        warnings: list[str]):
    """Add plot_file/plot_path to a GeoPackage layer if they are missing."""
    import geopandas as gpd

    gdf = gpd.read_file(str(gpkg), layer=layer)
    want_file = files
    want_path = [str((gpkg.parent / f).resolve()) for f in files]
    if ("plot_file" in gdf.columns and "plot_path" in gdf.columns
            and list(gdf["plot_file"]) == want_file
            and list(gdf["plot_path"]) == want_path):
        return gdf, gpkg
    gdf["plot_file"] = want_file
    gdf["plot_path"] = want_path
    tmp = gpkg.with_name(gpkg.stem + ".tmp.gpkg")
    if tmp.exists():
        tmp.unlink()
    gdf.to_file(str(tmp), driver="GPKG", layer=layer)
    try:
        os.replace(tmp, gpkg)
        return gdf, gpkg
    except PermissionError:
        # Windows: the layer is open in QGIS or ArcGIS. Leave it alone and
        # write the linked copy beside it.
        alt = gpkg.with_name(gpkg.stem + "_linked.gpkg")
        if alt.exists():
            alt.unlink()
        os.replace(tmp, alt)
        warnings.append(
            f"{gpkg.name} is open in another program, so the plot links went "
            f"into {alt.name} instead. Close it and rerun to update the "
            f"original.")
        return gdf, alt


def _render_jobs(jobs: list, d: Path, dpi: int, id_field: str,
                 sections: bool, reaches: bool, skip_existing: bool,
                 progress_label: str | None = None
                 ) -> tuple[int, int, list[str]]:
    """Render the section PNGs and reach sheets for a list of reaches.

    Module-level so a worker process can run it. Returns (sections drawn,
    sheets drawn, failures).
    """
    import matplotlib
    matplotlib.use("Agg")
    n_sec = n_reach = 0
    failed: list[str] = []
    canvas = _SectionCanvas(dpi) if sections else None
    sheets: dict[int, _ReachCanvas] = {}
    it = (progress(jobs, label=progress_label) if progress_label else jobs)
    try:
        for sid, rr, secs in it:
            if canvas is not None:
                for k, (o, z, r) in enumerate(secs, 1):
                    out = d / section_plot_file(r["xs_id"])
                    if skip_existing and out.exists():
                        continue
                    try:
                        canvas.render(out, o, z, r, rr, k, len(secs),
                                      id_field)
                        n_sec += 1
                    except Exception as exc:                 # noqa: BLE001
                        failed.append(f"xs {r['xs_id']}: "
                                      f"{type(exc).__name__}: {exc}")
            if reaches:
                out = d / reach_plot_file(sid)
                if skip_existing and out.exists():
                    continue
                try:
                    if len(secs) not in sheets:
                        sheets[len(secs)] = _ReachCanvas(len(secs), dpi)
                    sheets[len(secs)].render(out, secs, rr, sid)
                    n_reach += 1
                except Exception as exc:                     # noqa: BLE001
                    failed.append(f"reach {sid}: {type(exc).__name__}: "
                                  f"{exc}")
    finally:
        if canvas is not None:
            canvas.close()
        for c in sheets.values():
            c.close()
    return n_sec, n_reach, failed


def plot_stream_sections(stream_types_dir: str | Path,
                         sections: bool = True, reaches: bool = True,
                         style: bool = True, index: bool = True,
                         dpi: int = 90, skip_existing: bool = False,
                         workers: int | None = None,
                         reach_ids: list | None = None) -> dict[str, Any]:
    """Render the stream-type sections and link them to the GIS layers.

    Parameters
    ----------
    stream_types_dir
        The ``stream_types_dir`` folder of a run (any version that wrote
        ``stream_type_profiles.csv``). No DEM needed.
    sections, reaches
        One PNG per section line / one sheet per reach.
    style
        Write ``plot_file``/``plot_path`` into both GeoPackages and store the
        QGIS style that shows the plot on hover and opens it on click.
    index
        Write ``stream_type_plots.html``.
    skip_existing
        Keep PNGs already on disk (for resuming an interrupted render).
    reach_ids
        Render only these reaches (the links and the index still cover all).
    workers
        Processes to render with. None: one process under 800 sections,
        otherwise up to 8 (CPU count - 1). ~70 ms a section and ~200 ms a
        reach sheet per process; 0 or 1 forces one process.

    Returns counts, paths and timings.
    """
    import geopandas as gpd
    import matplotlib
    import pandas as pd

    matplotlib.use("Agg")
    t0 = time.monotonic()
    d = Path(stream_types_dir)
    sec_gpkg, reach_gpkg = d / "stream_type_sections.gpkg", d / "stream_types.gpkg"
    prof_csv = d / "stream_type_profiles.csv"
    for p in (sec_gpkg, prof_csv):
        if not p.exists():
            raise GeolensError(
                f"{p.name} not found in {d}. Point this at the stream_types_dir "
                f"folder of a run.")
    warnings: list[str] = []
    sec = gpd.read_file(str(sec_gpkg), layer="stream_type_sections")
    prof = pd.read_csv(prof_csv)
    id_field = next((c for c in prof.columns
                     if c not in ("xs_id", "station_m", "offset_m",
                                  "elevation_m")), "seg_id")
    reach_df = (gpd.read_file(str(reach_gpkg), layer="stream_types")
                if reach_gpkg.exists() else None)
    rmap = ({r[id_field]: r for r in reach_df.to_dict("records")}
            if reach_df is not None and id_field in reach_df.columns else {})

    prof = prof.dropna(subset=["offset_m", "elevation_m"])
    prof = prof.sort_values(["xs_id", "offset_m"])
    ids = prof["xs_id"].to_numpy()
    cuts = np.flatnonzero(np.diff(ids)) + 1
    o_all = prof["offset_m"].to_numpy(dtype=float)
    z_all = prof["elevation_m"].to_numpy(dtype=float)
    starts = np.r_[0, cuts]
    ends = np.r_[cuts, ids.size]
    pmap = {int(ids[a]): (o_all[a:b], z_all[a:b]) for a, b in zip(starts, ends)}

    recs = sec.to_dict("records")
    by_reach: dict[Any, list[dict]] = {}
    for r in recs:
        by_reach.setdefault(r[id_field], []).append(r)
    for sid, rs in by_reach.items():
        s, _ = _direction(rmap.get(sid))
        # Upstream first: station runs along the line as digitized, and
        # s < 0 means it was digitized downstream.
        rs.sort(key=lambda r: float(r.get("station_m") or 0.0),
                reverse=s > 0)

    # One job per reach: its sections, upstream first, with their profiles.
    jobs = []
    for sid, rs in by_reach.items():
        secs = []
        for r in rs:
            oz = pmap.get(int(r["xs_id"]))
            if oz is not None and oz[0].size >= 2:
                _derive_stages(oz[0], oz[1], r)
                secs.append((oz[0], oz[1], r))
        if secs and (reach_ids is None or sid in set(reach_ids)):
            jobs.append((sid, rmap.get(sid), secs))
    n_total = sum(len(j[2]) for j in jobs)
    if workers is None:
        # Worth a pool from about a minute of work; spawning costs a few
        # seconds per worker (Windows starts each one from scratch).
        workers = (0 if n_total < 800 else
                   max(0, min(8, (os.cpu_count() or 2) - 1)))
    args = dict(d=d, dpi=dpi, id_field=id_field, sections=sections,
                reaches=reaches, skip_existing=skip_existing)
    n_sec = n_reach = 0
    failed: list[str] = []
    done = False
    if workers and workers > 1 and len(jobs) > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        n_chunks = min(len(jobs), workers * 6)
        chunks = [jobs[i::n_chunks] for i in range(n_chunks)]
        try:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                futs = [ex.submit(_render_jobs, chunk, **args)
                        for chunk in chunks]
                for f in progress(as_completed(futs), total=len(futs),
                                  label=f"section plots ({workers} workers)"):
                    a_, b_, c_ = f.result()
                    n_sec, n_reach = n_sec + a_, n_reach + b_
                    failed += c_
            done = True
        except Exception as exc:                             # noqa: BLE001
            # A pool that will not start (a script without a __main__ guard
            # on Windows, a locked-down machine) must not cost the plots.
            warnings.append(f"Parallel rendering failed ({type(exc).__name__}"
                            f": {exc}); rendered in one process instead.")
            n_sec = n_reach = 0
            failed = []
            args["skip_existing"] = True
    if not done:
        a_, b_, c_ = _render_jobs(jobs, progress_label="section plots",
                                  **args)
        n_sec, n_reach, failed = n_sec + a_, n_reach + b_, failed + c_
    if failed:
        warnings.append(f"{len(failed)} plot(s) failed, first: {failed[0]}")

    linked = {}
    if style:
        from ..gis_style import plot_link_qml, write_qgis_style

        _, p = _rewrite_with_links(
            sec_gpkg, "stream_type_sections",
            [section_plot_file(x) for x in sec["xs_id"]], warnings)
        qml = plot_link_qml(
            display_expression=(f"'xs ' || \"xs_id\" || ' (reach ' || "
                                f"\"{id_field}\" || ') - ' || \"status\""),
            category_field="status",
            categories=[(k, c, lab) for k, (c, lab) in STATUS_STYLE.items()],
            line_width_mm=0.7, action_name="Open section plot",
            missing_note="No plot for this section yet. Render them with "
                         "geolens.api.plot_stream_sections(folder).")
        write_qgis_style(p, "stream_type_sections", qml)
        linked["sections"] = str(p)
        if reach_df is not None:
            _, p = _rewrite_with_links(
                reach_gpkg, "stream_types",
                [reach_plot_file(v) for v in reach_df[id_field]], warnings)
            qml = plot_link_qml(
                display_expression=(f"'Reach ' || \"{id_field}\" || ' - ' || "
                                    f"coalesce(\"stream_type\", 'untyped')"),
                category_field="stream_letter",
                categories=LETTER_COLOURS, other_colour="#b8b7b2",
                line_width_mm=1.0, action_name="Open reach sheet",
                missing_note="No sheet for this reach yet. Render them with "
                             "geolens.api.plot_stream_sections(folder).")
            write_qgis_style(p, "stream_types", qml)
            linked["reaches"] = str(p)

    idx = None
    if index and reaches and reach_df is not None:
        idx = d / INDEX_NAME
        _index_html(idx, reach_df.to_dict("records"), by_reach, id_field)

    secs_t = time.monotonic() - t0
    note(f"  section plots: {n_sec} sections, {n_reach} reach sheets in "
         f"{secs_t:.0f} s -> {d / SECTION_DIR}")
    return {"section_plots": n_sec, "reach_sheets": n_reach,
            "section_dir": str(d / SECTION_DIR),
            "reach_dir": str(d / REACH_DIR),
            "index": str(idx) if idx else None, "linked": linked,
            "seconds": round(secs_t, 1), "warnings": warnings}
