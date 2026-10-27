import socket
import threading
from typing import Optional

from distributed_smb.application.recovery.prober import RecoveryProber
from distributed_smb.network.serializer import Serializer
from distributed_smb.shared.messages.recovery import HostIdentityResponse
from distributed_smb.shared.session_metadata import CachedPeer


class _UdpResponder(threading.Thread):
    def __init__(
        self, session_id: str, host_ip: str, bind_port: int, bind_ip: str = "127.0.0.1"
    ):
        super().__init__(daemon=True)
        self.session_id = session_id
        self.host_ip = host_ip
        self.bind_port = bind_port
        self.bind_ip = bind_ip
        self.ready = threading.Event()
        self._socket: Optional[socket.socket] = None

    def run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.bind_ip, self.bind_port))
        self._socket = sock
        self.ready.set()
        try:
            sock.settimeout(2.0)
            data, addr = sock.recvfrom(4096)
            response = Serializer().encode_message(
                HostIdentityResponse(session_id=self.session_id, host_ip=self.host_ip)
            )
            sock.sendto(response, addr)
        except TimeoutError:
            pass
        finally:
            sock.close()


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _peers(*ips: str) -> list[CachedPeer]:
    return [
        CachedPeer(player_id=f"p{i}", ip=ip, join_index=i) for i, ip in enumerate(ips)
    ]


def test_prober_returns_the_first_peer_that_answers_for_this_session(monkeypatch):
    """Peers are probed in order, and an unreachable one must not stop the
    search — after a migration only one of them is the new host."""
    port = _find_free_port()
    monkeypatch.setattr(
        "distributed_smb.application.recovery.prober.HOST_UDP_PORT", port
    )
    responder = _UdpResponder(
        session_id="session-abc", host_ip="10.0.0.2", bind_port=port
    )
    responder.start()
    assert responder.ready.wait(1.0)

    prober = RecoveryProber()
    # 127.0.0.2 never answers, 127.0.0.1 does.
    result = prober.find_current_host(
        "session-abc",
        "127.0.0.5",
        _peers("127.0.0.2", "127.0.0.1"),
        timeout_per_peer=0.5,
    )

    assert result == "10.0.0.2"
    responder.join(1.0)


def test_prober_gives_up_when_nobody_answers_for_this_session(monkeypatch):
    """A silent peer and an answer about another session are both useless:
    rejoining the wrong session would split the game in two."""
    port = _find_free_port()
    monkeypatch.setattr(
        "distributed_smb.application.recovery.prober.HOST_UDP_PORT", port
    )
    prober = RecoveryProber()

    assert (
        prober.find_current_host(
            "session-abc", "127.0.0.5", _peers("127.0.0.2"), timeout_per_peer=0.1
        )
        is None
    )

    responder = _UdpResponder(
        session_id="other-session", host_ip="10.0.0.3", bind_port=port
    )
    responder.start()
    assert responder.ready.wait(1.0)
    assert (
        prober.find_current_host(
            "session-abc", "127.0.0.5", _peers("127.0.0.1"), timeout_per_peer=0.5
        )
        is None
    )
    responder.join(1.0)

    def fail_socket(*args, **kwargs):
        raise AssertionError("no socket may be opened when there is nobody to ask")

    monkeypatch.setattr(
        "distributed_smb.application.recovery.prober.socket.socket", fail_socket
    )
    assert (
        prober.find_current_host("session-abc", "127.0.0.5", [], timeout_per_peer=0.1)
        is None
    )
