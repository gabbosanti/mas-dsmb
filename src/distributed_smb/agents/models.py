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
class EnemyPerception:
    """Raw, immutable snapshot of the world sent to an enemy agent.

    It carries facts only: what the enemy can actually see, or which player
    is closest, is decided agent-side (see ``distributed_smb.agents.perception``).
    Each enemy kind has its own subclass, so a perception can never reach an
    agent of the wrong kind.
    """

    enemy_id: str
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
class GoombaPerception(EnemyPerception):
    """Snapshot for a Goomba: a walker on the ground."""


@dataclass(frozen=True, slots=True)
class KoopaPerception(EnemyPerception):
    """Snapshot for a Koopa (Lakitu): a flyer, not bound to the ground."""


@dataclass(frozen=True, slots=True)
class EnemyPerceptionMessage:
    """Queue envelope that ties a perception to its simulation tick."""

    simulation_sequence: int
    perception: EnemyPerception


#---------------------------------------------------------------------------

class EnemyAction(str, Enum):
    TURN_AROUND = "TURN_AROUND"
    MOVE_LEFT = "MOVE_LEFT"
    MOVE_RIGHT = "MOVE_RIGHT"
    STOP = "STOP"
    JUMP = "JUMP"
    DIVE = "DIVE"  # flying enemies: straight down
    RISE = "RISE"  # flying enemies: straight up


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Outcome of an agent's deliberation, put on the action queue by SPADE."""

    enemy_id: str
    action: EnemyAction
