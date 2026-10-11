import pytest

from call.state import STATE_RANK, is_terminal, map_dial_state
from test.fakes import FakeStore, call_row


@pytest.mark.parametrize(
    ("state", "termination", "expected"),
    [
        ("Queued", None, ("dialing", 1)),
        ("Ringing", None, ("ringing", 2)),
        ("In-Progress", None, ("talking", 3)),
        ("Terminated", "completed", ("extracting", 4)),
        ("Terminated", "busy", ("failed", 6)),
        ("Terminated", "no-answer", ("failed", 6)),
        ("Terminated", "failed", ("failed", 6)),
        ("Terminated", "canceled", ("failed", 6)),
        ("Terminated", None, ("failed", 6)),
    ],
)
def test_map_dial_state_table(state, termination, expected):
    assert map_dial_state({"state": state, "terminationType": termination}) == expected


def test_bare_terminated_without_termination_key_is_failed():
    assert map_dial_state({"state": "Terminated"}) == ("failed", 6)


def test_terminated_with_unrecognised_termination_is_failed_not_extracting():
    assert map_dial_state({"state": "Terminated", "terminationType": "something-new"}) == ("failed", 6)


@pytest.mark.parametrize(
    "status", [None, {}, {"state": None}, {"state": "Unknown"}, {"state": "Paused"}, "Queued"]
)
def test_unknown_status_leaves_row_unchanged(status):
    assert map_dial_state(status) is None


def test_mapping_is_case_tolerant():
    assert map_dial_state({"state": "in-progress"}) == ("talking", 3)
    assert map_dial_state({"state": "TERMINATED", "terminationType": "Completed"}) == ("extracting", 4)


def test_rank_table_and_terminal_states():
    assert STATE_RANK == {
        "planned": 0,
        "dialing": 1,
        "ringing": 2,
        "talking": 3,
        "extracting": 4,
        "done": 6,
        "failed": 6,
    }
    assert is_terminal("done") and is_terminal("failed")
    for state in ("planned", "dialing", "ringing", "talking", "extracting", None):
        assert not is_terminal(state)


def test_lower_rank_write_is_refused_by_store():
    store = FakeStore()
    run = store.seed_run("u1", calls=[call_row(state="talking", state_rank=3)])
    [row] = run["calls"]
    assert store.update_call_state(row["id"], "ringing", 2) is False
    assert store.update_call_state(row["id"], "talking", 3) is False
    assert store.update_call_state(row["id"], "done", 6, {"outcome": {"track": "quote"}}) is True
    assert store.update_call_state(row["id"], "failed", 6) is False
    refreshed = store.get_call(row["id"])
    assert refreshed["state"] == "done" and refreshed["outcome"] == {"track": "quote"}
