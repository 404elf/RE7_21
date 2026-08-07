import json

import re7_21


def test_load_config_uses_source_file_directory(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "game_settings": {"max_hp": 7},
                "trump_weights": {"Add 1": 99},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(re7_21, "__file__", str(tmp_path / "re7_21.py"))
    monkeypatch.delattr(re7_21.sys, "frozen", raising=False)

    loaded = re7_21.load_config()

    assert loaded["game_settings"]["max_hp"] == 7
    assert loaded["game_settings"]["target_score"] == 21
    assert loaded["trump_weights"]["Add 1"] == 99


def test_load_config_falls_back_to_defaults_on_invalid_json(tmp_path, monkeypatch):
    (tmp_path / "config.json").write_text("{invalid json", encoding="utf-8")

    monkeypatch.setattr(re7_21, "__file__", str(tmp_path / "re7_21.py"))
    monkeypatch.delattr(re7_21.sys, "frozen", raising=False)

    loaded = re7_21.load_config()

    assert loaded["game_settings"]["max_hp"] == 10
    assert loaded["game_settings"]["target_score"] == 21
    assert loaded["game_settings"]["initial_trumps_count"] == 4


def test_load_config_uses_executable_directory_when_frozen(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"game_settings": {"target_score": 24}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(re7_21.sys, "frozen", True, raising=False)
    monkeypatch.setattr(re7_21.sys, "executable", str(tmp_path / "RE7_21_UE.exe"))

    loaded = re7_21.load_config()

    assert loaded["game_settings"]["target_score"] == 24
    assert loaded["game_settings"]["max_hp"] == 10
