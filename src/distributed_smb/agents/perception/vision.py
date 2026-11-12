"""Agent-side sight: decide what an observer sees in a raw world snapshot.

This module has no game-engine dependency and works for any observer that
has a position (the point it looks from) and, optionally, a direction, so
Goombas, Koopas and autonomous players can all share it.
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

    # players: tuple[PlayerPerception, ...] = ()
    obstacles: tuple[ObstacleObservation, ...] = ()


@dataclass(frozen=True, slots=True)
class Vision:
    """Field of view limited by distance, direction and occlusion.

    ``position`` is the point the observer looks from (usually its center);
    ``direction=None`` means the observer looks in every direction.
    """

    sight_distance: float = DEFAULT_SIGHT_DISTANCE

    def __post_init__(self) -> None:
        if self.sight_distance < 0:
            raise ValueError("sight_distance must be non-negative")

    def obstacle_ahead(
        self,
        position: Position,
        direction: SightDirection | None,
        obstacles: Iterable[ObstacleObservation],
    ) -> bool:
        """True if an obstacle at the observer's height is within sight ahead.

        The distance is measured to the obstacle's nearest face, not to its
        center: a wide pipe and a thin block are noticed at the same gap.
        """
        obstacles = tuple(obstacles)
        for index, obstacle in enumerate(obstacles):
            # Ground below the observer supports movement but is not a
            # forward obstacle for the patrol plan to turn around from.
            if not obstacle.y <= position.y <= obstacle.y + obstacle.height:
                continue
            if self.can_see(
                position,
                direction,
                _nearest_face(obstacle, position),
                obstacles,
                ignored_index=index,
            ):
                return True
        return False

    def player_side(
        self,
        position: Position,
        players: Iterable[PlayerPerception],
        obstacles: Iterable[ObstacleObservation],
        *,
        vertical_tolerance: float,
        deadband: float = 0.0,
    ) -> SightDirection | None:
        """Side of the nearest player at the observer's height, if any.

        A player counts when its center is within ``vertical_tolerance`` of
        the observer's on the y axis (same height), within ``sight_distance``
        on the x axis and not hidden behind an obstacle. The observer's
        facing direction is ignored on purpose: a player behind it is noticed
        too, so that it can turn around. Players closer than ``deadband`` on
        the x axis are ignored, so that the side cannot flicker while the
        two bodies overlap.
        """
        obstacles = tuple(obstacles)
        candidates = [
            player
            for player in players
            if abs(player.center.y - position.y) <= vertical_tolerance
            and deadband < abs(player.center.x - position.x) <= self.sight_distance
            and has_clear_line(position, player.center, obstacles)
        ]
        if not candidates:
            return None
        nearest = min(
            candidates,
            key=lambda player: (abs(player.center.x - position.x), player.player_id),
        )
        if nearest.center.x < position.x:
            return SightDirection.LEFT
        return SightDirection.RIGHT

    def can_see(
        self,
        position: Position,
        direction: SightDirection | None,
        target: Position,
        obstacles: tuple[ObstacleObservation, ...],
        *,
        ignored_index: int | None = None,
    ) -> bool:
        # True if target is ahead within range (is_in_range) and not occluded (has_clear_line)
        return self.is_in_range(position, direction, target) and has_clear_line(
            position, target, obstacles, ignored_index=ignored_index
        )

    def is_in_range(
        self, position: Position, direction: SightDirection | None, target: Position
    ) -> bool:
        delta_x = target.x - position.x
        if direction is SightDirection.LEFT and delta_x > 0:
            return False
        if direction is SightDirection.RIGHT and delta_x < 0:
            return False
        return hypot(delta_x, target.y - position.y) <= self.sight_distance


def has_clear_line(
    start: Position,
    end: Position,
    obstacles: tuple[ObstacleObservation, ...],
    *,
    ignored_index: int | None = None,
) -> bool:
    # True if the segment start→end crosses no obstacle's interior.
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


def _nearest_face(obstacle: ObstacleObservation, position: Position) -> Position:
    """Point of the obstacle's vertical face closest to ``position``.

    If the observer already overlaps the obstacle horizontally, the obstacle's
    center is used, so the obstacle is "ahead" only on the side it extends to.
    """
    left, right = obstacle.x, obstacle.x + obstacle.width
    if position.x <= left:
        return Position(left, position.y)
    if position.x >= right:
        return Position(right, position.y)
    return obstacle.center


def _segment_rectangle_intersection(
    start: Position,
    end: Position,
    left: float,
    top: float,
    right: float,
    bottom: float,
) -> tuple[float, float] | None:
    ##Return the segment parameters inside an axis-aligned rectangle.
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
