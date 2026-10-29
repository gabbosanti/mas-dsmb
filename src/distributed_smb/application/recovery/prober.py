from __future__ import annotations

import logging
import socket
from typing import Iterable

from distributed_smb.network.serializer import Serializer
from distributed_smb.shared.config import HOST_UDP_PORT
from distributed_smb.shared.messages.recovery import (
    HostDiscoveryProbe,
    HostIdentityResponse,
)
from distributed_smb.shared.session_metadata import CachedPeer

LOGGER = logging.getLogger(__name__)


class RecoveryProber:
    def find_current_host(
        self,
        session_id: str,
        requester_ip: str,
        peers: Iterable[CachedPeer],
        timeout_per_peer: float,
    ) -> str | None:
        """Probe cached peers over UDP and return the first matching host IP."""
        if not peers:
            return None

        probe = HostDiscoveryProbe(session_id=session_id, requester_ip=requester_ip)
        serializer = Serializer()

        for peer in peers:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.settimeout(timeout_per_peer)
                    sock.bind(("0.0.0.0", 0))
                    sock.sendto(
                        serializer.encode_message(probe), (peer.ip, HOST_UDP_PORT)
                    )
                    response_payload, _ = sock.recvfrom(65535)
            except (OSError, TimeoutError) as error:
                LOGGER.debug(
                    "[recovery] probe to %s timed out or failed: %s",
                    peer.ip,
                    error,
                )
                continue

            try:
                response = serializer.decode_message(response_payload)
            except ValueError:
                LOGGER.debug(
                    "[recovery] received invalid recovery response from %s", peer.ip
                )
                continue

            if not isinstance(response, HostIdentityResponse):
                LOGGER.debug(
                    "[recovery] ignoring non-recovery response from %s: %s",
                    peer.ip,
                    type(response),
                )
                continue

            if response.session_id != session_id:
                LOGGER.debug(
                    "[recovery] ignoring recovery response with mismatched session_id %s from %s",
                    response.session_id,
                    peer.ip,
                )
                continue

            return response.host_ip

        return None
