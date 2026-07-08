"""Integration tests for ShadowCopy wiring in NodeController.

Covers Persona 3 task: integrate ShadowCopy (init after lobby, update on
snapshot, pass display state to Renderer) using noop stubs so Persona 2
can drop in the real interpolation implementation without conflicts.
"""

import time
from unittest.mock import patch

from distributed_smb.application.node_controller import NodeController
from distributed_smb.application.reconciliation import (
    NoopShadowCopy,
    ShadowCopyProtocol,
)
from distributed_smb.domain.world import CharacterState, WorldState
from distributed_smb.shared.enums import PlayerRole
from distributed_smb.shared.input import InputState
from distributed_smb.shared.messages.sync import WorldStateSnapshot

# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_noop_shadow_copy_satisfies_protocol():
    assert isinstance(NoopShadowCopy(), ShadowCopyProtocol)


def test_noop_shadow_copy_returns_none_before_first_update():
    sc = NoopShadowCopy()
    assert sc.get_display_state() is None


def test_noop_shadow_copy_returns_last_state_after_update():
    sc = NoopShadowCopy()
    state = CharacterState(player_id="p1", x=5, y=10)
    sc.update(state)
    assert sc.get_display_state() is state


def test_noop_shadow_copy_overwrites_on_subsequent_updates():
    sc = NoopShadowCopy()
    s1 = CharacterState(player_id="p1", x=0, y=0)
    s2 = CharacterState(player_id="p1", x=99, y=42)
    sc.update(s1)
    sc.update(s2)
    assert sc.get_display_state() is s2


# ---------------------------------------------------------------------------
# Initialisation after lobby
# ---------------------------------------------------------------------------


def _make_client_controller() -> NodeController:
    ctrl = NodeController()
    ctrl.bootstrap(role=PlayerRole.CLIENT)
    # Simulate post-lobby world: host (player1) and local client (player2) are both present.
    ctrl.remote_player_id = "player1"
    ctrl.engine.spawn_player("player1", x=100, y=100)
    return ctrl


def test_shadow_copies_empty_before_lobby():
    ctrl = NodeController().bootstrap(role=PlayerRole.CLIENT)
    assert ctrl.shadow_copies == {}


def test_shadow_copies_initialized_for_client_after_lobby():
    ctrl = _make_client_controller()
    ctrl._init_shadow_copies()
    # CLIENT: remote player should have a shadow copy; local player should not
    assert ctrl.remote_player_id in ctrl.shadow_copies
    assert ctrl.local_player_id not in ctrl.shadow_copies


def test_shadow_copy_not_initialized_for_host():
    ctrl = NodeController()
    ctrl.bootstrap(role=PlayerRole.HOST)
    ctrl._init_shadow_copies()
    assert ctrl.shadow_copies == {}


def test_shadow_copy_factory_is_used_for_instantiation():
    """Custom factory class is used, not the NoopShadowCopy default."""
    created = []

    class SpyShadowCopy:
        def update(self, state):
            pass

        def get_display_state(self):
            return None

    def factory():
        instance = SpyShadowCopy()
        created.append(instance)
        return instance

    ctrl = NodeController(shadow_copy_factory=factory)
    ctrl.bootstrap(role=PlayerRole.CLIENT)
    ctrl.engine.spawn_player("player1", x=100, y=100)  # simulate post-lobby remote player
    ctrl._init_shadow_copies()
    assert len(created) == 1  # one per remote player


# ---------------------------------------------------------------------------
# Shadow copy updated on snapshot arrival
# ---------------------------------------------------------------------------


def _make_snapshot(seq: int, characters: dict) -> WorldStateSnapshot:
    return WorldStateSnapshot(
        sequence_number=seq,
        world_state=WorldState(sequence_number=seq, characters=characters),
    )


def test_shadow_copy_updated_on_snapshot_arrival():
    ctrl = _make_client_controller()
    ctrl._init_shadow_copies()

    host_state = CharacterState(player_id=ctrl.remote_player_id, x=200, y=100)
    snapshot = _make_snapshot(1, {ctrl.remote_player_id: host_state})

    ctrl._update_shadow_copies(snapshot)
    shadow_state = ctrl.shadow_copies[ctrl.remote_player_id].get_display_state()
    assert shadow_state is not None
    assert shadow_state.x == host_state.x
    assert shadow_state.y == host_state.y


