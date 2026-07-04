"""UDP transport helpers."""

import heapq
import random
import socket
import time
from dataclasses import dataclass, field

from distributed_smb.shared.config import (
    ARTIFICIAL_LATENCY_MS,
    DEFAULT_PACKET_DROP_RATE,
    UDP_MAX_PACKET_SIZE,
)

# (send_at, tie-breaker, payload, host, port)
_QueueEntry = tuple[float, int, bytes, str, int]


@dataclass(slots=True)
class UdpHandler:
    """Own one UDP endpoint and expose async plus immediate polling helpers."""

    host: str
    port: int
    packet_drop_rate: float = DEFAULT_PACKET_DROP_RATE
    max_packet_size: int = UDP_MAX_PACKET_SIZE
    artificial_latency_ms: int = ARTIFICIAL_LATENCY_MS
    _socket: socket.socket | None = field(init=False, default=None, repr=False)
    _outgoing_queue: list[_QueueEntry] = field(init=False, default_factory=list, repr=False)
    _send_counter: int = field(init=False, default=0, repr=False)

    def open_socket(self) -> None:
        """Bind the UDP socket if it is not already open."""
        if self._socket is not None:
            return
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp_socket.bind((self.host, self.port))
        self.port = udp_socket.getsockname()[1]
        udp_socket.setblocking(False)
        self._socket = udp_socket

    def close_socket(self) -> None:
        """Close the underlying UDP socket."""
        if self._socket is None:
            return
        self._socket.close()
        self._socket = None

    def _should_drop_packet(self) -> bool:
        return random.random() < self.packet_drop_rate

    def _flush_outgoing(self) -> None:
        """Send all queued packets whose scheduled time has elapsed."""
        now = time.monotonic()
        while self._outgoing_queue and self._outgoing_queue[0][0] <= now:
            _, _, payload, remote_host, remote_port = heapq.heappop(self._outgoing_queue)
            assert self._socket is not None
            self._socket.sendto(payload, (remote_host, remote_port))

    def send_packet_nowait(self, payload: bytes, remote_host: str, remote_port: int) -> None:
        """Send one UDP packet from the game loop thread.

        If artificial_latency_ms > 0, the packet is queued and dispatched
        after the configured delay. Pending packets are flushed on every call.
        """
        self.open_socket()
        if self._should_drop_packet():
            return
        assert self._socket is not None

        if self.artificial_latency_ms == 0:
            self._socket.sendto(payload, (remote_host, remote_port))
            return

        self._flush_outgoing()
        send_at = time.monotonic() + self.artificial_latency_ms / 1000.0
        self._send_counter += 1
        heapq.heappush(
            self._outgoing_queue,
            (send_at, self._send_counter, payload, remote_host, remote_port),
        )

    def receive_packet_nowait(self) -> tuple[bytes, tuple[str, int]] | None:
        """Poll the socket once without blocking."""
        self.open_socket()
        assert self._socket is not None
        try:
            packet = self._socket.recvfrom(self.max_packet_size)
        except BlockingIOError:
            return None
        except ConnectionResetError:
            # On Windows, sending UDP packets to a peer that is not listening can
            # surface here as a connection reset on the next recv call.
            return None

        return packet
