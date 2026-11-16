import asyncio
import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING

import agentspeak as asp
from spade_bdi.bdi import BDIAgent
from spade_bdi.bdi_behaviour import PERCEPT_TAG, BDIBehaviour

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.models import (
    ActionResult,
    EnemyAction,
    KoopaPerception,
    KoopaPerceptionMessage,
    SightDirection,
)
from distributed_smb.agents.perception import GroundSensor, Vision, side_of

if TYPE_CHECKING:
    from distributed_smb.agents.spade_runtime import SpadeRuntime

LOGGER = logging.getLogger(__name__)

# spade_bdi's reasoning cycle never yields to the event loop on its own: it
# would spin at 100% CPU and, through the GIL, slow down the game thread.
BDI_CYCLE_PERIOD = 0.02

# kill_mario behaviour: a player is noticed when it is within this horizontal
# range and at the same height, i.e. its center is at most
# PLAYER_VERTICAL_TOLERANCE px above/below the Goomba's center.
PLAYER_DETECTION_RANGE = 200.0

PLAYER_VERTICAL_TOLERANCE = 32.0

# Below this horizontal distance the side of the player is ambiguous (the
# bodies overlap): ignore it rather than flip direction at every perception.
PLAYER_SIDE_DEADBAND = 4.0

# Upper bound of interpreter steps per cycle, so a runaway plan cannot
# monopolise the SPADE event loop.
MAX_STEPS_PER_CYCLE = 64


class KoopaPatrolBehaviour(BDIBehaviour):
    #spade_bdi reasoning cycle that runs at most once every BDI_CYCLE_PERIOD.

    async def run(self):
        await super().run()  # applies queued belief changes + one step
        if self.agent.bdi_enabled:
            for _ in range(MAX_STEPS_PER_CYCLE):
                if not self.agent.bdi_agent.step():
                    break
        await asyncio.sleep(BDI_CYCLE_PERIOD)


class KoopaAgent(BDIAgent):
    
    """
    Koopa driven by koopa.asl,

    This class only senses (snapshot -> beliefs) and acts (ASL action -> game
    command): every decision, including who talks to whom, is in the ASL.
    """

    # Both injected by SpadeRuntime right after construction.
    spade_runtime: "SpadeRuntime"
    goomba_id: str | None = None

    player_sensor = Vision(sight_distance=PLAYER_DETECTION_RANGE)

    def add_behaviour(self, behaviour, template=None):
        # BDIAgent.__init__ installs a plain BDIBehaviour: swap in the
        # throttled one, keeping the same message template.
        if isinstance(behaviour, BDIBehaviour) and not isinstance(
            behaviour, KoopaPatrolBehaviour
        ):
            behaviour = KoopaPatrolBehaviour()
        super().add_behaviour(behaviour, template)

    async def setup(self):
        LOGGER.info("Koopa agent %s started", self.goomba_id)

    def set_peers(self, jids: Iterable[str]) -> None:
        """Tell the agent which other agents it can talk to (``peers(List)``)."""
        peers = tuple(jids)
        self._peer_count = len(peers)
        self.bdi.set_belief("peers", peers)

    def perceive(self, message: KoopaPerceptionMessage) -> None:
        """Entry point used by SpadeRuntime to deliver this Goomba's perception."""
        if not getattr(self, "_logged_first_perception", False):
            LOGGER.info(
                "Koopa %s received perception %s",
                self.goomba_id,
                message.simulation_sequence,
            )
            self._logged_first_perception = True
        else:
            LOGGER.debug(
                "Goomba %s received perception %s",
                self.goomba_id,
                message.simulation_sequence,
            )
        self._update_beliefs(message.perception, message.simulation_sequence)

    def _update_beliefs(self, perception: KoopaPerception, sequence: int) -> None:
        """Translate a game snapshot into facts consumed by the ASL plans."""

        center = perception.center
        self._last_center = center
        self.bdi.set_belief("position", round(center.x), round(center.y))

        # kill_mario: is a player near, and on which side?
        player = self.player_sensor.nearest_player_at_height(
            center,
            perception.players,
            perception.obstacles,
            vertical_tolerance=PLAYER_VERTICAL_TOLERANCE,
            deadband=PLAYER_SIDE_DEADBAND,
        )
        self._last_player = player
        # encircle: the sighting the ASL shares with the other agents.
        self._set_single_belief(
            "player_sighted",
            (round(player.center.x), round(player.center.y), sequence)
            if player is not None
            else None,
        )
        self._set_single_belief(
            "player_near",
            (side_of(player.center, center).value,) if player is not None else None,
        )

        # TODO

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


def _agent_name(jid: str) -> str:
    """``koopa-enemy-9@localhost`` -> ``enemy-9``."""
    local = jid.split("@", 1)[0]
    return local.removeprefix("goomba-")
