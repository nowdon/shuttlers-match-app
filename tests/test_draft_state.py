import json

from utils.draft_state import (
    clear_draft_state,
    get_active_draft,
    is_active_draft,
    load_draft_state,
    save_draft_state,
)


def test_valid_draft_state_is_active():
    state = {
        "draft": True,
        "matches": [[1, 2, 3, 4]],
        "bench": [5],
    }

    assert is_active_draft(state) is True


def test_draft_false_is_not_active():
    state = {
        "draft": False,
        "matches": [[1, 2, 3, 4]],
        "bench": [5],
    }

    assert is_active_draft(state) is False


def test_empty_draft_true_is_active():
    state = {
        "draft": True,
        "matches": [],
        "bench": [],
    }
    assert is_active_draft(state) is True

def test_empty_draft_false_is_not_active():
    state = {
        "draft": False,
        "matches": [],
        "bench": [],
    }
    assert is_active_draft(state) is False


def test_draft_with_only_matches_is_active():
    state = {
        "draft": True,
        "matches": [[1, 2, 3, 4]],
        "bench": [],
    }

    assert is_active_draft(state) is True


def test_draft_with_only_bench_is_active():
    state = {
        "draft": True,
        "matches": [],
        "bench": [1],
    }

    assert is_active_draft(state) is True


def test_clear_draft_state_removes_active_draft(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    save_draft_state([[1, 2, 3, 4]], [5])

    assert get_active_draft() == load_draft_state()

    clear_draft_state()

    assert get_active_draft() is None
    assert not (tmp_path / "draft_state.json").exists()


def test_save_draft_state_keeps_existing_schema(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    save_draft_state([[1, 2, 3, 4]], [5])

    state = json.loads((tmp_path / "draft_state.json").read_text(encoding="utf-8"))
    assert state["draft"] is True
    assert state["matches"] == [[1, 2, 3, 4]]
    assert state["bench"] == [5]
    assert "timestamp" in state
    assert "court_count" not in state
