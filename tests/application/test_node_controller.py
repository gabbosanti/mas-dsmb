from unittest.mock import MagicMock, patch

import pygame

from distributed_smb.application.node_controller import NodeController
from distributed_smb.domain.entity import CooperativeGate, DestructibleBlock, ExclusivePowerUp
from distributed_smb.domain.world import CharacterState, WorldState
from distributed_smb.main import get_controller, parse_args
from distributed_smb.network.serializer import Serializer
from distributed_smb.presentation.input_handler import InputHandler
from distributed_smb.shared.config import DEFAULT_HOST, LOBBY_WS_PORT, TICK_INTERVAL
from distributed_smb.shared.enums import NodeState, PlayerRole
from distributed_smb.shared.input import InputState
from distributed_smb.shared.messages.sync import WorldStateSnapshot


def test_bootstrap_initializes_node_controller_state():
    controller = NodeController()

    bootstrapped_controller = controller.bootstrap()

    assert bootstrapped_controller is controller
    assert controller.is_bootstrapped is True
    assert controller.lifecycle.state is NodeState.IDLE
    assert controller.tick_interval == TICK_INTERVAL


def test_lifecycle_exposes_waiting_room_and_started_states():
    controller = NodeController().bootstrap()

    controller.lifecycle.move_to_lobby()

    assert controller.lifecycle.state is NodeState.IN_LOBBY
    assert controller.lifecycle.is_waiting_room is True
    assert controller.lifecycle.is_started is False

    controller.lifecycle.move_to_game()

    assert controller.lifecycle.state is NodeState.IN_GAME
    assert controller.lifecycle.is_started is True


def test_build_runtime_context_exposes_expected_components():
    fake_keys = {
        pygame.K_LEFT: False,
        pygame.K_a: False,
        pygame.K_RIGHT: False,
        pygame.K_d: False,
        pygame.K_SPACE: False,
        pygame.K_UP: False,
        pygame.K_w: False,
        pygame.K_DOWN: False,
        pygame.K_s: False,
    }
    handler = InputHandler(key_provider=lambda: fake_keys)
    controller = NodeController().bootstrap()
    controller.input_handler = handler

    runtime_context = controller.build_runtime_context()

    assert set(runtime_context) == {
        "engine",
        "input_handler",
        "local_player_id",
        "remote_player_id",
        "renderer",
        "role",
        "tick_interval",
    }
    assert runtime_context["engine"] is controller.engine
    assert runtime_context["input_handler"] is controller.input_handler
    assert runtime_context["local_player_id"] == controller.local_player_id
    assert runtime_context["remote_player_id"] == controller.remote_player_id
    assert runtime_context["renderer"] is controller.renderer
    assert runtime_context["role"] is controller.role
    assert runtime_context["tick_interval"] == controller.tick_interval
    assert isinstance(controller.input_handler.read_input(), InputState)


def test_main_returns_a_bootstrapped_controller():
    controller = get_controller()

    assert isinstance(controller, NodeController)
    assert controller.is_bootstrapped is True


def test_run_returns_false_when_presentation_runtime_is_missing():
    controller = NodeController().bootstrap()

    with patch.dict("sys.modules", {"distributed_smb.presentation.app": None}):
        started = controller.run()

    assert started is False


def test_host_bootstrap_configures_players_and_role():
    controller = NodeController().bootstrap(role=PlayerRole.HOST)

    assert controller.role is PlayerRole.HOST
    assert controller.engine.world_state.get_player(controller.local_player_id) is not None


def test_spawn_position_scales_with_join_index():
    controller = NodeController()
    assert controller._spawn_position_for(0) == (100, 100)
    assert controller._spawn_position_for(1) == (240, 100)
    assert controller._spawn_position_for(2) == (380, 100)
    assert controller._spawn_position_for(3) == (520, 100)


def test_client_process_frame_increments_input_sequence():
    controller = NodeController().bootstrap(role=PlayerRole.CLIENT)

    class FakeUdpHandler:
        def open_socket(self):
            return None

        def send_packet_nowait(self, payload, remote_host, remote_port):
            return None

        def receive_packet_nowait(self):
            return None

    controller.udp_handler = FakeUdpHandler()

    controller.process_frame(TICK_INTERVAL, InputState(right=True))

    assert controller.input_sequence_number == 1


def test_process_frame_marks_node_as_started():
    controller = NodeController().bootstrap(role=PlayerRole.CLIENT)

    class FakeUdpHandler:
        def open_socket(self):
            return None

        def send_packet_nowait(self, payload, remote_host, remote_port):
            return None

        def receive_packet_nowait(self):
            return None

    controller.udp_handler = FakeUdpHandler()

    controller.process_frame(TICK_INTERVAL, InputState())

    assert controller.lifecycle.state is NodeState.IN_GAME
    assert controller.lifecycle.is_started is True


