import json
from dataclasses import asdict

import pytest

from distributed_smb.shared.messages.gameplay import (
    BlockDestroyedMessage,
    GateStateChangedMessage,
    LevelResetMessage,
    MessageValidationError,
    PlayerDisconnected,
    PlayerLeft,
    PowerUpCollectedMessage,
)
from distributed_smb.shared.messages.recovery import HostDiscoveryProbe, HostIdentityResponse
from distributed_smb.shared.messages.session import RosterUpdate, SessionCreate, SessionJoin
from distributed_smb.shared.roster import GlobalRoster, RosterEntry, RosterValidationError


def test_message_creation():
    msg = SessionJoin(session_id="abc", player_id="p1", ip="127.0.0.1", port=5000)

    assert msg.player_id == "p1"
    assert msg.message_type.value == "session_join"


def test_serialization():
    msg = SessionJoin(session_id="abc", player_id="p1", ip="127.0.0.1", port=5000)

    data = json.dumps(asdict(msg))
    loaded = json.loads(data)

    assert loaded["player_id"] == "p1"


def test_roster_serialization():
    roster = GlobalRoster()
    roster.add_player(RosterEntry("p1", "127.0.0.1", 5000, 0))

    msg = RosterUpdate(roster=roster)

    data = json.dumps(asdict(msg))

    assert "p1" in data


def test_session_join_invalid_player_id():
    with pytest.raises(MessageValidationError, match="Invalid player_id"):
        SessionJoin(session_id="abc", player_id="", ip="127.0.0.1", port=5000)


def test_session_join_invalid_port():
    with pytest.raises(MessageValidationError, match="Port out of range"):
        SessionJoin(session_id="abc", player_id="p1", ip="127.0.0.1", port=100)


def test_session_join_invalid_session_id():
    with pytest.raises(MessageValidationError, match="Invalid session_id"):
        SessionJoin(session_id="", player_id="p1", ip="127.0.0.1", port=5000)


def test_session_create_invalid_player_id():
    with pytest.raises(MessageValidationError, match="Invalid player_id"):
        SessionCreate(player_id="", ip="127.0.0.1", udp_port=50010)


def test_roster_update_duplicate_join_index():
    roster = GlobalRoster()
    roster.add_player(RosterEntry("p1", "127.0.0.1", 5000, 0))

    with pytest.raises(RosterValidationError, match="Duplicate join_index"):
        roster.add_player(RosterEntry("p2", "127.0.0.1", 5001, 0))


def test_roster_update_validates_on_message():
    """RosterUpdate message validates roster state."""
    roster = GlobalRoster()
    roster.add_player(RosterEntry("p1", "127.0.0.1", 5000, 0))
    roster.add_player(RosterEntry("p2", "127.0.0.1", 5001, 1))

    msg = RosterUpdate(roster=roster)
    assert len(msg.roster.players) == 2


def test_player_left_message():
    msg = PlayerLeft(player_id="player1")

    assert msg.message_type.value == "player_left"
    assert msg.player_id == "player1"

    data = json.loads(json.dumps(asdict(msg)))
    assert data["player_id"] == "player1"


def test_player_disconnected_message():
    msg = PlayerDisconnected(player_id="player2")

    assert msg.message_type.value == "player_disconnected"
    assert msg.player_id == "player2"


def test_block_destroyed_message():
    msg = BlockDestroyedMessage(position=(1, 2))

    assert msg.message_type.value == "block_destroyed_message"
    assert msg.position == (1, 2)


def test_powerup_collected_message():
    msg = PowerUpCollectedMessage(powerup_id="power1", player_id="player1")

    assert msg.message_type.value == "powerup_collected_message"
    assert msg.player_id == "player1"


def test_gate_state_changed_message():
    msg = GateStateChangedMessage(gate_id="gate1", new_state="open")

    assert msg.message_type.value == "gate_state_changed_message"
    assert msg.new_state == "open"


def test_level_reset_message():
    msg = LevelResetMessage()

    assert msg.message_type.value == "level_reset_message"


def test_host_discovery_probe_message():
    msg = HostDiscoveryProbe(session_id="abc", requester_ip="10.0.0.5")

    assert msg.message_type.value == "host_discovery_probe"
    assert msg.requester_ip == "10.0.0.5"

    data = json.loads(json.dumps(asdict(msg)))
    assert data["session_id"] == "abc"


def test_host_discovery_probe_invalid_session_id():
    with pytest.raises(MessageValidationError, match="Invalid session_id"):
        HostDiscoveryProbe(session_id="", requester_ip="10.0.0.5")


def test_host_discovery_probe_invalid_requester_ip():
    with pytest.raises(MessageValidationError, match="Invalid requester_ip"):
        HostDiscoveryProbe(session_id="abc", requester_ip="")


def test_host_identity_response_message():
    msg = HostIdentityResponse(session_id="abc", host_ip="10.0.0.1")

    assert msg.message_type.value == "host_identity_response"
    assert msg.host_ip == "10.0.0.1"

    data = json.loads(json.dumps(asdict(msg)))
    assert data["host_ip"] == "10.0.0.1"


def test_host_identity_response_invalid_host_ip():
    with pytest.raises(MessageValidationError, match="Invalid host_ip"):
        HostIdentityResponse(session_id="abc", host_ip="")


def test_host_identity_response_invalid_session_id():
    with pytest.raises(MessageValidationError, match="Invalid session_id"):
        HostIdentityResponse(session_id="", host_ip="10.0.0.1")
