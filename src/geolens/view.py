"""Inline display for notebooks: look at each result as it comes out.

Everything else in this package writes files. That is right for products, and
wrong for the moment just after a step finishes, when the only question is
"does that look sane". Opening QGIS to answer it breaks the loop.

These functions return a matplotlib figure, so in Jupyter they draw inline. They
are for looking, not for publication — :mod:`geolens.render` makes the figures
you keep.

THE THING THAT MAKES THIS WORK ON REAL DATA
-------------------------------------------
**Every raster is read decimated.** A 1 m DEM over a county is a billion cells
and 4 GB as float32; reading it to draw a 900-pixel-wide picture would exhaust
memory to throw away 99.9% of what it read. Each read here asks rasterio for a
small ``out_shape``, which uses the file's overviews when it has them and
decimates on read when it does not. Either way the full array never exists.

This is not a nicety. Display is exactly where a "just read it and plot it"
helper silently works on every test fixture and then kills the kernel on the
first real DEM.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .safety import Guard, GeolensError

MAX_PIXELS = 1000          # longest display edge, before decimation

# Categorical palettes. Ordered, because confinement and valley type are ordered
# and a qualitative palette would hide that.
CONFINEMENT_COLORS = {
    "confined": "#7b3294",
    "partly confined": "#c2a5cf",
    "unconfined": "#a6dba0",
    "unknown": "#cccccc",
}

VALLEY_TYPE_COLORS = {
    "I": "#67001f", "II": "#b2182b", "III": "#ef8a62", "IV": "#fddbc7",
    "V": "#d1e5f0", "VI": "#67a9cf", "VII": "#2166ac", "VIII": "#8c510a",
    "IX": "#d8b365", "X": "#5ab4ac", "XI": "#01665e",
}


def _read_decimated(path: str | Path, max_px: int = MAX_PIXELS,
                    band: int = 1) -> tuple[np.ma.MaskedArray, tuple, Any]:
    """Read a raster small enough to draw, without loading it all.

    Returns the array, its extent for imshow, and the CRS.
    """
    import rasterio

    with rasterio.open(str(path)) as src:
        longest = max(src.width, src.height)
        scale = max(1, int(np.ceil(longest / max_px)))
        out_h = max(1, src.height // scale)
        out_w = max(1, src.width // scale)
        arr = src.read(band, out_shape=(out_h, out_w), masked=True)
        b = src.bounds
        return arr, (b.left, b.right, b.bottom, b.top), src.crs


def _hillshade(z: np.ma.MaskedArray, azimuth: float = 315.0,
               altitude: float = 45.0, vert_exag: float = 2.0) -> np.ndarray:
    """A plain hillshade, for context under everything else.

    Without relief underneath, a valley-bottom polygon is a blob on a white
    page and you cannot tell whether it followed the valley.
    """
    zz = np.asarray(z.filled(np.nan), dtype="float64") * vert_exag
    dy, dx = np.gradient(zz)
    slope = np.pi / 2.0 - np.arctan(np.hypot(dx, dy))
    aspect = np.arctan2(-dx, dy)
    az = np.radians(360.0 - azimuth + 90.0)
    alt = np.radians(altitude)
    hs = (np.sin(alt) * np.sin(slope)
          + np.cos(alt) * np.cos(slope) * np.cos(az - aspect))
    return np.clip(np.nan_to_num(hs, nan=0.5), 0, 1)


def _new_ax(ax=None, figsize=(9, 7)):
    import matplotlib.pyplot as plt

    if ax is not None:
        return ax.figure, ax
    fig, ax = plt.subplots(figsize=figsize)
    return fig, ax


def _tidy(ax, title: str | None, extent, crs=None):
    ax.set_title(title or "", fontsize=11, loc="left")
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    return ax


# -- rasters ------------------------------------------------------------------


def show_raster(path: str | Path, title: str | None = None,
                cmap: str = "viridis", hillshade: bool = False,
                dem: str | Path | None = None, max_px: int = MAX_PIXELS,
                percentile: tuple[float, float] = (2, 98),
                ax=None, colorbar: bool = True):
    """Draw one raster, stretched to percentiles rather than min/max.

    The percentile stretch matters: one nodata sliver or a single spike sets a
    min/max stretch to something that renders the whole raster flat grey, which
    reads as "the algorithm did nothing" when it is only the colour scale.
    """
    arr, extent, crs = _read_decimated(path, max_px)
    fig, ax = _new_ax(ax)

    if hillshade or dem is not None:
        src = dem if dem is not None else path
        hs, hs_extent, _ = _read_decimated(src, max_px)
        ax.imshow(_hillshade(hs), cmap="gray", extent=hs_extent,
                  origin="upper", alpha=1.0, interpolation="bilinear")

    valid = arr.compressed()
    if valid.size:
        vmin, vmax = np.percentile(valid, percentile)
        if vmin == vmax:
            vmin, vmax = float(valid.min()), float(valid.max() or 1)
    else:
        vmin = vmax = None

    im = ax.imshow(arr, cmap=cmap, extent=extent, origin="upper",
                   vmin=vmin, vmax=vmax,
                   alpha=0.75 if (hillshade or dem is not None) else 1.0,
                   interpolation="nearest")
    if colorbar and valid.size:
        cb = fig.colorbar(im, ax=ax, shrink=0.7, pad=0.02)
        cb.ax.tick_params(labelsize=8)
    _tidy(ax, title or Path(path).stem, extent, crs)
    return fig


def show_dem(path: str | Path, title: str | None = None, **kw):
    """Hillshaded elevation — the base map for everything else."""
    return show_raster(path, title=title or "Elevation", cmap="terrain",
                       hillshade=True, **kw)


def show_mask(mask: str | Path, dem: str | Path | None = None,
              title: str | None = None, color: str = "#2b8cbe",
              max_px: int = MAX_PIXELS, ax=None, alpha: float = 0.6):
    """Draw a 0/1 mask over hillshade — valley bottoms, flood extent, fans."""
    from matplotlib.colors import ListedColormap

    arr, extent, crs = _read_decimated(mask, max_px)
    fig, ax = _new_ax(ax)
    if dem is not None:
        hs, hs_extent, _ = _read_decimated(dem, max_px)
        ax.imshow(_hillshade(hs), cmap="gray", extent=hs_extent,
                  origin="upper", interpolation="bilinear")
    shown = np.ma.masked_where(arr <= 0, arr)
    ax.imshow(shown, cmap=ListedColormap([color]), extent=extent,
              origin="upper", alpha=alpha, interpolation="nearest")
    covered = float((arr > 0).sum()) / max(arr.size, 1)
    _tidy(ax, f"{title or Path(mask).stem}  ({covered:.1%} of frame)", extent, crs)
    return fig


# -- vectors ------------------------------------------------------------------


def show_vector(path: str | Path, column: str | None = None,
                dem: str | Path | None = None, title: str | None = None,
                colors: dict[str, str] | None = None, linewidth: float = 1.6,
                max_px: int = MAX_PIXELS, ax=None, legend: bool = True):
    """Draw a vector layer, categorically coloured by ``column`` if given.

    Categories use a fixed palette where one exists (confinement, valley type),
    so the same class is the same colour in every figure and between sessions.
    Randomly assigned category colours make two runs impossible to compare.
    """
    import geopandas as gpd

    gdf = gpd.read_file(str(path))
    if gdf.empty:
        raise GeolensError(f"No features in {path}")
    fig, ax = _new_ax(ax)

    if dem is not None:
        hs, extent, _ = _read_decimated(dem, max_px)
        ax.imshow(_hillshade(hs), cmap="gray", extent=extent, origin="upper",
                  interpolation="bilinear")

    if column and column in gdf.columns:
        pal = colors
        if pal is None:
            vals = set(str(v) for v in gdf[column].dropna().unique())
            if vals <= set(CONFINEMENT_COLORS):
                pal = CONFINEMENT_COLORS
            elif vals <= set(VALLEY_TYPE_COLORS):
                pal = VALLEY_TYPE_COLORS
        if pal:
            for val, sub in gdf.groupby(gdf[column].astype(str)):
                sub.plot(ax=ax, color=pal.get(val, "#999999"),
                         linewidth=linewidth, label=val,
                         markersize=18, zorder=3)
            if legend:
                ax.legend(fontsize=8, frameon=False, loc="best",
                          title=column, title_fontsize=8)
        else:
            gdf.plot(ax=ax, column=column, legend=legend, linewidth=linewidth,
                     markersize=18, zorder=3, cmap="viridis")
    else:
        gdf.plot(ax=ax, color="#2b8cbe", linewidth=linewidth, markersize=18,
                 zorder=3)

    _tidy(ax, f"{title or Path(path).stem}  ({len(gdf)} features)", None, gdf.crs)
    return fig


# -- cross-sections -----------------------------------------------------------


def show_sections(result: dict, delineation: dict | None = None,
                  n: int = 6, ncols: int = 3):
    """Small multiples of cross-sections, with bank tops marked when known.

    Small multiples rather than one stacked plot. Overlaying fifty sections was
    the earlier approach and it produced a solid band that answered nothing —
    the useful question is whether an individual section looks like a channel,
    which needs its own axes.

    Pass ``delineation`` (the output of :func:`geolens.api.delineate_channel`)
    to get bank tops and the bankfull surface drawn on. Without it you get the
    ground profile alone, which is still the fastest way to spot a centerline
    that is not on the channel.
    """
    import matplotlib.pyplot as plt

    secs = result.get("sections") or []
    # xs_id as export_sections numbers them: position in the full list + 1,
    # so a panel can be found in the exported layer's attribute table.
    ids = {id(s): i + 1 for i, s in enumerate(secs)}
    usable = [s for s in secs if s.get("points")]
    if not usable:
        raise GeolensError(
            "No sections with points in this result. Pass the output of "
            "api.cross_sections."
        )

    # Delineation records line up with sections by station, not by position --
    # failed sections are still present but carry status != ok.
    by_station = {}
    if delineation:
        for rec in delineation.get("sections", []):
            by_station[round(float(rec.get("station", -1)), 3)] = rec

    pick = usable[:: max(1, len(usable) // n)][:n]
    nrows = int(np.ceil(len(pick) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.2 * ncols, 2.9 * nrows),
                             squeeze=False)
    for ax, s in zip(axes.ravel(), pick):
        pts = s["points"]
        off = np.asarray([p["offset"] for p in pts], dtype="float64")
        el = np.asarray([np.nan if p["elevation"] is None else p["elevation"]
                         for p in pts], dtype="float64")
        ax.plot(off, el, color="#333333", linewidth=1.3, zorder=3)

        rec = by_station.get(round(float(s.get("station", -1)), 3))
        if rec and rec.get("status") == "ok":
            bf = rec.get("bankfull") or {}
            z = bf.get("elevation")
            for side in ("left", "right"):
                edge = (rec.get(side) or {}).get("bank_top_offset")
                if edge is not None:
                    ax.axvline(edge, color="#d95f02", linewidth=1.0,
                               linestyle="--", zorder=2)
            if z is not None:
                ax.axhline(z, color="#2b8cbe", linewidth=0.9, linestyle=":",
                           zorder=2)
                ax.fill_between(off, el, z, where=(el <= z), alpha=0.25,
                                color="#2b8cbe", zorder=1)
            w = bf.get("width")
            sub = f"W {w:.1f} m" if w else ""
        else:
            sub = "" if not delineation else (rec or {}).get("status", "")

        st_ = s.get("station")
        st_ = f"{st_:.0f}" if isinstance(st_, (int, float)) else "?"
        ax.set_title(f"xs {ids[id(s)]} · station {st_}  {sub}", fontsize=9,
                     loc="left")
        ax.tick_params(labelsize=7)
        ax.set_xlabel("offset (m)", fontsize=7)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
    for ax in axes.ravel()[len(pick):]:
        ax.axis("off")
    fig.tight_layout()
    return fig


# -- one-call panels ----------------------------------------------------------


def show_landscape(result: dict, dem: str | None = None, max_px: int = 700):
    """A four-panel summary of a landscape pass: relief, HAND, valley, segments.

    One call after the expensive step, so the sanity check costs nothing. The
    failure this catches most often is a valley bottom leaking onto uplands,
    which is obvious in a picture and invisible in a summary table.
    """
    import matplotlib.pyplot as plt

    terrain = result.get("terrain", result)
    valley = result.get("valley", {})
    classified = result.get("classified", {})
    dem = dem or terrain.get("dem")

    panels = []
    if dem:
        panels.append(("Relief", "dem", dem))
    if terrain.get("hand"):
        panels.append(("HAND", "raster", terrain["hand"]))
    # The key is `valley_raster`. Naming it wrong silently drops the panel --
    # the figure still renders, just without the layer you most wanted to check,
    # which is how a valley bottom leaking onto uplands goes unnoticed.
    vb = (valley.get("valley_raster") or valley.get("valley_bottom")
          or valley.get("valley_bottom_raster"))
    if vb:
        panels.append(("Valley bottom", "mask", vb))
    if classified.get("segments"):
        panels.append(("Confinement", "vector", classified["segments"]))
    if not panels:
        raise GeolensError("Nothing recognisable to plot in this result.")

    ncols = 2
    nrows = int(np.ceil(len(panels) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(7.5 * ncols, 6 * nrows),
                             squeeze=False)
    for ax, (title, kind, path) in zip(axes.ravel(), panels):
        try:
            if kind == "dem":
                show_raster(path, title=title, cmap="terrain", hillshade=True,
                            max_px=max_px, ax=ax, colorbar=False)
            elif kind == "raster":
                show_raster(path, title=title, cmap="Blues", dem=dem,
                            max_px=max_px, ax=ax, colorbar=False)
            elif kind == "mask":
                show_mask(path, dem=dem, title=title, max_px=max_px, ax=ax)
            else:
                show_vector(path, column="confinement", dem=dem, title=title,
                            max_px=max_px, ax=ax)
        except Exception as e:  # noqa: BLE001 - one bad panel must not kill the figure
            ax.text(0.5, 0.5, f"{title}\n{type(e).__name__}: {e}",
                    ha="center", va="center", fontsize=8, wrap=True)
            ax.axis("off")
    for ax in axes.ravel()[len(panels):]:
        ax.axis("off")
    fig.tight_layout()
    return fig


def show(thing: Any, **kw):
    """Plot whatever it is handed: a path, or a result dict.

    Convenience for the notebook, so a step's output can be looked at without
    first working out which function applies to it.
    """
    if isinstance(thing, dict):
        if "sections" in thing:
            return show_sections(thing, **kw)
        if any(k in thing for k in ("terrain", "valley", "classified")):
            return show_landscape(thing, **kw)
        for key in ("fans", "gullies", "headcuts", "segments", "polygons",
                    "valley_polygons", "valley_raster", "flood_envelope",
                    "slope_std", "hand"):
            if thing.get(key):
                return show(thing[key], **kw)
        raise GeolensError(f"Nothing plottable in this result: {list(thing)[:8]}")

    p = str(thing)
    if p.lower().endswith((".gpkg", ".shp", ".geojson", ".json")):
        return show_vector(p, **kw)
    if p.lower().endswith((".tif", ".tiff", ".vrt", ".img")):
        return show_raster(p, **kw)
    raise GeolensError(f"Do not know how to plot {p}")
