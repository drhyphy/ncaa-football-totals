"""Synthetic state, attribution, identity and prefix-invariance tests only."""
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from ncaaf_model import pbp_state_rows as parser

GAME = 9007199254741049
HOME, AWAY = 10, 20


def play(number, **changes):
    row = {"season": 2024, "game_id": GAME, "id": 9007199254741200 + number,
           "sequenceNumber": 9007199254741300 + number, "game_play_number": number,
           "homeTeamId": HOME, "awayTeamId": AWAY, "drive.id": "drive-one", "type.text": "Rush",
           "orig_play_type": "Rush", "text": "Runner rushes for four yards", "period.number": 1,
           "clock.displayValue": f"14:{60-number*10:02d}", "start.down": 1, "start.distance": 10,
           "start.yardsToEndzone": 70, "start.team.id": HOME, "end.team.id": HOME, "statYardage": 4,
           "homeScore": 0, "awayScore": 0, "scoringPlay": False, "isPenalty": False,
           "isTurnover": False, "penalty_flag": False, "penalty_no_play": False,
           "penalty_offset": False, "kneel_down": False, "kickoff_play": False, "punt_play": False}
    row.update(changes)
    return row


def raw(*records):
    frame = pd.DataFrame(list(records), columns=parser.RAW_COLUMNS)
    for name in parser.INTEGER_COLUMNS:
        # Build nullable arrays directly: no float conversion of large IDs.
        frame[name] = pd.array([row[name] for row in records], dtype="Int64")
    for name in parser.FLAG_COLUMNS:
        frame[name] = pd.array([row[name] for row in records], dtype="boolean")
    return frame


def schedule(**changes):
    row = {"game_id": GAME, "season": 2024, "week": 4, "game_date": "2024-09-21T16:00:00Z",
           "neutral_site": False, "home_id": HOME, "away_id": AWAY, "status": "STATUS_FINAL"}
    row.update(changes)
    return pd.DataFrame([row])


def three(**middle):
    return raw(play(1), play(2, **middle), play(3))


def test_precise_large_id_order_availability_and_response_units():
    frame = three(**{"statYardage": 10})
    rows, coverage = parser.prepare_rows(frame, schedule())
    assert rows.game_id.tolist() == [GAME, GAME]
    assert rows.play_id.tolist() == [9007199254741202, 9007199254741203]
    assert rows.sequence_number.tolist() == [9007199254741302, 9007199254741303]
    assert rows.game_play_number.tolist() == [2, 3]
    assert rows.clock_seconds.iloc[0] == 10 and np.isnan(rows.clock_seconds.iloc[1])
    assert rows.conversion.tolist() == [1., 0.]
    assert rows.half_seconds_remaining.tolist() == [1780, 1770]
    assert rows.available_at.eq(pd.Timestamp("2024-09-21T22:00:00Z")).all()
    assert rows.team_id.tolist() == [HOME, HOME] and rows.opponent_id.tolist() == [AWAY, AWAY]
    assert coverage["exclusions"]["unavailable_previous_post_score"] == 1


def test_input_order_is_not_play_order_and_extra_publisher_columns_cannot_leak():
    original = three()
    expected, _ = parser.prepare_rows(original, schedule())
    changed = original.iloc[::-1].copy()
    changed["EPA"] = [1e15, -1e15, np.nan]
    changed["start.homeScore"] = [99, 88, 77]
    changed["wp_before"] = [1, 0, 1]
    output, _ = parser.prepare_rows(changed, schedule())
    pd.testing.assert_frame_equal(output, expected)
    before = original.copy(deep=True); sched = schedule(); before_schedule = sched.copy(deep=True)
    parser.prepare_rows(original, sched)
    pd.testing.assert_frame_equal(original, before); pd.testing.assert_frame_equal(sched, before_schedule)


def test_pre_score_is_lagged_current_and_future_postscore_cannot_rewrite_current_state():
    baseline = raw(play(1, homeScore=14, awayScore=7), play(2), play(3))
    old, _ = parser.prepare_rows(baseline, schedule())
    modified = baseline.copy()
    modified.loc[1, ["homeScore", "awayScore"]] = [40, 10]
    modified.loc[2, ["homeScore", "awayScore"]] = [55, 20]
    new, _ = parser.prepare_rows(modified, schedule())
    assert old.iloc[0].score_margin == new.iloc[0].score_margin == 7
    assert new.iloc[0].pre_home_score == 14 and new.iloc[1].pre_home_score == 40
    assert not new.game_play_number.eq(1).any()


