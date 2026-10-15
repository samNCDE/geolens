"""Cross-section profiles with KNOWN bankfull, for testing bankfull detection.

Every case is built from straight segments so the truth is exact, then sampled
at a chosen spacing with optional lidar-like noise. The cases are the channel
forms that separate a working bankfull detector from one that only works on a
textbook floodplain:

``floodplain``  a trapezoidal channel in a flat floodplain -- the easy case.
``confined``    a steep-banked channel cut straight into a V valley. No
                floodplain and no crest: the ground keeps rising. The slope-
                break walker needs a flat to stop at and has none; a crest
                finder climbs the valley wall.
``trench``      a channel with a narrow inset bench, inside a trench whose
                walls rise to a terrace. The terrace is the tempting wrong
                answer; bankfull is the inner bank top.
``asymmetric``  floodplain on one bank, valley wall on the other -- the usual
                real case. Water leaves at the lower bank.
``pure_v``      a V with no channel break at all. There is no bankfull to
                find, and the honest answer is "indeterminate".
"""
from __future__ import annotations

import numpy as np


def _poly(points, offs):
    """Piecewise-linear elevation through (offset, z) knots."""
    xs, zs = zip(*points)
    return np.interp(offs, xs, zs)


def make(case: str, spacing: float = 0.25, noise: float = 0.0,
         seed: int = 1, half: float = 60.0):
    """Return (offsets, z, truth). Offsets are centred on the thalweg."""
    offs = np.arange(-half, half + spacing / 2, spacing)
    if case == "floodplain":
        # 14 m top, 12 m bed, 1 m deep, banks 1:1; floodplain to +-40 m.
        pts = [(-half, 4.0), (-40, 1.0), (-7, 1.0), (-6, 0.0), (6, 0.0),
               (7, 1.0), (40, 1.0), (half, 4.0)]
        truth = dict(width=14.0, max_depth=1.0, mean_depth=13.0 / 14.0,
                     floodprone_width=80.0 + 2 * (1.0 / (3.0 / 20.0)),
                     letter_hint="C")
    elif case == "confined":
        # 5 m top, 1 m deep, banks 3:1 (steep), then valley walls at 1.2.
        b = 1.0 / 3.0
        pts = [(-half, 1.0 + (half - 2.5) * 1.2), (-2.5, 1.0), (-2.5 + b, 0.0),
               (2.5 - b, 0.0), (2.5, 1.0), (half, 1.0 + (half - 2.5) * 1.2)]
        fp = 5.0 + 2 * (1.0 / 1.2)
        truth = dict(width=5.0, max_depth=1.0, mean_depth=(5.0 + 5.0 - 2 * b)
                     / 2 / 5.0, floodprone_width=fp, letter_hint="A/G")
    elif case == "trench":
        # 14 m top, 1 m deep, banks 1.5:1; 1 m bench each side; trench walls
        # at 1:1 rising 3 m to a terrace.
        b = 1.0 / 1.5
        pts = [(-half, 4.0), (-11.0, 4.0), (-8.0, 1.0), (-7.0, 1.0),
               (-7.0 + b, 0.0), (7.0 - b, 0.0), (7.0, 1.0), (8.0, 1.0),
               (11.0, 4.0), (half, 4.0)]
        truth = dict(width=14.0, max_depth=1.0,
                     mean_depth=(14.0 + 14.0 - 2 * b) / 2 / 14.0,
                     floodprone_width=14.0 + 2 * 1.0 + 2 * 1.0,
                     letter_hint="F")
    elif case == "asymmetric":
        # floodplain left, valley wall right; 8 m top, 0.8 m deep.
        pts = [(-half, 3.0), (-30, 0.8), (-4.0, 0.8), (-3.2, 0.0), (3.2, 0.0),
               (4.0, 0.8), (half, 0.8 + (half - 4.0) * 0.35)]
        truth = dict(width=8.0, max_depth=0.8,
                     mean_depth=(8.0 + 6.4) / 2 * 0.8 / 8.0,
                     floodprone_width=None, letter_hint=None)
    elif case == "pure_v":
        pts = [(-half, half * 0.5), (0.0, 0.0), (half, half * 0.5)]
        truth = dict(width=None, max_depth=None, mean_depth=None,
                     floodprone_width=None, letter_hint=None)
    else:
        raise ValueError(case)
    z = _poly(pts, offs)
    if noise > 0:
        z = z + np.random.default_rng(seed).normal(0.0, noise, z.shape)
    return offs, z, truth


CASES = ("floodplain", "confined", "trench", "asymmetric", "pure_v")
