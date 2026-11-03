"""Application adapters between agent contracts and the game simulation."""

from dataclasses import dataclass, field
from math import hypot

from distributed_smb.agents.controller import EnemyController
from distributed_smb.agents.models import (
    ActionRequest,
    ActionResult,
    ActionStatus,
    EnemyAction,
    GoombaPerception,
    MovementCommand,
    ObstacleKind,
    ObstacleObservation,
    PlayerPerception,
    Position,
    SightDirection,
)
from distributed_smb.domain.entity import Player
from distributed_smb.domain.game_engine import EnemyMovementCommand, GameEngine
from distributed_smb.shared.input import InputState

GOOMBA_SIGHT_DISTANCE = 200.0
OBSTACLE_SIGHT_DISTANCE = GOOMBA_SIGHT_DISTANCE


class EnemyNotFoundError(LookupError):
    """Raised when an observation is requested for an unknown enemy."""


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


@dataclass(slots=True)
class AgentGameIntegration:
    """Selectively runs agent controllers in the authoritative simulation tick.

    Each decision is consumed by exactly one engine tick. Disabled or
    unselected enemies continue using the engine's original patrol behavior.
    """

    engine: GameEngine
    enabled: bool = False
    controllers: dict[str, EnemyController | EnemyAction] = field(default_factory=dict)
    last_results: dict[str, ActionResult] = field(default_factory=dict, init=False)
    perceptor: EnemyPerceptor = field(init=False)

    def __post_init__(self) -> None:
        self.perceptor = EnemyPerceptor(self.engine)

    def control_enemy(
        self, enemy_id: str, controller: EnemyController | EnemyAction
    ) -> None:
        """Assign a controller or a fixed action to one enemy."""
        if enemy_id not in self.engine.world_state.environment.enemies:
            raise EnemyNotFoundError(f"Enemy {enemy_id!r} does not exist")
        self.controllers[enemy_id] = controller

    def release_enemy(self, enemy_id: str) -> None:
        self.controllers.pop(enemy_id, None)

    def observe(self, enemy_id: str) -> GoombaPerception:
        return self.perceptor.observe(enemy_id)

    def tick(
        self,
        dt: float,
        player_inputs: dict[str, InputState],
        action_requests: dict[str, ActionRequest] | None = None,
    ) -> dict[str, ActionResult]:
        """Run one game tick, consuming one synchronous decision per controlled enemy."""
        requests = action_requests or {}
        if any(not isinstance(request, ActionRequest) for request in requests.values()):
            raise TypeError("action_requests values must be ActionRequest instances")
        if not self.enabled:
            self.engine.tick(dt, player_inputs)
            self.last_results = {
                enemy_id: ActionResult(
                    enemy_id=enemy_id,
                    requested_action=request.action,
                    status=ActionStatus.DISABLED,
                    message="Agent control is disabled",
                )
                for enemy_id, request in requests.items()
            }
            return self.last_results

        directions: dict[str, EnemyMovementCommand] = {}
        pending_results: dict[str, ActionResult] = {}
        positions_before: dict[str, Position] = {}

        for enemy_id in self.controllers:
            enemy = self.engine.world_state.environment.enemies.get(enemy_id)
            request = requests.get(enemy_id)
            if enemy is None:
                pending_results[enemy_id] = ActionResult(
                    enemy_id=enemy_id,
                    requested_action=request.action if request is not None else None,
                    status=ActionStatus.INVALID_TARGET,
                    message=f"Enemy {enemy_id!r} does not exist",
                )
                continue

            positions_before[enemy_id] = Position(enemy.x, enemy.y)
            if request is None:
                controller = self.controllers[enemy_id]
                request = (
                    ActionRequest(enemy_id, controller)
                    if isinstance(controller, EnemyAction)
                    else controller.decide(self.observe(enemy_id))
                )
            if not isinstance(request, ActionRequest):
                raise TypeError("Enemy controllers must return an ActionRequest")
            if request.enemy_id != enemy_id:
                pending_results[enemy_id] = ActionResult(
                    enemy_id=enemy_id,
                    requested_action=request.action,
                    status=ActionStatus.INVALID_TARGET,
                    engine_command=MovementCommand.STOP,
                    position_before=positions_before[enemy_id],
                    message="An agent may only control its selected enemy",
                )
                directions[enemy_id] = EnemyMovementCommand.STOP
                continue

            command, status, message = self._translate_action(request.action)
            directions[enemy_id] = self._engine_command(command)
            pending_results[enemy_id] = ActionResult(
                enemy_id=enemy_id,
                requested_action=(
                    request.action if isinstance(request.action, EnemyAction) else None
                ),
                status=status,
                engine_command=command,
                position_before=positions_before[enemy_id],
                message=message,
            )

        for enemy_id, request in requests.items():
            if enemy_id in self.controllers:
                continue
            pending_results[enemy_id] = ActionResult(
                enemy_id=enemy_id,
                requested_action=request.action,
                status=ActionStatus.INVALID_TARGET,
                message=f"Enemy {enemy_id!r} is not selected for agent control",
            )

        if directions:
            self.engine.tick(dt, player_inputs, enemy_commands=directions)
        else:
            self.engine.tick(dt, player_inputs)

        self.last_results = {
            enemy_id: ActionResult(
                enemy_id=result.enemy_id,
                requested_action=result.requested_action,
                status=result.status,
                engine_command=result.engine_command,
                position_before=result.position_before,
                position_after=self._position_of(enemy_id),
                message=result.message,
            )
            for enemy_id, result in pending_results.items()
        }
        return self.last_results

    @staticmethod
    def _translate_action(
        action: EnemyAction,
    ) -> tuple[MovementCommand, ActionStatus, str]:
        if action is EnemyAction.MOVE_LEFT:
            return MovementCommand.MOVE_LEFT, ActionStatus.APPLIED, ""
        if action is EnemyAction.MOVE_RIGHT:
            return MovementCommand.MOVE_RIGHT, ActionStatus.APPLIED, ""
        if action is EnemyAction.STOP:
            return MovementCommand.STOP, ActionStatus.APPLIED, ""
        if action is EnemyAction.JUMP:
            return (
                MovementCommand.STOP,
                ActionStatus.UNSUPPORTED,
                "Enemies do not support jumping",
            )
        return (
            MovementCommand.STOP,
            ActionStatus.INVALID_ACTION,
            f"Unsupported action {action!r}",
        )

    @staticmethod
    def _engine_command(command: MovementCommand) -> EnemyMovementCommand:
        return {
            MovementCommand.MOVE_LEFT: EnemyMovementCommand.MOVE_LEFT,
            MovementCommand.MOVE_RIGHT: EnemyMovementCommand.MOVE_RIGHT,
            MovementCommand.STOP: EnemyMovementCommand.STOP,
        }[command]

    def _position_of(self, enemy_id: str) -> Position | None:
        enemy = self.engine.world_state.environment.enemies.get(enemy_id)
        return Position(enemy.x, enemy.y) if enemy is not None else None