def test_first_archived_row_never_assumes_zero_score_even_number_one():
    rows, _ = parser.prepare_rows(raw(play(1, homeScore=7), play(2)), schedule())
    assert rows.game_play_number.tolist() == [2] and rows.score_margin.tolist() == [7]


def test_missing_previous_post_score_drops_only_that_unavailable_state():
    rows, coverage = parser.prepare_rows(raw(play(1), play(2, homeScore=None), play(3), play(4)), schedule())
    assert rows.game_play_number.tolist() == [2, 4]
    assert coverage["exclusions"]["unavailable_previous_post_score"] == 2


def test_numbered_gap_withholds_only_first_potentially_stale_pre_score():
    frame = raw(play(1), play(2, homeScore=7), play(3, game_play_number=5, homeScore=14),
                play(4, game_play_number=6))
    rows, coverage = parser.prepare_rows(frame, schedule())
    assert rows.game_play_number.tolist() == [2, 6]
    assert rows.pre_home_score.tolist() == [0, 14]
    assert np.isnan(rows.clock_seconds.iloc[0])
    reason = "unavailable_pre_score_after_archived_play_number_gap"
    assert coverage["exclusions"][reason] == 1
    assert coverage["by_game"][0]["exclusions"][reason] == 1
    frame.loc[1, "homeScore"] = 42
    changed, _ = parser.prepare_rows(frame, schedule())
    pd.testing.assert_frame_equal(changed, rows)


def test_away_possession_uses_own_minus_opponent_score_and_second_half_clock():
    rows, _ = parser.prepare_rows(raw(play(1, homeScore=21, awayScore=7),
        play(2, **{"start.team.id":AWAY,"end.team.id":AWAY,"period.number":4,"clock.displayValue":"02:00"})), schedule(neutral_site=True))
    row=rows.iloc[0]
    assert row.score_margin == -14 and row.team_id == AWAY and row.opponent_id == HOME
    assert not row.is_home and row.neutral_site and row.half_seconds_remaining == 120


@pytest.mark.parametrize("change,reason", [
    ({"type.text":"Timeout","orig_play_type":"Timeout","text":"Home timeout"},"unsupported_play_type"),
    ({"isPenalty":True},"penalty_or_nullified"),
    ({"penalty_flag":True},"penalty_or_nullified"),
    ({"penalty_no_play":True},"penalty_or_nullified"),
    ({"penalty_offset":True},"penalty_or_nullified"),
    ({"text":"Rush for 20 yards, penalty declined"},"penalty_or_nullified"),
    ({"text":"Pass nullified; no play"},"penalty_or_nullified"),
    ({"kneel_down":True},"kneel"),
    ({"text":"Quarterback takes a knee"},"kneel"),
    ({"text":"Quarterback spiked the ball"},"spike"),
    ({"type.text":"Pass Spike"},"spike"),
    ({"kickoff_play":True},"special_team_provenance"),
    ({"punt_play":True},"special_team_provenance"),
    ({"orig_play_type":"Punt Return","type.text":"Fumble Recovery (Own)"},"special_team_provenance"),
])
def test_administrative_and_penalty_rows_are_not_bridged(change, reason):
    rows, report=parser.prepare_rows(raw(play(1),play(2),play(3,**change),play(4)),schedule())
    assert rows.game_play_number.tolist()==[2,4]
    assert np.isnan(rows.clock_seconds.iloc[0])
    assert report["exclusions"][reason]==1


@pytest.mark.parametrize("change,reason", [
    ({"period.number":5},"nonregulation_or_unknown_period"),
    ({"start.down":0},"invalid_scrimmage_state"),
    ({"start.distance":0},"invalid_scrimmage_state"),
    ({"start.yardsToEndzone":101},"invalid_scrimmage_state"),
    ({"start.team.id":999},"invalid_possession_team"),
    ({"clock.displayValue":"15:01"},"invalid_clock"),
    ({"clock.displayValue":"02:60"},"invalid_clock"),
])
def test_invalid_state_is_counted_without_destroying_other_plays(change, reason):
    rows, report=parser.prepare_rows(three(**change),schedule())
    assert rows.game_play_number.tolist()==[3] and report["exclusions"][reason]==1


@pytest.mark.parametrize("difference,expected", [(0,0.),(60,60.),(61,None),(-1,None)])
def test_clock_pair_boundaries(difference, expected):
    def clock(seconds):return f"{seconds//60:02d}:{seconds%60:02d}"
    rows,_=parser.prepare_rows(raw(play(1),play(2,**{"clock.displayValue":clock(500)}),
        play(3,**{"clock.displayValue":clock(500-difference)})),schedule())
    value=rows.clock_seconds.iloc[0]
    assert np.isnan(value) if expected is None else value==expected


