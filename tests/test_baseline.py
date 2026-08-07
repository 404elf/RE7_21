import re7_21


def test_legacy_module_imports_without_starting_client():
    """Importing the legacy module must not start the Pygame client loop."""
    assert hasattr(re7_21, "GameState")
    assert hasattr(re7_21, "GameClient")
    assert callable(re7_21.send_msg)
    assert callable(re7_21.recv_msg)
