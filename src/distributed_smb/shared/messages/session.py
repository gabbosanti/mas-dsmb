from dataclasses import dataclass, field

from distributed_smb.shared.enums import MessageType
from distributed_smb.shared.messages.common import (
    MessageValidationError,
    validate_join_index,
    validate_player_id,
    validate_port,
)
from distributed_smb.shared.roster import GlobalRoster


@dataclass(slots=True)
class SessionCreate:
    """Sent by the host to the lobby to create a new session."""

    player_id: str
    ip: str
    udp_port: int
    message_type: MessageType = field(init=False, default=MessageType.SESSION_CREATE)

    def __post_init__(self):
        validate_player_id(self.player_id)
        validate_port(self.udp_port)
        if not self.ip or not isinstance(self.ip, str):
            raise MessageValidationError(f"Invalid ip: {self.ip}")


@dataclass(slots=True)
class SessionJoin:
    session_id: str
    player_id: str
    ip: str
    port: int
    message_type: MessageType = field(init=False, default=MessageType.SESSION_JOIN)

    def __post_init__(self):
        validate_player_id(self.player_id)
        validate_port(self.port)
        if not self.session_id or not isinstance(self.session_id, str):
            raise MessageValidationError(f"Invalid session_id: {self.session_id}")
        if not self.ip or not isinstance(self.ip, str):
            raise MessageValidationError(f"Invalid ip: {self.ip}")


@dataclass(slots=True)
class SessionCreated:
    """Sent by the lobby to the host after a session is created successfully."""

    session_id: str
    join_index: int
    message_type: MessageType = field(init=False, default=MessageType.SESSION_CREATED)

    def __post_init__(self):
        if not self.session_id or not isinstance(self.session_id, str):
            raise MessageValidationError(f"Invalid session_id: {self.session_id}")
        validate_join_index(self.join_index)


@dataclass(slots=True)
class SessionJoined:
    """Sent by the lobby to the joining client after a successful join."""

    join_index: int
    message_type: MessageType = field(init=False, default=MessageType.SESSION_JOINED)

    def __post_init__(self):
        validate_join_index(self.join_index)


@dataclass(slots=True)
class RosterUpdate:
    roster: GlobalRoster
    message_type: MessageType = field(init=False, default=MessageType.ROSTER_UPDATE)

    def __post_init__(self):
        # Validate that all roster entries have unique join indices
        join_indices = [entry.join_index for entry in self.roster.players]
        if len(join_indices) != len(set(join_indices)):
            raise MessageValidationError("Roster has duplicate join_index values")


@dataclass(slots=True)
class SessionRecreate:
    """Sent by the promoted host to its own new lobby after M8 host migration (M9).

    Unlike SessionCreate (which generates a new session_id), SessionRecreate
    preserves the existing session_id so that recovering nodes can rejoin using
    the session_id stored in session_metadata.json.
    """

    session_id: str
    next_join_index: int
    host_ip: str
    host_udp_port: int
    host_join_index: int
    message_type: MessageType = field(init=False, default=MessageType.SESSION_RECREATE)

    def __post_init__(self):
        if not self.session_id or not isinstance(self.session_id, str):
            raise MessageValidationError(f"Invalid session_id: {self.session_id}")
        if self.next_join_index < 0:
            raise MessageValidationError(f"Invalid next_join_index: {self.next_join_index}")
        if not self.host_ip or not isinstance(self.host_ip, str):
            raise MessageValidationError(f"Invalid host_ip: {self.host_ip}")
        validate_port(self.host_udp_port)
        if self.host_join_index < 0:
            raise MessageValidationError(f"Invalid host_join_index: {self.host_join_index}")


@dataclass(slots=True)
class GameStart:
    session_id: str
    message_type: MessageType = field(init=False, default=MessageType.GAME_START)

    def __post_init__(self):
        if not self.session_id or not isinstance(self.session_id, str):
            raise MessageValidationError(f"Invalid session_id: {self.session_id}")
