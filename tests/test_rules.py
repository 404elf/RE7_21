import re7_21


def test_resolve_round_player_one_wins_by_closer_score():
    state = re7_21.GameState()
    state.p1_hand = [10, 10]
    state.p2_hand = [5, 5]
    state.target_score = 21

    state.resolve_round()

    assert state.phase == "RESULT"
    assert state.round_winner == 1
    assert state.round_damage >= 0


def test_check_bust_uses_target_score():
    state = re7_21.GameState()
    state.target_score = 21
    state.p1_hand = [10, 12]

    assert state.check_bust(1) is True
