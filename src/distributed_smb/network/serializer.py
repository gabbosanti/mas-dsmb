"""Message serialization helpers."""

import json
from dataclasses import asdict, is_dataclass
from typing import Any, Union

from pydantic import ValidationError

from distributed_smb.domain.world import WorldState
from distributed_smb.shared.enums import ConnectionStatus
from distributed_smb.shared.input import InputState
from distributed_smb.shared.messages.election import (
    ElectionAck,
    ElectionNack,
    NewHostClaim,
    ReconnectionAck,
)
from distributed_smb.shared.messages.gameplay import (
    BlockDestroyedMessage,
    GateStateChangedMessage,
    PlayerDeathMessage,
    PlayerDisconnected,
    PlayerInputPacket,
    PlayerLeft,
    PowerUpCollectedMessage,
)
from distributed_smb.shared.messages.recovery import (
    HostDiscoveryProbe,
    HostIdentityResponse,
)
from distributed_smb.shared.messages.schemas import (
    BlockDestroyedMessageSchema,
    ElectionAckSchema,
    ElectionNackSchema,
    GameStartSchema,
    GateStateChangedMessageSchema,
    HostDiscoveryProbeSchema,
    HostIdentityResponseSchema,
    InitialStateSyncSchema,
    NewHostClaimSchema,
    PlayerDeathMessageSchema,
    PlayerDisconnectedSchema,
    PlayerInputSchema,
    PlayerLeftSchema,
    PowerUpCollectedMessageSchema,
    ReconnectionAckSchema,
    RosterUpdateSchema,
    SessionCreatedSchema,
    SessionCreateSchema,
    SessionJoinedSchema,
    SessionJoinSchema,
    SessionRecreateSchema,
    WorldStateSchema,
)
from distributed_smb.shared.messages.session import (
    GameStart,
    MessageType,
    RosterUpdate,
    SessionCreate,
    SessionCreated,
    SessionJoin,
    SessionJoined,
    SessionRecreate,
)
from distributed_smb.shared.messages.sync import InitialStateSync, WorldStateSnapshot
from distributed_smb.shared.roster import GlobalRoster, RosterEntry

# Union type for all WebSocket coordination messages
WsMessage = Union[
    SessionCreate,
    SessionCreated,
    SessionJoin,
    SessionJoined,
    SessionRecreate,
    RosterUpdate,
    GameStart,
    InitialStateSync,
    BlockDestroyedMessage,
    PowerUpCollectedMessage,
    GateStateChangedMessage,
    PlayerLeft,
    PlayerDeathMessage,
    PlayerDisconnected,
    NewHostClaim,
    ElectionAck,
    ElectionNack,
    ReconnectionAck,
]


class DeserializationError(ValueError):
    """Raised when deserialization fails."""

    pass


