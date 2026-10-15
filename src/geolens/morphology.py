"""Channel / floodplain delineation from cross-section geometry.

Walks outward from the thalweg on each side of a section and classifies the
terrain by slope break:

    channel bed  →  bank face  →  bank top  →  floodplain  →  valley wall
      (flat)        (steep)       (break)       (flat)        (steep, STOP)

The valley-wall test is what stops the floodplain from climbing a confining
hillslope forever: once terrain beyond the bank top resumes a sustained climb,
the floodplain is closed at that point and the section is marked *confined*.

Derived metrics (bankfull width, mean depth, width/depth ratio, entrenchment
ratio) are the standard inputs to channel classification, so they are reported
alongside the raw breakpoints.

All thresholds are slopes expressed as rise/run (0.15 = 15% = about 8.5°).
"""

from __future__ import annotations

from typing import Any

from .safety import Guard, GeolensError, progress


# -- helpers ------------------------------------------------------------------


def _smooth(values: list[float], window: int) -> list[float]:
    """Centered moving average. Window is forced odd and >= 1."""
    n = len(values)
    if window <= 1 or n == 0:
        return list(values)
    w = window if window % 2 else window + 1
    half = w // 2
    out = []
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        seg = values[lo:hi]
        out.append(sum(seg) / len(seg))
    return out


def _arm(points: list[tuple[float, float]], thalweg_offset: float, side: str):
    """Extract one side of a section, ordered outward from the thalweg.

    Returns (distances_from_thalweg, elevations, signed_offsets).
    """
    if side == "left":
        sel = [(o, z) for o, z in points if o <= thalweg_offset]
        sel.sort(key=lambda t: -t[0])  # walk outward = decreasing offset
    else:
        sel = [(o, z) for o, z in points if o >= thalweg_offset]
        sel.sort(key=lambda t: t[0])
    dists = [abs(o - thalweg_offset) for o, _ in sel]
    elevs = [z for _, z in sel]
    offs = [o for o, _ in sel]
    return dists, elevs, offs


