"""Statistical helpers used by benchmark summaries."""

from __future__ import annotations

import math


def _percent(numerator: int, denominator: int) -> float:
    return round(numerator * 100.0 / denominator, 2)


def _wilson_score_interval(successes: int, total: int, confidence_z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    p = successes / total
    z = confidence_z
    denominator = 1.0 + (z * z) / total
    center = (p + (z * z) / (2.0 * total)) / denominator
    margin = (z * math.sqrt((p * (1.0 - p) + (z * z) / (4.0 * total)) / total)) / denominator
    low = max(0.0, center - margin)
    high = min(1.0, center + margin)
    return round(low * 100.0, 2), round(high * 100.0, 2)
