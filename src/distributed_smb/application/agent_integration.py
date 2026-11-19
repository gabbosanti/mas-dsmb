"""Game-side actuator/sensor for SPADE-controlled enemies.

All deliberation happens in the SPADE thread. On every simulation tick this
adapter only:

1. drains the ``ActionResult`` messages produced by the agents and translates
   them into engine movement commands;
2. advances the engine;
3. periodically publishes fresh perceptions for the agents.
"""

import logging
from dataclasses import dataclass, field

from distributed_smb.agents.models import (
    ActionResult,
    EnemyAction,
    EnemyPerception,
    EnemyPerceptionMessage,
)
from distributed_smb.application.enemy_perceptor import (
    AGENT_TYPE_OF,
    EnemyNotFoundError,
    EnemyPerceptor,
    agent_type_of,
)
from distributed_smb.application.spade_agent_bridge import SpadeAgentBridge
from distributed_smb.domain.entity import Enemy
from distributed_smb.domain.game_engine import EnemyMovementCommand, GameEngine
from distributed_smb.shared.input import InputState

LOGGER = logging.getLogger(__name__)

__all__ = [
    "AgentGameIntegration",
    "EnemyNotFoundError",
    "SpadeAgentBridge",
    "agent_type_of",
]

# Agent types that drive enemies: their action queues are drained every tick.
ENEMY_AGENT_TYPES = tuple(dict.fromkeys(AGENT_TYPE_OF.values()))


