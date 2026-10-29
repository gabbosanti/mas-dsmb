from dataclasses import dataclass, field

from distributed_smb.domain.events import (
    BlockDestroyedEvent,
    GateStateChangedEvent,
    PowerUpCollectedEvent,
)
from distributed_smb.shared.config import (
    ENEMY_HEIGHT,
    ENEMY_WIDTH,
    PLAYER_HEIGHT,
    PLAYER_WIDTH,
)


@dataclass(slots=True)
class Platform:
    x: int
    y: int
    width: int
    height: int


@dataclass(slots=True)
class DestructibleBlock:
    x: int
    y: int
    width: int = 32
    height: int = 32
    destroyed: bool = False

    def destroy(self) -> BlockDestroyedEvent:
        if self.destroyed:
            raise ValueError(f"Block at {self.x}, {self.y} is already destroyed")
        self.destroyed = True
        return BlockDestroyedEvent(position=(self.x, self.y))


@dataclass(slots=True)
class ExclusivePowerUp:
    x: int
    y: int
    powerup_id: str
    width: int = 32
    height: int = 32
    collected: bool = False
    owner: str | None = None

    def collect(self, player_id: str) -> PowerUpCollectedEvent:
        if self.collected:
            raise ValueError(
                f"Power-up {self.powerup_id} is already collected by {self.owner}"
            )

        self.collected = True
        self.owner = player_id
        return PowerUpCollectedEvent(powerup_id=self.powerup_id, player_id=player_id)


@dataclass(slots=True)
class CooperativeGate:
    x: int
    y: int
    gate_id: str
    width: int = 32
    height: int = 32
    state: str = "closed"
    contributions: set[str] = field(default_factory=set)
    coins_required: int = 0
    blocks_required: int = 0
    enemies_required: int = 0
    # Only a final gate triggers victory when open and touched. Non-final
    # gates (checkpoints) just unblock the path once the team meets their
    # (lower) requirement — reusing the same shared, cumulative counters.
    is_final: bool = True

    def update_state(self, should_be_open: bool) -> GateStateChangedEvent | None:
        new_state = "open" if should_be_open else "closed"

        if new_state == self.state:
            return None

        self.state = new_state
        return GateStateChangedEvent(gate_id=self.gate_id, new_state=self.state)


@dataclass(slots=True)
class Enemy:
    enemy_id: str
    x: float
    y: float
    width: int = ENEMY_WIDTH
    height: int = ENEMY_HEIGHT
    vx: float = 50.0
    left_bound: float = 0.0
    right_bound: float = 0.0
    movement_speed: float | None = None

    def __post_init__(self) -> None:
        if self.movement_speed is None:
            self.movement_speed = abs(self.vx)


@dataclass(slots=True)
class Player:
    """A player character: what every node simulates and reconciles."""

    player_id: str
    x: float = 0.0
    y: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    width: int = PLAYER_WIDTH
    height: int = PLAYER_HEIGHT
    on_ground: bool = False
    is_crouching: bool = False
    prev_x: float = 0.0
    prev_y: float = 0.0
    join_index: int = 0
    powerup_effect_expires_at: float | None = None
