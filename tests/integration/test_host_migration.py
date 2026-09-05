"""Integration tests for fault tolerance and host migration."""

import json
import time

from distributed_smb.application.election import (
    ElectionCoordinator,
    ElectionState,
    EnvironmentalStateBuffer,
    FollowingHost,
    HostTimeoutWatcher,
    SelfElected,
)
from distributed_smb.application.node_controller import NodeController
from distributed_smb.domain.world import WorldState
from distributed_smb.shared.config import (
    HOST_TIMEOUT_S,
    T_ELECTION_BASE_S,
    T_ELECTION_DELTA_S,
)
from distributed_smb.shared.enums import MessageType, PlayerRole
from distributed_smb.shared.messages.election import ElectionAck, ReconnectionAck
from distributed_smb.shared.messages.session import SessionCreated, SessionRecreate
from distributed_smb.shared.messages.sync import WorldStateSnapshot
from distributed_smb.shared.roster import GlobalRoster, RosterEntry

# ---------------------------------------------------------------------------
# Unit-level integration: timeout watcher + election coordinator together
# ---------------------------------------------------------------------------


class TestTimeoutThenElection:
    """Verify the timeout → election handoff without any network."""

    def test_timeout_triggers_start_election(self):
        """Once timeout fires, start_election() should move coordinator to ELECTION_PENDING."""
        watcher = HostTimeoutWatcher(timeout_s=1.0)
        coordinator = ElectionCoordinator(
            join_index=0,
            my_ip="10.0.0.2",
            timeout_base_s=T_ELECTION_BASE_S,
            timeout_delta_s=T_ELECTION_DELTA_S,
        )
        t0 = time.time()
        watcher.reset(t0)

        assert not watcher.tick(t0 + 0.5)
        assert coordinator.state == ElectionState.IDLE

        assert watcher.tick(t0 + 1.001)
        coordinator.start_election({"10.0.0.3"})
        coordinator.set_election_timer(t0 + 1.001)
        assert coordinator.state == ElectionState.ELECTION_PENDING

    def test_coordinator_self_elects_after_timer(self):
        """After timeout and election start, the lowest-index node self-elects."""
        coordinator = ElectionCoordinator(
            join_index=0,
            my_ip="10.0.0.2",
            timeout_base_s=0.5,
            timeout_delta_s=0.3,
        )
        t0 = time.time()
        coordinator.start_election(set())
        coordinator.set_election_timer(t0)

        event = coordinator.tick(t0 + 0.5)
        assert isinstance(event, SelfElected)
        assert event.my_ip == "10.0.0.2"

    def test_higher_index_yields_to_lower_claim(self):
        """Node with join_index=1 stops its election on receiving claim from join_index=0."""
        coordinator = ElectionCoordinator(
            join_index=1,
            my_ip="10.0.0.3",
            timeout_base_s=0.5,
            timeout_delta_s=0.3,
        )
        t0 = time.time()
        coordinator.start_election({"10.0.0.2"})
        coordinator.set_election_timer(t0)

        event = coordinator.on_new_host_claim(claimer_join_index=0, claimer_ip="10.0.0.2")
        assert isinstance(event, FollowingHost)
        assert coordinator.state == ElectionState.FOLLOWER

        later_event = coordinator.tick(t0 + 1.0)
        assert later_event is None
        assert coordinator.state == ElectionState.FOLLOWER


class TestEnvironmentalStatePreservation:
    """Verify that the last snapshot is preserved through the buffer."""

    def _make_snapshot(self, seq: int, blocks_destroyed: bool = False):
        from unittest.mock import MagicMock

        ws = MagicMock()
        ws.sequence_number = seq
        ws.environment.destructible_blocks = [{"pos": (100, 100), "destroyed": blocks_destroyed}]
        snap = WorldStateSnapshot.__new__(WorldStateSnapshot)
        object.__setattr__(snap, "sequence_number", seq)
        object.__setattr__(snap, "world_state", ws)
        return snap

    def test_buffer_preserves_last_snapshot_before_crash(self):
        buf = EnvironmentalStateBuffer()
        for seq in range(1, 5):
            buf.update(self._make_snapshot(seq))
        last = buf.get_last()
        assert last.sequence_number == 4

    def test_buffer_with_destroyed_block(self):
        buf = EnvironmentalStateBuffer()
        buf.update(self._make_snapshot(1, blocks_destroyed=False))
        buf.update(self._make_snapshot(2, blocks_destroyed=True))
        last = buf.get_last()
        assert last.world_state.environment.destructible_blocks[0]["destroyed"] is True


