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


@dataclass(frozen=True, slots=True)
class ObstacleObservation:
    kind: ObstacleKind
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class EnemyObservation:
    """Read-only view of one enemy and its nearest player target."""

    enemy_id: str
    enemy_position: Position
    target_player_id: str | None
    mario_position: Position | None
    nearby_obstacles: tuple[ObstacleObservation, ...] = ()


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
