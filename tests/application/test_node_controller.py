from distributed_smb.application.node_controller import NodeController
from distributed_smb.domain.entity import (
    CooperativeGate,
    DestructibleBlock,
    ExclusivePowerUp,
    Player,
)
from distributed_smb.domain.world import WorldState
from distributed_smb.network.serializer import Serializer
from distributed_smb.shared.config import PREDICTION_LEAD_DRIFT_TOLERANCE, TICK_INTERVAL
from distributed_smb.shared.enums import NodeState, PlayerRole
from distributed_smb.shared.input import InputState
from distributed_smb.shared.messages.sync import WorldStateSnapshot


def test_bootstrap_initializes_state_and_spawns_the_local_player():
    """Joining uses the same TMX spawn points as respawn-after-death."""
    controller = NodeController()

    bootstrapped = controller.bootstrap(role=PlayerRole.HOST)

    assert bootstrapped is controller
    assert controller.is_bootstrapped is True
    assert controller.role is PlayerRole.HOST
    assert controller.lifecycle.state is NodeState.IDLE
    assert controller.tick_interval == TICK_INTERVAL
    assert (
        controller.engine.world_state.get_player(controller.local_player_id) is not None
    )

    spawn_points = controller.engine.spawn_points
    for join_index in range(4):
        point = spawn_points[join_index % len(spawn_points)]
        assert controller._spawn_position_for(join_index) == (point.x, point.y)


class FakeUdpHandler:
    """Drops what is sent and hands over `packet` once, if given."""

    def __init__(self, packet: bytes | None = None):
        self.packet = packet

    def open_socket(self):
        return None

    def send_packet_nowait(self, payload, remote_host, remote_port):
        return None

    def receive_packet_nowait(self):
        if self.packet is None:
            return None
        packet, self.packet = self.packet, None
        return packet, ("127.0.0.1", 50010)


def test_client_frame_sends_one_input_and_enters_the_game():
    controller = NodeController().bootstrap(role=PlayerRole.CLIENT)
    controller.udp_handler = FakeUdpHandler()

    controller.process_frame(InputState(right=True))

    assert controller.input_sequence_number == 1
    assert controller.lifecycle.state is NodeState.IN_GAME
    assert controller.lifecycle.is_started is True


def test_client_snapshot_updates_characters_preserves_environment():
    """Characters come from the snapshot, the environment never does: it is
    owned by the WS events, which would otherwise arrive already stale."""
    controller = NodeController().bootstrap(role=PlayerRole.CLIENT)
    serializer = Serializer()

    controller.engine.world_state.environment.destructible_blocks[0].destroyed = False

    # A snapshot that tries to override the environment.
    world_state = WorldState(
        sequence_number=12,
        characters={"player1": Player(player_id="player1", x=180.0, y=96.0)},
    )
    world_state.add_block(DestructibleBlock(x=12, y=20, destroyed=True))
    world_state.add_power_up(
        ExclusivePowerUp(x=40, y=20, powerup_id="pu-a", collected=True, owner="player1")
    )
    world_state.add_gate(CooperativeGate(x=72, y=20, gate_id="gate-a", state="open"))
    payload = serializer.encode_message(
        WorldStateSnapshot(sequence_number=99, world_state=world_state)
    )

    controller.udp_handler = FakeUdpHandler(payload)
    controller._drain_snapshot_packets()

    assert controller.engine.world_state.sequence_number == 12
    assert controller.engine.world_state.characters["player1"].x == 180.0
    assert (
        controller.engine.world_state.environment.destructible_blocks[0].destroyed
        is False
    )
    assert "pu-a" not in controller.engine.world_state.environment.power_ups
    assert "gate-a" not in controller.engine.world_state.environment.cooperative_gates


