"""Stable, engine-independent observation and action contracts for agents."""

from dataclasses import dataclass
from enum import Enum


# Top-left world coordinates in pixels.
@dataclass(frozen=True, slots=True)
class Position:
    x: float
    y: float


class SightDirection(str, Enum):
    LEFT = "left"
    RIGHT = "right"


@dataclass(frozen=True, slots=True)
class ObstacleObservation:
    """A solid rectangle: it blocks sight and can be walked on."""

    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> Position:
        return Position(self.x + self.width / 2, self.y + self.height / 2)


@dataclass(frozen=True, slots=True)
class PlayerPerception:
    player_id: str
    position: Position
    width: float
    height: float

    @property
    def center(self) -> Position:
        return Position(
            self.position.x + self.width / 2, self.position.y + self.height / 2
        )


@dataclass(frozen=True, slots=True)
class GoombaPerception:
    """Raw, immutable snapshot of the world sent to a Goomba agent.

    It carries facts only: what the Goomba can actually see, or which player
    is closest, is decided agent-side (see ``distributed_smb.agents.perception``).
    """

    goomba_id: str
    position: Position
    width: float
    height: float
    direction: SightDirection
    players: tuple[PlayerPerception, ...] = ()
    obstacles: tuple[ObstacleObservation, ...] = ()

    @property
    def center(self) -> Position:
        return Position(
            self.position.x + self.width / 2, self.position.y + self.height / 2
        )


@dataclass(frozen=True, slots=True)
class GoombaPerceptionMessage:
    """Queue envelope that ties a perception to its simulation tick."""

    simulation_sequence: int
    perception: GoombaPerception


@dataclass(frozen=True, slots=True)
class PlayerPerceptionMessage:
    """Queue envelope for a dummy player's snapshot."""

    simulation_sequence: int
    perception: PlayerPerception


class PlayerAction(str, Enum):
    MOVE_LEFT = "MOVE_LEFT"
    MOVE_RIGHT = "MOVE_RIGHT"
    JUMP = "JUMP"
    CROUCH = "CROUCH"
    STOP = "STOP"


@dataclass(frozen=True, slots=True)
class PlayerActionResult:
    """Random action selected by a dummy SPADE player agent."""

    player_id: str
    action: PlayerAction


class EnemyAction(str, Enum):
    TURN_AROUND = "TURN_AROUND"
    MOVE_LEFT = "MOVE_LEFT"
    MOVE_RIGHT = "MOVE_RIGHT"
    STOP = "STOP"
    JUMP = "JUMP"


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Outcome of an agent's deliberation, put on the action queue by SPADE."""

    enemy_id: str
    action: EnemyAction
