import re7_21


def test_get_trump_card_number_branch_preserves_legacy_tuple(monkeypatch):
    state = re7_21.GameState()

    monkeypatch.setattr(re7_21.random, "random", lambda: 0.0)
    monkeypatch.setattr(re7_21.random, "choice", lambda seq: seq[0])

    card = state.get_trump_card()

    assert card == ("Two", "DRAW_SPEC", 2)


def test_calculate_potential_damage_applies_add_and_shield():
    state = re7_21.GameState()
    state.active_trumps = [
        {"owner": 1, "type": "ADD", "val": 2, "name": "Add 2"},
        {"owner": 2, "type": "SHIELD", "val": 1, "name": "Shield"},
    ]

    assert state.calculate_potential_damage(2) == 2
    assert state.calculate_potential_damage(1) == 0