@pytest.mark.parametrize("change", [{"drive.id":"new-drive"},{"drive.id":None},{"period.number":2},
    {"start.team.id":AWAY,"end.team.id":AWAY},{"game_play_number":5}])
def test_changed_drive_period_possession_or_number_gap_breaks_clock(change):
    rows,_=parser.prepare_rows(raw(play(1),play(2),play(3,**change)),schedule())
    assert np.isnan(rows.clock_seconds.iloc[0]) and rows.conversion.iloc[0]==0


@pytest.mark.parametrize("change", [{"end.team.id":AWAY},{"end.team.id":None},{"isTurnover":True}])
def test_current_retention_is_required_for_clock(change):
    rows,_=parser.prepare_rows(three(**change),schedule())
    assert np.isnan(rows.clock_seconds.iloc[0])


@pytest.mark.parametrize("typ", ["Sack","Interception","Pass Interception Return Touchdown", "Fumble Recovery (Opponent)","Fumble Return Touchdown"])
def test_sacks_and_turnover_plays_are_observed_failures_not_filtered_out(typ):
    rows,_=parser.prepare_rows(three(**{"type.text":typ,"orig_play_type":typ,"statYardage":-5,
        "end.team.id":AWAY if typ!="Sack" else HOME}),schedule())
    assert rows.conversion.iloc[0]==0
    if typ!="Sack":assert np.isnan(rows.clock_seconds.iloc[0])


@pytest.mark.parametrize("after,end", [(6,HOME),(7,AWAY),(8,None)])
def test_offensive_td_attribution_allows_appended_pat_and_end_boundary(after,end):
    rows,_=parser.prepare_rows(three(**{"type.text":"Passing Touchdown","orig_play_type":"Passing Touchdown",
        "text":"Pass for a touchdown (Kicker kick is good)","homeScore":after,"scoringPlay":True,
        "end.team.id":end,"statYardage":None}),schedule())
    assert rows.conversion.iloc[0]==1 and rows.pass_play.iloc[0]==1 and np.isnan(rows.clock_seconds.iloc[0])


def test_td_requires_explicit_offensive_score_attribution_and_defensive_score_fails():
    rows,_=parser.prepare_rows(three(**{"type.text":"Rushing Touchdown","orig_play_type":"Rushing Touchdown", "homeScore":0,"scoringPlay":True}),schedule())
    assert np.isnan(rows.conversion.iloc[0])
    rows,_=parser.prepare_rows(three(**{"type.text":"Rushing Touchdown","orig_play_type":"Rushing Touchdown", "awayScore":6,"scoringPlay":True}),schedule())
    assert rows.conversion.iloc[0]==0


def test_ambiguous_offensive_td_does_not_become_failure_from_end_team_change():
    rows,_=parser.prepare_rows(three(**{"type.text":"Passing Touchdown", "orig_play_type":"Passing Touchdown",
        "homeScore":None,"scoringPlay":True,"end.team.id":AWAY}),schedule())
    assert np.isnan(rows.conversion.iloc[0])


def test_opening_week_zero_is_valid_without_relaxing_game_identity():
    rows,_=parser.prepare_rows(three(),schedule(week=0))
    assert len(rows)==2 and rows.week.eq(0).all()


def test_retained_fumble_conversion_and_unknown_pass_category():
    row={"type.text":"Fumble Recovery (Own)","orig_play_type":"Fumble Recovery (Own)","text":"Ball fumbled and recovered", "statYardage":11}
    rows,report=parser.prepare_rows(three(**row),schedule())
    assert rows.conversion.iloc[0]==1 and np.isnan(rows.pass_play.iloc[0]) and report["pass_unknown_rows"]==1
    row["text"]="Receiver completed pass, fumbled and recovered"
    rows,_=parser.prepare_rows(three(**row),schedule())
    assert rows.pass_play.iloc[0]==1
    row.update({"type.text":"Fumble Recovery (Own) Touchdown","homeScore":7,"scoringPlay":True,"statYardage":None})
    rows,_=parser.prepare_rows(three(**row),schedule())
    assert rows.conversion.iloc[0]==1


