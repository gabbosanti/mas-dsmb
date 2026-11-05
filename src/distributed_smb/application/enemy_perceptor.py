"""Application adapters between agent contracts and the game simulation."""

from dataclasses import dataclass
from math import hypot
from typing import TypeVar

from distributed_smb.agents.models import (
    GoombaPerception,
    ObstacleKind,
    ObstacleObservation,
    PlayerPerception,
    Position,
    SightDirection,
)
from distributed_smb.application.agent_integration import EnemyNotFoundError
from distributed_smb.domain.entity import Player
from distributed_smb.domain.game_engine import GameEngine

GOOMBA_SIGHT_DISTANCE = 200.0
OBSTACLE_SIGHT_DISTANCE = GOOMBA_SIGHT_DISTANCE
AGENT_QUEUE_CAPACITY = 64
QueueItem = TypeVar("QueueItem")


@dataclass(slots=True)
class EnemyPerceptor:
    """Build immutable observations without exposing mutable simulation objects."""

    engine: GameEngine
    sight_distance: float = GOOMBA_SIGHT_DISTANCE

    def __post_init__(self) -> None:
        if self.sight_distance < 0:
            raise ValueError("sight_distance must be non-negative")

    def observe(self, enemy_id: str) -> GoombaPerception:
        enemy = self.engine.world_state.environment.enemies.get(enemy_id)
        if enemy is None:
            raise EnemyNotFoundError(f"Enemy {enemy_id!r} does not exist")

        obstacles = self._obstacle_candidates()
        origin = Position(enemy.x + enemy.width / 2, enemy.y + enemy.height / 2)
        direction = SightDirection.LEFT if enemy.vx < 0 else SightDirection.RIGHT
        return GoombaPerception(
            goomba_id=enemy.enemy_id,
            position=Position(enemy.x, enemy.y),
            direction=direction,
            visible_players=tuple(
                self._perceive_player(player)
                for player in sorted(
                    self.engine.world_state.characters.values(),
                    key=lambda visible_player: visible_player.player_id,
                )
                if self._can_see_player(player, origin, direction, obstacles)
            ),
            visible_obstacles=self._visible_obstacles(origin, direction, obstacles),
        )

    def _obstacle_candidates(self) -> tuple[ObstacleObservation, ...]:
        return tuple(
            ObstacleObservation(
                kind=ObstacleKind.PLATFORM,
                x=platform.x,
                y=platform.y,
                width=platform.width,
                height=platform.height,
            )
            for platform in self.engine.platforms
        ) + tuple(
            ObstacleObservation(
                kind=ObstacleKind.BLOCK,
                x=block.x,
                y=block.y,
                width=block.width,
                height=block.height,
            )
            for block in self.engine.world_state.environment.destructible_blocks
            if not block.destroyed
        )

    def _can_see_player(
        self,
        player: Player,
        origin: Position,
        direction: SightDirection,
        obstacles: tuple[ObstacleObservation, ...],
    ) -> bool:
        target = Position(player.x + player.width / 2, player.y + player.height / 2)
        return self._is_in_sight(origin, target, direction) and self._has_clear_line(
            origin, target, obstacles
        )

    @staticmethod
    def _perceive_player(player: Player) -> PlayerPerception:
        return PlayerPerception(
            player_id=player.player_id,
            position=Position(player.x, player.y),
            width=player.width,
            height=player.height,
        )

    def _visible_obstacles(
        self,
        origin: Position,
        direction: SightDirection,
        obstacles: tuple[ObstacleObservation, ...],
    ) -> tuple[ObstacleObservation, ...]:
        return tuple(
            obstacle
            for index, obstacle in enumerate(obstacles)
            if self._is_in_sight(origin, self._center_of(obstacle), direction)
            and self._has_clear_line(
                origin, self._center_of(obstacle), obstacles, index
            )
        )

    def _is_in_sight(
        self, origin: Position, target: Position, direction: SightDirection
    ) -> bool:
        delta_x = target.x - origin.x
        is_forward = delta_x <= 0 if direction is SightDirection.LEFT else delta_x >= 0
        return is_forward and hypot(delta_x, target.y - origin.y) <= self.sight_distance

    @staticmethod
    def _center_of(obstacle: ObstacleObservation) -> Position:
        return Position(
            obstacle.x + obstacle.width / 2,
            obstacle.y + obstacle.height / 2,
        )

    @staticmethod
    def _has_clear_line(
        origin: Position,
        target: Position,
        obstacles: tuple[ObstacleObservation, ...],
        ignored_obstacle_index: int | None = None,
    ) -> bool:
        for index, obstacle in enumerate(obstacles):
            if index == ignored_obstacle_index:
                continue
            intersection = EnemyPerceptor._segment_rectangle_intersection(
                origin,
                target,
                obstacle.x,
                obstacle.y,
                obstacle.x + obstacle.width,
                obstacle.y + obstacle.height,
            )
            if intersection is not None:
                entry, exit = intersection
                if entry < 1.0 - 1e-9 and exit > 1e-9:
                    return False
        return True

    @staticmethod
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