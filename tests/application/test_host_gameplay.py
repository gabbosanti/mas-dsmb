from distributed_smb.application.node_controller import NodeController
from distributed_smb.network.serializer import Serializer
from distributed_smb.shared.input import InputState
from distributed_smb.shared.messages.gameplay import PlayerInputPacket
from distributed_smb.shared.messages.recovery import HostDiscoveryProbe, HostIdentityResponse
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


def test_host_discovery_probe_is_replied_to_with_host_identity_response():
    controller = NodeController()
    controller.session_id = "session-abc"
    controller.local_ip = "10.0.0.1"
    controller.udp_handler = FakeUdpHandler(
        [
            (
                Serializer().encode_message(
                    HostDiscoveryProbe(session_id="session-abc", requester_ip="127.0.0.5")
                ),
                ("127.0.0.5", 50010),
            )
        ]
    )

    drained = controller._drain_remote_input_packets()

    assert drained == 0
    assert len(controller.udp_handler.sent) == 1
    payload, remote_host, remote_port = controller.udp_handler.sent[0]
    assert remote_host == "127.0.0.5"
    assert remote_port == 50010

    response = controller.serializer.decode_message(payload)
    assert isinstance(response, HostIdentityResponse)
    assert response.session_id == "session-abc"
    assert response.host_ip == "10.0.0.1"


def test_host_discovery_probe_with_wrong_session_id_is_ignored():
    controller = NodeController()
    controller.session_id = "session-abc"
    controller.local_ip = "10.0.0.1"
    controller.udp_handler = FakeUdpHandler(
        [
            (
                Serializer().encode_message(
                    HostDiscoveryProbe(session_id="other-session", requester_ip="127.0.0.5")
                ),
                ("127.0.0.5", 50010),
            )
        ]
    )

    drained = controller._drain_remote_input_packets()

    assert drained == 0
    assert controller.udp_handler.sent == []


def test_host_discovery_probe_and_player_input_are_processed_in_same_cycle():
    controller = NodeController()
    controller.session_id = "session-abc"
    controller.local_ip = "10.0.0.1"
    controller.udp_handler = FakeUdpHandler(
        [
            (
                Serializer().encode_message(
                    HostDiscoveryProbe(session_id="session-abc", requester_ip="127.0.0.5")
                ),
                ("127.0.0.5", 50010),
            ),
            (
                Serializer().encode_message(
                    PlayerInputPacket(
                        player_id="player2",
                        sequence_number=1,
                        input_state=InputState(left=True),
                    )
                ),
                ("127.0.0.5", 50010),
            ),
        ]
    )

    drained = controller._drain_remote_input_packets()

    assert drained == 1
    assert len(controller.udp_handler.sent) == 1

    payload, remote_host, remote_port = controller.udp_handler.sent[0]
    response = controller.serializer.decode_message(payload)
    assert isinstance(response, HostIdentityResponse)
    assert response.host_ip == "10.0.0.1"
    assert controller.cached_remote_inputs["player2"].left is True
    assert controller.last_remote_input_sequence["player2"] == 1


# ---------------------------------------------------------------------------
# _check_for_rejoining_players (M9 Bug #4 fix)
# ---------------------------------------------------------------------------


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


def test_check_for_rejoining_players_adds_player_to_roster_and_world():
    controller = NodeController()
    controller.session_id = "session-abc"
    controller.local_player_id = "player2"
    new_entry = RosterEntry(player_id="player3", host="10.0.0.3", udp_port=49500, join_index=2)
    controller.ws_handler = FakeWsHandler([_roster_update_with(new_entry)])

    controller._check_for_rejoining_players()

    assert controller.roster.get_player("player3") is not None
    assert controller.engine.world_state.get_player("player3") is not None
    assert "player3" in controller.last_input_time


def test_check_for_rejoining_players_is_idempotent():
    """Two RosterUpdates with the same player do not add them twice."""
    controller = NodeController()
    controller.session_id = "session-abc"
    controller.local_player_id = "player2"
    new_entry = RosterEntry(player_id="player3", host="10.0.0.3", udp_port=49500, join_index=2)
    # Provide the same RosterUpdate twice (e.g. lobby broadcasts on each join)
    controller.ws_handler = FakeWsHandler(
        [_roster_update_with(new_entry), _roster_update_with(new_entry)]
    )

    controller._check_for_rejoining_players()
    controller._check_for_rejoining_players()  # must not raise RosterValidationError

    assert controller.roster.get_player("player3") is not None


def test_check_for_rejoining_players_noop_when_no_new_joiners():
    """Empty ws_handler inbox — roster stays unchanged."""
    controller = NodeController()
    controller.session_id = "session-abc"
    controller.local_player_id = "player1"
    controller.ws_handler = FakeWsHandler([])

    before = len(controller.roster.get_all_players())
    controller._check_for_rejoining_players()
    assert len(controller.roster.get_all_players()) == before
