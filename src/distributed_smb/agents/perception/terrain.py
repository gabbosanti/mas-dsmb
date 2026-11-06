"""Agent-side ground sensing: is there something to walk on just ahead?

Like ``vision``, this module has no game-engine dependency: it works on the
raw obstacles of a perception, so any walking agent can reuse it.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from distributed_smb.agents.models import ObstacleObservation, Position, SightDirection

DEFAULT_LOOKAHEAD = 16.0
DEFAULT_GROUND_TOLERANCE = 4.0


@dataclass(frozen=True, slots=True)
class GroundSensor:
    """Probe the floor a few pixels beyond the walker's leading edge.

    ``lookahead`` is how far ahead of the feet the probe is placed: it should
    cover the distance travelled while the agent decides, otherwise the
    turn arrives late. ``tolerance`` absorbs small misalignments between the
    walker's feet and the top of the surface it stands on.
    """

    lookahead: float = DEFAULT_LOOKAHEAD
    tolerance: float = DEFAULT_GROUND_TOLERANCE

    def __post_init__(self) -> None:
        if self.lookahead < 0:
            raise ValueError("lookahead must be non-negative")
        if self.tolerance < 0:
            raise ValueError("tolerance must be non-negative")

    def is_void_ahead(
        self,
        position: Position,
        width: float,
        height: float,
        direction: SightDirection,
        obstacles: Iterable[ObstacleObservation],
    ) -> bool:
        """True if no walkable surface lies just ahead of the walker's feet.

        ``position`` is the walker's top-left corner, as in the perceptions.
        """
        probe = self.probe_point(position, width, height, direction)
        return not any(self._supports(obstacle, probe) for obstacle in obstacles)

    def probe_point(
        self,
        position: Position,
        width: float,
        height: float,
        direction: SightDirection,
    ) -> Position:
        """Point at feet level, ``lookahead`` pixels past the leading edge."""
        feet_y = position.y + height
        if direction is SightDirection.LEFT:
            return Position(position.x - self.lookahead, feet_y)
        return Position(position.x + width + self.lookahead, feet_y)

    def _supports(self, obstacle: ObstacleObservation, probe: Position) -> bool:
        """True if the obstacle's top surface is under ``probe``."""
        return (
            obstacle.x <= probe.x <= obstacle.x + obstacle.width
            and abs(obstacle.y - probe.y) <= self.tolerance
        )
