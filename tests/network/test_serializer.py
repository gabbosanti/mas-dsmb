import json
import typing

import pytest

from distributed_smb.domain.entity import (
    CooperativeGate,
    DestructibleBlock,
    Enemy,
    ExclusivePowerUp,
    Player,
)
from distributed_smb.domain.world import WorldState
from distributed_smb.network.serializer import Serializer, WsMessage
from distributed_smb.shared.enums import ConnectionStatus
from distributed_smb.shared.input import InputState
from distributed_smb.shared.messages.election import (
    ElectionAck,
    NewHostClaim,
    ReconnectionAck,
)
from distributed_smb.shared.messages.gameplay import (
    BlockDestroyedMessage,
    GateStateChangedMessage,
    LevelResetMessage,
    PlayerDeathMessage,
    PlayerDisconnected,
    PlayerInputPacket,
    PlayerLeft,
    PowerUpCollectedMessage,
)
from distributed_smb.shared.messages.recovery import (
    HostDiscoveryProbe,
    HostIdentityResponse,
)
from distributed_smb.shared.messages.session import (
    GameStart,
    RosterUpdate,
    SessionClosed,
    SessionCreate,
    SessionCreated,
    SessionJoin,
    SessionJoined,
    SessionJoinRejected,
    SessionLeave,
    SessionRecreate,
)
from distributed_smb.shared.messages.sync import InitialStateSync, WorldStateSnapshot
from distributed_smb.shared.roster import GlobalRoster, RosterEntry


def _roster() -> GlobalRoster:
    roster = GlobalRoster()
    roster.add_player(
        RosterEntry("player1", "10.0.0.1", 50010, 0, ConnectionStatus.CONNECTED, True)
    )
    roster.add_player(
        RosterEntry("player2", "10.0.0.2", 50011, 1, ConnectionStatus.CONNECTED)
    )
    return roster


WS_MESSAGES = [
    SessionCreate(player_id="player1", ip="10.0.0.1", udp_port=50010),
    SessionCreated(session_id="abc123", join_index=1),
    SessionJoin(session_id="abc123", player_id="player2", ip="10.0.0.2", port=50011),
    SessionJoin(
        session_id="abc123",
        player_id="player1",
        ip="10.0.0.1",
        port=50010,
        rejoin_join_index=0,
    ),
    SessionJoined(join_index=2),
    SessionJoinRejected(
        session_id="abc123", reason="The session already has 4 players"
    ),
    SessionRecreate(
        session_id="abc123",
        next_join_index=3,
        host_ip="10.0.0.2",
        host_udp_port=50010,
        host_join_index=1,
        reserved_join_indices=[0, 2],
    ),
    SessionClosed(session_id="abc123"),
    SessionLeave(session_id="abc123", join_index=2),
    RosterUpdate(roster=_roster()),
    GameStart(session_id="abc123"),
    BlockDestroyedMessage(position=(96, 320)),
    PowerUpCollectedMessage(powerup_id="coin-1", player_id="player1"),
    GateStateChangedMessage(gate_id="gate-1", new_state="open"),
    PlayerLeft(player_id="player2"),
    PlayerDeathMessage(player_id="player1", enemy_id="goomba-1"),
    PlayerDisconnected(player_id="player3"),
    LevelResetMessage(),
    NewHostClaim(claimer_ip="10.0.0.2", claimer_join_index=1, session_id="abc123"),
    ElectionAck(from_ip="10.0.0.3", session_id="abc123"),
    ReconnectionAck(
        new_host_ip="10.0.0.2",
        udp_port=50010,
        game_events_port=50003,
        session_id="abc123",
    ),
]