class TestReconnectionAckValidation:
    """ReconnectionAck carries the data the client needs to resume."""

    def test_valid_reconnection_ack(self):
        ack = ReconnectionAck(
            new_host_ip="10.0.0.2",
            udp_port=50010,
            game_events_port=50003,
            session_id="test-session",
        )
        assert ack.new_host_ip == "10.0.0.2"
        assert ack.udp_port == 50010
        assert ack.game_events_port == 50003

    def test_reconnection_ack_serialization(self):
        from distributed_smb.network.serializer import Serializer

        serializer = Serializer()
        ack = ReconnectionAck(
            new_host_ip="10.0.0.2",
            udp_port=50010,
            game_events_port=50003,
            session_id="test-session",
        )
        encoded = serializer.encode_ws_message(ack)
        decoded = serializer.decode_ws_message(encoded)
        assert isinstance(decoded, ReconnectionAck)
        assert decoded.new_host_ip == "10.0.0.2"
        assert decoded.udp_port == 50010
        assert decoded.game_events_port == 50003
        assert decoded.session_id == "test-session"


# ---------------------------------------------------------------------------
# End-to-end process-level tests (Persona 2 migration + election logic)
# ---------------------------------------------------------------------------


class SpyBroker:
    def __init__(self):
        self.sent: list[dict] = []
        self.promoted_port: int | None = None
        self.reconnected_to: tuple[str, int] | None = None

    def send(self, payload: bytes) -> None:
        self.sent.append(json.loads(payload.decode()))

    def get_disconnected_player(self) -> str | None:
        return None

    def launch(self, host: str = "0.0.0.0", port: int = 0) -> None:
        pass

    def reconnect(self, host: str, port: int) -> None:
        self.reconnected_to = (host, port)

    def promote_to_server(self, port: int) -> None:
        self.promoted_port = port


class SpyWsHandler:
    def __init__(self):
        self.sent: list[object] = []
        self._queue: list[object] = []

    def connect(self, timeout: float = 10.0) -> None:
        pass

    def send(self, message) -> None:
        self.sent.append(message)
        if isinstance(message, SessionRecreate):
            self._queue.append(SessionCreated(session_id=message.session_id, join_index=0))

    def poll(self):
        return self._queue.pop(0) if self._queue else None

    def close(self) -> None:
        pass


def _make_controller(
    *,
    local_ip: str,
    local_player_id: str,
    join_index: int,
    peers: list[tuple[str, str, int, int]],
    old_host_ip: str | None = None,
) -> tuple[NodeController, SpyBroker]:
    broker = SpyBroker()
    controller = NodeController(game_event_broker=broker).bootstrap(role=PlayerRole.CLIENT)
    controller.local_ip = local_ip
    controller.local_player_id = local_player_id
    controller.join_index = join_index
    controller.session_id = "migration-session"
    controller.roster = GlobalRoster()
    if old_host_ip is not None:
        controller.roster.add_player(
            RosterEntry(
                player_id="player1",
                host=old_host_ip,
                udp_port=50010,
                join_index=0,
                is_host=True,
            )
        )
    for remote_player_id, remote_ip, remote_udp_port, remote_join_index in peers:
        controller.roster.add_player(
            RosterEntry(
                player_id=remote_player_id,
                host=remote_ip,
                udp_port=remote_udp_port,
                join_index=remote_join_index,
                is_host=False,
            )
        )
    controller.roster.add_player(
        RosterEntry(
            player_id=local_player_id,
            host=local_ip,
            udp_port=50011 + join_index,
            join_index=join_index,
            is_host=False,
        )
    )
    controller.election_coordinator = ElectionCoordinator(
        join_index=join_index,
        my_ip=local_ip,
        timeout_base_s=T_ELECTION_BASE_S,
        timeout_delta_s=T_ELECTION_DELTA_S,
    )
    controller.timeout_watcher = HostTimeoutWatcher(timeout_s=HOST_TIMEOUT_S)
    controller.env_state_buffer = EnvironmentalStateBuffer()
    controller.ws_handler = SpyWsHandler()
    controller._make_lobby_ws_client = lambda host, port: setattr(
        controller, "ws_handler", SpyWsHandler()
    )
    controller._reconnect_game_event_handler = lambda *args, **kwargs: None
    return controller, broker