def _walk_arm(
    dists: list[float],
    elevs: list[float],
    offs: list[float],
    bank_slope: float,
    flat_slope: float,
    min_bank_height: float,
    valley_slope: float,
    run_length: int,
    smooth_window: int,
    max_offset: float | None,
) -> dict[str, Any]:
    """Classify one arm outward from the thalweg. Returns breakpoints."""
    n = len(dists)
    result: dict[str, Any] = {
        "bank_top_offset": None,
        "bank_top_elevation": None,
        "bank_height": None,
        "bank_slope_mean": None,
        "floodplain_edge_offset": None,
        "floodplain_edge_elevation": None,
        "floodplain_width": None,
        "confined": False,
        "termination": "no_bank_detected",
    }
    if n < 3:
        result["termination"] = "too_few_points"
        return result

    z = _smooth(elevs, smooth_window)
    z0 = z[0]

    # Slope at each index, rise per unit outward distance.
    #
    # Use a CENTERED difference on interior points: a forward difference
    # describes the interval [i, i+1] but gets tested as though it described
    # point i, which pushes every detected breakpoint one sample outward. That
    # bias compounds with the smoothing lag and lands bank tops ~2 samples late.
    slopes: list[float] = [0.0] * n
    for i in range(n):
        lo, hi = max(0, i - 1), min(n - 1, i + 1)
        dd = dists[hi] - dists[lo]
        slopes[i] = (z[hi] - z[lo]) / dd if dd > 0 else 0.0

    state = "bed"
    bank_start = None
    last_steep = None
    steep_run = 0
    flat_run = 0

    for i in range(n):
        if max_offset is not None and dists[i] > max_offset:
            result["termination"] = (
                "max_offset_reached" if state != "floodplain" else "max_offset_reached"
            )
            if state == "floodplain":
                result["floodplain_edge_offset"] = offs[i]
                result["floodplain_edge_elevation"] = elevs[i]
            break

        s = slopes[i]

        if state == "bed":
            if s >= bank_slope:
                steep_run += 1
                if bank_start is None:
                    bank_start = i
                last_steep = i
                if steep_run >= run_length:
                    state = "bank"
            else:
                steep_run = 0
                bank_start = None

        elif state == "bank":
            if s >= bank_slope:
                last_steep = i
            if s < flat_slope:
                flat_run += 1
                if flat_run >= run_length:
                    # Localize the break at the END of the steep face rather
                    # than the start of the flat run. Waiting for the flat run
                    # to confirm is what puts the bank top a sample or two
                    # outboard; the last steeply-climbing sample IS the top.
                    top = last_steep if last_steep is not None else i - flat_run + 1
                    top = min(top, n - 1)
                    height = elevs[top] - elevs[0]
                    if height >= min_bank_height:
                        result["bank_top_offset"] = offs[top]
                        result["bank_top_elevation"] = elevs[top]
                        result["bank_height"] = height
                        seg = slopes[bank_start:top] if bank_start is not None else []
                        result["bank_slope_mean"] = (
                            sum(seg) / len(seg) if seg else None
                        )
                        state = "floodplain"
                        result["termination"] = "floodplain_open"
                        steep_run = 0
                        flat_run = 0
                    else:
                        # Too small to be a real bank — treat as bed roughness.
                        state = "bed"
                        steep_run = 0
                        flat_run = 0
                        bank_start = None
                        last_steep = None
            else:
                flat_run = 0

        elif state == "floodplain":
            if s >= valley_slope:
                steep_run += 1
                if steep_run >= run_length:
                    edge = i - steep_run + 1
                    result["floodplain_edge_offset"] = offs[edge]
                    result["floodplain_edge_elevation"] = elevs[edge]
                    result["confined"] = True
                    result["termination"] = "valley_confinement"
                    break
            else:
                steep_run = 0

    # Ran off the end of the section without hitting a valley wall.
    if state == "floodplain" and result["floodplain_edge_offset"] is None:
        result["floodplain_edge_offset"] = offs[-1]
        result["floodplain_edge_elevation"] = elevs[-1]
        result["termination"] = "section_end"
    if state == "bank" and result["bank_top_offset"] is None:
        result["termination"] = "bank_not_closed"

    if (
        result["bank_top_offset"] is not None
        and result["floodplain_edge_offset"] is not None
    ):
        result["floodplain_width"] = abs(
            result["floodplain_edge_offset"] - result["bank_top_offset"]
        )

    return result


# -- public API ---------------------------------------------------------------


