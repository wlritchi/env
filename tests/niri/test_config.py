from wlrenv.niri.config import STATE_DIR, get_storage_path


def test_state_dir_is_under_local_state() -> None:
    assert ".local/state/niri" in str(STATE_DIR)


def test_get_storage_path_returns_json_file() -> None:
    path = get_storage_path("tmux")
    assert path.suffix == ".json"
    assert path.name == "tmux.json"