@dataclass(slots=True)
class AgentGameIntegration:
    """Connect selected enemies to their SPADE agents without ever blocking.

    Enemies not handed to SPADE (or every enemy, while ``enabled`` is False)
    keep the engine's original patrol behaviour.
    """

    engine: GameEngine
    enabled: bool = False
    perception_interval_ticks: int = 5
    bridge: SpadeAgentBridge = field(default_factory=SpadeAgentBridge)
    spade_controlled_enemy_ids: set[str] = field(
        default_factory=set
    )  # tracks spade agents
    perceptor: EnemyPerceptor = field(init=False)
    # The engine honours STOP for a single tick only, so a STOP chosen by an
    # agent is re-sent every tick until that agent picks another action.
    _stopped_enemy_ids: set[str] = field(default_factory=set, init=False, repr=False)
    # Enemies whose agent has sent at least one valid action.
    _enemies_with_decision: set[str] = field(
        default_factory=set, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.perception_interval_ticks < 1:
            raise ValueError("perception_interval_ticks must be at least one")
        self.perceptor = EnemyPerceptor(self.engine)

    def control_enemy_with_spade(self, enemy_id: str) -> None:
        if enemy_id not in self.engine.world_state.environment.enemies:
            raise EnemyNotFoundError(f"Enemy {enemy_id!r} does not exist")
        self.spade_controlled_enemy_ids.add(enemy_id)

    # Test only
    def release_enemy(self, enemy_id: str) -> None:
        self.spade_controlled_enemy_ids.discard(enemy_id)
        self._stopped_enemy_ids.discard(enemy_id)
        self._enemies_with_decision.discard(enemy_id)

    def observe(self, enemy_id: str) -> EnemyPerception:
        return self.perceptor.observe(enemy_id)

    def tick(self, dt: float, player_inputs: dict[str, InputState]) -> None:
        """Run one simulation tick without waiting for SPADE."""
        if self.enabled == False:
            self._stopped_enemy_ids.clear()
            self._enemies_with_decision.clear()
            self.bridge.discard_actions()
            self.bridge.discard_perceptions()
            self.engine.tick(dt, player_inputs)
            return

        # Retreive agents' action and turn them into engine commands.
        enemy_commands = self._retreive_agent_actions()

        # Advance the authoritative simulation.
        self.engine.tick(dt, player_inputs, enemy_commands=enemy_commands)

        # Sense: observe the new world state and hand it to the agents.
        sequence = self.engine.world_state.sequence_number
        if sequence % self.perception_interval_ticks != 0:  # every 5 ticks
            return

        enemies = self.engine.world_state.environment.enemies
        for enemy_id in self._live_spade_enemy_ids():
            perception = self.observe(enemy_id)
            # Each enemy's perception goes to the queue of its agent type.
            self.bridge.publish(
                agent_type_of(enemies[enemy_id]),
                EnemyPerceptionMessage(
                    simulation_sequence=sequence,
                    perception=perception,
                ),
            )

    def _live_spade_enemy_ids(self) -> list[str]:
        """SPADE-controlled enemies still present in the world, in stable order."""
        enemies = self.engine.world_state.environment.enemies
        return sorted(
            enemy_id
            for enemy_id in self.spade_controlled_enemy_ids
            if enemy_id in enemies
        )

    def _retreive_agent_actions(self) -> dict[str, EnemyMovementCommand]:
        # Pop every queued ActionResult and translate it into an engine command.
        enemies = self.engine.world_state.environment.enemies
        self._stopped_enemy_ids &= enemies.keys()  # maintains only alive enemies
        self._enemies_with_decision &= enemies.keys()
        # Include all SPADE-controlled enemies every tick, preserving their
        # last heading between asynchronous BDI decisions.
        commands: dict[str, EnemyMovementCommand] = {}
        for enemy_id in self._live_spade_enemy_ids():
            enemy = enemies[enemy_id]
            if (
                enemy_id not in self._enemies_with_decision
                or enemy_id in self._stopped_enemy_ids
                or enemy.vx == 0
            ):
                # Until its agent has decided at least once (SPADE may take
                # seconds to start), the enemy waits instead of walking blind.
                commands[enemy_id] = EnemyMovementCommand.STOP
            elif enemy.vx > 0:
                commands[enemy_id] = EnemyMovementCommand.MOVE_RIGHT
            else:
                commands[enemy_id] = EnemyMovementCommand.MOVE_LEFT

        for result in self._poll_agent_actions():
            enemy = enemies.get(result.enemy_id)
            if enemy is None or result.enemy_id not in self.spade_controlled_enemy_ids:
                LOGGER.debug(
                    "Discarding action for uncontrolled enemy %r", result.enemy_id
                )
                continue

            command = self._to_engine_command(
                enemy, result.action, commands.get(enemy.enemy_id)
            )
            if command is None:
                LOGGER.warning(
                    "Enemy %r cannot perform %s", result.enemy_id, result.action
                )
                continue

            commands[result.enemy_id] = command
            self._enemies_with_decision.add(result.enemy_id)
            if command is EnemyMovementCommand.STOP:
                self._stopped_enemy_ids.add(result.enemy_id)
            else:
                self._stopped_enemy_ids.discard(result.enemy_id)

        return commands

    def _poll_agent_actions(self):
        """Every queued ActionResult, from the queue of each enemy agent type.

        An action is accepted only from the queue of the enemy's own agent
        type: a Goomba agent cannot drive a Koopa, and vice versa.
        """
        enemies = self.engine.world_state.environment.enemies
        for agent_type in ENEMY_AGENT_TYPES:
            while (result := self.bridge.poll_action(agent_type)) is not None:
                if not isinstance(result, ActionResult):
                    # Never let a malformed agent message crash the game thread.
                    LOGGER.warning(
                        "Discarding %s action of type %s: expected ActionResult",
                        agent_type.value,
                        type(result).__name__,
                    )
                    continue
                enemy = enemies.get(result.enemy_id)
                if enemy is not None and agent_type_of(enemy) is not agent_type:
                    LOGGER.warning(
                        "Discarding %s action for %s enemy %r",
                        agent_type.value,
                        enemy.enemy_type.value,
                        result.enemy_id,
                    )
                    continue
                yield result

    @staticmethod
    def _to_engine_command(
        enemy: Enemy,
        action: EnemyAction,
        pending: EnemyMovementCommand | None,
    ) -> EnemyMovementCommand | None:
        """Map an agent action onto what the engine can execute, if anything."""
        if action is EnemyAction.MOVE_LEFT:
            return EnemyMovementCommand.MOVE_LEFT
        if action is EnemyAction.MOVE_RIGHT:
            return EnemyMovementCommand.MOVE_RIGHT
        if action is EnemyAction.STOP:
            return EnemyMovementCommand.STOP
        if action is EnemyAction.TURN_AROUND:
            # Turn relative to a move already chosen this tick, else to the
            # current velocity (STOP keeps vx, so a stopped enemy still has one).
            if pending in (
                EnemyMovementCommand.MOVE_LEFT,
                EnemyMovementCommand.MOVE_RIGHT,
            ):
                heading_left = pending is EnemyMovementCommand.MOVE_LEFT
            else:
                heading_left = enemy.vx < 0
            return (
                EnemyMovementCommand.MOVE_RIGHT
                if heading_left
                else EnemyMovementCommand.MOVE_LEFT
            )
        return None
