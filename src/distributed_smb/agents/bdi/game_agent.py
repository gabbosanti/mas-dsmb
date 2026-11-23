"""Common base of every SPADE-BDI agent of the game (enemies, players).

An agent only senses (game snapshot -> beliefs) and acts (ASL action -> game
input): every decision lives in its ``.asl`` file. This module holds what all
the agents share: the throttled reasoning cycle, the peers, the belief
helpers and the log of behaviour changes. A concrete agent turns its
perception into beliefs (``_update_beliefs``), registers its ASL actions
(``_add_actions``) and says what it publishes (``_action_result``).
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, ClassVar

import agentspeak as asp
from spade_bdi.bdi import BDIAgent
from spade_bdi.bdi_behaviour import PERCEPT_TAG, BDIBehaviour

from distributed_smb.agents.agent_type import AgentType

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


class GameAgent(BDIAgent, ABC):
    """Interface and shared machinery of every BDI agent of the game."""

    # Which queues the agent talks through: set by each concrete agent.
    agent_type: ClassVar[AgentType]

    # Injected by SpadeRuntime right after construction.
    spade_runtime: "SpadeRuntime"

    # ------------------------------------------------------------------ #
    # What a concrete agent must / may provide
    # ------------------------------------------------------------------ #

    @property
    @abstractmethod
    def agent_id(self) -> str | None:
        """Id of the game entity the agent drives (enemy id, player id)."""

    @abstractmethod
    def _update_beliefs(self, perception: Any, sequence: int) -> None:
        """Translate a game snapshot into the beliefs read by the ASL plans.

        It must end by setting ``perception(Sequence)``: the event that
        triggers exactly one ASL decision for this snapshot.
        """

    @abstractmethod
    def _action_result(self, action: Any) -> Any:
        """The message put on the action queue for ``action``."""

    def _add_actions(self, actions) -> None:
        """Register the ASL actions specific to this agent (optional)."""

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
        self.logger.info("%s agent %s started", self.agent_type.value, self.agent_id)

    def set_peers(self, peers: Mapping[AgentType, Iterable[str]]) -> None:
        """Tell the agent which other agents it can talk to.

        Beliefs: ``peers(All)`` and one list per kind, e.g. ``goomba_peers(L)``
        and ``koopa_peers(L)`` (possibly empty), so that a plan can address
        only the agents of a given kind.
        """
        by_type = {AgentType(t): tuple(jids) for t, jids in peers.items()}
        everyone = tuple(jid for jids in by_type.values() for jid in jids)
        self._peer_count = len(everyone)
        self.bdi.set_belief("peers", everyone)
        for agent_type in AgentType:
            self.bdi.set_belief(
                f"{agent_type.value}_peers", by_type.get(agent_type, ())
            )

    def perceive(self, message: Any) -> None:
        """Entry point used by SpadeRuntime to deliver this agent's perception."""
        first = not getattr(self, "_logged_first_perception", False)
        self.logger.log(
            logging.INFO if first else logging.DEBUG,
            "%s %s received perception %s",
            self.agent_type.value,
            self.agent_id,
            message.simulation_sequence,
        )
        self._logged_first_perception = True
        self._update_beliefs(message.perception, message.simulation_sequence)

    # ------------------------------------------------------------------ #
    # Helpers for _update_beliefs
    # ------------------------------------------------------------------ #

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
    # Actions
    # ------------------------------------------------------------------ #

    def add_custom_actions(self, actions):
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
            self.agent_id,
            getattr(self, "_behaviour", None) or "start",
            behaviour,
            detail,
        )
        self._behaviour = behaviour
        self._behaviour_key = key

    def _publish_action(self, action: Any) -> None:
        """Publish the action selected by the ASL plan to the game thread."""
        if self.agent_id is None:
            self.logger.warning("Agent without an id, %s dropped", action)
            return
        self.spade_runtime.put_action(self.agent_type, self._action_result(action))


def agent_name(jid: str) -> str:
    """``goomba-enemy-9@localhost`` -> ``enemy-9`` (any agent kind)."""
    local = jid.split("@", 1)[0]
    for agent_type in AgentType:
        local = local.removeprefix(f"{agent_type.value}-")
    return local
