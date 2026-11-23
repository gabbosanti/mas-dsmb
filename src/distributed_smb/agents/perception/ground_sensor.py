"""
Agent-side ground sensing: is there something to walk on just ahead?

Like vision, this module has no game-engine dependency: it works on the
raw obstacles of a perception, so any walking agent can reuse it.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass

from distributed_smb.agents.models import ObstacleObservation, Position, SightDirection

DEFAULT_LOOKAHEAD = 16.0
DEFAULT_GROUND_TOLERANCE = 4.0
# Distance between two floor samples: narrower than any pit of the level.
_PROBE_STEP = 8.0


@dataclass(frozen=True, slots=True)
class GroundSensor:
    ##Probe the floor a few pixels beyond the walker's leading edge.

    """
    lookahead is how far ahead of the feet the probe is placed: it should
    cover the distance travelled while the agent decides, otherwise the
    turn arrives late."""

    lookahead: float = DEFAULT_LOOKAHEAD

    """
    tolerance absorbs small misalignments between the
    walker's feet and the top of the surface it stands on.
    """
    tolerance: float = DEFAULT_GROUND_TOLERANCE

    def __post_init__(self) -> None:
        if self.lookahead < 0:
            raise ValueError("lookahead must be non-negative")
        if self.tolerance < 0:
            raise ValueError("tolerance must be non-negative")

    def is_void_ahead(
        self,
        position: Position,  # position is the walker's top-left corner, as in the perceptions.
        width: float,
        height: float,
        direction: SightDirection,
        obstacles: Iterable[ObstacleObservation],
    ) -> bool:
        """True if the floor is missing anywhere just ahead of the feet.

        The whole stretch from the leading edge to ``lookahead`` px beyond it
        is sampled, not only its far end: a probe that lands on the other side
        of a pit would otherwise hide the pit itself (e.g. a walker that lands
        with its leading edge already past the edge of a platform).
        """
        obstacles = tuple(obstacles)
        far = self.probe_point(position, width, height, direction)
        near_x = position.x if direction is SightDirection.LEFT else position.x + width
        steps = max(1, math.ceil(abs(far.x - near_x) / _PROBE_STEP))
        for i in range(steps + 1):
            probe = Position(near_x + (far.x - near_x) * i / steps, far.y)
            if not any(self._supports(obstacle, probe) for obstacle in obstacles):
                return True
        return False

    def wall_ahead(
        self,
        position: Position,  # top-left corner, as in the perceptions
        width: float,
        height: float,
        direction: SightDirection,
        obstacles: Iterable[ObstacleObservation],
        distance: float,
    ) -> bool:
        """True if an obstacle blocks the body within ``distance`` px ahead.

        Unlike ``Vision.obstacle_ahead`` (which looks at the eye's height), any
        overlap with the body counts, so a low step is a wall too; the surface
        the walker stands on is not one.
        """
        head, feet = position.y, position.y + height
        for obstacle in obstacles:
            if not (obstacle.y < feet - self.tolerance and obstacle.y + obstacle.height > head):
                continue
            if direction is SightDirection.RIGHT:
                gap = obstacle.x - (position.x + width)
            else:
                gap = position.x - (obstacle.x + obstacle.width)
            if -self.tolerance <= gap <= distance:
                return True
        return False

    def clearance_below(
        self,
        position: Position,  # top-left corner, as in the perceptions
        width: float,
        height: float,
        obstacles: Iterable[ObstacleObservation],
    ) -> float | None:
        """Free space between the body's bottom and the first surface under it.

        Only surfaces horizontally under the body count. None when there is
        nothing under it (e.g. a flyer above a pit).
        """
        bottom = position.y + height
        gaps = [
            obstacle.y - bottom
            for obstacle in obstacles
            if obstacle.x < position.x + width
            and position.x < obstacle.x + obstacle.width
            and obstacle.y >= bottom - self.tolerance
        ]
        return min(gaps, default=None)

    def probe_point(
        self,
        position: Position,
        width: float,
        height: float,
        direction: SightDirection,
    ) -> Position:
        # Point at feet level, "lookahead" pixels past the leading edge.
        feet_y = position.y + height
        if direction is SightDirection.LEFT:
            return Position(position.x - self.lookahead, feet_y)
        return Position(position.x + width + self.lookahead, feet_y)

    def _supports(self, obstacle: ObstacleObservation, probe: Position) -> bool:
        # "True if the obstacle's top surface is under probe
        return (
            obstacle.x <= probe.x <= obstacle.x + obstacle.width
            and abs(obstacle.y - probe.y) <= self.tolerance
        )
