"""Clip a DEM to a complete watershed and analyse it whole.

**Tiling does not make sense for watershed work.** A rectangular tile cuts
across drainage divides, so flow paths are truncated at every edge and
accumulation, HAND and everything derived from them are wrong there. A basin
derived from a coarsened copy of the DEM is better but still approximate —
its boundary is drawn at the coarse cell size, not the real divide.

A **complete watershed** has no inflow across its boundary by definition, so
accumulation computed inside the clip is exactly right for every cell in it.
That makes it the only defensible unit for this kind of analysis, and it is
also the unit the work is actually about: valley types from the valley bottom
to the top of the watershed.

Three ways to say which watershed:

* ``boundary`` — a polygon you already have. A WBD HUC12, a permit boundary,
  a shapefile from a previous project. This is the common case, and geolens
  checks whether the polygon really is a complete watershed rather than
  assuming it.
* ``pour_point`` — a coordinate. The contributing area is delineated from the
  DEM. Use this for a gage, a confluence, a culvert, a project site.
* ``huc`` — fetched from the USGS Watershed Boundary Dataset for the DEM's
  extent. Convenient, but it is a network call; see ``huc_selftest``.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from ..safety import Guard, GeolensError, note
from ..raster import gtiff_profile


def _snap_to_drainage(acc: np.ndarray, r: int, c: int,
                      radius_cells: int) -> tuple[int, int, float]:
    """Move a pour point to the largest drainage within ``radius_cells``.

    A coordinate typed from a map, or read off a gage listing, essentially
    never lands on the exact cell the model routes flow through. One cell off
    the channel is not a small error: that cell's contributing area is its own
    hillslope, so the "watershed" comes back as a few hundred square metres and
    looks like a bug in the delineation rather than a bug in the point.

    Snapping to the maximum accumulation in a small window is the standard fix.
    The snap distance is reported so a point that moved a long way is visible
    rather than silently accepted.
    """
    H, W = acc.shape
    r0, r1 = max(0, r - radius_cells), min(H, r + radius_cells + 1)
    c0, c1 = max(0, c - radius_cells), min(W, c + radius_cells + 1)
    win = acc[r0:r1, c0:c1]
    if win.size == 0:
        raise GeolensError(
            f"The pour point (row {r}, col {c}) is outside the DEM."
        )
    k = int(np.argmax(win))
    dr, dc = divmod(k, win.shape[1])
    rr, cc = r0 + dr, c0 + dc
    moved = float(np.hypot(rr - r, cc - c))
    return rr, cc, moved


def _polygonise(mask: np.ndarray, transform, crs):
    """Largest polygon of a boolean mask, as a GeoDataFrame."""
    import geopandas as gpd
    from rasterio import features as rfeat
    from shapely.geometry import shape

    shapes = [shape(g) for g, v in
              rfeat.shapes(mask.astype("uint8"), mask=mask, transform=transform)
              if v == 1]
    if not shapes:
        raise GeolensError("The delineated watershed is empty.")
    shapes.sort(key=lambda g: g.area, reverse=True)
    return gpd.GeoDataFrame({"watershed_id": [0]}, geometry=[shapes[0]], crs=crs)


def _clip(dem_path: Path, geom, out_path: Path, buffer_m: float = 0.0) -> str:
    """Clip a DEM to a geometry, writing nodata outside it."""
    import rasterio
    from rasterio.mask import mask as rio_mask

    g = geom.buffer(buffer_m) if buffer_m else geom
    with rasterio.open(str(dem_path)) as src:
        arr, transform = rio_mask(src, [g], crop=True, filled=True,
                                  nodata=src.nodata if src.nodata is not None
                                  else -9999.0)
        prof = src.profile.copy()
        nodata = src.nodata if src.nodata is not None else -9999.0
    prof.update(height=arr.shape[1], width=arr.shape[2], transform=transform,
                nodata=nodata)
    with rasterio.open(str(out_path), "w", **gtiff_profile(prof,
                                                           dtype=prof["dtype"],
                                                           nodata=nodata)) as dst:
        dst.write(arr[0], 1)
    return str(out_path)


def check_inflow(dem: str | Path, boundary_geom, guard: Guard | None = None,
                 flow=None) -> dict[str, Any]:
    """Does anything flow INTO this polygon across its boundary?

    This is what separates a real watershed from a polygon someone drew. If
    water enters across the boundary, then accumulation computed inside the
    clip is missing that upstream area, and every contributing-area-derived
    quantity — the stream network, HAND, confinement, valley bottoms — is wrong
    by however much was left out.

    A boundary that fails this is not unusable; it just has to be understood as
    a study area rather than a watershed. What is not acceptable is not
    knowing.

    Returns the number of cells draining inward, and the contributing area they
    carry as a fraction of the polygon's own area.
    """
    import rasterio
    from rasterio import features as rfeat

    from .flow import flow_routing, _CODES, _DR, _DC

    guard = guard or Guard.from_env()
    p = guard.read_path(dem)
    if flow is None:
        flow = flow_routing(str(p), guard=guard, out_prefix="inflow_check")

    with rasterio.open(flow.flow_dir) as src:
        fdir = src.read(1)
        transform = src.transform
        shape_ = (src.height, src.width)
    with rasterio.open(flow.accumulation) as src:
        acc = src.read(1).astype("float64")

    inside = rfeat.rasterize([(boundary_geom, 1)], out_shape=shape_,
                             transform=transform, fill=0,
                             dtype="uint8").astype(bool)
    if not inside.any():
        raise GeolensError(
            "The boundary does not overlap the DEM. Check that the two are in "
            "the same coordinate system."
        )

    # For every cell OUTSIDE, where does it go? If its downstream neighbour is
    # inside, that is inflow across the boundary.
    H, W = fdir.shape
    inflow = np.zeros_like(inside)
    for k in range(8):
        code, dr, dc = int(_CODES[k]), int(_DR[k]), int(_DC[k])
        sel = (fdir == code) & ~inside
        if not sel.any():
            continue
        rr, cc = np.nonzero(sel)
        tr, tc = rr + dr, cc + dc
        ok = (tr >= 0) & (tr < H) & (tc >= 0) & (tc < W)
        rr, cc, tr, tc = rr[ok], cc[ok], tr[ok], tc[ok]
        lands_inside = inside[tr, tc]
        inflow[rr[lands_inside], cc[lands_inside]] = True

    n_in = int(inflow.sum())
    area_in = float(acc[inflow].sum()) if n_in else 0.0
    area_own = float(inside.sum())
    return {
        "inflow_cells": n_in,
        "inflow_area_cells": area_in,
        "boundary_area_cells": area_own,
        "inflow_fraction": (area_in / area_own) if area_own else 0.0,
        "is_complete_watershed": n_in == 0,
    }


def watershed(
    dem: str | Path,
    boundary: str | Path | None = None,
    pour_point: "tuple[float, float] | str | None" = None,
    pour_crs: str | None = None,
    huc: str | None = None,
    buffer_m: float = 0.0,
    snap_radius_m: float = 150.0,
    verify_inflow: bool = True,
    guard: Guard | None = None,
    out_prefix: str = "watershed",
) -> dict[str, Any]:
    """Clip a DEM to one complete watershed, so it can be analysed whole.

    Give exactly one of ``boundary``, ``pour_point`` or ``huc``.

    Parameters
    ----------
    boundary
        A polygon layer. If it holds several features they are dissolved; if
        you want one of them, filter it first.
    pour_point
        ``(x, y)``, ``"x,y"``, or a point layer (``.gpkg``/``.shp``, first
        point, in the layer's own CRS). Coordinates are in the DEM's CRS
        unless ``pour_crs`` says otherwise — pass ``pour_crs="EPSG:4326"`` for
        longitude/latitude.
    snap_radius_m
        The point is snapped to the largest drainage within this distance.
        A coordinate off a map essentially never lands on the routed channel,
        and one cell off gives a watershed of a few hundred square metres.
    verify_inflow
        For a supplied ``boundary``, check whether anything drains into it
        across its edge. Costs one flow-routing pass over the full DEM, and it
        is the difference between a watershed and a polygon. Turn it off only
        when you already know.
    buffer_m
        Outward buffer on the clip. Flow routing needs none inside a complete
        watershed, but window-based steps (slope, TPI, roughness) read
        neighbours, so a small buffer avoids a rim of edge artefacts. The
        buffered ring is not a complete watershed, so anything derived from
        contributing area is still only trustworthy inside the true boundary.
    """
    import geopandas as gpd
    import rasterio

    guard = guard or Guard.from_env()
    t0 = time.monotonic()
    given = [x is not None for x in (boundary, pour_point, huc)]
    if sum(given) != 1:
        raise GeolensError(
            "Give exactly one of boundary=, pour_point= or huc= — they are "
            "three ways of naming the same thing, and passing two leaves it "
            "ambiguous which one won."
        )

    p = guard.read_path(dem)
    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    result: dict[str, Any] = {"source_dem": str(p)}

    with rasterio.open(str(p)) as src:
        dem_crs = src.crs
        cell = abs(src.res[0])

    if huc is not None:
        from .huc import fetch_huc
        gdf, meta = fetch_huc(str(p), huc=huc, guard=guard)
        result["huc"] = meta
        geom = gdf.to_crs(dem_crs).union_all()
        result["boundary_source"] = "huc"

    elif boundary is not None:
        bp = guard.read_path(boundary)
        gdf = gpd.read_file(str(bp))
        if gdf.crs is None:
            raise GeolensError(
                f"{bp} has no CRS, so it cannot be placed against the DEM. "
                f"Assign one in QGIS or with geopandas before using it."
            )
        geom = gdf.to_crs(dem_crs).union_all()
        result["boundary_source"] = "supplied"
        result["boundary_features"] = int(len(gdf))
        if len(gdf) > 1:
            warnings.append(
                f"{len(gdf)} features in {Path(str(bp)).name} were dissolved "
                f"into one boundary. If you meant a single watershed, filter "
                f"the layer first — a dissolved set of neighbouring HUCs is a "
                f"complete watershed only if they nest."
            )

    else:
        pt = pour_point
        if isinstance(pt, (str, Path)) and Path(str(pt)).suffix.lower() in (
                ".gpkg", ".shp", ".geojson", ".json"):
            # A point layer: its first point, in its own CRS.
            pts = gpd.read_file(str(guard.read_path(pt)))
            pts = pts[pts.geometry.notna() & ~pts.geometry.is_empty]
            if pts.empty:
                raise GeolensError(f"{pt} has no points.")
            if len(pts) > 1:
                warnings.append(f"{Path(str(pt)).name} has {len(pts)} points; "
                                f"the first was used as the pour point.")
            g0 = pts.geometry.iloc[0]
            g0 = g0 if g0.geom_type == "Point" else g0.representative_point()
            pour_crs = pour_crs or (pts.crs.to_string() if pts.crs else None)
            pt = (g0.x, g0.y)
        if isinstance(pt, str):
            parts = [q.strip() for q in pt.replace(";", ",").split(",")]
            if len(parts) != 2:
                raise GeolensError(
                    f"pour_point={pt!r} should be 'x,y' (two numbers)."
                )
            pt = (float(parts[0]), float(parts[1]))
        x, y = float(pt[0]), float(pt[1])
        if pour_crs:
            from pyproj import Transformer
            tr = Transformer.from_crs(pour_crs, dem_crs, always_xy=True)
            x, y = tr.transform(x, y)

        from .flow import flow_routing, catchment

        note("routing flow to delineate the contributing watershed")
        fl = flow_routing(str(p), guard=guard, out_prefix=f"{out_prefix}_flow")
        with rasterio.open(fl.accumulation) as src:
            acc = src.read(1)
            transform = src.transform
            r, c = src.index(x, y)
        if not (0 <= r < acc.shape[0] and 0 <= c < acc.shape[1]):
            raise GeolensError(
                f"The pour point ({x:.1f}, {y:.1f}) falls outside the DEM."
            )
        radius = max(1, int(round(snap_radius_m / max(cell, 1e-9))))
        r2, c2, moved = _snap_to_drainage(acc, int(r), int(c), radius)
        moved_m = moved * cell
        result["pour_point_snap_m"] = moved_m
        note(f"snapped the pour point {moved_m:.0f} m to the largest drainage "
             f"within {snap_radius_m:g} m")
        if moved_m > snap_radius_m * 0.8:
            warnings.append(
                f"The pour point moved {moved_m:.0f} m during snapping, which "
                f"is most of the {snap_radius_m:g} m search radius — the point "
                f"may not be on the channel you meant. Check it, or set "
                f"snap_radius_m smaller to refuse the move."
            )

        with rasterio.open(fl.flow_dir) as src:
            fdir = src.read(1)
        mask = catchment(fdir, r2, c2)
        gdf = _polygonise(mask, transform, dem_crs)
        geom = gdf.geometry.iloc[0]
        result["boundary_source"] = "pour_point"
        result["pour_point"] = [x, y]
        # Delineated from the flow graph, so it is complete by construction.
        result["inflow"] = {"inflow_cells": 0, "is_complete_watershed": True,
                            "note": "delineated from the flow graph"}

    bnd_path = out_dir / "watershed_boundary.gpkg"
    gpd.GeoDataFrame({"watershed_id": [0]}, geometry=[geom],
                     crs=dem_crs).to_file(str(bnd_path), driver="GPKG")
    result["boundary"] = str(bnd_path)

    if verify_inflow and "inflow" not in result:
        note("checking whether anything drains into this boundary")
        info = check_inflow(str(p), geom, guard=guard)
        result["inflow"] = info
        if not info["is_complete_watershed"]:
            warnings.append(
                f"{info['inflow_cells']:,} cells drain INTO this boundary "
                f"across its edge, carrying {info['inflow_fraction']:.1%} of "
                f"the boundary's own area in upstream contribution. It is a "
                f"study area, not a complete watershed: contributing area "
                f"inside it is understated, so the stream network, HAND and "
                f"everything keyed off them are wrong by that much. Use "
                f"pour_point= to delineate a complete watershed instead."
            )

    clipped = _clip(Path(str(p)), geom, out_dir / "watershed_dem.tif",
                    buffer_m=buffer_m)
    result["dem"] = clipped
    with rasterio.open(clipped) as src:
        from ..units import unit_info
        u = unit_info(src.crs, abs(src.res[0]), abs(src.res[1]))
        valid = int((~np.ma.getmaskarray(src.read(1, masked=True))).sum())
        result["cells"] = valid
        result["area_km2"] = valid * u.cell_area_m2 / 1e6
        result["cell_size_m"] = u.cell_size_m
    result["buffer_m"] = buffer_m
    result["seconds"] = time.monotonic() - t0
    result["warnings"] = warnings
    note(f"watershed: {result['area_km2']:.2f} km2, {valid:,} cells, "
         f"in {result['seconds']:.1f}s")
    return result
