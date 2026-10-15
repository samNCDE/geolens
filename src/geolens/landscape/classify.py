"""Per-segment metrics and confinement classification.

Valley width is computed as **allocated valley area / segment length**, not by
cutting perpendicular sections. That is the single change that makes landscape
scale tractable: one zonal statistic per segment instead of thousands of DEM
samples each.

Classification is deliberately two-tier:

* **Confinement class** (confined / partly confined / unconfined) is derived from
  DEM geometry alone and is fully defensible.
* **Rosgen valley type** is a *crosswalk*, not a measurement. Several Rosgen
  types are distinguished by genesis — colluvial vs alluvial vs glacial vs
  lacustrine — which terrain cannot see. Segments are labelled with candidate
  types and an explicit ``needs_geology`` flag rather than a false certainty.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..safety import Guard, GeolensError, note
from .terrain import TerrainResult
from .valley import ValleyResult
from .network import NetworkResult


# Confinement class breaks, as valley width / channel width.
CONFINED_MAX = 2.0
PARTLY_MAX = 5.0

# Gradient class breaks (rise/run).
GRADIENT_BREAKS = [(0.0, 0.005, "low"), (0.005, 0.02, "moderate"),
                   (0.02, 0.04, "steep"), (0.04, 1.0, "very steep")]


@dataclass
class ClassifyResult:
    table: str
    segments: str
    segment_count: int
    classified: int
    summary: dict[str, Any]
    parameters: dict[str, Any]
    seconds: float
    warnings: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _gradient_class(g: float) -> str:
    if not np.isfinite(g):
        return "unknown"
    for lo, hi, name in GRADIENT_BREAKS:
        if lo <= g < hi:
            return name
    return "very steep"


def _confinement_class(ratio: float, confined_max: float = CONFINED_MAX,
                       partly_max: float = PARTLY_MAX) -> str:
    if not np.isfinite(ratio):
        return "unknown"
    if ratio <= confined_max:
        return "confined"
    if ratio <= partly_max:
        return "partly confined"
    return "unconfined"


def _rosgen_candidates(conf: str, grad: str, width_m: float) -> tuple[str, bool]:
    """Crosswalk geometry to candidate Rosgen valley types.

    Returns (candidates, needs_geology). Types that hinge on depositional
    history are returned as alternatives, never as a single answer.
    """
    if conf == "unknown" or grad == "unknown":
        return "", True
    steep = grad in ("steep", "very steep")
    moderate = grad == "moderate"

    if conf == "confined" and steep:
        return "I", False                      # steep confined V-notch, colluvial
    if conf == "confined" and moderate:
        return "II|V", True                    # glacial trough vs V-notch fluvial
    if conf == "confined":
        return "IV", True                      # gentle canyon / gorge
    if conf == "partly confined" and steep:
        return "III|VII", True                 # alluvial fan vs steep fluvial
    if conf == "partly confined":
        return "II|VIII", True
    # unconfined
    if steep:
        return "III", True                     # fans / debris cones
    if moderate:
        return "VIII|IX", True                 # terraced alluvial vs outwash
    if width_m > 2000:
        return "X|XI", True                    # broad lacustrine / deltaic
    return "VIII|X", True


def classify_segments(
    terrain: TerrainResult,
    valley: ValleyResult,
    network: NetworkResult,
    channel_width_m: float | None = None,
    channel_width_coef: float = 2.7,
    channel_width_exp: float = 0.35,
    confined_max: float = CONFINED_MAX,
    partly_confined_max: float = PARTLY_MAX,
    guard: Guard | None = None,
    out_prefix: str = "classified",
) -> ClassifyResult:
    """Compute per-segment valley metrics and classify confinement.

    Parameters
    ----------
    channel_width_m
        Fixed channel width for the confinement ratio. Leave ``None`` to
        estimate it from drainage area with a downstream hydraulic-geometry
        relation, which is the sane default across a whole network.
    channel_width_coef, channel_width_exp
        Coefficients in ``W = a * A^b`` with A in km². The defaults are a
        generic regional relation — **replace them with a local one if you have
        gauge or survey data**, because confinement ratio scales directly with W.
    confined_max, partly_confined_max
        Confinement-ratio breaks. Defaults follow the common convention that a
        confined valley is under ~2 channel widths and a partly confined one
        under ~5. **These are the parameters most worth calibrating.** Run the
        pass once, look at the ``confinement_ratio`` distribution the summary
        reports, and set breaks that match how your landscape actually divides —
        a single set of breaks rarely transfers between physiographic provinces.
    """
    import geopandas as gpd
    import rasterio
    from scipy import ndimage

    guard = guard or Guard.from_env()
    t0 = time.monotonic()
    warnings: list[str] = []

    with rasterio.open(network.segment_raster) as src:
        seg_ids = src.read(1)
        cell = abs(src.res[0])
    with rasterio.open(valley.valley_raster) as src:
        vb = src.read(1).astype(bool)
    with rasterio.open(valley.core_raster) as src:
        core = src.read(1).astype(bool)
    with rasterio.open(terrain.hand) as src:
        # masked, then NaN. Reading raw pulls the -9999 fill in as a real
        # height: it averaged to hand_mean_m = -4233 m on a landscape whose
        # whole relief is a few hundred metres.
        hand = np.ma.filled(src.read(1, masked=True).astype("float32"), np.nan)
    with rasterio.open(terrain.accumulation) as src:
        acc = np.ma.filled(
            src.read(1, masked=True).astype("float64"), 0.0)

    # TRUE metres. `cell` above is in the CRS's own units, and on a US
    # survey-foot state plane grid -- the normal case for US lidar -- using it
    # directly makes every area and width come out in feet under an `_m` name:
    #   valley_area_m2   10.76x too large (ft2 reported as m2)
    #   valley_width_m    3.28x too large
    #   drainage_km2     10.76x too large
    # and confinement_ratio then divides a foot width by a METRE channel width
    # from the hydraulic-geometry relation, so it is wrong by a different
    # factor again. Measured on a real 2.4 ftUS DEM.
    _u = getattr(terrain, "units", None) or {}
    cell_m = float(_u.get("cell_size_m") or terrain.cell_size or cell)
    cell_area = float(_u.get("cell_area_m2") or (cell_m * cell_m))
    cell_area_km2 = cell_area / 1e6
    mpu = float(_u.get("metres_per_unit") or 1.0)

    note("allocating valley cells to nearest segment")
    # Euclidean allocation: every cell inherits the id of the nearest segment
    # cell. One pass over the grid regardless of segment count.
    has_id = seg_ids > 0
    if not has_id.any():
        raise GeolensError("Segment id raster is empty.")
    # return_distances=False: the distance array is full-size float64 and is
    # discarded immediately -- 15.5 GB on a 1.94-billion-cell grid.
    ir, ic = ndimage.distance_transform_edt(
        ~has_id, sampling=cell_m, return_indices=True, return_distances=False
    )
    alloc = seg_ids[ir, ic]

    note("summarizing valley area per segment")
    n_seg = int(seg_ids.max())
    v_counts = np.bincount(alloc[vb].ravel(), minlength=n_seg + 1)
    c_counts = np.bincount(alloc[core].ravel(), minlength=n_seg + 1)

    # Drainage area over each segment's own stream cells.
    seg_flat = seg_ids.ravel()
    on_stream = seg_flat > 0
    acc_max = np.zeros(n_seg + 1)
    np.maximum.at(acc_max, seg_flat[on_stream], acc.ravel()[on_stream])

    # Mean HAND over each segment's VALLEY cells, not its stream cells.
    #
    # HAND is zero at every stream cell by construction — it is the height
    # above the nearest drainage, and a stream cell IS the drainage. The
    # segment-id raster is burned along the stream skeleton, so averaging HAND
    # over it returned 0.0 for every segment, always. The column looked like a
    # measurement and carried no information at all.
    #
    # Over the allocated valley bottom it means something: how high this
    # segment's valley floor sits above its own channel, which is the vertical
    # counterpart to valley_width_m.
    alloc_flat = alloc.ravel()
    hand_flat = hand.ravel()
    vb_sel = vb.ravel() & np.isfinite(hand_flat)
    hand_sum = np.bincount(alloc_flat[vb_sel], weights=hand_flat[vb_sel],
                           minlength=n_seg + 1)
    valley_n = np.bincount(alloc_flat[vb_sel], minlength=n_seg + 1)

    gdf = gpd.read_file(network.segments)
    sid = gdf["seg_id"].to_numpy().astype(int) + 1

    valley_area = v_counts[sid] * cell_area
    core_area = c_counts[sid] * cell_area
    # length_m is ALREADY metres: extract_network writes line.length * mpu
    # (network.py). This line used to multiply by mpu again -- a second
    # conversion left over from before network.py was fixed -- so on a foot
    # grid length_m came out at 0.3048 of the truth and valley_width_m,
    # core_width_m and confinement_ratio 3.28x too large (Eagle Creek v23:
    # every reach "unconfined", valley widths 77-343 m for 23-105 m).
    # Invisible to the feet/metres parity test until v25, because both passes
    # wrote to one folder and it compared the metric file with itself.
    length = gdf["length_m"].to_numpy().astype("float64")
    length = np.where(length > 0, length, np.nan)

    gdf["valley_area_m2"] = valley_area
    gdf["core_area_m2"] = core_area
    gdf["valley_width_m"] = valley_area / length
    gdf["core_width_m"] = core_area / length
    gdf["drainage_km2"] = acc_max[sid] * cell_area_km2
    with np.errstate(invalid="ignore", divide="ignore"):
        gdf["hand_mean_m"] = np.where(
            valley_n[sid] > 0, hand_sum[sid] / np.maximum(valley_n[sid], 1),
            np.nan
        )

    if channel_width_m is not None:
        cw = np.full(len(gdf), float(channel_width_m))
        cw_source = f"fixed {channel_width_m} m"
    else:
        area = np.maximum(gdf["drainage_km2"].to_numpy(), 1e-3)
        cw = channel_width_coef * area ** channel_width_exp
        cw_source = (
            f"estimated W = {channel_width_coef} * A^{channel_width_exp} (A in km²)"
        )
        warnings.append(
            "Channel width is estimated from drainage area with a generic "
            "hydraulic-geometry relation. Confinement ratio scales directly with "
            "it — substitute a local relation before using these classes for "
            "anything consequential."
        )
    gdf["channel_width_m"] = cw
    gdf["confinement_ratio"] = gdf["valley_width_m"] / cw

    note("classifying")
    gdf["confinement"] = [
        _confinement_class(r, confined_max, partly_confined_max)
        for r in gdf["confinement_ratio"]
    ]
    gdf["gradient_class"] = [_gradient_class(g) for g in gdf["gradient"]]
    ros = [
        _rosgen_candidates(c, g, w)
        for c, g, w in zip(gdf["confinement"], gdf["gradient_class"],
                           gdf["valley_width_m"])
    ]
    gdf["rosgen_candidates"] = [r[0] for r in ros]
    gdf["needs_geology"] = [r[1] for r in ros]

    classified = int((gdf["confinement"] != "unknown").sum())
    zero_valley = int((gdf["valley_area_m2"] == 0).sum())
    if zero_valley:
        warnings.append(
            f"{zero_valley:,} segment(s) were allocated no valley bottom — they "
            "sit outside the extracted valley, often headwater spurs. Their "
            "confinement is unknown, not zero."
        )

    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    seg_out = out_dir / "segments_classified.gpkg"
    gdf.to_file(str(seg_out), driver="GPKG")
    csv_out = out_dir / "segments_classified.csv"
    gdf.drop(columns="geometry").to_csv(csv_out, index=False)

    def _dist(col):
        vc = gdf[col].value_counts()
        return {str(k): int(v) for k, v in vc.items()}

    def _percentiles(col):
        s_ = gdf[col].replace([np.inf, -np.inf], np.nan).dropna()
        if s_.empty:
            return None
        qs = [5, 10, 25, 50, 75, 90, 95]
        return {f"p{q}": round(float(np.percentile(s_, q)), 3) for q in qs}

    def _stat(col):
        s = gdf[col].replace([np.inf, -np.inf], np.nan).dropna()
        if s.empty:
            return None
        return {
            "n": int(s.size), "min": float(s.min()), "median": float(s.median()),
            "mean": float(s.mean()), "max": float(s.max()),
        }

    length_by_conf = (
        gdf.groupby("confinement")["length_m"].sum() / 1000.0
    ).round(2).to_dict()

    summary = {
        "confinement_counts": _dist("confinement"),
        "confinement_length_km": {str(k): float(v) for k, v in length_by_conf.items()},
        "gradient_counts": _dist("gradient_class"),
        "rosgen_candidate_counts": _dist("rosgen_candidates"),
        "needs_geology_fraction": float(gdf["needs_geology"].mean()),
        "valley_width_m": _stat("valley_width_m"),
        "confinement_ratio": _stat("confinement_ratio"),
        "confinement_ratio_percentiles": _percentiles("confinement_ratio"),
        "gradient": _stat("gradient"),
    }

    elapsed = time.monotonic() - t0
    note(f"classified {classified:,}/{len(gdf):,} segments in {elapsed:.1f}s")

    return ClassifyResult(
        table=str(csv_out),
        segments=str(seg_out),
        segment_count=len(gdf),
        classified=classified,
        summary=summary,
        parameters={
            "channel_width": cw_source,
            "confined_max_ratio": confined_max,
            "partly_confined_max_ratio": partly_confined_max,
            "gradient_breaks": [[a, b, n] for a, b, n in GRADIENT_BREAKS],
            "segment_length_m": network.segment_length_m,
            **valley.parameters,
        },
        seconds=elapsed,
        warnings=warnings,
    )


def reclassify_confinement(
    segments: str | Path,
    confined_max: float = CONFINED_MAX,
    partly_confined_max: float = PARTLY_MAX,
    out_prefix: str = "reclassified",
    guard: Guard | None = None,
) -> ClassifyResult:
    """Apply new confinement breaks to segments that are already classified.

    **Changing the breaks does not require recomputing anything.** They are two
    thresholds on ``confinement_ratio``, which is already a column in the
    segments layer -- so this reads a table, relabels a column and writes it
    back.

    Until this existed, the only way to change them was to call
    ``landscape_pass`` again, which conditions the DEM, routes flow, computes
    HAND, extracts the valley bottom and re-segments the network before
    reaching the two lines that actually use the parameters. The starting
    notebook told you to do exactly that, in the step whose whole purpose is
    trying different breaks. On a 1.94-billion-cell DEM that is 2.7 hours to
    relabel a column.

    Calibration is supposed to be iterative. It is only iterative if it is fast.
    """
    import geopandas as gpd

    t0 = time.monotonic()
    guard = guard or Guard.from_env()
    warnings: list[str] = []

    gdf = gpd.read_file(str(guard.read_path(segments)))
    if "confinement_ratio" not in gdf.columns:
        raise GeolensError(
            f"{segments} has no confinement_ratio column, so the breaks cannot "
            f"be re-applied. Pass segments_classified.gpkg from the landscape "
            f"pass. Columns present: {sorted(gdf.columns)[:10]}"
        )
    if confined_max >= partly_confined_max:
        raise GeolensError(
            f"confined_max ({confined_max}) must be below partly_confined_max "
            f"({partly_confined_max}) — a confined valley is narrower than a "
            f"partly confined one."
        )

    note(f"re-applying breaks to {len(gdf):,} segments "
         f"(confined <= {confined_max:g}, partly <= {partly_confined_max:g})")
    gdf["confinement"] = [
        _confinement_class(r, confined_max, partly_confined_max)
        for r in gdf["confinement_ratio"]
    ]
    if "gradient_class" not in gdf.columns:
        gdf["gradient_class"] = [_gradient_class(g) for g in gdf["gradient"]]
    ros = [
        _rosgen_candidates(c, g, w)
        for c, g, w in zip(gdf["confinement"], gdf["gradient_class"],
                           gdf["valley_width_m"])
    ]
    gdf["rosgen_candidates"] = [r[0] for r in ros]
    gdf["needs_geology"] = [r[1] for r in ros]

    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    seg_out = out_dir / "segments_classified.gpkg"
    csv_out = out_dir / "segments_classified.csv"
    gdf.to_file(str(seg_out), driver="GPKG")
    gdf.drop(columns="geometry").to_csv(str(csv_out), index=False)

    def _dist(col):
        return {str(k): int(v) for k, v in gdf[col].value_counts().items()}

    length_by_conf = gdf.groupby("confinement")["length_m"].sum() / 1000.0
    classified = int((gdf["confinement"] != "unknown").sum())
    elapsed = time.monotonic() - t0
    note(f"reclassified in {elapsed:.2f}s")

    return ClassifyResult(
        table=str(csv_out), segments=str(seg_out), segment_count=len(gdf),
        classified=classified,
        summary={
            "confinement_counts": _dist("confinement"),
            "confinement_length_km": {str(k): float(v)
                                      for k, v in length_by_conf.items()},
            "rosgen_candidate_counts": _dist("rosgen_candidates"),
            "needs_geology_fraction": float(gdf["needs_geology"].mean()),
            "confinement_ratio_percentiles": {
                f"p{q}": float(gdf["confinement_ratio"].quantile(q / 100.0))
                for q in (5, 10, 25, 50, 75, 90, 95)
            },
        },
        parameters={"confined_max_ratio": confined_max,
                    "partly_confined_max_ratio": partly_confined_max,
                    "source_segments": str(segments)},
        seconds=elapsed, warnings=warnings,
    )


def confinement_break_sweep(
    segments: str | Path,
    candidates: "list[tuple[float, float]] | None" = None,
    guard: Guard | None = None,
) -> dict[str, Any]:
    """Class counts for several candidate breaks, without writing anything.

    What calibration actually needs: see how the landscape divides under
    several break pairs and pick one, rather than committing to a pair and
    re-running to find out. Reads one table; costs milliseconds per candidate.

    Defaults to percentile-based pairs of the segments' own distribution, which
    is the right starting point -- the literature values (2 and 5 channel
    widths) rarely transfer between landscapes.
    """
    import geopandas as gpd

    guard = guard or Guard.from_env()
    gdf = gpd.read_file(str(guard.read_path(segments)))
    if "confinement_ratio" not in gdf.columns:
        raise GeolensError(f"{segments} has no confinement_ratio column.")

    r = gdf["confinement_ratio"].astype(float)
    q = {p: float(r.quantile(p / 100.0)) for p in (10, 25, 33, 50, 66, 75, 90)}
    if candidates is None:
        candidates = [
            (CONFINED_MAX, PARTLY_MAX),        # the literature defaults
            (q[25], q[50]),
            (q[33], q[66]),
            (q[10], q[50]),
            (q[25], q[75]),
        ]

    rows = []
    skipped: list[tuple[float, float]] = []
    total_km = float(gdf["length_m"].sum()) / 1000.0
    for cmax, pmax in candidates:
        if cmax >= pmax:
            # Degenerate on a landscape whose ratio distribution is flat: two
            # percentiles can coincide. Dropping these silently made a
            # candidate the caller passed vanish from the results with no
            # explanation.
            skipped.append((float(cmax), float(pmax)))
            continue
        cls = [_confinement_class(x, cmax, pmax) for x in r]
        gdf["_c"] = cls
        by_km = (gdf.groupby("_c")["length_m"].sum() / 1000.0).to_dict()
        counts = {str(k): int(v) for k, v in gdf["_c"].value_counts().items()}
        rows.append({
            "confined_max": round(float(cmax), 3),
            "partly_confined_max": round(float(pmax), 3),
            "counts": counts,
            "length_km": {str(k): round(float(v), 2) for k, v in by_km.items()},
            "fraction_km": {str(k): round(float(v) / total_km, 3)
                            for k, v in by_km.items()} if total_km else {},
        })
    gdf.drop(columns=["_c"], errors="ignore", inplace=True)

    return {
        "segments": str(segments),
        "n_segments": len(gdf),
        "total_length_km": round(total_km, 2),
        "confinement_ratio_percentiles": q,
        "candidates": rows,
        "skipped": [
            {"confined_max": a, "partly_confined_max": b,
             "reason": "confined_max is not below partly_confined_max"}
            for a, b in skipped
        ],
        "note": (
            "Breaks are thresholds on confinement_ratio, which is already "
            "computed — none of this re-runs the DEM. Pick a pair and apply it "
            "with reclassify_confinement()."
        ),
    }
