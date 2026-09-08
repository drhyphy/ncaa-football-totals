import numpy as np
import pandas as pd

from ncaaf_model.backtest import MarketWinModel, tune_alpha


def test_market_model_and_alpha_are_fit_from_supplied_training_rows_only() -> None:
    train = pd.DataFrame(
        {
            "market_home_margin": [-14, -7, -3, 0, 3, 7, 14] * 20,
            "total": [50, 48, 52, 55, 52, 48, 50] * 20,
            "home_win": [0, 0, 0, 0, 1, 1, 1] * 20,
        }
    )
    model = MarketWinModel.fit(train)
    probabilities = model.predict(train)
    assert probabilities[0] < probabilities[-1]
    external = np.clip(probabilities + np.where(train["home_win"].to_numpy() == 1, 0.05, -0.05), 0.01, 0.99)
    alpha = tune_alpha(train["home_win"].to_numpy(), probabilities, external, 0.01)
    assert 0.0 <= alpha <= 1.0