def test_shadow_copy_not_updated_for_unknown_player():
    """Snapshot with no entry for remote player leaves shadow copy state unchanged."""
    ctrl = _make_client_controller()
    ctrl._init_shadow_copies()

    snapshot = _make_snapshot(1, {})  # no character data at all
    ctrl._update_shadow_copies(snapshot)
    assert ctrl.shadow_copies[ctrl.remote_player_id].get_display_state() is None


# ---------------------------------------------------------------------------
# Display world state
# ---------------------------------------------------------------------------


def test_display_world_state_uses_shadow_copy_state():
    ctrl = _make_client_controller()
    ctrl._init_shadow_copies()

    interpolated = CharacterState(player_id=ctrl.remote_player_id, x=150, y=100)
    ctrl.shadow_copies[ctrl.remote_player_id].update(interpolated)

    display = ctrl._build_visual_world_state()
    assert display.characters[ctrl.remote_player_id].x == interpolated.x
    assert display.characters[ctrl.remote_player_id].y == interpolated.y


def test_display_world_state_does_not_mutate_engine():
    ctrl = _make_client_controller()
    ctrl._init_shadow_copies()

    original_chars = dict(ctrl.engine.world_state.characters)
    interpolated = CharacterState(player_id=ctrl.remote_player_id, x=999, y=999)
    ctrl.shadow_copies[ctrl.remote_player_id].update(interpolated)

    ctrl._build_visual_world_state()

    # engine.world_state.characters must be unchanged
    assert ctrl.engine.world_state.characters == original_chars


def test_display_world_state_preserves_local_player_from_engine():
    ctrl = _make_client_controller()
    ctrl._init_shadow_copies()

    local_state = ctrl.engine.world_state.characters[ctrl.local_player_id]
    display = ctrl._build_visual_world_state()
    assert display.characters[ctrl.local_player_id] is local_state


def test_build_visual_world_state_includes_local_player_when_removed_by_reconcile():
    """local_visual_state is shown even if reconcile removed the local player from the engine."""
    ctrl = _make_client_controller()
    ctrl._init_shadow_copies()

    local_state = ctrl.engine.world_state.characters[ctrl.local_player_id]
    # Simulate reconcile removing local player (host snapshot didn't include them).
    del ctrl.engine.world_state.characters[ctrl.local_player_id]

    display = ctrl._build_visual_world_state(local_visual_state=local_state)
    assert ctrl.local_player_id in display.characters
    assert display.characters[ctrl.local_player_id] is local_state


def test_build_visual_world_state_excludes_local_player_during_respawn():
    """A dead player must disappear immediately instead of being kept by visual smoothing."""
    ctrl = _make_client_controller()
    ctrl._init_shadow_copies()

    local_state = ctrl.engine.world_state.characters[ctrl.local_player_id]
    del ctrl.engine.world_state.characters[ctrl.local_player_id]
    ctrl.engine.world_state.respawn_timers[ctrl.local_player_id] = time.time() + 10.0

    display = ctrl._build_visual_world_state(local_visual_state=local_state)
    assert ctrl.local_player_id not in display.characters
    assert ctrl.local_player_id in display.respawn_timers

def test_display_world_state_no_shadow_copies_returns_engine_state():
    """HOST path or pre-lobby CLIENT: visual state matches engine.world_state data."""
    ctrl = NodeController()
    ctrl.bootstrap(role=PlayerRole.HOST)
    # shadow_copies is empty for HOST
    display = ctrl._build_visual_world_state()
    assert display.sequence_number == ctrl.engine.world_state.sequence_number
    assert display.characters == ctrl.engine.world_state.characters


def test_no_crash_when_no_snapshot_arrives():
    """Shadow copy returns None before first snapshot — display state falls back to engine."""
    ctrl = _make_client_controller()
    ctrl._init_shadow_copies()

    # No update called on shadow copy yet → get_display_state() returns None
    display = ctrl._build_visual_world_state()
    # Remote player should still be present (from engine), not from shadow copy
    assert ctrl.remote_player_id in display.characters


