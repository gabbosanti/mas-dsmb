"""Application adapters between agent contracts and the game simulation."""

from dataclasses import dataclass, field
from math import hypot
from queue import Empty, Full, Queue
from typing import TypeVar

from distributed_smb.agents.controller import EnemyController
from distributed_smb.agents.models import (
    ActionRequest,
    ActionResult,
    ActionStatus,
    EnemyAction,
    GoombaPerception,
    GoombaPerceptionMessage,
    MovementCommand,
    ObstacleKind,
    ObstacleObservation,
    PlayerPerception,
    Position,
    SightDirection,
)
from distributed_smb.application.enemy_perceptor import EnemyPerceptor
from distributed_smb.application.spade_agent_bridge import SpadeAgentBridge
from distributed_smb.domain.entity import Player
from distributed_smb.domain.game_engine import EnemyMovementCommand, GameEngine
from distributed_smb.shared.input import InputState

GOOMBA_SIGHT_DISTANCE = 200.0
OBSTACLE_SIGHT_DISTANCE = GOOMBA_SIGHT_DISTANCE
AGENT_QUEUE_CAPACITY = 64
QueueItem = TypeVar("QueueItem")


class EnemyNotFoundError(LookupError):
    """Raised when an observation is requested for an unknown enemy."""

@dataclass(slots=True)
class AgentGameIntegration:
    """Selectively runs agent controllers in the authoritative simulation tick.

    Each decision is consumed by exactly one engine tick. Disabled or
    unselected enemies continue using the engine's original patrol behavior.
    """

    engine: GameEngine
    enabled: bool = False
    perception_interval_ticks: int = 5
    action_max_age_ticks: int | None = None
    controllers: dict[str, EnemyController | EnemyAction] = field(default_factory=dict)
    spade_controlled_enemy_ids: set[str] = field(default_factory=set)
    bridge: SpadeAgentBridge = field(default_factory=SpadeAgentBridge)
    last_results: dict[str, ActionResult] = field(default_factory=dict, init=False)
    perceptor: EnemyPerceptor = field(init=False)
    _latest_spade_actions: dict[str, ActionRequest] = field(
        default_factory=dict, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.perception_interval_ticks < 1:
            raise ValueError("perception_interval_ticks must be at least one")
        if self.action_max_age_ticks is None:
            self.action_max_age_ticks = 2 * self.perception_interval_ticks
        elif self.action_max_age_ticks < 0:
            raise ValueError("action_max_age_ticks must be non-negative")
        self.perceptor = EnemyPerceptor(self.engine)

    @property
    def _max_action_age_ticks(self) -> int:
        if self.action_max_age_ticks is not None:
            return self.action_max_age_ticks
        return 2 * self.perception_interval_ticks

    def control_enemy(
        self, enemy_id: str, controller: EnemyController | EnemyAction
    ) -> None:
        if enemy_id not in self.engine.world_state.environment.enemies:
            raise EnemyNotFoundError(f"Enemy {enemy_id!r} does not exist")
        self.spade_controlled_enemy_ids.discard(enemy_id)
        self.controllers[enemy_id] = controller

    def control_enemy_with_spade(self, enemy_id: str) -> None:
        if enemy_id not in self.engine.world_state.environment.enemies:
            raise EnemyNotFoundError(f"Enemy {enemy_id!r} does not exist")
        if enemy_id in self.controllers:
            raise ValueError(f"Enemy {enemy_id!r} already has a synchronous controller")
        self.spade_controlled_enemy_ids.add(enemy_id)

    def release_enemy(self, enemy_id: str) -> None:
        self.controllers.pop(enemy_id, None)
        self.spade_controlled_enemy_ids.discard(enemy_id)
        self._latest_spade_actions.pop(enemy_id, None)

    def observe(self, enemy_id: str) -> GoombaPerception:
        return self.perceptor.observe(enemy_id)

    def tick(
        self,
        dt: float,
        player_inputs: dict[str, InputState],
        action_requests: dict[str, ActionRequest] | None = None,
    ) -> dict[str, ActionResult]:
        """Run one simulation tick without waiting for SPADE."""
        requests = action_requests or {}
        if any(not isinstance(request, ActionRequest) for request in requests.values()):
            raise TypeError("action_requests values must be ActionRequest instances")
        if not self.enabled:
            self._latest_spade_actions.clear()
            self._discard_queued_actions()
            self.bridge.discard_perceptions()
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

        current_sequence = self.engine.world_state.sequence_number
        self._drain_spade_actions(current_sequence)
        directions: dict[str, EnemyMovementCommand] = {}
        pending_results: dict[str, ActionResult] = {}
        positions_before: dict[str, Position] = {}

        controlled_ids = self.controllers.keys() | self.spade_controlled_enemy_ids
        for enemy_id in controlled_ids:
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
                controller = self.controllers.get(enemy_id)
                if isinstance(controller, EnemyAction):
                    request = ActionRequest(enemy_id, controller)
                elif controller is not None:
                    request = controller.decide(self.observe(enemy_id))
                else:
                    request = self._spade_action_for(enemy_id, current_sequence)
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
            if enemy_id in controlled_ids:
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

        sequence = self.engine.world_state.sequence_number
        if sequence % self.perception_interval_ticks == 0:
            self._publish_perceptions(sequence)

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

    def _publish_perceptions(self, sequence: int) -> None:
        for enemy_id in sorted(self.spade_controlled_enemy_ids):
            if enemy_id not in self.engine.world_state.environment.enemies:
                continue
            self.bridge.publish(
                GoombaPerceptionMessage(
                    simulation_sequence=sequence,
                    perception=self.observe(enemy_id),
                )
            )

    def _drain_spade_actions(self, current_sequence: int) -> None:
        while True:
            try:
                request = self.bridge.action_requests.get_nowait()
            except Empty:
                return

            observation_sequence = request.observation_sequence
            if (
                request.enemy_id not in self.spade_controlled_enemy_ids
                or not isinstance(observation_sequence, int)
                or isinstance(observation_sequence, bool)
                or observation_sequence > current_sequence
                or current_sequence - observation_sequence > self._max_action_age_ticks
            ):
                continue

            previous = self._latest_spade_actions.get(request.enemy_id)
            if (
                previous is None
                or previous.observation_sequence is None
                or observation_sequence >= previous.observation_sequence
            ):
                self._latest_spade_actions[request.enemy_id] = request

    def _spade_action_for(self, enemy_id: str, current_sequence: int) -> ActionRequest:
        request = self._latest_spade_actions.get(enemy_id)
        if (
            request is None
            or request.observation_sequence is None
            or current_sequence - request.observation_sequence
            > self._max_action_age_ticks
        ):
            self._latest_spade_actions.pop(enemy_id, None)
            return ActionRequest(enemy_id=enemy_id, action=EnemyAction.STOP)
        return request

    def _discard_queued_actions(self) -> None:
        while True:
            try:
                self.bridge.action_requests.get_nowait()
            except Empty:
                return

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
