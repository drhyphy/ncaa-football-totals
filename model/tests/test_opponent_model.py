from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from ncaaf_model.opponent_model import TARGETS, DEFAULTS, adjusted_features, weekly_cutoff, fit_artifact, projections


def history():
    rng = np.random.default_rng(1)
    rows=[]
    for i in range(80):
        team, opponent = i%4, (i//4+1)%4
        rows.append({'game_id':i,'team_id':team,'opponent_id':opponent,'season':2025,'week':i//8+1,
            'available_at':pd.Timestamp('2025-08-25',tz='UTC')+pd.Timedelta(days=i),
            'is_home':i%2==0,'neutral_site':False,
            **{k:v+rng.normal(0,.1) for k,v in zip(TARGETS,DEFAULTS)}})
    return pd.DataFrame(rows)


def matchup():
    return pd.DataFrame([{'game_id':900,'season':2025,'week':12,'game_date':'2025-11-22T18:00:00Z',
        'home_id':0,'away_id':1,'neutral_site':False,'market_total':50.,'market_home_spread':-3.,'actual_total':60.}])


def test_weekly_information_boundary_is_eastern_and_never_future():
    assert weekly_cutoff('2025-11-22T18:00:00Z') == pd.Timestamp('2025-11-17T05:00:00Z')
    assert weekly_cutoff('2025-11-22T18:00:00Z','2025-11-16T10:30:00Z') == pd.Timestamp('2025-11-16T10:30:00Z')


def test_results_after_cutoff_cannot_change_opponent_adjustments():
    h=history(); games=matchup()
    original=adjusted_features(games,h)
    future=h.copy(); future['available_at']=pd.Timestamp('2025-11-18',tz='UTC')
    future['points_for']=900; future['ppd']=100
    changed=adjusted_features(games,pd.concat([h,future],ignore_index=True))
    assert changed.adjusted_score_total.iloc[0] == pytest.approx(original.adjusted_score_total.iloc[0])
    assert changed.adjusted_drive_total.iloc[0] == pytest.approx(original.adjusted_drive_total.iloc[0])
    games['actual_total']=-1000
    own_score_changed=adjusted_features(games,h)
    assert own_score_changed.adjusted_score_total.iloc[0] == pytest.approx(original.adjusted_score_total.iloc[0])


def test_ratings_adjust_for_defense_and_unseen_teams_remain_finite():
    h=history()
    # The same strong offense scores 10 extra, and opponent 3 concedes 8 extra.
    h['points_for']=28+10*(h.team_id==0)+8*(h.opponent_id==3)
    g=matchup()
    base=adjusted_features(g,h)
    g['away_id']=3
    weak_defense=adjusted_features(g,h)
    assert weak_defense.adjusted_score_total.iloc[0] > base.adjusted_score_total.iloc[0]
    g['home_id']=9999
    unseen=adjusted_features(g,h)
    assert np.isfinite(unseen.adjusted_score_total.iloc[0])
    assert unseen.adjusted_history_games.iloc[0] == 0
