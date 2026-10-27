import pytest

from distributed_smb.shared.roster import (
    GlobalRoster,
    RosterEntry,
    RosterValidationError,
)


def test_roster_keeps_players_ordered_by_join_index_and_knows_its_host():
    """join_index is the total order the election relies on, so the roster
    must expose it consistently however entries were added."""
    roster = GlobalRoster()
    roster.add_player(RosterEntry("p2", "127.0.0.1", 5001, 1))
    roster.add_player(RosterEntry("p1", "127.0.0.1", 5000, 0, is_host=True))
    roster.add_player(RosterEntry("p3", "127.0.0.1", 5002, 2))

    assert [p.player_id for p in roster.get_all_players()] == ["p1", "p2", "p3"]
    assert roster.get_host().player_id == "p1"
    assert roster.get_player("p1").is_host is True


def test_a_duplicate_join_index_is_refused():
    """Two players sharing an index would both claim the same election slot."""
    roster = GlobalRoster()
    roster.add_player(RosterEntry("p1", "127.0.0.1", 5000, 0))

    with pytest.raises(RosterValidationError, match="Duplicate join_index"):
        roster.add_player(RosterEntry("p2", "127.0.0.1", 5001, 0))


def test_roster_entry_rejects_malformed_fields():
    """A bad entry would otherwise reach every peer through RosterUpdate."""
    with pytest.raises(RosterValidationError, match="Invalid player_id"):
        RosterEntry("", "127.0.0.1", 5000, 0)

    with pytest.raises(RosterValidationError, match="udp_port out of range"):
        RosterEntry("p1", "127.0.0.1", 100, 0)

    with pytest.raises(RosterValidationError, match="join_index must be >= 0"):
        RosterEntry("p1", "127.0.0.1", 5000, -1)


def test_promote_host_moves_the_host_flag():
    roster = GlobalRoster()
    roster.add_player(RosterEntry("p1", "10.0.0.1", 50010, 0, is_host=True))
    roster.add_player(RosterEntry("p2", "10.0.0.2", 50011, 1))

    roster.promote_host("p2")

    assert roster.get_host().player_id == "p2"
    assert roster.get_player("p1").is_host is False
