from dataclasses import dataclass, field

from distributed_smb.shared.enums import MessageType
from distributed_smb.shared.input import InputState
from distributed_smb.shared.messages.common import MessageValidationError, validate_player_id


@dataclass(slots=True)
class PlayerInputPacket:
    """Represents an input packet sent by a client to the host."""

    player_id: str
    sequence_number: int
    input_state: InputState = field(default_factory=InputState)
    message_type: MessageType = field(init=False, default=MessageType.PLAYER_INPUT)

    def __post_init__(self):
        validate_player_id(self.player_id)


@dataclass(slots=True)
class BlockDestroyedMessage:
    position: tuple[int, int]
    message_type: MessageType = field(init=False, default=MessageType.BLOCK_DESTROYED_MESSAGE)

    def __post_init__(self):
        if (
            not isinstance(self.position, tuple)
            or len(self.position) != 2
            or not all(isinstance(coord, int) for coord in self.position)
        ):
            raise MessageValidationError(f"Invalid position: {self.position}")


@dataclass(slots=True)
class PowerUpCollectedMessage:
    powerup_id: str
    player_id: str
    message_type: MessageType = field(init=False, default=MessageType.POWERUP_COLLECTED_MESSAGE)

    def __post_init__(self):
        if not self.powerup_id or not isinstance(self.powerup_id, str):
            raise MessageValidationError(f"Invalid powerup_id: {self.powerup_id}")
        validate_player_id(self.player_id)


@dataclass(slots=True)
class GateStateChangedMessage:
    gate_id: str
    new_state: str
    message_type: MessageType = field(init=False, default=MessageType.GATE_STATE_CHANGED_MESSAGE)

    def __post_init__(self):
        if not self.gate_id or not isinstance(self.gate_id, str):
            raise MessageValidationError(f"Invalid gate_id: {self.gate_id}")
        if not self.new_state or not isinstance(self.new_state, str):
            raise MessageValidationError(f"Invalid new_state: {self.new_state}")


@dataclass(slots=True)
class PlayerLeft:
    player_id: str
    message_type: MessageType = field(init=False, default=MessageType.PLAYER_LEFT)

    def __post_init__(self):
        validate_player_id(self.player_id)


@dataclass(slots=True)
class PlayerDisconnected:
    player_id: str
    message_type: MessageType = field(init=False, default=MessageType.PLAYER_DISCONNECTED)

    def __post_init__(self):
        validate_player_id(self.player_id)


@dataclass(slots=True)
class PlayerDeathMessage:
    player_id: str
    enemy_id: str
    message_type: MessageType = field(init=False, default=MessageType.PLAYER_DEATH_MESSAGE)

    def __post_init__(self):
        validate_player_id(self.player_id)
        if not self.enemy_id or not isinstance(self.enemy_id, str):
            raise MessageValidationError(f"Invalid enemy_id: {self.enemy_id}")