class TestHostMigration3Players:
    """Host crash followed by a surviving client election and state bootstrap."""

    MIGRATION_TIMEOUT_S = HOST_TIMEOUT_S + T_ELECTION_BASE_S + 1.0

    def test_crash_and_resume(self):
        """A surviving client becomes host and broadcasts a ReconnectionAck."""
        candidate, broker = _make_controller(
            local_ip="10.0.0.2",
            local_player_id="player2",
            join_index=1,
            peers=[("player3", "10.0.0.3", 50012, 2)],
            old_host_ip="10.0.0.1",
        )
        candidate.timeout_watcher.reset(time.time() - HOST_TIMEOUT_S - 0.1)
        candidate._tick_election_state()
        assert candidate.election_triggered is True

        candidate.election_coordinator.start_election({"10.0.0.3"})
        candidate.election_coordinator.set_election_timer(time.time())
        event = candidate.election_coordinator.tick(
            time.time() + T_ELECTION_BASE_S + T_ELECTION_DELTA_S + 0.1
        )
        assert isinstance(event, SelfElected)

        candidate._on_self_elected(event)
        assert candidate._pending_election_acks == {"10.0.0.3"}
        candidate._on_election_ack(ElectionAck(from_ip="10.0.0.3", session_id="migration-session"))

        assert candidate._promotion_done is True
        assert candidate.role is PlayerRole.HOST
        assert candidate.engine.is_authoritative is True
        assert any(msg["message_type"] == MessageType.RECONNECTION_ACK.value for msg in broker.sent)

    def test_environmental_state_preserved(self):
        """Destroyed blocks in the last snapshot survive host promotion."""
        candidate, _ = _make_controller(
            local_ip="10.0.0.2",
            local_player_id="player2",
            join_index=1,
            peers=[("player3", "10.0.0.3", 50012, 2)],
            old_host_ip="10.0.0.1",
        )
        world = WorldState()
        world.environment.destructible_blocks.append(
            type("Block", (), {"x": 100, "y": 200, "destroyed": False})()
        )
        world.environment.destructible_blocks[0].destroyed = True
        snapshot = WorldStateSnapshot(sequence_number=7, world_state=world)
        candidate.env_state_buffer.update(snapshot)

        candidate._promote_to_host()

        assert candidate.engine.world_state is world
        assert candidate.engine.world_state.environment.destructible_blocks[0].destroyed is True
        assert candidate.last_snapshot_sequence == 7


class TestHostMigration4Players:
    """Multiple surviving peers all reconnect to the promoted host."""

    def test_all_clients_reconnect(self):
        """After a host crash, each remaining client receives a ReconnectionAck."""
        promoted, broker = _make_controller(
            local_ip="10.0.0.2",
            local_player_id="player2",
            join_index=1,
            peers=[
                ("player3", "10.0.0.3", 50013, 2),
                ("player4", "10.0.0.4", 50014, 3),
            ],
            old_host_ip="10.0.0.1",
        )

        promoted._promote_to_host()

        assert promoted.role is PlayerRole.HOST
        assert any(msg["message_type"] == MessageType.RECONNECTION_ACK.value for msg in broker.sent)

        for peer_ip in ("10.0.0.3", "10.0.0.4"):
            peer = NodeController(game_event_broker=SpyBroker()).bootstrap(role=PlayerRole.CLIENT)
            peer.local_ip = peer_ip
            peer.reconnected = False
            peer.remote_host = ""
            peer.remote_port = 0
            peer._on_reconnection_ack(
                ReconnectionAck(
                    new_host_ip="10.0.0.2",
                    udp_port=50010,
                    game_events_port=50003,
                    session_id="migration-session",
                )
            )
            assert peer.reconnected is True
            assert peer.remote_host == "10.0.0.2"


class TestCascadingFallback:
    """A second candidate promotes after the first candidate crashes during election."""

    def test_primary_candidate_crash_elects_secondary(self):
        """JoinIndex=1 becomes host when join_index=0 candidate disappears mid-election."""
        primary, _ = _make_controller(
            local_ip="10.0.0.1",
            local_player_id="player1",
            join_index=0,
            peers=[("player2", "10.0.0.2", 50011, 1)],
        )
        secondary, _ = _make_controller(
            local_ip="10.0.0.2",
            local_player_id="player2",
            join_index=1,
            peers=[("player1", "10.0.0.1", 50010, 0)],
        )

        # Simulate the lower-index candidate crashing before it can finish its timer.
        secondary.timeout_watcher.reset(time.time() - HOST_TIMEOUT_S - 0.1)
        secondary._tick_election_state()
        assert secondary.election_triggered is True

        secondary.election_coordinator.start_election({"10.0.0.1"})
        secondary.election_coordinator.set_election_timer(time.time())
        cascade_event = secondary.election_coordinator.tick(
            time.time() + T_ELECTION_BASE_S + T_ELECTION_DELTA_S + 0.1
        )

        assert isinstance(cascade_event, SelfElected)
        assert cascade_event.my_ip == "10.0.0.2"