class Serializer:
    """Serialize basic Python objects and dataclasses to JSON."""

    def encode(self, payload: object) -> str:
        """Encode the given payload to JSON."""
        if is_dataclass(payload):
            payload = asdict(payload)
        return json.dumps(payload, default=lambda o: list(o) if isinstance(o, set) else o)

    def decode(self, payload: str | bytes) -> Any:
        """Decode JSON data into a Python object."""
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        return json.loads(payload)

    # ------------------------------------------------------------------
    # UDP transport
    # ------------------------------------------------------------------

    def encode_message(
        self,
        payload: PlayerInputPacket | WorldStateSnapshot | HostDiscoveryProbe | HostIdentityResponse,
    ) -> bytes:
        """Encode a gameplay packet to bytes ready for UDP transport."""
        return self.encode(payload).encode("utf-8")

    def decode_message(
        self,
        payload: str | bytes,
    ) -> PlayerInputPacket | WorldStateSnapshot | HostDiscoveryProbe | HostIdentityResponse:
        """Decode a UDP gameplay packet into its typed dataclass."""
        data = self.decode(payload)
        message_type = data.get("message_type")

        try:
            if message_type == MessageType.PLAYER_INPUT:
                validated = PlayerInputSchema(**data)
                return PlayerInputPacket(
                    player_id=validated.player_id,
                    sequence_number=validated.sequence_number,
                    input_state=InputState(**validated.input_state),
                )

            if message_type == MessageType.WORLD_STATE:
                validated = WorldStateSchema(**data)
                world_state = self._decode_world_state(validated.world_state)
                return WorldStateSnapshot(
                    sequence_number=validated.sequence_number,
                    world_state=world_state,
                )

            if message_type == MessageType.HOST_DISCOVERY_PROBE:
                validated = HostDiscoveryProbeSchema(**data)
                return HostDiscoveryProbe(
                    session_id=validated.session_id,
                    requester_ip=validated.requester_ip,
                )

            if message_type == MessageType.HOST_IDENTITY_RESPONSE:
                validated = HostIdentityResponseSchema(**data)
                return HostIdentityResponse(
                    session_id=validated.session_id,
                    host_ip=validated.host_ip,
                )

            raise DeserializationError(f"Unsupported UDP message type: {message_type}")

        except ValidationError as e:
            raise DeserializationError(f"Invalid UDP message format: {e.errors()}")

    # ------------------------------------------------------------------
    # WebSocket transport
    # ------------------------------------------------------------------

    def encode_ws_message(self, payload: WsMessage) -> dict:
        """Encode a lobby coordination message to a JSON-compatible dict.

        The returned dict is sent as-is over the WebSocket connection; the
        WebSocket layer is responsible for calling json.dumps on it.
        """
        return asdict(payload)

    def decode_ws_message(self, data: dict) -> WsMessage:
        """Decode a dict received from a WebSocket into a typed message."""
        message_type = data.get("message_type")

        try:
            if message_type == MessageType.SESSION_CREATE:
                validated = SessionCreateSchema(**data)
                return SessionCreate(
                    player_id=validated.player_id,
                    ip=validated.ip,
                    udp_port=validated.udp_port,
                )

            if message_type == MessageType.SESSION_CREATED:
                validated = SessionCreatedSchema(**data)
                return SessionCreated(
                    session_id=validated.session_id,
                    join_index=validated.join_index,
                )

            if message_type == MessageType.SESSION_JOIN:
                validated = SessionJoinSchema(**data)
                return SessionJoin(
                    session_id=validated.session_id,
                    player_id=validated.player_id,
                    ip=validated.ip,
                    port=validated.port,
                )

            if message_type == MessageType.SESSION_RECREATE:
                validated = SessionRecreateSchema(**data)
                return SessionRecreate(
                    session_id=validated.session_id,
                    next_join_index=validated.next_join_index,
                    host_ip=validated.host_ip,
                    host_udp_port=validated.host_udp_port,
                    host_join_index=validated.host_join_index,
                )

            if message_type == MessageType.SESSION_JOINED:
                validated = SessionJoinedSchema(**data)
                return SessionJoined(join_index=validated.join_index)

            if message_type == MessageType.ROSTER_UPDATE:
                validated = RosterUpdateSchema(**data)
                roster = GlobalRoster()
                for entry_data in validated.roster.players:
                    roster.add_player(
                        RosterEntry(
                            player_id=entry_data.player_id,
                            host=entry_data.host,
                            udp_port=entry_data.udp_port,
                            join_index=entry_data.join_index,
                            status=ConnectionStatus(entry_data.status),
                            is_host=entry_data.is_host,
                        )
                    )
                return RosterUpdate(roster=roster)

            if message_type == MessageType.GAME_START:
                validated = GameStartSchema(**data)
                return GameStart(session_id=validated.session_id)

            if message_type == MessageType.INITIAL_STATE_SYNC:
                validated = InitialStateSyncSchema(**data)
                world_state = self._decode_world_state(validated.world_state)
                return InitialStateSync(world_state=world_state)

            if message_type == MessageType.BLOCK_DESTROYED_MESSAGE:
                validated = BlockDestroyedMessageSchema(**data)
                return BlockDestroyedMessage(position=tuple(validated.position))

            if message_type == MessageType.POWERUP_COLLECTED_MESSAGE:
                validated = PowerUpCollectedMessageSchema(**data)
                return PowerUpCollectedMessage(
                    powerup_id=validated.powerup_id,
                    player_id=validated.player_id,
                )

            if message_type == MessageType.PLAYER_DEATH_MESSAGE:
                validated = PlayerDeathMessageSchema(**data)
                return PlayerDeathMessage(
                    player_id=validated.player_id,
                    enemy_id=validated.enemy_id,
                )

            if message_type == MessageType.GATE_STATE_CHANGED_MESSAGE:
                validated = GateStateChangedMessageSchema(**data)
                return GateStateChangedMessage(
                    gate_id=validated.gate_id,
                    new_state=validated.new_state,
                )

            if message_type == MessageType.PLAYER_LEFT:
                validated = PlayerLeftSchema(**data)
                return PlayerLeft(player_id=validated.player_id)

            if message_type == MessageType.PLAYER_DISCONNECTED:
                validated = PlayerDisconnectedSchema(**data)
                return PlayerDisconnected(player_id=validated.player_id)

            if message_type == MessageType.NEW_HOST_CLAIM:
                validated = NewHostClaimSchema(**data)
                return NewHostClaim(
                    claimer_ip=validated.claimer_ip,
                    claimer_join_index=validated.claimer_join_index,
                    session_id=validated.session_id,
                )

            if message_type == MessageType.ELECTION_ACK:
                validated = ElectionAckSchema(**data)
                return ElectionAck(from_ip=validated.from_ip, session_id=validated.session_id)

            if message_type == MessageType.ELECTION_NACK:
                validated = ElectionNackSchema(**data)
                return ElectionNack(
                    from_ip=validated.from_ip,
                    session_id=validated.session_id,
                    reason=validated.reason,
                )

            if message_type == MessageType.RECONNECTION_ACK:
                validated = ReconnectionAckSchema(**data)
                return ReconnectionAck(
                    new_host_ip=validated.new_host_ip,
                    udp_port=validated.udp_port,
                    game_events_port=validated.game_events_port,
                    session_id=validated.session_id,
                )

            raise DeserializationError(f"Unsupported WebSocket message type: {message_type}")

        except ValidationError as e:
            raise DeserializationError(
                f"Invalid WebSocket message format (type: {message_type}): {e.errors()}"
            )

    def _decode_world_state(self, data: dict[str, Any]) -> WorldState:
        """Rebuild a full world state, including environmental entities."""
        environment = data.get("environment", {})
        normalized_environment = {
            "destructible_blocks": environment.get("destructible_blocks", []),
            "power_ups": environment.get("power_ups", {}),
            "cooperative_gates": environment.get("cooperative_gates", {}),
            "enemies": environment.get("enemies", {}),
        }
        normalized_data = dict(data)
        normalized_data["environment"] = normalized_environment
        return WorldState.from_dict(normalized_data)
