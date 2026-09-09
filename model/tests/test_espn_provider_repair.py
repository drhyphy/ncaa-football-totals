from ncaaf_model.espn_provider_repair import select_provider, normalized_row


def quote(provider_id,name,total):
    return {"provider":{"id":provider_id,"name":name},"overUnder":total,"spread":-3.5,"homeTeamOdds":{"favorite":True}}


def test_live_only_is_rejected_even_with_plausible_total_and_opening_field():
    row=quote("59","ESPN Bet - Live Odds",51.5)
    row["open"]={"total":{"american":"48.5"}}
    selected,reason=select_provider({"items":[row]})
    assert selected is None
    assert reason=="live_provider_only"


def test_fixed_priority_is_independent_of_order_and_line_or_result():
    preferred=quote("58","ESPN BET",55.5)
    other=quote("100","DraftKings",51.5)
    for rows in [[other,preferred],[preferred,other]]:
        assert select_provider({"items":rows})[0] is preferred


def test_unknown_role_or_predictive_provider_is_not_a_book():
    for row in [quote("1001","accuscore",50.5),quote("58","ESPN BET LIVE",50.5),quote("59","ESPN BET",50.5)]:
        assert select_provider({"items":[row]})[0] is None


def test_missing_total_does_not_substitute_opening_or_defaults():
    row=quote("58","ESPN BET",None)
    row["open"]={"total":{"american":"48.5"}}
    assert select_provider({"items":[row]})[0] is None


def test_export_preserves_observation_vs_unverified_market_time_and_signed_spread():
    game={"game_id":1,"game_date":"2024-10-01","home_score":20,"away_score":10,"over_under":51.5,"home_team_spread":-3.5}
    response={"source_url":"https://example.com","observed_at":"2026-09-09T00:00:00Z","response_sha256":"abc"}
    selected=quote("58","ESPN BET",48.5)
    selected["homeTeamOdds"]={"favorite":False}
    row=normalized_row(game,response,selected)
    assert row["spread"]==3.5
    assert row["market_total"]==48.5
    assert row["quote_timestamp"] is None
    assert row["closing_time_verified"] is False
    assert row["role_verified"] is True
