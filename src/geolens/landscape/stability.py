"""Stable or unstable form: each reach's stream type against its valley type.

Rosgen's valley types each carry the stream types that form naturally in
them, and a few that appear only when the channel is out of balance -- a G
(gully) or F in a wide alluvial valley (VIII) is an incised channel, not that
valley's stable form. That is the question this answers per reach:

``stable``
    The stream type is one Rosgen associates with the valley type.
``unstable``
    The stream type is one Rosgen lists for this valley type only under
    disequilibrium: VI (G under fault disequilibrium), VIII (D, F, G
    depending on local streambed and riparian conditions), X (G and F
    where local base grades have changed).
``check``
    The stream type is not associated with this valley type at all. Either
    the channel has departed from its stable form, or one of the two
    classifications is wrong -- the section plots and the valley layer say
    which. Deliberately not called "unstable": on a DEM-derived type that
    would be claiming more than is known.
``unknown``
    No valley type, or no stream type.

The table is NRCS (2007) National Engineering Handbook Part 654, Technical
Supplement 3E, Table TS3E-3 "Valley type, morphological description, and
stream type association", after Rosgen (1996). It is transcribed in
``VALLEY_STREAM_TYPES`` with the source wording kept beside each row, so a
reader can check it -- and Sam, who knows the newer valley descriptors, can
correct it.

Both classifications carry a confidence; the result takes the lower. A
"stable" from two low-confidence calls is a hypothesis, not a finding.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..safety import Guard, GeolensError, note

# valley type -> (associated stream letters, letters listed only under
# disequilibrium, the table's own wording)
VALLEY_STREAM_TYPES: dict[str, tuple[frozenset, frozenset, str]] = {
    "I": (frozenset({"A"}), frozenset(), "A and Aa+"),
    "II": (frozenset({"B"}), frozenset(), "B"),
    "III": (frozenset({"A", "B", "G", "D"}), frozenset(),
            "A, B, G, and D"),
    "IV": (frozenset({"F"}), frozenset(), "F"),
    "V": (frozenset({"C", "D", "G"}), frozenset(), "C, D, G"),
    "VI": (frozenset({"B", "C", "F"}), frozenset({"G"}),
           "Mostly B with C and F; some G (G observed under fault "
           "disequilibrium)"),
    "VII": (frozenset({"A", "G"}), frozenset(), "A and G"),
    "VIII": (frozenset({"C", "E"}), frozenset({"D", "F", "G"}),
             "C and E / D, F, and G (depending on local streambed and "
             "riparian conditions)"),
    "IX": (frozenset({"C", "D"}), frozenset(), "C and D"),
    "X": (frozenset({"E", "C", "DA"}), frozenset({"G", "F"}),
          "E or C and DA, G, and F (G and F common where local base grades "
          "have been changed)"),
    "XI": (frozenset({"DA", "C", "E"}), frozenset(), "DA; C and E"),
}
SOURCE = ("NRCS (2007) NEH Part 654 Technical Supplement 3E, Table TS3E-3, "
          "after Rosgen (1996)")

STATUS_COLOURS = [("stable", "#1baf7a", "stable form"),
                  ("unstable", "#c2352b", "unstable form"),
                  ("check", "#eda100", "not expected in this valley - check"),
                  ("unknown", "#b8b7b2", "unknown")]

_RANK = {"high": 2, "medium": 1, "low": 0}


def form_status(valley_type: Any, stream_letter: Any) -> tuple[str, str]:
    """(status, note) for one valley type and one stream letter."""
    v = str(valley_type).strip() if valley_type is not None else ""
    s = str(stream_letter).strip() if stream_letter is not None else ""
    if v in ("", "nan", "None") or s in ("", "nan", "None"):
        return "unknown", ("no valley type" if v in ("", "nan", "None")
                           else "no stream type")
    if v not in VALLEY_STREAM_TYPES:
        return "unknown", f"valley type {v} is not in the table"
    ok, bad, words = VALLEY_STREAM_TYPES[v]
    if s in ok:
        return "stable", f"{s} is a stream type of valley type {v} ({words})"
    if s in bad:
        return "unstable", (f"{s} appears in valley type {v} only out of "
                            f"balance ({words}); its stable forms are "
                            f"{', '.join(sorted(ok))}")
    return "check", (f"{s} is not a stream type of valley type {v} "
                     f"({words}): a departure, or one of the two "
                     f"classifications is wrong")


def _combined(valley_type, alternatives, stream_letter) -> tuple[str, str]:
    """Handle an unresolved valley type: judge every candidate."""
    if valley_type is not None and str(valley_type) not in ("", "nan", "None"):
        return form_status(valley_type, stream_letter)
    cands = [c for c in str(alternatives or "").replace(",", "|").split("|")
             if c.strip() and c.strip() in VALLEY_STREAM_TYPES]
    if not cands:
        return form_status(None, stream_letter)
    res = {c: form_status(c, stream_letter)[0] for c in cands}
    if len(set(res.values())) == 1:
        st = next(iter(res.values()))
        return st, (f"valley type unresolved ({'/'.join(cands)}); "
                    f"{st} under every candidate")
    return "check", ("depends on the valley type, which is unresolved: "
                     + ", ".join(f"{c} -> {v}" for c, v in res.items()))


def stream_stability(valley_types: str | Path, stream_types: str | Path,
                     out_prefix: str = "stability",
                     guard: Guard | None = None) -> dict[str, Any]:
    """Join valley and stream types per reach and judge the form.

    ``valley_types`` is ``valley_types.gpkg`` (or ``valley_extent.gpkg``);
    ``stream_types`` is ``stream_types.gpkg``. Both from the same run, joined
    on ``seg_id``. Writes ``stability_dir/stream_stability.gpkg`` -- the
    reaches, both classifications, ``form_status``, ``form_note`` and
    ``form_confidence`` -- with a QGIS style coloured by status whose map tip
    is the reach's section sheet.
    """
    import geopandas as gpd
    import pandas as pd

    guard = guard or Guard.from_env()
    t0 = time.monotonic()
    st = gpd.read_file(str(guard.read_path(stream_types)), layer="stream_types")
    vt = gpd.read_file(str(guard.read_path(valley_types)))
    key = "seg_id"
    if key not in st.columns or key not in vt.columns:
        raise GeolensError("Both layers need a seg_id column; pass the "
                           "valley_types.gpkg and stream_types.gpkg of one run.")
    vcols = [c for c in ("valley_type", "alternatives", "rosgen_candidates",
                         "confidence", "basis") if c in vt.columns]
    v = pd.DataFrame(vt[[key] + vcols]).rename(columns={
        "confidence": "valley_confidence", "basis": "valley_basis",
        "alternatives": "valley_alternatives"})
    g = st.merge(v, on=key, how="left")
    alt_col = ("valley_alternatives" if "valley_alternatives" in g.columns
               else "rosgen_candidates")
    out = [_combined(r.get("valley_type"),
                     r.get(alt_col) if pd.isna(r.get("valley_type")) else None,
                     r.get("stream_letter"))
           for r in g.to_dict("records")]
    g["form_status"] = [o[0] for o in out]
    g["form_note"] = [o[1] for o in out]

    def _conf(a, b):
        ra = _RANK.get(str(a), None)
        rb = _RANK.get(str(b), None)
        if ra is None or rb is None:
            return "low"
        return {2: "high", 1: "medium", 0: "low"}[min(ra, rb)]

    g["form_confidence"] = [_conf(a, b) for a, b in zip(
        g.get("valley_confidence", pd.Series([None] * len(g))),
        g.get("stream_confidence", pd.Series([None] * len(g))))]
    g["form_source"] = SOURCE

    out_dir = Path(guard.write_path(f"{out_prefix}_dir")).with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "stream_stability.gpkg"
    # The reach sheets live with the stream types: point the links there.
    if "plot_file" in g.columns:
        src_dir = Path(str(guard.read_path(stream_types))).parent
        g["plot_path"] = [str((src_dir / f).resolve()) if isinstance(f, str)
                          else None for f in g["plot_file"]]
        g["plot_file"] = [str(Path("..") / src_dir.name / f)
                          if isinstance(f, str) else None
                          for f in g["plot_file"]]
    gpd.GeoDataFrame(g, geometry="geometry", crs=st.crs).to_file(
        str(path), driver="GPKG", layer="stream_stability")
    try:
        from ..gis_style import plot_link_qml, write_qgis_style
        write_qgis_style(path, "stream_stability", plot_link_qml(
            display_expression=("'Reach ' || \"seg_id\" || ' - ' || "
                                "coalesce(\"valley_type\", '?') || ' / ' || "
                                "coalesce(\"stream_type\", '?') || ' - ' || "
                                "\"form_status\""),
            category_field="form_status", categories=STATUS_COLOURS,
            line_width_mm=1.2, action_name="Open reach sheet",
            missing_note="No section sheet for this reach."))
    except Exception:                                        # noqa: BLE001
        pass
    counts = g["form_status"].value_counts().to_dict()
    km = (g.assign(_km=g.geometry.length)
          .groupby("form_status")["_km"].sum())
    note(f"  stream form: {counts}")
    return {"stability": str(path),
            "counts": {str(k): int(v) for k, v in counts.items()},
            "by_confidence": {str(k): int(v) for k, v in
                              g["form_confidence"].value_counts().items()},
            "length_by_status_crs": {str(k): float(v) for k, v in km.items()},
            "source": SOURCE, "seconds": round(time.monotonic() - t0, 1)}