UDP_MESSAGES = [
    PlayerInputPacket(
        player_id="player2",
        sequence_number=7,
        input_state=InputState(left=True, jump=True),
    ),
    HostDiscoveryProbe(session_id="abc123", requester_ip="10.0.0.5"),
    HostIdentityResponse(session_id="abc123", host_ip="10.0.0.1"),
    NewHostClaim(claimer_ip="10.0.0.2", claimer_join_index=1, session_id="abc123"),
    ElectionAck(from_ip="10.0.0.3", session_id="abc123"),
    ReconnectionAck(
        new_host_ip="10.0.0.2",
        udp_port=50010,
        game_events_port=50003,
        session_id="abc123",
    ),
]


def test_every_ws_message_survives_the_wire():
    """Encode, cross the wire as JSON, decode: the message must come back equal."""
    serializer = Serializer()
    for message in WS_MESSAGES:
        wire = json.loads(json.dumps(serializer.encode_ws_message(message)))
        assert serializer.decode_ws_message(wire) == message, type(message).__name__


def test_every_udp_message_survives_the_wire():
    serializer = Serializer()
    for message in UDP_MESSAGES:
        decoded = serializer.decode_message(serializer.encode_message(message))
        assert decoded == message, type(message).__name__


def test_every_ws_message_type_has_a_round_trip_case():
    """A new message type added to WsMessage must also be added above."""
    covered = {type(m) for m in WS_MESSAGES} | {InitialStateSync}
    assert covered == set(typing.get_args(WsMessage))


def test_the_world_state_survives_both_transports():
    """The snapshot travels over UDP and the initial sync over WebSocket, and
    both must rebuild characters, environment and enemies."""
    serializer = Serializer()
    world_state = WorldState(
        sequence_number=3,
        characters={
            "player1": Player(player_id="player1", x=10.0, y=20.0),
            "player2": Player(player_id="player2", x=30.0, y=40.0),
        },
    )
    world_state.add_block(DestructibleBlock(x=120, y=64, destroyed=True))
    world_state.add_power_up(
        ExclusivePowerUp(
            x=150, y=80, powerup_id="star-1", collected=True, owner="player1"
        )
    )
    gate = CooperativeGate(x=200, y=90, gate_id="gate-1", state="open")
    gate.contributions.update({"player1", "player2"})
    world_state.add_gate(gate)
    world_state.environment.enemies["goomba-1"] = Enemy(
        enemy_id="goomba-1", x=500.0, y=64.0
    )

    over_udp = serializer.decode_message(
        serializer.encode_message(
            WorldStateSnapshot(sequence_number=9, world_state=world_state)
        )
    )

    assert over_udp.sequence_number == 9
    decoded = over_udp.world_state
    assert decoded.sequence_number == 3
    assert set(decoded.characters) == {"player1", "player2"}
    assert decoded.characters["player2"].x == 30.0
    assert decoded.environment.destructible_blocks[0].destroyed is True
    assert decoded.environment.power_ups["star-1"].owner == "player1"
    assert decoded.environment.cooperative_gates["gate-1"].state == "open"
    assert decoded.environment.cooperative_gates["gate-1"].contributions == {
        "player1",
        "player2",
    }
    assert decoded.environment.enemies["goomba-1"].x == 500.0

    over_ws = serializer.decode_ws_message(
        serializer.encode_ws_message(InitialStateSync(world_state=world_state))
    )

    assert isinstance(over_ws, InitialStateSync)
    assert over_ws.world_state.characters["player1"].x == 10.0
    assert over_ws.world_state.environment.destructible_blocks[0].destroyed is True
    assert over_ws.world_state.environment.cooperative_gates["gate-1"].state == "open"


def test_an_unknown_message_type_is_refused_on_both_transports():
    """Silently ignoring it would hide a version mismatch between peers."""
    serializer = Serializer()

    with pytest.raises(ValueError, match="Unsupported UDP message type"):
        serializer.decode_message('{"message_type": "unsupported_udp_type"}')

    with pytest.raises(ValueError, match="Unsupported WebSocket message type"):
        serializer.decode_ws_message({"message_type": "unknown_type"})
