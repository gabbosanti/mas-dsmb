"""Host migration end to end: the steps of test_election_mixin.py, in sequence."""

import json
import time

import pytest

from distributed_smb.application.election import (
    ElectionCoordinator,
    EnvironmentalStateBuffer,
    HostTimeoutWatcher,
    SelfElected,
)
from distributed_smb.application.node_controller import NodeController
from distributed_smb.domain.entity import DestructibleBlock
from distributed_smb.domain.world import WorldState
from distributed_smb.network.serializer import Serializer
from distributed_smb.shared.config import (
    HOST_TIMEOUT_S,
    T_ELECTION_BASE_S,
    T_ELECTION_DELTA_S,
)
from distributed_smb.shared.enums import MessageType, PlayerRole
from distributed_smb.shared.messages.election import ElectionAck
from distributed_smb.shared.messages.session import SessionCreated, SessionRecreate
from distributed_smb.shared.messages.sync import WorldStateSnapshot
from distributed_smb.shared.roster import GlobalRoster, RosterEntry

pytestmark = pytest.mark.usefixtures("no_real_lobby_relaunch")

SESSION = "migration-session"


class SpyBroker:
    """The WS relay: records what the promoted node broadcast to the others."""

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, payload: bytes) -> None:
        self.sent.append(json.loads(payload.decode()))

    def get_disconnected_player(self) -> str | None:
        return None

    def launch(self, host: str = "0.0.0.0", port: int = 0) -> None:
        pass

    def reconnect(self, host: str, port: int) -> None:
        pass

    def promote_to_server(self, port: int) -> None:
        pass


class SpyWsHandler:
    def __init__(self):
        self._queue: list = []

    def connect(self, timeout: float = 10.0) -> None:
        pass

    def send(self, message) -> None:
        if isinstance(message, SessionRecreate):
            self._queue.append(
                SessionCreated(session_id=message.session_id, join_index=0)
            )

    def poll(self):
        return self._queue.pop(0) if self._queue else None

    def close(self) -> None:
        pass


def _client(
    local_ip: str, player_id: str, join_index: int, peers: list[tuple]
) -> NodeController:
    """A client mid-session: crashed host plus `peers` in its roster."""
    node = NodeController(game_event_broker=SpyBroker()).bootstrap(
        role=PlayerRole.CLIENT
    )
    node.local_ip = local_ip
    node.local_player_id = player_id
    node.join_index = join_index
    node.session_id = SESSION
    node.roster = GlobalRoster()
    node.roster.add_player(
        RosterEntry(
            player_id="player1",
            host="10.0.0.1",
            udp_port=50010,
            join_index=0,
            is_host=True,
        )
    )
    for peer_id, peer_ip, peer_port, peer_index in [
        *peers,
        (player_id, local_ip, 50011, join_index),
    ]:
        node.roster.add_player(
            RosterEntry(
                player_id=peer_id,
                host=peer_ip,
                udp_port=peer_port,
                join_index=peer_index,
            )
        )
    node.election_coordinator = ElectionCoordinator(
        join_index=join_index,
        my_ip=local_ip,
        timeout_base_s=T_ELECTION_BASE_S,
        timeout_delta_s=T_ELECTION_DELTA_S,
    )
    node.timeout_watcher = HostTimeoutWatcher(timeout_s=HOST_TIMEOUT_S)
    node.env_state_buffer = EnvironmentalStateBuffer()
    node.ws_handler = SpyWsHandler()
    node._make_lobby_ws_client = lambda host, port: setattr(
        node, "ws_handler", SpyWsHandler()
    )
    node._reconnect_game_event_handler = lambda *a, **kw: None
    node._reconnect_lobby_ws_handler = lambda *a, **kw: None
    return node


def test_a_crashed_host_is_verified_then_replaced_and_its_world_survives():
    candidate = _client(
        "10.0.0.2", "player2", 1, peers=[("player3", "10.0.0.3", 50012, 2)]
    )
    # The last snapshot seen before the crash, with a block already destroyed.
    world = WorldState()
    world.environment.destructible_blocks.append(
        DestructibleBlock(x=100, y=200, destroyed=True)
    )
    candidate.env_state_buffer.update(
        WorldStateSnapshot(sequence_number=7, world_state=world)
    )

    candidate.timeout_watcher.reset(time.time() - HOST_TIMEOUT_S - 0.1)
    candidate._tick_election_state()
    assert candidate.election_triggered is False  # the host is probed first

    candidate._host_verify_deadline = time.time() - 0.1
    candidate._tick_election_state()
    assert candidate.election_triggered is True

    event = candidate.election_coordinator.tick(
        time.time() + T_ELECTION_BASE_S + T_ELECTION_DELTA_S + 0.1
    )
    assert isinstance(event, SelfElected)
    candidate._on_self_elected(event)
    assert candidate._pending_election_acks == {"10.0.0.3"}

    try:
        candidate._on_election_ack(ElectionAck(from_ip="10.0.0.3", session_id=SESSION))

        assert candidate.role is PlayerRole.HOST
        assert candidate.engine.is_authoritative is True
        assert candidate.engine.world_state is world
        assert candidate.engine.world_state.environment.destructible_blocks[0].destroyed is True
        assert candidate.last_snapshot_sequence == 7
    finally:
        candidate.udp_handler.close_socket()


def test_every_survivor_follows_the_ack_the_new_host_broadcast():
    promoted = _client(
        "10.0.0.2",
        "player2",
        1,
        peers=[("player3", "10.0.0.3", 50013, 2), ("player4", "10.0.0.4", 50014, 3)],
    )

    try:
        promoted._promote_to_host()

        acks = [
            msg
            for msg in promoted.game_event_broker.sent
            if msg["message_type"] == MessageType.RECONNECTION_ACK.value
        ]
        assert len(acks) == 1
        ack = Serializer().decode_ws_message(acks[0])
    finally:
        promoted.udp_handler.close_socket()

    for peer_ip, peer_id, peer_index in (
        ("10.0.0.3", "player3", 2),
        ("10.0.0.4", "player4", 3),
    ):
        survivor = _client(peer_ip, peer_id, peer_index, peers=[])
        survivor._on_reconnection_ack(ack)

        assert survivor.reconnected is True
        assert survivor.remote_host == "10.0.0.2"
