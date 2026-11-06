"""Agent-side sight: decide what an observer sees in a raw world snapshot.

This module has no game-engine dependency and works for any observer that
has an eye position and, optionally, a facing direction, so Goombas,
Koopas and autonomous players can all share it.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from math import hypot

from distributed_smb.agents.models import (
    ObstacleObservation,
    PlayerPerception,
    Position,
    SightDirection,
)

DEFAULT_SIGHT_DISTANCE = 200.0
_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class VisualField:
    """What an observer sees at a given instant."""

    players: tuple[PlayerPerception, ...] = ()
    obstacles: tuple[ObstacleObservation, ...] = ()


@dataclass(frozen=True, slots=True)
class Vision:
    """Field of view limited by distance, facing direction and occlusion.
    ``facing=None`` means the observer looks in every direction.
    """

    sight_distance: float = DEFAULT_SIGHT_DISTANCE

    def __post_init__(self) -> None:
        if self.sight_distance < 0:
            raise ValueError("sight_distance must be non-negative")

    def look(
        self,
        eye: Position,
        facing: SightDirection | None,
        players: Iterable[PlayerPerception],
        obstacles: Iterable[ObstacleObservation],
    ) -> VisualField:
        obstacles = tuple(obstacles)
        return VisualField(
            players=self.visible_players(eye, facing, players, obstacles),
            obstacles=self.visible_obstacles(eye, facing, obstacles),
        )

    def visible_players(
        self,
        eye: Position,
        facing: SightDirection | None,
        players: Iterable[PlayerPerception],
        obstacles: Iterable[ObstacleObservation],
    ) -> tuple[PlayerPerception, ...]:
        obstacles = tuple(obstacles)
        return tuple(
            player
            for player in players
            if self.can_see(eye, facing, player.center, obstacles)
        )

    def visible_obstacles(
        self,
        eye: Position,
        facing: SightDirection | None,
        obstacles: Iterable[ObstacleObservation],
    ) -> tuple[ObstacleObservation, ...]:
        obstacles = tuple(obstacles)
        return tuple(
            obstacle
            for index, obstacle in enumerate(obstacles)
            if self.can_see(eye, facing, obstacle.center, obstacles, ignored_index=index)
        )

    def can_see(
        self,
        eye: Position,
        facing: SightDirection | None,
        target: Position,
        obstacles: tuple[ObstacleObservation, ...],
        *,
        ignored_index: int | None = None,
    ) -> bool:
        """True if ``target`` is ahead, within range and not occluded."""
        return self.is_in_range(eye, facing, target) and has_clear_line(
            eye, target, obstacles, ignored_index=ignored_index
        )

    def is_in_range(
        self, eye: Position, facing: SightDirection | None, target: Position
    ) -> bool:
        delta_x = target.x - eye.x
        if facing is SightDirection.LEFT and delta_x > 0:
            return False
        if facing is SightDirection.RIGHT and delta_x < 0:
            return False
        return hypot(delta_x, target.y - eye.y) <= self.sight_distance


def nearest_player(
    eye: Position, players: Iterable[PlayerPerception]
) -> PlayerPerception | None:
    """Closest player to ``eye``; ties are broken by player id."""
    return min(
        players,
        key=lambda player: (
            hypot(player.center.x - eye.x, player.center.y - eye.y),
            player.player_id,
        ),
        default=None,
    )


def has_clear_line(
    start: Position,
    end: Position,
    obstacles: tuple[ObstacleObservation, ...],
    *,
    ignored_index: int | None = None,
) -> bool:
    """True if the segment start→end crosses no obstacle's interior."""
    for index, obstacle in enumerate(obstacles):
        if index == ignored_index:
            continue
        intersection = _segment_rectangle_intersection(
            start,
            end,
            obstacle.x,
            obstacle.y,
            obstacle.x + obstacle.width,
            obstacle.y + obstacle.height,
        )
        if intersection is not None:
            entry, exit = intersection
            if entry < 1.0 - _EPSILON and exit > _EPSILON:
                return False
    return True


def _segment_rectangle_intersection(
    start: Position,
    end: Position,
    left: float,
    top: float,
    right: float,
    bottom: float,
) -> tuple[float, float] | None:
    """Return the segment parameters inside an axis-aligned rectangle."""
    delta_x = end.x - start.x
    delta_y = end.y - start.y
    entry, exit = 0.0, 1.0
    for origin, delta, minimum, maximum in (
        (start.x, delta_x, left, right),
        (start.y, delta_y, top, bottom),
    ):
        if delta == 0:
            if origin < minimum or origin > maximum:
                return None
            continue
        first = (minimum - origin) / delta
        second = (maximum - origin) / delta
        if first > second:
            first, second = second, first
        entry = max(entry, first)
        exit = min(exit, second)
        if entry > exit:
            return None
    return entry, exit
