import asyncio
import logging
from typing import TYPE_CHECKING

from spade_bdi.bdi import BDIAgent
from spade_bdi.bdi_behaviour import BDIBehaviour

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.models import (
    ActionResult,
    EnemyAction,
    GoombaPerception,
    GoombaPerceptionMessage,
)
from distributed_smb.agents.perception import GroundSensor, Vision

if TYPE_CHECKING:
    from distributed_smb.agents.spade_runtime import SpadeRuntime

LOGGER = logging.getLogger(__name__)

# spade_bdi's reasoning cycle never yields to the event loop on its own: it
# would spin at 100% CPU and, through the GIL, slow down the game thread.
BDI_CYCLE_PERIOD = 0.02
# Centre-to-centre distance at which a wall ahead makes the Goomba turn.
# Kept short on purpose: with the 200px sight range a Goomba between two
# pipes would always see a wall ahead and turn on every perception.
OBSTACLE_REACTION_DISTANCE = 80.0


class ThrottledBDIBehaviour(BDIBehaviour):
    """spade_bdi reasoning cycle that runs at most once every ``BDI_CYCLE_PERIOD``."""

    async def run(self):
        await super().run()
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

        @actions.add(".turn_around", 0)
        def turn_around(agent, term, intention):
            # Turn relative to the perceived direction, but send an absolute
            # move: if two stale perceptions both ask to turn, the second
            # request is a no-op instead of turning the Goomba back again.
            direction = self.bdi.get_belief_value("direction")
            if direction == ("right",):
                self._publish_action(EnemyAction.MOVE_LEFT)
                self.bdi.set_belief("direction", "left")
            elif direction == ("left",):
                self._publish_action(EnemyAction.MOVE_RIGHT)
                self.bdi.set_belief("direction", "right")
            self.bdi.remove_belief("obstacle_ahead")
            self.bdi.remove_belief("void_ahead")
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
