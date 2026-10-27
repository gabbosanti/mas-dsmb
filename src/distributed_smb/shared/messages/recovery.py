"""Host discovery and rejoin messages."""

from dataclasses import dataclass, field

from distributed_smb.shared.enums import MessageType
from distributed_smb.shared.messages.common import MessageValidationError


@dataclass(slots=True)
class HostDiscoveryProbe:
    """Sent by a recovering node to each cached peer IP at HOST_UDP_PORT."""

    session_id: str
    requester_ip: str
    message_type: MessageType = field(
        init=False, default=MessageType.HOST_DISCOVERY_PROBE
    )

    def __post_init__(self):
        if not self.session_id or not isinstance(self.session_id, str):
            raise MessageValidationError(f"Invalid session_id: {self.session_id}")
        if not self.requester_ip or not isinstance(self.requester_ip, str):
            raise MessageValidationError(f"Invalid requester_ip: {self.requester_ip}")


@dataclass(slots=True)
class HostIdentityResponse:
    """Reply from the current acting host confirming it is authoritative for session_id."""

    session_id: str
    host_ip: str
    message_type: MessageType = field(
        init=False, default=MessageType.HOST_IDENTITY_RESPONSE
    )

    def __post_init__(self):
        if not self.session_id or not isinstance(self.session_id, str):
            raise MessageValidationError(f"Invalid session_id: {self.session_id}")
        if not self.host_ip or not isinstance(self.host_ip, str):
            raise MessageValidationError(f"Invalid host_ip: {self.host_ip}")
