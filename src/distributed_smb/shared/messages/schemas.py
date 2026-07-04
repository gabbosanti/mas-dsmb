from pydantic import BaseModel, Field


class PlayerInputSchema(BaseModel):
    """Schema for UDP PlayerInputPacket validation."""

    player_id: str = Field(..., min_length=1)
    sequence_number: int = Field(...)
    input_state: dict = Field(default_factory=dict)
    message_type: str = Field(default="player_input")


class WorldStateSchema(BaseModel):
    """Schema for UDP WorldStateSnapshot validation."""

    sequence_number: int = Field(...)
    world_state: dict = Field(...)
    message_type: str = Field(default="world_state")


class SessionCreateSchema(BaseModel):
    """Schema for WebSocket SessionCreate message."""

    player_id: str = Field(..., min_length=1)
    ip: str = Field(..., min_length=1)
    udp_port: int = Field(..., ge=1024, le=65535)
    message_type: str = Field(default="session_create")


class SessionJoinSchema(BaseModel):
    """Schema for WebSocket SessionJoin message."""

    session_id: str = Field(..., min_length=1)
    player_id: str = Field(..., min_length=1)
    ip: str = Field(..., min_length=1)
    port: int = Field(..., ge=1024, le=65535)
    message_type: str = Field(default="session_join")


class SessionRecreateSchema(BaseModel):
    """Schema for WebSocket SessionRecreate message."""

    session_id: str = Field(..., min_length=1)
    next_join_index: int = Field(..., ge=0)
    message_type: str = Field(default="session_recreate")


class SessionCreatedSchema(BaseModel):
    """Schema for WebSocket SessionCreated message."""

    session_id: str = Field(..., min_length=1)
    join_index: int = Field(..., ge=0)
    message_type: str = Field(default="session_created")


class SessionJoinedSchema(BaseModel):
    """Schema for WebSocket SessionJoined message."""

    join_index: int = Field(..., ge=0)
    message_type: str = Field(default="session_joined")


class RosterEntrySchema(BaseModel):
    """Schema for roster entry validation."""

    player_id: str = Field(..., min_length=1)
    host: str = Field(..., min_length=1)
    udp_port: int = Field(..., ge=1024, le=65535)
    join_index: int = Field(..., ge=0)
    status: str = Field(...)
    is_host: bool = Field(...)


class RosterSchema(BaseModel):
    """Schema for roster."""

    players: list[RosterEntrySchema] = Field(...)


class RosterUpdateSchema(BaseModel):
    """Schema for WebSocket RosterUpdate message."""

    roster: RosterSchema = Field(...)
    message_type: str = Field(default="roster_update")


class GameStartSchema(BaseModel):
    """Schema for WebSocket GameStart message."""

    session_id: str = Field(..., min_length=1)
    message_type: str = Field(default="game_start")


class InitialStateSyncSchema(BaseModel):
    """Schema for WebSocket InitialStateSync message."""

    world_state: dict = Field(...)
    message_type: str = Field(default="initial_state_sync")


class BlockDestroyedMessageSchema(BaseModel):
    position: list[int] = Field(..., min_length=2, max_length=2)
    message_type: str = Field(default="block_destroyed_message")


class PowerUpCollectedMessageSchema(BaseModel):
    powerup_id: str = Field(..., min_length=1)
    player_id: str = Field(..., min_length=1)
    message_type: str = Field(default="powerup_collected_message")


class GateStateChangedMessageSchema(BaseModel):
    gate_id: str = Field(..., min_length=1)
    new_state: str = Field(..., min_length=1)
    message_type: str = Field(default="gate_state_changed_message")


class PlayerLeftSchema(BaseModel):
    player_id: str = Field(..., min_length=1)
    message_type: str = Field(default="player_left")


class PlayerDisconnectedSchema(BaseModel):
    player_id: str = Field(..., min_length=1)
    message_type: str = Field(default="player_disconnected")


class NewHostClaimSchema(BaseModel):
    claimer_ip: str = Field(..., min_length=1)
    claimer_join_index: int = Field(..., ge=0)
    session_id: str = Field(..., min_length=1)
    message_type: str = Field(default="new_host_claim")


class ElectionAckSchema(BaseModel):
    from_ip: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    message_type: str = Field(default="election_ack")


class ElectionNackSchema(BaseModel):
    from_ip: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)
    message_type: str = Field(default="election_nack")


class ReconnectionAckSchema(BaseModel):
    new_host_ip: str = Field(..., min_length=1)
    udp_port: int = Field(..., ge=1024, le=65535)
    game_events_port: int = Field(..., ge=1024, le=65535)
    session_id: str = Field(..., min_length=1)
    message_type: str = Field(default="reconnection_ack")


class HostDiscoveryProbeSchema(BaseModel):
    """Schema for UDP HostDiscoveryProbe validation."""

    session_id: str = Field(..., min_length=1)
    requester_ip: str = Field(..., min_length=1)
    message_type: str = Field(default="host_discovery_probe")


class HostIdentityResponseSchema(BaseModel):
    """Schema for UDP HostIdentityResponse validation."""

    session_id: str = Field(..., min_length=1)
    host_ip: str = Field(..., min_length=1)
    message_type: str = Field(default="host_identity_response")
