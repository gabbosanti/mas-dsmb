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

    def look(
        self,
        position: Position,
        direction: SightDirection | None,
        players: Iterable[PlayerPerception],
        obstacles: Iterable[ObstacleObservation],
    ) -> VisualField:
        obstacles = tuple(obstacles)
        return VisualField(
            # players=self.visible_players(position, direction, players, obstacles),
            obstacles=self.visible_obstacles(position, direction, obstacles),
        )

    def visible_players(
        self,
        position: Position,
        direction: SightDirection | None,
        players: Iterable[PlayerPerception],
        obstacles: Iterable[ObstacleObservation],
    ) -> tuple[PlayerPerception, ...]:
        obstacles = tuple(obstacles)
        return tuple(
            player
            for player in players
            if self.can_see(position, direction, player.center, obstacles)
        )

    def visible_obstacles(
        self,
        position: Position,
        direction: SightDirection | None,
        obstacles: Iterable[ObstacleObservation],
    ) -> tuple[ObstacleObservation, ...]:
        obstacles = tuple(obstacles)
        return tuple(
            obstacle
            for index, obstacle in enumerate(obstacles)
            if self.can_see(
                position, direction, obstacle.center, obstacles, ignored_index=index
            )
        )

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


# Not used in Milestone 1
def nearest_player(
    position: Position, players: Iterable[PlayerPerception]
) -> PlayerPerception | None:
    # Return the closest player to position ; ties are broken by player id.
    return min(
        players,
        key=lambda player: (
            hypot(player.center.x - position.x, player.center.y - position.y),
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
