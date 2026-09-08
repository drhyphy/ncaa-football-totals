import math

from ncaaf_model.math import (
    american_to_decimal,
    blend_logit,
    devig_pair,
    expected_value,
    kelly_fraction,
)


def test_american_odds_and_devig() -> None:
    assert american_to_decimal(+150) == 2.5
    assert math.isclose(american_to_decimal(-200), 1.5)
    home, away = devig_pair(-110, -110)
    assert math.isclose(home, 0.5)
    assert math.isclose(away, 0.5)


def test_ev_and_kelly() -> None:
    assert math.isclose(expected_value(0.5, +110), 0.05)
    assert kelly_fraction(0.5, +110) > 0
    assert kelly_fraction(0.4, -110) == 0


def test_logit_blend_is_bounded_and_market_anchored() -> None:
    assert math.isclose(blend_logit(0.4, 0.8, 0.0), 0.4)
    assert math.isclose(blend_logit(0.4, 0.8, 1.0), 0.8)
    blended = blend_logit(0.4, 0.8, 0.2)
    assert 0.4 < blended < 0.8
