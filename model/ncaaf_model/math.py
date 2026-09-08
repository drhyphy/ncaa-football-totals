from __future__ import annotations

import math
from statistics import median
from typing import Iterable


def clamp(value: float, low: float = 1e-6, high: float = 1.0 - 1e-6) -> float:
    return max(low, min(high, float(value)))


def logit(probability: float) -> float:
    p = clamp(probability)
    return math.log(p / (1.0 - p))


def expit(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def blend_logit(market_probability: float, model_probability: float, alpha: float) -> float:
    a = max(0.0, min(1.0, float(alpha)))
    return expit(logit(market_probability) + a * (logit(model_probability) - logit(market_probability)))


def american_to_decimal(american: float) -> float:
    if american == 0:
        raise ValueError("American odds cannot be zero")
    return 1.0 + (american / 100.0 if american > 0 else 100.0 / abs(american))


def american_to_implied(american: float) -> float:
    return 1.0 / american_to_decimal(american)


def decimal_to_american(decimal: float) -> int:
    if decimal <= 1.0:
        raise ValueError("Decimal odds must exceed 1.0")
    if decimal >= 2.0:
        return int(round((decimal - 1.0) * 100.0))
    return int(round(-100.0 / (decimal - 1.0)))


def devig_pair(home_american: float, away_american: float) -> tuple[float, float]:
    home = american_to_implied(home_american)
    away = american_to_implied(away_american)
    total = home + away
    return home / total, away / total


def expected_value(probability: float, american: float) -> float:
    decimal = american_to_decimal(american)
    return clamp(probability) * decimal - 1.0


def kelly_fraction(probability: float, american: float) -> float:
    decimal = american_to_decimal(american)
    b = decimal - 1.0
    p = clamp(probability)
    return max(0.0, (b * p - (1.0 - p)) / b)


def median_or_none(values: Iterable[float]) -> float | None:
    cleaned = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return float(median(cleaned)) if cleaned else None
