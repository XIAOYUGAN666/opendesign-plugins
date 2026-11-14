from dlubal_rfem_mcp.setup import select_sdk


def test_setup_matches_complete_build_without_reading_key():
    assert select_sdk("6.14.0013.249") == "2.14.13"
    assert select_sdk("6.14.0013") is None
    assert select_sdk("6.14.0013.250") is None
