"""Linear units: the assumption that silently breaks every threshold.

Every parameter in geolens is documented in metres -- ``stream_threshold_km2``,
``segment_length_m``, ``max_hand_m``, ``min_bank_height``. The code computed all
of them by treating the DEM's cell size as metres, which is true only when the
DEM is in a metric CRS.

It very often is not. State plane in the United States is published in US survey
feet, and a DEM on NAD83(2011) / New Mexico Central (ftUS) with 2.4 ft cells is
0.73 m data. Nothing about that is visible in the numbers:

* ``stream_threshold_km2=1.0`` was computed as 1e6 / 2.4**2 = 173,605 cells.
  The true count is 1e6 / 0.7315**2 = 1,868,657 -- so the threshold was
  **10.8x too low** and the stream network came out an order of magnitude
  denser than asked for. Everything keyed off the network inherits that.
* ``segment_length_m=500`` produced 500-foot segments: 152 m.
* Vertical thresholds are worse, because a GeoTIFF almost never declares its
  vertical unit. Elevations of 6,066-11,974 in a ftUS CRS are feet, and
  ``max_hand_m=15`` then means 15 feet: 4.6 m.

So: read the unit, convert, and when the vertical unit cannot be known, say what
was assumed and show the relief both ways so the user can see at a glance which
one is real.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Anything within this of 1.0 metre per unit is metric for our purposes.
_METRIC_TOL = 1e-6


@dataclass
class UnitInfo:
    horizontal_unit: str
    metres_per_unit: float
    cell_x: float                  # in CRS units
    cell_y: float
    cell_size_m: float             # mean cell size in TRUE metres
    cell_area_m2: float
    is_metric: bool
    square_cells: bool
    vertical_metres_per_unit: float
    vertical_unit: str
    vertical_assumed: bool
    warnings: list[str] = field(default_factory=list)

    def cells_for_area_km2(self, km2: float) -> int:
        """Cell count for a contributing area, using TRUE metres."""
        return max(1, int(round(km2 * 1e6 / self.cell_area_m2)))

    def to_crs_units(self, metres: float) -> float:
        """A horizontal distance in metres, expressed in the CRS's own units."""
        return metres / self.metres_per_unit

    def z_to_metres(self, z: float) -> float:
        return z * self.vertical_metres_per_unit

    def z_from_metres(self, metres: float) -> float:
        """A vertical threshold in metres, expressed in the DEM's z unit."""
        return metres / self.vertical_metres_per_unit

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _as_pyproj(crs):
    """Accept a rasterio CRS, a pyproj CRS, an EPSG string or a WKT.

    rasterio's CRS has no ``axis_info``, so reading the unit straight off
    whatever the caller passed silently returned "unknown, 1.0" -- i.e. metres
    -- for every rasterio dataset, which is exactly the case this module
    exists to handle.
    """
    if crs is None:
        return None
    if hasattr(crs, "axis_info"):
        return crs
    try:
        import pyproj

        return pyproj.CRS.from_user_input(crs)
    except Exception:                              # noqa: BLE001
        try:
            import pyproj

            return pyproj.CRS.from_wkt(crs.to_wkt())
        except Exception:                          # noqa: BLE001
            return None


def _horizontal(crs) -> tuple[str, float]:
    crs = _as_pyproj(crs)
    if crs is None:
        return "unknown", 1.0
    try:
        ax = crs.axis_info[0]
        return str(ax.unit_name), float(ax.unit_conversion_factor)
    except Exception:                              # noqa: BLE001
        return "unknown", 1.0


def _vertical(crs) -> tuple[str, float] | None:
    """The declared vertical unit, if the CRS has a vertical component."""
    crs = _as_pyproj(crs)
    if crs is None:
        return None
    try:
        for sub in (getattr(crs, "sub_crs_list", None) or []):
            if getattr(sub, "is_vertical", False) and sub.axis_info:
                ax = sub.axis_info[0]
                return str(ax.unit_name), float(ax.unit_conversion_factor)
    except Exception:                              # noqa: BLE001
        pass
    return None


def unit_info(crs, cell_x: float, cell_y: float,
              z_range: tuple[float, float] | None = None,
              vertical_unit: str | None = None) -> UnitInfo:
    """Work out what a cell and an elevation actually mean on the ground.

    ``vertical_unit`` overrides the guess: pass ``"m"``, ``"ft"`` or
    ``"ftUS"`` when you know what the DEM's z values are.
    """
    name, mpu = _horizontal(crs)
    cx, cy = abs(float(cell_x)), abs(float(cell_y))
    warnings: list[str] = []

    is_metric = abs(mpu - 1.0) < _METRIC_TOL
    square = abs(cx - cy) <= 1e-6 * max(cx, cy, 1.0)

    cell_size_m = 0.5 * (cx + cy) * mpu
    cell_area_m2 = (cx * mpu) * (cy * mpu)

    # -- vertical ------------------------------------------------------------
    explicit = {"m": ("metre", 1.0), "metre": ("metre", 1.0),
                "meter": ("metre", 1.0), "metres": ("metre", 1.0),
                "ft": ("foot", 0.3048),
                "foot": ("foot", 0.3048),
                "ftus": ("US survey foot", 0.30480060960121924),
                "us survey foot": ("US survey foot", 0.30480060960121924)}
    v_assumed = False
    if vertical_unit and vertical_unit.lower() in explicit:
        v_name, v_mpu = explicit[vertical_unit.lower()]
    else:
        declared = _vertical(crs)
        if declared is not None:
            v_name, v_mpu = declared
        else:
            # No vertical CRS. A DEM is virtually always published with z in
            # the same unit as x and y, so that is the assumption -- but it IS
            # an assumption and is reported as one.
            v_name, v_mpu = name, mpu
            v_assumed = True

    # -- what to tell the user ----------------------------------------------
    if not is_metric:
        warnings.append(
            f"This DEM is in {name}, not metres ({mpu:.6f} m per unit). Cells "
            f"are {cx:g} {name} = {cx * mpu:.4f} m. geolens parameters are in "
            f"METRES and are converted for you, but any number you read off "
            f"the raster in a GIS is in {name}."
        )
    if not square:
        warnings.append(
            f"Cells are not square ({cx:g} x {cy:g}). D8 flow routing assumes "
            f"they are, so flow direction is biased toward the shorter axis, "
            f"and drainage area is computed from the true cell area "
            f"({cell_area_m2:.4f} m2) rather than from cell_size**2."
        )
    if v_assumed and not is_metric and z_range is not None:
        lo, hi = float(z_range[0]), float(z_range[1])
        warnings.append(
            f"Vertical unit is not declared by the CRS, so it is assumed to "
            f"match the horizontal unit ({v_name}). Elevations run "
            f"{lo:,.0f} to {hi:,.0f}, which under that assumption is "
            f"{lo * v_mpu:,.0f} to {hi * v_mpu:,.0f} m. If those metres look "
            f"wrong for this landscape, pass vertical_unit= explicitly -- "
            f"every vertical threshold depends on it."
        )

    return UnitInfo(
        horizontal_unit=name, metres_per_unit=mpu, cell_x=cx, cell_y=cy,
        cell_size_m=cell_size_m, cell_area_m2=cell_area_m2,
        is_metric=is_metric, square_cells=square,
        vertical_metres_per_unit=v_mpu, vertical_unit=v_name,
        vertical_assumed=v_assumed, warnings=warnings,
    )
