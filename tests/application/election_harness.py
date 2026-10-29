"""Shared fakes and builders for the election tests in this directory."""

import json
import time

from distributed_smb.application.election import (
    ElectionCoordinator,
    EnvironmentalStateBuffer,
)
from distributed_smb.application.node_controller import NodeController
from distributed_smb.shared.config import (
    ELECTION_CLAIM_TIMEOUT_S,
    HOST_UDP_PORT,
    T_ELECTION_BASE_S,
    T_ELECTION_DELTA_S,
)
from distributed_smb.shared.enums import PlayerRole
from distributed_smb.shared.messages.election import (
    ElectionAck,
    NewHostClaim,
    ReconnectionAck,
)
from distributed_smb.shared.messages.session import SessionCreated, SessionRecreate
from distributed_smb.shared.roster import RosterEntry


class FakeWsHandler:
    """WsHandler stub — connect/send/poll/close are no-ops; auto-acks SessionRecreate."""

    def __init__(self):
        self.sent: list = []
        self._queue: list = []

    def connect(self, timeout: float = 10.0) -> None:
        pass

    def send(self, message) -> None:
        self.sent.append(message)
        if isinstance(message, SessionRecreate):
            self._queue.append(
                SessionCreated(session_id=message.session_id, join_index=0)
            )

    def poll(self):
        return self._queue.pop(0) if self._queue else None

    def close(self) -> None:
        pass


class SpyBroker:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.promoted_port: int | None = None
        self.reconnected_to: tuple[str, int] | None = None

    def send(self, payload: bytes) -> None:
        self.sent.append(payload)

    def get_disconnected_player(self) -> str | None:
        return None

    def launch(self, host: str = "0.0.0.0", port: int = 0) -> None:
        pass

    def reconnect(self, host: str, port: int) -> None:
        self.reconnected_to = (host, port)

    def promote_to_server(self, port: int) -> None:
        self.promoted_port = port

    def last_message_type(self) -> str | None:
        if not self.sent:
            return None
        return json.loads(self.sent[-1]).get("message_type")


class SpyLobbyService:
    def __init__(self) -> None:
        self.launched = False

    def launch(self, host: str = "0.0.0.0", port: int = 0) -> None:
        self.launched = True


RECONNECTION_ACK = ReconnectionAck(
    new_host_ip="10.0.0.2",
    udp_port=50010,
    game_events_port=50003,
    session_id="test-session",
)


SESSION = "test-session"


def claim(ip: str = "10.0.0.2", join_index: int = 1) -> NewHostClaim:
    return NewHostClaim(
        claimer_ip=ip, claimer_join_index=join_index, session_id=SESSION
    )


def ack(ip: str) -> ElectionAck:
    return ElectionAck(from_ip=ip, session_id=SESSION)


def peer(
    player_id: str, ip: str, udp_port: int, join_index: int, is_host: bool = False
):
    return RosterEntry(
        player_id=player_id,
        host=ip,
        udp_port=udp_port,
        join_index=join_index,
        is_host=is_host,
    )


def awaiting_acks(nc, *peers: str) -> None:
    nc._pending_election_acks = set(peers)
    nc._election_claim_deadline = time.time() + ELECTION_CLAIM_TIMEOUT_S


def make_controller(
    local_ip: str = "10.0.0.2",
    local_player_id: str = "player2",
    join_index: int = 1,
    with_host_in_roster: bool = True,
) -> tuple[NodeController, SpyBroker]:
    broker = SpyBroker()
    nc = NodeController(game_event_broker=broker)
    nc.bootstrap(role=PlayerRole.CLIENT)
    nc.local_ip = local_ip
    nc.local_player_id = local_player_id
    nc.join_index = join_index
    nc.session_id = SESSION

    if with_host_in_roster:
        nc.roster.add_player(
            peer("player1", "10.0.0.1", HOST_UDP_PORT, 0, is_host=True)
        )
    nc.roster.add_player(peer(local_player_id, local_ip, 50011, join_index))
    nc.election_coordinator = ElectionCoordinator(
        join_index=join_index,
        my_ip=local_ip,
        timeout_base_s=T_ELECTION_BASE_S,
        timeout_delta_s=T_ELECTION_DELTA_S,
    )
    nc.env_state_buffer = EnvironmentalStateBuffer()
    # Patch out network-touching paths so unit tests never block on real I/O.
    fake_ws = FakeWsHandler()
    nc.ws_handler = fake_ws
    nc._make_lobby_ws_client = lambda host, port: setattr(
        nc, "ws_handler", FakeWsHandler()
    )
    nc._reconnect_game_event_handler = lambda *a, **kw: None
    return nc, broker


class SpyUdpHandler:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, str, int]] = []

    def send_packet_nowait(
        self, payload: bytes, remote_host: str, remote_port: int
    ) -> None:
        self.sent.append((payload, remote_host, remote_port))

    def receive_packet_nowait(self):
        return None

    def open_socket(self) -> None:
        pass

    def close_socket(self) -> None:
        pass


class FakeRecoveryProber:
    def __init__(self, found_ip: str | None) -> None:
        self.found_ip = found_ip
        self.calls: list = []

    def find_current_host(self, session_id, requester_ip, peers, timeout_per_peer):
        self.calls.append((session_id, requester_ip, list(peers), timeout_per_peer))
        return self.found_ip
