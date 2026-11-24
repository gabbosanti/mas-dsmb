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

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.models import (
    ActionResult,
    EnemyAction,
    EnemyPerception,
    EnemyPerceptionMessage,
    PlayerAction,
    PlayerActionResult,
    PlayerPerceptionMessage,
)
from distributed_smb.application.enemy_perceptor import (
    AGENT_TYPE_OF,
    EnemyNotFoundError,
    EnemyPerceptor,
    agent_type_of,
)
from distributed_smb.application.player_perceptor import PlayerPerceptor
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

HELD_COMMANDS = frozenset(
    {
        EnemyMovementCommand.STOP,
        EnemyMovementCommand.DIVE,
        EnemyMovementCommand.RISE,
    }
)

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
    # A running character moves ~6 times faster than an enemy (300 vs 50-80
    # px/s): its agent needs fresher snapshots to jump in time.
    player_perception_interval_ticks: int = 2
    bridge: SpadeAgentBridge = field(default_factory=SpadeAgentBridge)
    spade_controlled_enemy_ids: set[str] = field(
        default_factory=set
    )  # tracks spade agents
    # Characters of disconnected players, driven by a player agent.
    spade_controlled_player_ids: set[str] = field(default_factory=set)
    perceptor: EnemyPerceptor = field(init=False)
    player_perceptor: PlayerPerceptor = field(init=False)
    # Last input chosen by each player agent: like a held key, it is applied
    # on every tick until the agent decides again (~every 5 ticks).
    _held_player_inputs: dict[str, InputState] = field(
        default_factory=dict, init=False, repr=False
    )
    # Commands re-sent every tick until the agent picks another action: the
    # engine honours STOP for a single tick, and DIVE/RISE would otherwise be
    # cancelled by the horizontal command derived from the enemy's heading.
    _held_commands: dict[str, EnemyMovementCommand] = field(
        default_factory=dict, init=False, repr=False
    )
    # Enemies whose agent has sent at least one valid action.
    _enemies_with_decision: set[str] = field(
        default_factory=set, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.perception_interval_ticks < 1:
            raise ValueError("perception_interval_ticks must be at least one")
        self.perceptor = EnemyPerceptor(self.engine)
        self.player_perceptor = PlayerPerceptor(self.engine)

    def control_enemy_with_spade(self, enemy_id: str) -> None:
        if enemy_id not in self.engine.world_state.environment.enemies:
            raise EnemyNotFoundError(f"Enemy {enemy_id!r} does not exist")
        self.spade_controlled_enemy_ids.add(enemy_id)

    # Test only
    def release_enemy(self, enemy_id: str) -> None:
        self.spade_controlled_enemy_ids.discard(enemy_id)
        self._held_commands.pop(enemy_id, None)
        self._enemies_with_decision.discard(enemy_id)

    def observe(self, enemy_id: str) -> EnemyPerception:
        return self.perceptor.observe(enemy_id)

    def control_player_with_spade(self, player_id: str) -> None:
        """Hand the character of a disconnected player to its player agent."""
        world = self.engine.world_state
        if world.get_player(player_id) is None and player_id not in world.respawn_timers:
            raise LookupError(f"Player {player_id!r} does not exist")
        self.spade_controlled_player_ids.add(player_id)

    def release_player(self, player_id: str) -> None:
        """The player is back: its own inputs drive the character again."""
        self.spade_controlled_player_ids.discard(player_id)
        self._held_player_inputs.pop(player_id, None)

    def tick(self, dt: float, player_inputs: dict[str, InputState]) -> None:
        """Run one simulation tick without waiting for SPADE."""
        if self.enabled == False:
            self._held_commands.clear()
            self._held_player_inputs.clear()
            self._enemies_with_decision.clear()
            self.bridge.discard_actions()
            self.bridge.discard_perceptions()
            self.engine.tick(dt, player_inputs)
            return

        # Retreive agents' action and turn them into engine commands.
        enemy_commands = self._retreive_agent_actions()
        authoritative_player_inputs = dict(player_inputs)
        authoritative_player_inputs.update(self._retrieve_player_actions())

        # Advance the authoritative simulation.
        self.engine.tick(
            dt,
            authoritative_player_inputs,
            enemy_commands=enemy_commands,
        )

        # Sense: observe the new world state and hand it to the agents.
        sequence = self.engine.world_state.sequence_number
        if sequence % self.player_perception_interval_ticks == 0:
            self._publish_player_perceptions(sequence)
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

    def _publish_player_perceptions(self, sequence: int) -> None:
        for player_id in sorted(self.spade_controlled_player_ids):
            perception = self.player_perceptor.observe(player_id)
            if perception is None:  # dead, waiting to respawn
                continue
            self.bridge.publish(
                AgentType.PLAYER,
                PlayerPerceptionMessage(
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

    def _retrieve_player_actions(self) -> dict[str, InputState]:
        """Input of every agent-driven character for this tick.

        The newest action of each player agent replaces its held input; the
        held input is then applied on every tick, so the character keeps
        running (or keeps the jump key down) between two decisions.
        """
        while (result := self.bridge.poll_action(AgentType.PLAYER)) is not None:
            if not isinstance(result, PlayerActionResult):
                LOGGER.warning(
                    "Discarding PLAYER action of type %s: expected PlayerActionResult",
                    type(result).__name__,
                )
                continue
            if result.player_id not in self.spade_controlled_player_ids:
                LOGGER.debug(
                    "Discarding action for uncontrolled player %r", result.player_id
                )
                continue
            self._held_player_inputs[result.player_id] = _player_input(result.action)

        return {
            player_id: held
            for player_id, held in self._held_player_inputs.items()
            if player_id in self.spade_controlled_player_ids
        }

    def _retreive_agent_actions(self) -> dict[str, EnemyMovementCommand]:
        # Pop every queued ActionResult and translate it into an engine command.
        enemies = self.engine.world_state.environment.enemies
        for enemy_id in self._held_commands.keys() - enemies.keys():
            del self._held_commands[enemy_id]  # maintains only alive enemies
        self._enemies_with_decision &= enemies.keys()
        # Include all SPADE-controlled enemies every tick, preserving their
        # last heading between asynchronous BDI decisions.
        commands: dict[str, EnemyMovementCommand] = {}
        for enemy_id in self._live_spade_enemy_ids():
            enemy = enemies[enemy_id]
            if enemy_id in self._held_commands:
                commands[enemy_id] = self._held_commands[enemy_id]
            elif enemy_id not in self._enemies_with_decision or enemy.vx == 0:
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
            if command in HELD_COMMANDS:
                self._held_commands[result.enemy_id] = command
            else:
                self._held_commands.pop(result.enemy_id, None)

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
        if action is EnemyAction.DIVE:
            return EnemyMovementCommand.DIVE
        if action is EnemyAction.RISE:
            return EnemyMovementCommand.RISE
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


def _player_input(action: PlayerAction) -> InputState:
    """The keys a player would press for ``action``."""
    return InputState(
        left=action is PlayerAction.MOVE_LEFT,
        right=action in (PlayerAction.MOVE_RIGHT, PlayerAction.JUMP_RIGHT),
        jump=action in (PlayerAction.JUMP, PlayerAction.JUMP_RIGHT),
        down=action is PlayerAction.CROUCH,
    )
