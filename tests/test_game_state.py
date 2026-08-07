import re7_21


def test_game_state_initializes_with_two_players():
    state = re7_21.GameState()

    assert len(state.hands) == 2
    assert len(state.hp) == 2
    assert state.phase == "ACTION"
    assert state.turn_player in (0, 1)


def test_full_reset_restores_gameplay_state():
    state = re7_21.GameState()
    state.hp = [1, 2]
    state.phase = "GAMEOVER"
    state.round_id = 99

    state.full_reset()

    assert state.hp == [re7_21.MAX_HP, re7_21.MAX_HP]
    assert state.phase == "ACTION"
    assert state.round_id == 0


def test_reset_round_keeps_target_score_from_settings():
    state = re7_21.GameState()
    state.reset_round()

    assert state.target_score == re7_21.SETTINGS["target_score"]
    assert len(state.hands[0]) == 0
    assert len(state.hands[1]) == 0
