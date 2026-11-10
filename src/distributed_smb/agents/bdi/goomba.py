import asyncio
import logging
from typing import TYPE_CHECKING

import agentspeak as asp
from spade_bdi.bdi import BDIAgent
from spade_bdi.bdi_behaviour import BDIBehaviour

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.models import (
    ActionResult,
    EnemyAction,
    GoombaPerception,
    GoombaPerceptionMessage,
    SightDirection,
)
from distributed_smb.agents.perception import GroundSensor, Vision

if TYPE_CHECKING:
    from distributed_smb.agents.spade_runtime import SpadeRuntime

LOGGER = logging.getLogger(__name__)

# spade_bdi's reasoning cycle never yields to the event loop on its own: it
# would spin at 100% CPU and, through the GIL, slow down the game thread.
BDI_CYCLE_PERIOD = 0.02
# Distance from the Goomba's center to a wall's nearest face that makes it
# turn: half its width (~25px) plus a ~22px gap that absorbs the BDI latency
# (one perception every 5 ticks + dispatch + reasoning ≈ 12px at 80px/s).
# It must stay below half the narrowest corridor, otherwise a Goomba between
# two pipes always sees a wall ahead and turns on every perception: in the
# level the gap between pipe-2 and pipe-3 is only 136px.
OBSTACLE_REACTION_DISTANCE = 48.0


# Upper bound of interpreter steps per cycle, so a runaway plan cannot
# monopolise the SPADE event loop.
MAX_STEPS_PER_CYCLE = 64


class ThrottledBDIBehaviour(BDIBehaviour):
    """spade_bdi reasoning cycle that runs at most once every ``BDI_CYCLE_PERIOD``.

    The stock cycle executes a single interpreter step per run, so a plan of
    a few steps took several cycles (60-80ms) to produce its action. Here
    each cycle runs the intentions until they are idle, so a perception is
    turned into an action within the same cycle.
    """

    async def run(self):
        await super().run()  # applies queued belief changes + one step
        if self.agent.bdi_enabled:
            for _ in range(MAX_STEPS_PER_CYCLE):
                if not self.agent.bdi_agent.step():
                    break
        await asyncio.sleep(BDI_CYCLE_PERIOD)


class GoombaAgent(BDIAgent):
    # Both injected by SpadeRuntime right after construction.
    spade_runtime: "SpadeRuntime"
    goomba_id: str | None = None

    bump_sensor = Vision(sight_distance=OBSTACLE_REACTION_DISTANCE)
    ground_sensor = GroundSensor()

    def add_behaviour(self, behaviour, template=None):
        # BDIAgent.__init__ installs a plain BDIBehaviour: swap in the
        # throttled one, keeping the same message template.
        if isinstance(behaviour, BDIBehaviour) and not isinstance(
            behaviour, ThrottledBDIBehaviour
        ):
            behaviour = ThrottledBDIBehaviour()
        super().add_behaviour(behaviour, template)

    async def setup(self):
        LOGGER.info("Goomba agent %s started", self.goomba_id)

    def perceive(self, message: GoombaPerceptionMessage) -> None:
        """Entry point used by SpadeRuntime to deliver this Goomba's perception."""
        if not getattr(self, "_logged_first_perception", False):
            LOGGER.info(
                "Goomba %s received perception %s",
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

    def _update_beliefs(self, perception: GoombaPerception, sequence: int) -> None:
        """Translate a game snapshot into facts consumed by the ASL plans."""
        obstacle_ahead = self.bump_sensor.obstacle_ahead(
            position=perception.center,
            direction=perception.direction,
            obstacles=perception.obstacles,
        )
        void_ahead = self.ground_sensor.is_void_ahead(
            position=perception.position,
            width=perception.width,
            height=perception.height,
            direction=perception.direction,
            obstacles=perception.obstacles,
        )

        for belief, present in (
            ("obstacle_ahead", obstacle_ahead),
            ("void_ahead", void_ahead),
            ("clear_ahead", not obstacle_ahead and not void_ahead),
        ):
            if present:
                self.bdi.set_belief(belief)
            else:
                self.bdi.remove_belief(belief)

        self.bdi.set_belief("direction", perception.direction.value)
        # This fresh event triggers exactly one ASL decision for this snapshot.
        self.bdi.set_belief("perception", sequence)

    def add_custom_actions(self, actions):
        @actions.add(".move_right", 0)
        def move_right(agent, term, intention):
            self._publish_action(EnemyAction.MOVE_RIGHT)
            yield

        @actions.add(".move_left", 0)
        def move_left(agent, term, intention):
            self._publish_action(EnemyAction.MOVE_LEFT)
            yield

        @actions.add(".turn_around", 1)
        def turn_around(agent, term, intention):
            # Turn away from the heading of the snapshot that triggered the
            # plan (passed by the ASL), never from the current belief: by now
            # a newer perception may have updated it, and turning from that
            # would send the Goomba straight back towards the hazard.
            # The move is absolute, so repeated turns for stale snapshots of
            # the same situation are no-ops instead of cancelling out.
            # Beliefs are not touched here: perceptions are their only source.
            heading = str(asp.grounded(term.args[0], intention.scope))
            if heading == SightDirection.RIGHT.value:
                self._publish_action(EnemyAction.MOVE_LEFT)
            elif heading == SightDirection.LEFT.value:
                self._publish_action(EnemyAction.MOVE_RIGHT)
            else:
                LOGGER.warning("Goomba %s: unknown heading %r", self.goomba_id, heading)
            yield

    def _publish_action(self, action: EnemyAction) -> None:
        """Publish the action selected by the ASL plan to the game thread."""
        if self.goomba_id is None:
            LOGGER.warning("Goomba agent without an enemy id, %s dropped", action)
            return
        self.spade_runtime.put_action(
            AgentType.GOOMBA,
            ActionResult(enemy_id=self.goomba_id, action=action),
        )