# ---------------------------------------------------------------------------
# Game loop: 100ms artificial latency — no freeze
# ---------------------------------------------------------------------------


def test_game_loop_no_freeze_with_100ms_latency():
    """process_frame() completes in well under 100ms even with simulated latency."""
    from distributed_smb.network.udp_handler import UdpHandler

    ctrl = NodeController(
        udp_handler=UdpHandler(host="0.0.0.0", port=19876, artificial_latency_ms=100)
    )
    ctrl.bootstrap(role=PlayerRole.CLIENT)
    ctrl.udp_handler.open_socket()

    local_input = InputState()
    start = time.monotonic()
    ctrl.process_frame(dt=0.016, local_input=local_input)
    elapsed_ms = (time.monotonic() - start) * 1000

    ctrl.udp_handler.close_socket()
    assert elapsed_ms < 50, f"Frame took {elapsed_ms:.1f}ms — game loop would freeze"


# ---------------------------------------------------------------------------
# Integration: drain_snapshot_packets wires reconcile + shadow copy together
# ---------------------------------------------------------------------------


def test_drain_snapshot_updates_both_engine_and_shadow():
    """On snapshot receipt: engine world_state updated (via reconcile) AND shadow copy updated."""
    ctrl = _make_client_controller()
    ctrl._init_shadow_copies()

    host_state = CharacterState(player_id=ctrl.remote_player_id, x=50, y=100)
    snapshot = _make_snapshot(seq=1, characters={ctrl.remote_player_id: host_state})
    payload = ctrl.serializer.encode_message(snapshot)

    with patch(
        "distributed_smb.network.udp_handler.UdpHandler.receive_packet_nowait",
        side_effect=[(payload, ("127.0.0.1", 9999)), None],
    ):
        ctrl._drain_snapshot_packets()

    # Reconcile applied: engine world_state reflects the snapshot
    assert ctrl.engine.world_state.characters[ctrl.remote_player_id].x == 50

    # Shadow copy also updated with snapshot state
    shadow_state = ctrl.shadow_copies[ctrl.remote_player_id].get_display_state()
    assert shadow_state is not None
    assert shadow_state.x == 50


# ---------------------------------------------------------------------------
# Reconciliation at 50ms simulated latency corrects the position
# ---------------------------------------------------------------------------


def test_reconciliation_at_50ms_corrects_position():
    """With 50ms artificial latency, reconcile() corrects the client position
    to match the authoritative snapshot once it arrives.

    The NoopPredictionEngine applies the correction directly
    (engine.world_state = snapshot.world_state).  The real engine will do
    rollback+replay instead, but the observable contract is the same: after
    reconcile(), the client position matches the authoritative one.
    """
    from distributed_smb.network.udp_handler import UdpHandler

    ctrl = NodeController(
        udp_handler=UdpHandler(host="0.0.0.0", port=19877, artificial_latency_ms=50)
    )
    ctrl.bootstrap(role=PlayerRole.CLIENT)
    ctrl._init_shadow_copies()
    ctrl.udp_handler.open_socket()

    # Authoritative position that differs from the current local state
    authoritative_x = 999.0
    auth_state = CharacterState(player_id=ctrl.local_player_id, x=authoritative_x, y=100)
    snapshot = _make_snapshot(seq=1, characters={ctrl.local_player_id: auth_state})
    payload = ctrl.serializer.encode_message(snapshot)

    # Simulate a snapshot packet arriving (bypassing the send-side delay — we
    # are testing the receive+reconcile path, not the send queue).
    with patch(
        "distributed_smb.network.udp_handler.UdpHandler.receive_packet_nowait",
        side_effect=[(payload, ("127.0.0.1", 9999)), None],
    ):
        ctrl._drain_snapshot_packets()

    ctrl.udp_handler.close_socket()

    # After reconcile(), client position matches the authoritative snapshot
    corrected_x = ctrl.engine.world_state.characters[ctrl.local_player_id].x
    assert corrected_x == authoritative_x, (
        f"Expected position corrected to {authoritative_x}, got {corrected_x}"
    )


