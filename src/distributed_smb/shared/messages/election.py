"""Election and host migration messages."""

from dataclasses import dataclass, field

from distributed_smb.shared.enums import MessageType
from distributed_smb.shared.messages.common import MessageValidationError


@dataclass(slots=True)
class NewHostClaim:
    """Broadcast peer-to-peer: node claims to be the new host."""

    claimer_ip: str
    claimer_join_index: int
    session_id: str
    message_type: MessageType = field(init=False, default=MessageType.NEW_HOST_CLAIM)

    def __post_init__(self):
        if not self.claimer_ip or not isinstance(self.claimer_ip, str):
            raise MessageValidationError(f"Invalid claimer_ip: {self.claimer_ip}")
        if self.claimer_join_index < 0:
            raise MessageValidationError(
                f"Invalid claimer_join_index: {self.claimer_join_index}"
            )
        if not self.session_id or not isinstance(self.session_id, str):
            raise MessageValidationError(f"Invalid session_id: {self.session_id}")


@dataclass(slots=True)
class ElectionAck:
    """Unicast reply to NewHostClaim: peer acknowledges the claim."""

    from_ip: str
    session_id: str
    message_type: MessageType = field(init=False, default=MessageType.ELECTION_ACK)

    def __post_init__(self):
        if not self.from_ip or not isinstance(self.from_ip, str):
            raise MessageValidationError(f"Invalid from_ip: {self.from_ip}")
        if not self.session_id or not isinstance(self.session_id, str):
            raise MessageValidationError(f"Invalid session_id: {self.session_id}")


@dataclass(slots=True)
class ReconnectionAck:
    """Sent by the newly promoted host to each surviving client."""

    new_host_ip: str
    udp_port: int
    game_events_port: int
    session_id: str
    message_type: MessageType = field(init=False, default=MessageType.RECONNECTION_ACK)

    def __post_init__(self):
        if not self.new_host_ip or not isinstance(self.new_host_ip, str):
            raise MessageValidationError(f"Invalid new_host_ip: {self.new_host_ip}")
        if not (1024 <= self.udp_port <= 65535):
            raise MessageValidationError(f"udp_port out of range: {self.udp_port}")
        if not (1024 <= self.game_events_port <= 65535):
            raise MessageValidationError(
                f"game_events_port out of range: {self.game_events_port}"
            )
        if not self.session_id or not isinstance(self.session_id, str):
            raise MessageValidationError(f"Invalid session_id: {self.session_id}")