def test_parse_args_defaults():
    args = parse_args([])
    assert args.host_ip is None
    assert args.local_ip is None
    assert args.session_id == ""
    assert not args.host
    assert not args.client


def test_detect_local_ip_falls_back_to_default_host_without_route():
    from distributed_smb.main import _detect_local_ip

    fake_socket = MagicMock()
    fake_socket.__enter__.return_value = fake_socket
    fake_socket.connect.side_effect = OSError("network unreachable")

    with patch("distributed_smb.main.socket.socket", return_value=fake_socket):
        assert _detect_local_ip() == DEFAULT_HOST


def test_main_uses_explicit_local_ip_when_provided():
    from distributed_smb.main import main

    controller = main(role=PlayerRole.HOST, local_ip="10.0.0.5")

    assert controller.local_ip == "10.0.0.5"


def test_main_auto_detects_local_ip_when_not_provided():
    from distributed_smb.main import main

    with patch("distributed_smb.main._detect_local_ip", return_value="192.168.1.42"):
        controller = main(role=PlayerRole.HOST)

    assert controller.local_ip == "192.168.1.42"


def test_parse_args_client_flags():
    args = parse_args(["--client", "--host-ip", "192.168.1.10", "--session-id", "abc123"])
    assert args.client is True
    assert args.host_ip == "192.168.1.10"
    assert args.session_id == "abc123"


def test_main_client_sets_ws_handler_host():
    from distributed_smb.main import main

    controller = main(role=PlayerRole.CLIENT, host_ip="192.168.1.10")
    assert controller.ws_handler.host == "192.168.1.10"
    assert controller.ws_handler.port == LOBBY_WS_PORT
    assert controller.remote_host == "192.168.1.10"


def test_main_client_discovery_join_prompts_for_session_id_only():
    from distributed_smb.main import main

    calls = []

    class FakeConnection:
        def close(self):
            calls.append("close")

        def close_socket(self):
            calls.append("close_socket")

    class FakeContainerManager:
        def stop(self):
            calls.append("containers_stop")

    class FakeGameEventHandler:
        def connect(self):
            calls.append("ws_connect")

    class FakeController:
        def __init__(self):
            self.roster = object()
            self.ws_handler = FakeConnection()
            self.udp_handler = FakeConnection()
            self.lobby_container_manager = FakeContainerManager()
            self.game_event_handler = FakeGameEventHandler()
            self.use_discovery = True

        def lobby_phase(self, *, session_id, on_update, start_requested):
            calls.append(f"lobby:{session_id}")
            return self.roster

        def run(self):
            calls.append("run")

    class FakeLobbyScreen:
        def __init__(self):
            self.start_requested = True

        def prompt_session_id(self, *, initial_session_id):
            calls.append("prompt_session_id")
            return "abc123"

        def prompt_join_details(self, **kwargs):
            calls.append("prompt_join_details")
            return ("10.0.0.5", "abc123")

        def render(self, **kwargs):
            return True

        def play_game_start_transition(self, **kwargs):
            return True

        def close(self):
            calls.append("screen_close")

    fake_controller = FakeController()

    with patch("distributed_smb.main.build_controller", return_value=fake_controller):
        with patch("distributed_smb.main.LobbyScreen", FakeLobbyScreen):
            controller = main(run_app=True, role=PlayerRole.CLIENT, host_ip=None, session_id="")

    assert controller is fake_controller
    assert "prompt_session_id" in calls
    assert "prompt_join_details" not in calls
    assert "lobby:abc123" in calls


def test_main_plays_transition_before_game_run():
    from distributed_smb.main import main

    calls = []

    class FakeConnection:
        def close(self):
            calls.append("close")

    class FakeContainerManager:
        def stop(self):
            calls.append("containers_stop")

    class FakeController:
        def __init__(self):
            self.roster = object()
            self.ws_handler = FakeConnection()
            self.udp_handler = FakeConnection()
            self.lobby_container_manager = FakeContainerManager()

        def lobby_phase(self, *, session_id, on_update, start_requested):
            calls.append("lobby")
            assert start_requested() is False
            return self.roster

        def run(self):
            calls.append("run")

    class FakeLobbyScreen:
        def __init__(self):
            calls.append("screen")
            self.start_requested = False

        def render(self, **kwargs):
            calls.append("render")
            return True

        def play_game_start_transition(self, **kwargs):
            calls.append("transition")
            return True

        def close(self):
            calls.append("screen_close")

    fake_controller = FakeController()

    with patch("distributed_smb.main.build_controller", return_value=fake_controller):
        with patch("distributed_smb.main.LobbyScreen", FakeLobbyScreen):
            controller = main(run_app=True, role=PlayerRole.HOST)

    assert controller is fake_controller
    assert calls.index("lobby") < calls.index("transition") < calls.index("run")
    assert "containers_stop" in calls
    assert calls.index("run") < calls.index("containers_stop")


