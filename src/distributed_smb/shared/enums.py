"""Shared enumerations used across the project."""

from enum import StrEnum


class PlayerRole(StrEnum):
    """Possible runtime roles for a node/player."""

    HOST = "host"
    CLIENT = "client"


class SessionPhase(StrEnum):
    """Session lifecycle states."""

    WAITING = "waiting"
    PLAYING = "playing"
    ENDED = "ended"


class NodeState(StrEnum):
    """Local node lifecycle states."""

    IDLE = "idle"
    IN_LOBBY = "in_lobby"
    IN_GAME = "in_game"
    ELECTION = "election"
    RECOVERING = "recovering"


class ConnectionStatus(StrEnum):
    """Connectivity states for a remote peer."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


class MessageType(StrEnum):
    """Message types exchanged over UDP (gameplay) and WebSocket (coordination)."""

    # UDP — high-frequency gameplay
    PLAYER_INPUT = "player_input"
    WORLD_STATE = "world_state"

    # UDP — host discovery / rejoin (M9)
    HOST_DISCOVERY_PROBE = "host_discovery_probe"
    HOST_IDENTITY_RESPONSE = "host_identity_response"

    # WebSocket — lobby coordination (client → lobby)
    SESSION_CREATE = "session_create"
    SESSION_JOIN = "session_join"
    SESSION_RECREATE = "session_recreate"
    GAME_START = "game_start"
    INITIAL_STATE_SYNC = "initial_state_sync"

    # WebSocket — lobby coordination (lobby → client)
    SESSION_CREATED = "session_created"
    SESSION_JOINED = "session_joined"
    ROSTER_UPDATE = "roster_update"

    # WebSocket — election and host migration
    NEW_HOST_CLAIM = "new_host_claim"
    ELECTION_ACK = "election_ack"
    ELECTION_NACK = "election_nack"
    RECONNECTION_ACK = "reconnection_ack"

    # Event messages
    BLOCK_DESTROYED_MESSAGE = "block_destroyed_message"
    POWERUP_COLLECTED_MESSAGE = "powerup_collected_message"
    GATE_STATE_CHANGED_MESSAGE = "gate_state_changed_message"
    PLAYER_LEFT = "player_left"
    PLAYER_DISCONNECTED = "player_disconnected"
    PLAYER_DEATH_MESSAGE = "player_death_message"
    LEVEL_RESET_MESSAGE = "level_reset_message"
