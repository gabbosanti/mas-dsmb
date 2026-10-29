"""Application adapters between agent contracts and the game simulation."""

from dataclasses import dataclass, field
from math import hypot

from distributed_smb.agents.controller import EnemyController
from distributed_smb.agents.models import (
    ActionRequest,
    ActionResult,
    ActionStatus,
    EnemyAction,
    EnemyObservation,
    MovementCommand,
    ObstacleKind,
    ObstacleObservation,
    Position,
)
from distributed_smb.domain.entity import Enemy, Player
from distributed_smb.domain.game_engine import EnemyMovementCommand, GameEngine
from distributed_smb.shared.input import InputState

OBSTACLE_SIGHT_DISTANCE = 200.0


class EnemyNotFoundError(LookupError):
    """Raised when an observation is requested for an unknown enemy."""


@dataclass(slots=True)
class EnemyPerceptor:
    """Build immutable observations without exposing mutable simulation objects."""

    engine: GameEngine
    obstacle_sight_distance: float = OBSTACLE_SIGHT_DISTANCE

    def __post_init__(self) -> None:
        if self.obstacle_sight_distance < 0:
            raise ValueError("obstacle_sight_distance must be non-negative")

    def observe(self, enemy_id: str) -> EnemyObservation:
        enemy = self.engine.world_state.environment.enemies.get(enemy_id)
        if enemy is None:
            raise EnemyNotFoundError(f"Enemy {enemy_id!r} does not exist")

        target = self._nearest_player(enemy)
        return EnemyObservation(
            enemy_id=enemy.enemy_id,
            enemy_position=Position(enemy.x, enemy.y),
            target_player_id=target.player_id if target is not None else None,
            mario_position=(
                Position(target.x, target.y) if target is not None else None
            ),
            nearby_obstacles=self._nearby_obstacles(enemy),
        )

    def _nearest_player(self, enemy: Enemy) -> Player | None:
        players = self.engine.world_state.characters.values()
        return min(
            players,
            key=lambda player: (
                (player.x - enemy.x) ** 2 + (player.y - enemy.y) ** 2,
                player.join_index,
                player.player_id,
            ),
            default=None,
        )

    def _nearby_obstacles(self, enemy: Enemy) -> tuple[ObstacleObservation, ...]:
        obstacles = [
            ObstacleObservation(
                kind=ObstacleKind.PLATFORM,
                x=platform.x,
                y=platform.y,
                width=platform.width,
                height=platform.height,
            )
            for platform in self.engine.platforms
        ]
        obstacles.extend(
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

        enemy_center_x = enemy.x + enemy.width / 2
        enemy_center_y = enemy.y + enemy.height / 2
        return tuple(
            obstacle
            for obstacle in obstacles
            if hypot(
                max(
                    obstacle.x - enemy_center_x,
                    0,
                    enemy_center_x - obstacle.x - obstacle.width,
                ),
                max(
                    obstacle.y - enemy_center_y,
                    0,
                    enemy_center_y - obstacle.y - obstacle.height,
                ),
            )
            <= self.obstacle_sight_distance
        )


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

    def observe(self, enemy_id: str) -> EnemyObservation:
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