def test_client_snapshot_updates_characters_preserves_environment():
    """UDP snapshot updates character positions but must NOT override environment state.

    Blocks, power-ups, and gates are managed exclusively by WebSocket events.
    Applying environment from UDP snapshots would cause WS events to arrive late
    (block already destroyed, power-up already collected) and be silently ignored.
    """
    controller = NodeController().bootstrap(role=PlayerRole.CLIENT)
    serializer = Serializer()

    # Set a known local environment state that should be preserved after reconciliation.
    local_block = controller.engine.world_state.environment.destructible_blocks[0]
    local_block.destroyed = False

    # Build a snapshot that tries to override the environment.
    world_state = WorldState(
        sequence_number=12,
        characters={"player1": CharacterState(player_id="player1", x=180.0, y=96.0)},
    )
    world_state.add_block(DestructibleBlock(x=12, y=20, destroyed=True))
    world_state.add_power_up(
        ExclusivePowerUp(x=40, y=20, powerup_id="pu-a", collected=True, owner="player1")
    )
    world_state.add_gate(CooperativeGate(x=72, y=20, gate_id="gate-a", state="open"))
    payload = serializer.encode_message(
        WorldStateSnapshot(sequence_number=99, world_state=world_state)
    )

    class FakeUdpHandler:
        def __init__(self, packet):
            self.packet = packet

        def receive_packet_nowait(self):
            if self.packet is None:
                return None
            packet = self.packet
            self.packet = None
            return packet, ("127.0.0.1", 50010)

    controller.udp_handler = FakeUdpHandler(payload)
    controller._drain_snapshot_packets()

    # Characters are updated from the snapshot.
    assert controller.engine.world_state.sequence_number == 12
    assert controller.engine.world_state.characters["player1"].x == 180.0
    # Environment is preserved from local state — snapshot's environment is discarded.
    assert controller.engine.world_state.environment.destructible_blocks[0].destroyed is False
    assert "pu-a" not in controller.engine.world_state.environment.power_ups
    assert "gate-a" not in controller.engine.world_state.environment.cooperative_gates


def test_client_process_frame_returns_visual_state_with_predicted_local_player():
    controller = NodeController().bootstrap(role=PlayerRole.CLIENT)
    serializer = Serializer()
    local_pid = controller.local_player_id  # "player2" placeholder after bootstrap
    authoritative_world = WorldState(
        sequence_number=20,
        characters={
            "player1": CharacterState(player_id="player1", x=100.0, y=100.0),
            local_pid: CharacterState(player_id=local_pid, x=100.0, y=100.0),
        },
    )
    payload = serializer.encode_message(
        WorldStateSnapshot(sequence_number=20, world_state=authoritative_world)
    )

    class FakeUdpHandler:
        def __init__(self, packet):
            self.packet = packet

        def open_socket(self):
            return None

        def send_packet_nowait(self, payload, remote_host, remote_port):
            return None

        def receive_packet_nowait(self):
            if self.packet is None:
                return None
            packet = self.packet
            self.packet = None
            return packet, ("127.0.0.1", 50010)

    controller.udp_handler = FakeUdpHandler(payload)
    controller.time_provider = lambda: 10.0

    visual_world = controller.process_frame(TICK_INTERVAL, InputState(right=True))

    assert visual_world.characters[local_pid].x > authoritative_world.characters[local_pid].x
    assert (
        controller.engine.world_state.characters[local_pid].x
        == authoritative_world.characters[local_pid].x
    )


def test_visual_world_state_clones_environment_from_authoritative_state():
    controller = NodeController().bootstrap(role=PlayerRole.CLIENT)
    controller.engine.world_state.add_block(DestructibleBlock(x=12, y=20, destroyed=False))
    controller.engine.world_state.add_power_up(
        ExclusivePowerUp(x=40, y=20, powerup_id="pu-a", collected=False)
    )
    controller.engine.world_state.add_gate(
        CooperativeGate(x=72, y=20, gate_id="gate-a", state="closed")
    )

    visual_world = controller._build_visual_world_state()

    assert visual_world.environment is not controller.engine.world_state.environment

    visual_world.environment.destructible_blocks[0].destroyed = True
    visual_world.environment.power_ups["pu-a"].collected = True
    visual_world.environment.cooperative_gates["gate-a"].state = "open"

    assert controller.engine.world_state.environment.destructible_blocks[0].destroyed is False
    assert controller.engine.world_state.environment.power_ups["pu-a"].collected is False
    assert controller.engine.world_state.environment.cooperative_gates["gate-a"].state == "closed"
