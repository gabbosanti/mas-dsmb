from distributed_smb.agents.models import (
    ActionStatus,
    EnemyAction,
)
from distributed_smb.application.node_controller import NodeController
from distributed_smb.domain.entity import Player
from distributed_smb.domain.game_engine import GameEngine
from distributed_smb.network.serializer import Serializer
from distributed_smb.shared.config import TICK_INTERVAL
from distributed_smb.shared.input import InputState
from distributed_smb.shared.messages.gameplay import PlayerInputPacket
from distributed_smb.shared.messages.recovery import (
    HostDiscoveryProbe,
    HostIdentityResponse,
)
from distributed_smb.shared.messages.session import RosterUpdate
from distributed_smb.shared.roster import GlobalRoster, RosterEntry


class FakeUdpHandler:
    def __init__(self, packets):
        self._packets = list(packets)
        self.sent = []

    def open_socket(self):
        pass

    def send_packet_nowait(self, payload, remote_host, remote_port):
        self.sent.append((payload, remote_host, remote_port))

    def receive_packet_nowait(self):
        if not self._packets:
            return None
        return self._packets.pop(0)


def _probe_packet(session_id: str) -> tuple:
    payload = Serializer().encode_message(
        HostDiscoveryProbe(session_id=session_id, requester_ip="127.0.0.5")
    )
    return payload, ("127.0.0.5", 50010)


def test_the_host_answers_a_discovery_probe_only_for_its_own_session():
    """A recovering node probes every cached peer: answering for a session we
    do not host would send it into the wrong game."""
    controller = NodeController()
    controller.session_id = "session-abc"
    controller.local_ip = "10.0.0.1"
    controller.udp_handler = FakeUdpHandler([_probe_packet("session-abc")])

    assert controller._drain_remote_input_packets() == 0
    payload, remote_host, remote_port = controller.udp_handler.sent[0]
    assert (remote_host, remote_port) == ("127.0.0.5", 50010)
    response = controller.serializer.decode_message(payload)
    assert isinstance(response, HostIdentityResponse)
    assert (response.session_id, response.host_ip) == ("session-abc", "10.0.0.1")

    stranger = NodeController()
    stranger.session_id = "session-abc"
    stranger.local_ip = "10.0.0.1"
    stranger.udp_handler = FakeUdpHandler([_probe_packet("other-session")])

    assert stranger._drain_remote_input_packets() == 0
    assert stranger.udp_handler.sent == []


def _input_packet(player_id: str = "player2", sequence_number: int = 1) -> tuple:
    payload = Serializer().encode_message(
        PlayerInputPacket(
            player_id=player_id,
            sequence_number=sequence_number,
            input_state=InputState(left=True),
        )
    )
    return payload, ("127.0.0.5", 50010)


def test_a_probe_in_the_queue_does_not_swallow_the_input_behind_it():
    """Both arrive on the same socket, and gameplay must not lose a frame."""
    controller = NodeController()
    controller.session_id = "session-abc"
    controller.local_ip = "10.0.0.1"
    controller.udp_handler = FakeUdpHandler(
        [_probe_packet("session-abc"), _input_packet()]
    )

    assert controller._drain_remote_input_packets() == 1

    payload, _, _ = controller.udp_handler.sent[0]
    assert isinstance(
        controller.serializer.decode_message(payload), HostIdentityResponse
    )
    assert controller.cached_remote_inputs["player2"].left is True
    assert controller.last_remote_input_sequence["player2"] == 1


class FakeWsHandler:
    """WsHandler stub that returns pre-loaded messages from poll()."""

    def __init__(self, messages: list):
        self._messages = list(messages)
        self.sent: list = []

    def poll(self):
        if not self._messages:
            return None
        return self._messages.pop(0)

    def send(self, message) -> None:
        self.sent.append(message)

    def connect(self, timeout: float = 10.0) -> None:
        pass


def _roster_update_with(entry: RosterEntry) -> RosterUpdate:
    roster = GlobalRoster()
    roster.add_player(entry)
    return RosterUpdate(roster=roster)


def test_a_rejoining_player_is_added_once_however_often_the_lobby_says_so():
    """The lobby re-broadcasts the roster on every join, so the same entry
    arrives repeatedly and must not be added twice."""
    controller = NodeController()
    controller.session_id = "session-abc"
    controller.local_player_id = "player2"
    new_entry = RosterEntry(
        player_id="player3", host="10.0.0.3", udp_port=49500, join_index=2
    )
    controller.ws_handler = FakeWsHandler(
        [_roster_update_with(new_entry), _roster_update_with(new_entry)]
    )

    controller._check_for_rejoining_players()
    controller._check_for_rejoining_players()  # must not raise RosterValidationError

    assert controller.roster.get_player("player3") is not None
    assert controller.engine.world_state.get_player("player3") is not None
    assert "player3" in controller.last_input_time
    assert [p.player_id for p in controller.roster.get_all_players()] == ["player3"]


def test_a_rejoining_player_restores_its_original_slot_and_character_state():
    controller = NodeController()
    controller.local_player_id = "player2"
    controller.roster.add_player(
        RosterEntry(
            player_id="player1",
            host="10.0.0.1",
            udp_port=50010,
            join_index=0,
            is_host=True,
        )
    )
    controller.engine.world_state.add_player(
        Player(player_id="player1", x=231, y=92, vx=45, join_index=0)
    )

    controller._evict_player("player1")
    rejoined = RosterEntry(
        player_id="player1",
        host="10.0.0.1",
        udp_port=49500,
        join_index=0,
    )
    controller.ws_handler = FakeWsHandler([_roster_update_with(rejoined)])
    controller._check_for_rejoining_players()

    player = controller.engine.world_state.get_player("player1")
    assert player is not None
    assert (player.x, player.y, player.vx, player.join_index) == (231, 92, 45, 0)
    assert controller.rejoin_states == {}


def test_process_host_frame_ticks_engine_at_fixed_interval(monkeypatch):
    """Physics scales with the step, so host and client must integrate every
    tick by the same fixed amount or the replayed positions diverge."""
    controller = NodeController()
    controller.local_player_id = "player1"
    controller.udp_handler = FakeUdpHandler([])
    controller.ws_handler = FakeWsHandler([])

    recorded_dts: list[float] = []
    original_tick = GameEngine.tick

    def spy_tick(self, dt, inputs):
        recorded_dts.append(dt)
        return original_tick(self, dt, inputs)

    monkeypatch.setattr(GameEngine, "tick", spy_tick)

    controller._process_host_frame(InputState())

    assert recorded_dts == [TICK_INTERVAL]


def test_authoritative_host_frame_applies_the_selected_agent_action():
    controller = NodeController()
    controller.local_player_id = "player1"
    controller.udp_handler = FakeUdpHandler([])
    controller.ws_handler = FakeWsHandler([])
    enemy = controller.engine.world_state.environment.enemies["enemy-1"]
    enemy.x = 520
    controller.agent_integration.enabled = True
    controller.agent_integration.control_enemy("enemy-1", EnemyAction.MOVE_LEFT)

    controller._process_host_frame(InputState())

    assert enemy.x < 520
    assert (
        controller.agent_integration.last_results["enemy-1"].status
        is ActionStatus.APPLIED
    )