def delineate(
    sections_result: dict[str, Any],
    bank_slope: float = 0.15,
    flat_slope: float = 0.05,
    valley_slope: float = 0.20,
    min_bank_height: float = 0.3,
    run_length: int = 2,
    smooth_window: int = 3,
    max_floodplain_offset: float | None = None,
) -> dict[str, Any]:
    """Classify channel, banks and floodplain for every section.

    Parameters
    ----------
    bank_slope
        Slope (rise/run) that marks the start of a bank face. Lower values catch
        subtler banks but pick up more bed roughness.
    flat_slope
        Slope below which terrain reads as flat. The bank top is the first
        sustained return below this after a bank face.
    valley_slope
        Slope that marks valley confinement. Must normally exceed ``flat_slope``;
        the floodplain is closed where terrain resumes this climb.
    min_bank_height
        Minimum rise above the thalweg for a slope break to count as a bank —
        the main guard against bed roughness registering as a bank.
    run_length
        Consecutive samples a slope condition must hold before it counts. Raise
        it on noisy lidar; lower it on smooth or coarse DEMs.
    smooth_window
        Moving-average window (in samples) applied before computing slope.
    max_floodplain_offset
        Hard cap on floodplain search distance from the centerline.

    Returns one record per section with left/right breakpoints plus derived
    bankfull and entrenchment metrics.
    """
    sections = sections_result.get("sections")
    if not sections:
        raise GeolensError("No sections in input — run extract_cross_sections first.")
    if valley_slope <= flat_slope:
        raise GeolensError(
            f"valley_slope ({valley_slope}) must exceed flat_slope ({flat_slope}), "
            "otherwise every floodplain closes immediately."
        )

    out: list[dict[str, Any]] = []
    for sec in progress(sections, label="delineating channel"):
        pts = [
            (p["offset"], p["elevation"])
            for p in sec["points"]
            if p["elevation"] is not None
        ]
        rec: dict[str, Any] = {
            "station": sec["station"],
            "thalweg_offset": sec["thalweg_offset"],
            "thalweg_elevation": sec["thalweg_elevation"],
        }

        if len(pts) < 5 or sec["thalweg_offset"] is None:
            rec["status"] = "insufficient_data"
            rec["left"] = rec["right"] = None
            out.append(rec)
            continue

        t_off = sec["thalweg_offset"]
        arms = {}
        for side in ("left", "right"):
            d, e, o = _arm(pts, t_off, side)
            arms[side] = _walk_arm(
                d, e, o, bank_slope, flat_slope, min_bank_height,
                valley_slope, run_length, smooth_window, max_floodplain_offset,
            )

        rec["left"] = arms["left"]
        rec["right"] = arms["right"]

        lb, rb = arms["left"]["bank_top_offset"], arms["right"]["bank_top_offset"]
        lz = arms["left"]["bank_top_elevation"]
        rz = arms["right"]["bank_top_elevation"]

        if lb is not None and rb is not None:
            bankfull_w = abs(rb - lb)
            bankfull_z = min(lz, rz)  # water escapes at the lower bank
            inside = [z for o, z in pts if lb <= o <= rb and z <= bankfull_z]
            mean_depth = (
                (bankfull_z - sum(inside) / len(inside)) if inside else None
            )
            max_depth = bankfull_z - sec["thalweg_elevation"]
            area = None
            if inside:
                area = sum(
                    max(0.0, bankfull_z - z)
                    for o, z in pts
                    if lb <= o <= rb
                ) * sections_result.get("point_spacing", 1.0)

            rec["bankfull"] = {
                "width": bankfull_w,
                "elevation": bankfull_z,
                "max_depth": max_depth,
                "mean_depth": mean_depth,
                "area": area,
                "width_depth_ratio": (
                    bankfull_w / mean_depth if mean_depth and mean_depth > 0 else None
                ),
                "low_bank": "left" if lz <= rz else "right",
            }

            le = arms["left"]["floodplain_edge_offset"]
            re_ = arms["right"]["floodplain_edge_offset"]
            if le is not None and re_ is not None:
                valley_w = abs(re_ - le)
                rec["floodplain"] = {
                    "left_edge_offset": le,
                    "right_edge_offset": re_,
                    "total_width": valley_w,
                    "left_width": arms["left"]["floodplain_width"],
                    "right_width": arms["right"]["floodplain_width"],
                    "entrenchment_ratio": (
                        valley_w / bankfull_w if bankfull_w > 0 else None
                    ),
                    "confined_left": arms["left"]["confined"],
                    "confined_right": arms["right"]["confined"],
                }
            else:
                rec["floodplain"] = None
            rec["status"] = "ok"
        else:
            rec["bankfull"] = None
            rec["floodplain"] = None
            missing = [s for s in ("left", "right")
                       if arms[s]["bank_top_offset"] is None]
            rec["status"] = f"bank_not_found:{','.join(missing)}"

        out.append(rec)

    ok = [r for r in out if r["status"] == "ok"]
    widths = [r["bankfull"]["width"] for r in ok if r["bankfull"]]
    ers = [
        r["floodplain"]["entrenchment_ratio"]
        for r in ok
        if r.get("floodplain") and r["floodplain"]["entrenchment_ratio"]
    ]
    wds = [
        r["bankfull"]["width_depth_ratio"]
        for r in ok
        if r["bankfull"] and r["bankfull"]["width_depth_ratio"]
    ]

    def _stat(vals):
        if not vals:
            return None
        s = sorted(vals)
        return {
            "n": len(s),
            "min": s[0],
            "median": s[len(s) // 2],
            "max": s[-1],
            "mean": sum(s) / len(s),
        }

    warnings: list[str] = []
    failed = len(out) - len(ok)
    if failed:
        warnings.append(
            f"{failed} of {len(out)} sections did not yield banks on both sides. "
            "Lower bank_slope or min_bank_height if the channel is subtle; raise "
            "run_length if the DEM is noisy."
        )
    unconfined = sum(
        1
        for r in ok
        if r.get("floodplain")
        and not (
            r["floodplain"]["confined_left"] and r["floodplain"]["confined_right"]
        )
    )
    if unconfined:
        warnings.append(
            f"{unconfined} section(s) reached the section edge without meeting a "
            "valley wall — widen the cross-sections or set "
            "max_floodplain_offset, or the floodplain width is a floor, not a "
            "measurement."
        )

    return {
        "parameters": {
            "bank_slope": bank_slope,
            "flat_slope": flat_slope,
            "valley_slope": valley_slope,
            "min_bank_height": min_bank_height,
            "run_length": run_length,
            "smooth_window": smooth_window,
            "max_floodplain_offset": max_floodplain_offset,
        },
        "section_count": len(out),
        "resolved": len(ok),
        "summary": {
            "bankfull_width": _stat(widths),
            "width_depth_ratio": _stat(wds),
            "entrenchment_ratio": _stat(ers),
        },
        "sections": out,
        "warnings": warnings,
        "note": (
            "Entrenchment ratio = floodplain width / bankfull width. "
            "Width/depth ratio uses bankfull mean depth. Both are geometry only — "
            "they are not a substitute for a field-verified bankfull indicator."
        ),
    }


def delineation_to_csv(
    result: dict[str, Any],
    name: str = "channel_delineation.csv",
    guard: Guard | None = None,
) -> dict[str, Any]:
    """Write one row per section with the key channel/floodplain metrics."""
    import csv

    guard = guard or Guard.from_env()
    out = guard.write_path(name)
    cols = [
        "station", "status", "thalweg_offset", "thalweg_elevation",
        "left_bank_offset", "left_bank_elevation", "left_bank_height",
        "right_bank_offset", "right_bank_elevation", "right_bank_height",
        "bankfull_width", "bankfull_elevation", "bankfull_mean_depth",
        "bankfull_max_depth", "width_depth_ratio",
        "floodplain_left_edge", "floodplain_right_edge", "floodplain_total_width",
        "entrenchment_ratio", "confined_left", "confined_right",
    ]
    rows = 0
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in result.get("sections", []):
            left = r.get("left") or {}
            right = r.get("right") or {}
            bf = r.get("bankfull") or {}
            fp = r.get("floodplain") or {}
            w.writerow([
                r.get("station"), r.get("status"),
                r.get("thalweg_offset"), r.get("thalweg_elevation"),
                left.get("bank_top_offset"), left.get("bank_top_elevation"),
                left.get("bank_height"),
                right.get("bank_top_offset"), right.get("bank_top_elevation"),
                right.get("bank_height"),
                bf.get("width"), bf.get("elevation"), bf.get("mean_depth"),
                bf.get("max_depth"), bf.get("width_depth_ratio"),
                fp.get("left_edge_offset"), fp.get("right_edge_offset"),
                fp.get("total_width"), fp.get("entrenchment_ratio"),
                fp.get("confined_left"), fp.get("confined_right"),
            ])
            rows += 1
    return {"path": str(out), "rows": rows}
