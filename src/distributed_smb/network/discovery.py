"""UDP broadcast discovery — resolves session_id → (host_ip, lobby_port) without a registry."""

import logging
import socket
import threading
from collections.abc import Iterable

from distributed_smb.shared.config import DISCOVERY_TIMEOUT, DISCOVERY_UDP_PORT

LOGGER = logging.getLogger(__name__)


def _local_ipv4_addresses() -> list[str]:
    """Return all non-loopback IPv4 addresses bound to local interfaces."""
    addresses: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                addresses.add(ip)
    except OSError:
        pass
    return list(addresses)


class DiscoveryService:
    def __init__(self) -> None:
        self._running = False
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self._allowed_ips: set[str] | None = None

    def announce(
        self,
        session_id: str,
        lobby_port: int,
        allowed_ips: Iterable[str] | None = None,
    ) -> None:
        """Listen for discovery queries and respond with lobby_port."""
        self._running = True
        self._allowed_ips = set(allowed_ips) if allowed_ips is not None else None
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", DISCOVERY_UDP_PORT))
        sock.settimeout(DISCOVERY_TIMEOUT)
        self._sock = sock

        query = f"WHO {session_id}".encode()
        response = str(lobby_port).encode()

        def _listen() -> None:
            LOGGER.info(
                "[discovery] listening on UDP :%d for session %s",
                DISCOVERY_UDP_PORT,
                session_id,
            )
            while self._running:
                try:
                    data, addr = sock.recvfrom(256)
                    if data != query:
                        continue
                    if (
                        self._allowed_ips is not None
                        and addr[0] not in self._allowed_ips
                    ):
                        LOGGER.debug(
                            "[discovery] ignoring query from unauthorized IP %s",
                            addr[0],
                        )
                        continue
                    LOGGER.info(
                        "[discovery] query from %s:%d → port %d",
                        addr[0],
                        addr[1],
                        lobby_port,
                    )
                    sock.sendto(response, addr)
                except TimeoutError:
                    continue
                except OSError:
                    break

        self._thread = threading.Thread(
            target=_listen, name="discovery-announce", daemon=True
        )
        self._thread.start()

    def discover(self, session_id: str, timeout: float = 5.0) -> tuple[str, int]:
        """Broadcast a discovery query and return (host_ip, lobby_port)."""
        query = f"WHO {session_id}".encode()
        local_ips = _local_ipv4_addresses() or ["0.0.0.0"]
        per_ip_timeout = max(timeout / len(local_ips), 0.5)

        LOGGER.info(
            "[discovery] broadcasting WHO %s on port %d from local IPs: %s",
            session_id,
            DISCOVERY_UDP_PORT,
            local_ips,
        )
        for local_ip in local_ips:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    sock.bind((local_ip, 0))
                    sock.settimeout(per_ip_timeout)
                    sock.sendto(query, ("255.255.255.255", DISCOVERY_UDP_PORT))
                    data, (host_ip, _) = sock.recvfrom(64)
            except (OSError, TimeoutError):
                continue

            lobby_port = int(data.decode())
            LOGGER.info(
                "[discovery] found host at %s, lobby port %d", host_ip, lobby_port
            )
            return host_ip, lobby_port

        raise TimeoutError("discovery timed out on all local interfaces")

    def set_allowed_ips(self, allowed_ips: Iterable[str] | None) -> None:
        self._allowed_ips = set(allowed_ips) if allowed_ips is not None else None

    def stop(self) -> None:
        self._running = False
        self._allowed_ips = None
        if self._sock:
            self._sock.close()
            self._sock = None
