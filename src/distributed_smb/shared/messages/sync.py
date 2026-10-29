from dataclasses import dataclass, field

from distributed_smb.shared.enums import MessageType


@dataclass(slots=True)
class WorldStateSnapshot:
    """Represents a world snapshot sent by the authoritative host."""

    sequence_number: int
    world_state: dict
    message_type: MessageType = field(init=False, default=MessageType.WORLD_STATE)


@dataclass(slots=True)
class InitialStateSync:
    world_state: dict
    message_type: MessageType = field(
        init=False, default=MessageType.INITIAL_STATE_SYNC
    )