def test_goal_to_go_uses_shorter_physical_target_and_response_missingness_is_separate():
    rows,_=parser.prepare_rows(three(**{"start.distance":10,"start.yardsToEndzone":3,"statYardage":3}),schedule())
    assert rows.conversion.iloc[0]==1
    rows,_=parser.prepare_rows(three(**{"statYardage":None}),schedule())
    assert np.isnan(rows.conversion.iloc[0]) and rows.clock_seconds.iloc[0]==10
    rows,_=parser.prepare_rows(three(**{"end.team.id":None}),schedule())
    assert np.isnan(rows.conversion.iloc[0]) and np.isnan(rows.clock_seconds.iloc[0])


def test_unknown_helper_flags_are_preserved_in_coverage_without_imaginary_veto():
    rows,report=parser.prepare_rows(three(**{"penalty_flag":None,"isPenalty":None,"isTurnover":None}),schedule())
    assert rows.game_play_number.tolist()==[2,3] and rows.clock_seconds.iloc[0]==10
    assert report["unknown_raw_flags"]["penalty_flag"]==1 and report["unknown_raw_flags"]["isPenalty"]==1


@pytest.mark.parametrize("column", ["id","game_play_number"])
def test_ambiguous_duplicate_play_id_or_number_rejects_whole_game(column):
    frame=three();frame.loc[1,column]=frame.loc[0,column]
    rows,report=parser.prepare_rows(frame,schedule())
    assert rows.empty and report["exclusions"]["ambiguous_duplicate_play_identity"]==3


@pytest.mark.parametrize("column", ["id","sequenceNumber","game_play_number"])
def test_missing_play_identity_or_order_still_rejects_whole_game(column):
    frame=three();frame.loc[1,column]=pd.NA
    rows,report=parser.prepare_rows(frame,schedule())
    assert rows.empty and report["exclusions"]["missing_or_invalid_play_identity"]==3


def test_local_order_disagreement_withholds_states_without_rejecting_game():
    frame=three();frame.loc[[1,2],"game_play_number"]=[3,2]
    rows,report=parser.prepare_rows(frame,schedule())
    assert rows.empty
    assert report["exclusions"]["unavailable_pre_score_after_archived_play_number_gap"]==2
    assert report["by_game"][0]["rejected_reason"] is None
    assert report["matched_final_games"]==1


def test_schedule_identity_mismatch_still_rejects_whole_game():
    for col in ["homeTeamId","awayTeamId","season"]:
        frame=three();frame.loc[1,col]=999
        rows,report=parser.prepare_rows(frame,schedule())
        assert rows.empty and report["exclusions"]["raw_schedule_identity_mismatch"]==3


def ordered_chain(count=8):
    return raw(*(play(i, **{"clock.displayValue": f"13:{(count-i)*5:02d}"})
                 for i in range(1, count+1)))


def test_sequence_ties_are_local_state_and_clock_barriers():
    frame=ordered_chain()
    frame.loc[3,"sequenceNumber"]=frame.loc[2,"sequenceNumber"]
    rows,report=parser.prepare_rows(frame,schedule())
    assert rows.game_play_number.tolist()==[2,6,7,8]
    assert np.isnan(rows.clock_seconds.iloc[0])
    assert rows.clock_seconds.iloc[1:3].tolist()==[5.,5.]
    assert report["version"]=="raw-play-state-rows-v2"
    assert report["exclusions"]["ambiguous_sequence_number"]==2
    assert report["exclusions"]["unavailable_pre_score_after_ambiguous_sequence_number"]==1
    assert report["sequence_tie_rows"]==2 and report["sequence_tie_groups"]==1
    assert report["by_game"][0]["rejected_reason"] is None
    assert report["by_game"][0]["sequence_tie_rows"]==2


def test_tie_input_permutation_and_tied_scores_cannot_change_retained_state():
    frame=ordered_chain()
    frame.loc[3,"sequenceNumber"]=frame.loc[2,"sequenceNumber"]
    frame.loc[2,["homeScore","awayScore"]]=[10,20]
    frame.loc[3,["homeScore","awayScore"]]=[30,40]
    expected,coverage=parser.prepare_rows(frame,schedule())
    permuted=frame.iloc[[7,3,0,6,2,4,1,5]].copy()
    changed,changed_coverage=parser.prepare_rows(permuted,schedule())
    pd.testing.assert_frame_equal(changed,expected)
    assert changed_coverage==coverage
    frame.loc[[2,3],["homeScore","awayScore"]]=999
    poisoned,_=parser.prepare_rows(frame,schedule())
    pd.testing.assert_frame_equal(poisoned,expected)


