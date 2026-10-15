"""Landscape-scale valley delineation.

Reach-scale tools (``geolens.sections``, ``geolens.morphology``) measure one
channel precisely. This subpackage generalizes across whole drainage networks:
condition the DEM once, derive HAND and slope, extract a valley bottom, then
summarize onto network segments and classify confinement.
"""

from .terrain import (  # noqa: F401
    build_terrain, available_backends, backend_notes, compare_backends,
    restream, terrain_from_result, TerrainResult,
)
from .valley import extract_valley_bottom, ValleyResult  # noqa: F401
from .network import extract_network, NetworkResult  # noqa: F401
from .classify import (  # noqa: F401
    classify_segments, reclassify_confinement, confinement_break_sweep,
    ClassifyResult,
)
from .features import (  # noqa: F401
    terrain_roughness, suggest_slope_std_min, detect_gullies,
    detect_headcuts,
    RoughnessResult, GullyResult, HeadcutResult,
)
from .plan import plan_run, available_ram_gb, RunPlan  # noqa: F401
from .tiles import (  # noqa: F401
    split_by_basins, derive_basins, run_tiled, merge_vector_outputs, grid_tiles,
    TileSet,
)
from .flow import (  # noqa: F401
    flow_routing, catchment, complete_watersheds, FlowResult,
)
from .streamtype import (  # noqa: F401
    rosgen_stream_types, classify_stream_type, StreamTypeResult,
    STREAM_TYPE_DESC, SUBSTRATE_DESC,
)
from .valleyform import (  # noqa: F401
    valley_form, valley_catchments, side_slope_class, vf_class,
    ValleyFormResult,
)
from .watershed import watershed, check_inflow  # noqa: F401
from .huc import fetch_huc, huc_selftest  # noqa: F401
from .fans import detect_alluvial_fans, FanResult  # noqa: F401
from .rosgen import (  # noqa: F401
    rosgen_valley_types, classify_valley_type, gradient_class,
    VALLEY_TYPES, ValleyTypeResult,
)
from .pipeline import run_landscape  # noqa: F401

__all__ = [
    "build_terrain", "available_backends", "backend_notes",
    "compare_backends", "restream", "terrain_from_result", "TerrainResult",
    "extract_valley_bottom", "ValleyResult",
    "extract_network", "NetworkResult",
    "classify_segments", "reclassify_confinement",
    "confinement_break_sweep", "ClassifyResult",
    "terrain_roughness", "suggest_slope_std_min", "detect_gullies",
    "detect_headcuts",
    "RoughnessResult", "GullyResult", "HeadcutResult",
    "plan_run", "available_ram_gb", "RunPlan",
    "split_by_basins", "derive_basins", "run_tiled", "merge_vector_outputs",
    "grid_tiles",
    "TileSet",
    "flow_routing", "catchment", "complete_watersheds", "FlowResult",
    "watershed", "check_inflow", "fetch_huc", "huc_selftest",
    "rosgen_stream_types", "classify_stream_type",
    "StreamTypeResult", "STREAM_TYPE_DESC", "SUBSTRATE_DESC",
    "valley_form", "valley_catchments", "side_slope_class",
    "vf_class",
    "ValleyFormResult",
    "detect_alluvial_fans", "FanResult",
    "rosgen_valley_types", "classify_valley_type", "gradient_class",
    "VALLEY_TYPES", "ValleyTypeResult",
    "run_landscape",
]
