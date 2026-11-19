"""Common base of the SPADE-BDI enemy agents (Goomba, Koopa, ...).

An enemy agent only senses (game snapshot -> beliefs) and acts (ASL action ->
game command): every decision lives in its ``.asl`` file. This module holds
everything the enemy kinds share; a concrete agent only declares its
``agent_type``, turns its perception into beliefs (``_update_beliefs``) and,
if needed, adds the ASL actions that only it knows (``_add_actions``).
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import TYPE_CHECKING, ClassVar

import agentspeak as asp
from spade_bdi.bdi import BDIAgent
from spade_bdi.bdi_behaviour import PERCEPT_TAG, BDIBehaviour

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.models import (
    ActionResult,
    EnemyAction,
    EnemyPerception,
    EnemyPerceptionMessage,
    Position,
    SightDirection,
)

if TYPE_CHECKING:
    from distributed_smb.agents.spade_runtime import SpadeRuntime

# spade_bdi's reasoning cycle never yields to the event loop on its own: it
# would spin at 100% CPU and, through the GIL, slow down the game thread.
BDI_CYCLE_PERIOD = 0.02

# Upper bound of interpreter steps per cycle, so a runaway plan cannot
# monopolise the SPADE event loop.
MAX_STEPS_PER_CYCLE = 64


class ThrottledBDIBehaviour(BDIBehaviour):
    """spade_bdi reasoning cycle that runs at most once every BDI_CYCLE_PERIOD.

    The stock cycle executes a single interpreter step per run, so a plan of
    a few steps took several cycles to produce its action. Here each cycle
    runs the intentions until they are idle: a perception becomes an action
    within the same cycle.
    """

    async def run(self):
        await super().run()  # applies queued belief changes + one step
        if self.agent.bdi_enabled:
            for _ in range(MAX_STEPS_PER_CYCLE):
                if not self.agent.bdi_agent.step():
                    break
        await asyncio.sleep(BDI_CYCLE_PERIOD)


class EnemyAgent(BDIAgent, ABC):
    """Interface and shared machinery of every enemy agent."""

    # Which queues the agent talks through: set by each concrete agent.
    agent_type: ClassVar[AgentType]

    # Both injected by SpadeRuntime right after construction.
    spade_runtime: "SpadeRuntime"
    enemy_id: str | None = None

    # ------------------------------------------------------------------ #
    # What a concrete agent must / may provide
    # ------------------------------------------------------------------ #

    @abstractmethod
    def _update_beliefs(self, perception: EnemyPerception, sequence: int) -> None:
        """Translate a game snapshot into the beliefs read by the ASL plans.

        It must end with ``self._new_perception(sequence)``: the event that
        triggers exactly one ASL decision for this snapshot.
        """

    def _add_actions(self, actions) -> None:
        """Register the ASL actions specific to this enemy kind (optional)."""

    # ------------------------------------------------------------------ #
    # SPADE lifecycle and runtime entry points
    # ------------------------------------------------------------------ #

    @property
    def logger(self) -> logging.Logger:
        # The concrete agent's module, so logs read e.g. "...bdi.goomba".
        return logging.getLogger(type(self).__module__)

    def add_behaviour(self, behaviour, template=None):
        # BDIAgent.__init__ installs a plain BDIBehaviour: swap in the
        # throttled one, keeping the same message template.
        if isinstance(behaviour, BDIBehaviour) and not isinstance(
            behaviour, ThrottledBDIBehaviour
        ):
            behaviour = ThrottledBDIBehaviour()
        super().add_behaviour(behaviour, template)

    async def setup(self):
        self.logger.info("%s agent %s started", self.agent_type.value, self.enemy_id)

    def set_peers(self, jids: Iterable[str]) -> None:
        """Tell the agent which other agents it can talk to (``peers(List)``)."""
        peers = tuple(jids)
        self._peer_count = len(peers)
        self.bdi.set_belief("peers", peers)

    def perceive(self, message: EnemyPerceptionMessage) -> None:
        """Entry point used by SpadeRuntime to deliver this agent's perception."""
        first = not getattr(self, "_logged_first_perception", False)
        self.logger.log(
            logging.INFO if first else logging.DEBUG,
            "%s %s received perception %s",
            self.agent_type.value,
            self.enemy_id,
            message.simulation_sequence,
        )
        self._logged_first_perception = True
        self._update_beliefs(message.perception, message.simulation_sequence)

    # ------------------------------------------------------------------ #
    # Helpers for _update_beliefs
    # ------------------------------------------------------------------ #

    def _sense_position(self, perception: EnemyPerception) -> Position:
        """Set ``position(X, Y)`` (the agent's center) and return it."""
        center = perception.center
        self._last_center = center
        self.bdi.set_belief("position", round(center.x), round(center.y))
        return center

    def _new_perception(self, perception: EnemyPerception, sequence: int) -> None:
        """Last step of ``_update_beliefs``: heading + the decision event."""
        self.bdi.set_belief("direction", perception.direction.value)
        # This fresh event triggers exactly one ASL decision for this snapshot.
        self.bdi.set_belief("perception", sequence)

    def _set_single_belief(self, name: str, args: tuple | None) -> None:
        """Keep at most one ``name(*args)`` belief, or none when ``args`` is None.

        ``remove_belief(name)`` alone would not match ``name(left)`` (different
        arity), so the arguments last set are remembered to remove them.
        """
        single_beliefs: dict[str, tuple | None] = self.__dict__.setdefault(
            "_single_beliefs", {}
        )
        previous = single_beliefs.get(name)
        if previous == args:
            return
        if args is not None:
            self.bdi.set_belief(name, *args)  # also replaces the previous one
        elif previous is not None:
            self.bdi.remove_belief(name, *previous)
        single_beliefs[name] = args

    def _set_flag_belief(self, name: str, arg: str, present: bool) -> None:
        """Add or remove ``name(arg)`` without touching ``name(other_arg)``.

        ``bdi.set_belief`` replaces every other belief with the same name, so
        it cannot hold ``path_blocked(left)`` and ``path_blocked(right)`` at
        the same time: the addition is queued directly, as spade_bdi does.
        """
        flags: set[tuple[str, str]] = self.__dict__.setdefault("_flag_beliefs", set())
        key = (name, arg)
        if present == (key in flags):
            return
        if present:
            flags.add(key)
            term = asp.Literal(name, (asp.Literal(arg),), PERCEPT_TAG)
            self.bdi_intention_buffer.append(
                (
                    asp.Trigger.addition,
                    asp.GoalType.belief,
                    term,
                    asp.runtime.Intention(),
                )
            )
        else:
            flags.discard(key)
            self.bdi.remove_belief(name, arg)

    # ------------------------------------------------------------------ #
    # ASL actions shared by every enemy
    # ------------------------------------------------------------------ #

    def add_custom_actions(self, actions):
        @actions.add(".move_right", 0)
        def move_right(agent, term, intention):
            self._enter_behaviour("patrol", "moving right")
            self._publish_action(EnemyAction.MOVE_RIGHT)
            yield

        @actions.add(".move_left", 0)
        def move_left(agent, term, intention):
            self._enter_behaviour("patrol", "moving left")
            self._publish_action(EnemyAction.MOVE_LEFT)
            yield

        @actions.add(".turn_around", 1)
        def turn_around(agent, term, intention):
            # Turn away from the heading of the snapshot that triggered the
            # plan (passed by the ASL), never from the current belief: a newer
            # perception may have changed it. The move is absolute, so repeated
            # turns for stale snapshots are no-ops instead of cancelling out.
            self._enter_behaviour("patrol", "turning around")
            heading = self._grounded(term, intention, 0)
            if heading == SightDirection.RIGHT.value:
                self._publish_action(EnemyAction.MOVE_LEFT)
            elif heading == SightDirection.LEFT.value:
                self._publish_action(EnemyAction.MOVE_RIGHT)
            else:
                self.logger.warning("%s: unknown heading %r", self.enemy_id, heading)
            yield

        @actions.add(".chase", 1)
        def chase(agent, term, intention):
            # kill_mario: move towards the player's side. A separate action
            # from .move_* only to tell the behaviours apart in the logs.
            side = self._grounded(term, intention, 0)
            player = getattr(self, "_last_player", None)
            where = f"x={player.center.x:.0f}" if player is not None else "?"
            self._enter_behaviour(
                "kill_mario", f"sees the player at {where} on its {side}"
            )
            self._walk_towards(side)
            yield

        @actions.add(".hold", 0)
        def hold(agent, term, intention):
            self._enter_behaviour("wait", "holding its position")
            self._publish_action(EnemyAction.STOP)
            yield

        self._add_actions(actions)

    @staticmethod
    def _grounded(term, intention, index: int) -> str:
        return str(asp.grounded(term.args[index], intention.scope)).strip('"')

    def _enter_behaviour(
        self, behaviour: str, detail: str, *, key: object = None
    ) -> None:
        """Log once per change of behaviour: what the agent does now, and why.

        ``key`` tells two situations of the same behaviour apart (e.g. the
        encircle partner): a new key is logged even if the behaviour is the same.
        """
        key = key if key is not None else behaviour
        if getattr(self, "_behaviour_key", None) == key:
            return
        self.logger.info(
            "[%s %s] %s -> %s: %s",
            self.agent_type.value,
            self.enemy_id,
            getattr(self, "_behaviour", None) or "start",
            behaviour,
            detail,
        )
        self._behaviour = behaviour
        self._behaviour_key = key

    def _walk_towards(self, side: str) -> None:
        if side == SightDirection.RIGHT.value:
            self._publish_action(EnemyAction.MOVE_RIGHT)
        elif side == SightDirection.LEFT.value:
            self._publish_action(EnemyAction.MOVE_LEFT)
        else:
            self.logger.warning("%s: unknown side %r", self.enemy_id, side)

    def _publish_action(self, action: EnemyAction) -> None:
        """Publish the action selected by the ASL plan to the game thread."""
        if self.enemy_id is None:
            self.logger.warning("Agent without an enemy id, %s dropped", action)
            return
        self.spade_runtime.put_action(
            self.agent_type,
            ActionResult(enemy_id=self.enemy_id, action=action),
        )


def agent_name(jid: str) -> str:
    """``goomba-enemy-9@localhost`` -> ``enemy-9`` (any enemy kind)."""
    local = jid.split("@", 1)[0]
    for agent_type in AgentType:
        local = local.removeprefix(f"{agent_type.value}-")
    return local
