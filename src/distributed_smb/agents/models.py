"""Stable, engine-independent observation and action contracts for agents."""

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True, slots=True)
class Position:
    """Top-left world coordinates in pixels."""

    x: float
    y: float


class ObstacleKind(str, Enum):
    PLATFORM = "platform"
    BLOCK = "block"


class SightDirection(str, Enum):
    LEFT = "left"
    RIGHT = "right"


@dataclass(frozen=True, slots=True)
class ObstacleObservation:
    kind: ObstacleKind
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class PlayerPerception:
    player_id: str
    position: Position
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class GoombaPerception:
    """Immutable visual perception assembled for a Goomba agent."""

    goomba_id: str
    position: Position
    direction: SightDirection
    visible_players: tuple[PlayerPerception, ...] = ()
    visible_obstacles: tuple[ObstacleObservation, ...] = ()

    @property
    def enemy_id(self) -> str:
        """Compatibility name used by the existing controller contract."""
        return self.goomba_id

    @property
    def enemy_position(self) -> Position:
        """Compatibility name used by the existing controller contract."""
        return self.position

    @property
    def target_player_id(self) -> str | None:
        target = self._nearest_visible_player()
        return target.player_id if target is not None else None

    @property
    def mario_position(self) -> Position | None:
        """Return the nearest visible player's position for older controllers."""
        target = self._nearest_visible_player()
        return target.position if target is not None else None

    @property
    def nearby_obstacles(self) -> tuple[ObstacleObservation, ...]:
        """Compatibility name; these obstacles are now filtered by line of sight."""
        return self.visible_obstacles

    def _nearest_visible_player(self) -> PlayerPerception | None:
        return min(
            self.visible_players,
            key=lambda player: (
                (player.position.x - self.position.x) ** 2
                + (player.position.y - self.position.y) ** 2,
                player.player_id,
            ),
            default=None,
        )


# Existing action-controller clients can continue using the general name.
EnemyObservation = GoombaPerception


class EnemyAction(str, Enum):
    MOVE_LEFT = "MOVE_LEFT"
    MOVE_RIGHT = "MOVE_RIGHT"
    STOP = "STOP"
    JUMP = "JUMP"


class MovementCommand(str, Enum):
    """The horizontal commands currently accepted by the game engine."""

    MOVE_LEFT = "MOVE_LEFT"
    MOVE_RIGHT = "MOVE_RIGHT"
    STOP = "STOP"


@dataclass(frozen=True, slots=True)
class ActionRequest:
    enemy_id: str
    action: EnemyAction


class ActionStatus(str, Enum):
    APPLIED = "applied"
    UNSUPPORTED = "unsupported"
    INVALID_ACTION = "invalid_action"
    INVALID_TARGET = "invalid_target"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class ActionResult:
    enemy_id: str
    requested_action: EnemyAction | None
    status: ActionStatus
    engine_command: MovementCommand | None = None
    position_before: Position | None = None
    position_after: Position | None = None
    message: str = ""
