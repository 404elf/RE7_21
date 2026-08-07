import re7_21


def test_game_state_initializes_with_two_players():
    state = re7_21.GameState()

    assert len(state.p1_hand) == 2
    assert len(state.p2_hand) == 2
    assert state.p1_fingers == re7_21.MAX_HP
    assert state.p2_fingers == re7_21.MAX_HP
    assert state.phase == "ACTION"
    assert state.turn in (1, 2)
    assert state.round_id == 1


def test_full_reset_restores_gameplay_state():
    state = re7_21.GameState()
    state.p1_fingers = 1
    state.p2_fingers = 2
    state.phase = "GAMEOVER"
    state.round_id = 99

    state.full_reset()

    assert state.p1_fingers == re7_21.MAX_HP
    assert state.p2_fingers == re7_21.MAX_HP
    assert state.phase == "ACTION"
    assert state.round_id == 100
    assert len(state.p1_hand) == 2
    assert len(state.p2_hand) == 2


def test_reset_round_keeps_target_score_from_settings_and_deals_two_cards():
    state = re7_21.GameState()
    previous_round_id = state.round_id

    state.reset_round()

    assert state.target_score == re7_21.SETTINGS["target_score"]
    assert len(state.p1_hand) == 2
    assert len(state.p2_hand) == 2
    assert state.round_id == previous_round_id + 1
    assert state.phase == "ACTION"
