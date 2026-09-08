import pandas as pd

from ncaaf_model.config import load_settings
from ncaaf_model.scoring import score_candidates


def test_only_recommended_candidate_can_be_paper_eligible() -> None:
    settings = load_settings()
    game = pd.DataFrame(
        [
            {
                "event_id": "x",
                "espn_game_id": 1,
                "commence_time": "2099-08-29T16:00:00Z",
                "week": 1,
                "away_team": "Away",
                "home_team": "Home",
                "market_home_probability": 0.50,
                "best_home_american": 110,
                "best_away_american": 110,
                "best_home_book": "fanduel",
                "best_away_book": "draftkings",
                "book_count": 5,
                "consensus_dispersion": 0.01,
                "consensus_home_spread": 0.0,
                "consensus_total": 50.0,
                "fpi_home_probability": 0.90,
                "ratings_home_probability": 0.85,
                "schedule_match": True,
            }
        ]
    )
    artifact = {"live_alpha": {"weeks_0_4": 0.20, "weeks_5_plus": 0.30}}
    scored = score_candidates(game, artifact, settings, "2099-08-20T00:00:00Z")
    assert scored.loc[scored["paper_bet"], "candidate"].unique().tolist() == ["market_fpi_residual"]
    assert not scored.loc[scored["candidate"].eq("market_public_ensemble"), "paper_bet"].any()