def test_multiple_tie_blocks_do_not_merge_or_bridge_into_valid_segments():
    frame=ordered_chain()
    frame.loc[1,"sequenceNumber"]=frame.loc[0,"sequenceNumber"]
    frame.loc[4,"sequenceNumber"]=frame.loc[3,"sequenceNumber"]
    rows,report=parser.prepare_rows(frame,schedule())
    assert rows.game_play_number.tolist()==[7,8]
    assert rows.clock_seconds.iloc[0]==5
    assert report["sequence_tie_groups"]==2 and report["sequence_tie_rows"]==4
    assert report["exclusions"]["unavailable_pre_score_after_ambiguous_sequence_number"]==2


def test_administrative_tied_row_still_blocks_the_immediate_successor():
    frame=ordered_chain(6)
    frame.loc[3,"sequenceNumber"]=frame.loc[2,"sequenceNumber"]
    frame.loc[3,"type.text"]="Timeout"
    frame.loc[3,"orig_play_type"]="Timeout"
    frame.loc[3,"text"]="Timeout"
    rows,report=parser.prepare_rows(frame,schedule())
    assert rows.game_play_number.tolist()==[2,6]
    assert report["exclusions"]["ambiguous_sequence_number"]==2
    assert report["exclusions"]["unavailable_pre_score_after_ambiguous_sequence_number"]==1
    assert rows.clock_seconds.isna().all()


def test_all_tied_rows_have_no_fallback_order_or_state():
    frame=ordered_chain()
    frame["sequenceNumber"]=pd.array([9007199254741311]*len(frame),dtype="Int64")
    rows,report=parser.prepare_rows(frame,schedule())
    assert rows.empty and report["exclusions"]["ambiguous_sequence_number"]==8
    assert report["sequence_tie_groups"]==1 and report["matched_final_games"]==1


def test_global_play_number_reversal_retains_only_locally_agreeing_segments():
    frame=ordered_chain()
    frame["game_play_number"]=pd.array([1,2,3,10,11,4,5,6],dtype="Int64")
    rows,report=parser.prepare_rows(frame,schedule())
    assert rows.game_play_number.tolist()==[2,3,11,5,6]
    assert rows.play_id.tolist()==[9007199254741202,9007199254741203,
                                  9007199254741205,9007199254741207,9007199254741208]
    assert rows.clock_seconds.dropna().tolist()==[5.,5.]
    assert report["exclusions"]["unavailable_pre_score_after_archived_play_number_gap"]==2
    assert report["by_game"][0]["rejected_reason"] is None


def test_sparse_but_unique_sequence_values_do_not_require_numeric_adjacency():
    frame=three()
    frame["sequenceNumber"]=pd.array([100,200,500],dtype="Int64")
    rows,report=parser.prepare_rows(frame,schedule())
    assert rows.game_play_number.tolist()==[2,3] and rows.clock_seconds.iloc[0]==10
    assert report["sequence_tie_rows"]==report["sequence_tie_groups"]==0


def test_unmatched_nonfinal_and_invalid_schedule_context_are_not_training_data():
    for changes,reason in [({"game_id":GAME+1},"unmatched_schedule_game"),
                           ({"status":"STATUS_SCHEDULED"},"nonfinal_schedule_game"),
                           ({"home_id":AWAY},"invalid_schedule_context"),
                           ({"game_date":"2024-09-21"},"invalid_schedule_kickoff")]:
        rows,report=parser.prepare_rows(three(),schedule(**changes))
        assert rows.empty and report["exclusions"][reason]==3
    with pytest.raises(ValueError,match="duplicate canonical"):
        parser.prepare_rows(three(),pd.concat([schedule(),schedule()]))


def test_float_identity_and_wrong_schema_fail_before_any_rounding():
    frame=three();frame["id"]=frame.id.astype(float)
    with pytest.raises(ValueError,match="without float"):
        parser.prepare_rows(frame,schedule())
    with pytest.raises(ValueError,match="columns"):
        parser.prepare_rows(three().drop(columns="orig_play_type"),schedule())
    frame=three();frame["isPenalty"]=frame.isPenalty.astype(int)
    with pytest.raises(ValueError,match="Boolean"):
        parser.prepare_rows(frame,schedule())


def test_missing_raw_game_id_and_empty_inputs_have_explicit_coverage():
    frame=three();frame.loc[0,"game_id"]=pd.NA
    rows,report=parser.prepare_rows(frame,schedule())
    assert report["exclusions"]["missing_or_invalid_game_id"]==1 and rows.game_play_number.tolist()==[3]
    rows,report=parser.prepare_rows(three().iloc[:0],schedule())
    assert rows.empty and list(rows)==list(parser.OUTPUT_COLUMNS) and report["raw_rows"]==0