def test_the_view_shows_the_prediction_and_never_mutates_the_authoritative_state():
    """The player sees their own predicted position, while the engine keeps
    the host's — and editing the view must not reach back into it."""
    controller = NodeController().bootstrap(role=PlayerRole.CLIENT)
    local_pid = controller.local_player_id
    # Pinned, so the expected direction of the correction survives spawn changes.
    controller.engine.world_state.characters[local_pid].x = 100.0
    controller.engine.world_state.characters[local_pid].y = 100.0
    controller.engine.world_state.add_block(
        DestructibleBlock(x=12, y=20, destroyed=False)
    )
    authoritative_world = WorldState(
        sequence_number=20,
        characters={
            "player1": Player(player_id="player1", x=100.0, y=100.0),
            local_pid: Player(player_id=local_pid, x=100.0, y=100.0),
        },
    )
    payload = Serializer().encode_message(
        WorldStateSnapshot(sequence_number=20, world_state=authoritative_world)
    )
    controller.udp_handler = FakeUdpHandler(payload)
    controller.time_provider = lambda: 10.0

    visual_world = controller.process_frame(InputState(right=True))

    assert (
        visual_world.characters[local_pid].x
        > authoritative_world.characters[local_pid].x
    )
    assert (
        controller.engine.world_state.characters[local_pid].x
        == authoritative_world.characters[local_pid].x
    )

    visual_state = controller._build_visual_world_state()
    assert visual_state.environment is not controller.engine.world_state.environment
    visual_state.environment.destructible_blocks[0].destroyed = True
    assert (
        controller.engine.world_state.environment.destructible_blocks[0].destroyed
        is False
    )


class _FakePredictionEngine:
    def __init__(self, pending: int) -> None:
        self._pending = pending

    def pending_count(self) -> int:
        return self._pending


def test_the_frozen_baseline_ignores_noise_and_never_follows_the_drift():
    """The tolerance band absorbs the per-snapshot noise; a lead beyond it is
    corrected without moving the target, or the target would chase the drift."""
    steady = NodeController()
    steady.prediction_lead_calibration_remaining = 0
    steady.prediction_lead_baseline = 3.0
    steady.prediction_engine = _FakePredictionEngine(pending=5)

    steady._adjust_prediction_lead()

    assert steady.prediction_lead_baseline == 3.0
    assert steady.pending_tick_adjustment == 0

    drifting = NodeController()
    drifting.prediction_lead_calibration_remaining = 0
    drifting.prediction_lead_baseline = 3.0
    drifting.prediction_engine = _FakePredictionEngine(pending=8)

    for _ in range(10):
        drifting._adjust_prediction_lead()

    assert drifting.prediction_lead_baseline == 3.0
    assert drifting.pending_tick_adjustment == -1


class _DriftingPredictionEngine:
    """A client whose tick loop runs slightly faster than the host's."""

    def __init__(self, pending: int, controller: NodeController) -> None:
        self._pending = pending
        self._controller = controller

    def pending_count(self) -> int:
        return self._pending

    def advance_frame(self, frame: int) -> None:
        if self._controller.pending_tick_adjustment == -1:
            self._pending -= 1  # the skipped tick is one input fewer in flight
        elif frame % 3 == 0:
            self._pending += 1  # clock drift against the host
        self._controller.pending_tick_adjustment = 0


def test_a_sustained_clock_drift_keeps_the_prediction_lead_bounded():
    """Real-LAN regression: lead and baseline used to climb together for the
    whole session, turning a 5px correction into a 150px one."""
    controller = NodeController()
    controller.prediction_lead_calibration_remaining = 0
    controller.prediction_lead_baseline = 3.0
    engine = _DriftingPredictionEngine(pending=3, controller=controller)
    controller.prediction_engine = engine

    for frame in range(1, 1201):
        controller._adjust_prediction_lead()
        engine.advance_frame(frame)

    assert engine.pending_count() <= 3.0 + PREDICTION_LEAD_DRIFT_TOLERANCE + 1
    assert controller.prediction_lead_baseline == 3.0


def test_adjust_prediction_lead_drains_backlog_after_reconnection_reset():
    """A backlog must not become the new baseline, or it never drains."""
    controller = NodeController()
    controller.prediction_lead_baseline = 0.0
    controller.prediction_engine = _FakePredictionEngine(pending=60)

    controller._adjust_prediction_lead()

    assert controller.pending_tick_adjustment == -1
