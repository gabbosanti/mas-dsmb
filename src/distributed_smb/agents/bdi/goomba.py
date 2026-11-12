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


class GoombaPatrolBehaviour(BDIBehaviour):
    #spade_bdi reasoning cycle that runs at most once every BDI_CYCLE_PERIOD.

    async def run(self):
        await super().run()  # applies queued belief changes + one step
        if self.agent.bdi_enabled:
            for _ in range(MAX_STEPS_PER_CYCLE):
                if not self.agent.bdi_agent.step():
                    break
        await asyncio.sleep(BDI_CYCLE_PERIOD)


class GoombaAgent(BDIAgent):
    """
    Goomba driven by goomba.asl, which groups its plans in two behaviours:
        - patrol: walk and turn around at walls and at the edge of platforms;
        - kill_mario: when a player is near and at the same height, walk towards
          it, unless that side is blocked by a wall or a void.
    """

    # Both injected by SpadeRuntime right after construction.
    spade_runtime: "SpadeRuntime"
    goomba_id: str | None = None

    ground_sensor = GroundSensor()
    bump_sensor = Vision(sight_distance=OBSTACLE_REACTION_DISTANCE)
    player_sensor = Vision(sight_distance=PLAYER_DETECTION_RANGE)

    def add_behaviour(self, behaviour, template=None):
        # BDIAgent.__init__ installs a plain BDIBehaviour: swap in the
        # throttled one, keeping the same message template.
        if isinstance(behaviour, BDIBehaviour) and not isinstance(
            behaviour, GoombaPatrolBehaviour
        ):
            behaviour = GoombaPatrolBehaviour()
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
        
        void_ahead = self._void_towards(perception, perception.direction)

        # kill_mario: where is the player, and can the Goomba walk that way?
        player_side = self.player_sensor.player_side(
            perception.center,
            perception.players,
            perception.obstacles,
            vertical_tolerance=PLAYER_VERTICAL_TOLERANCE,
            deadband=PLAYER_SIDE_DEADBAND,
        )

        blocked_side = None
        if player_side is not None and self._blocked_towards(perception, player_side):
            blocked_side = player_side

        self._set_side_belief("player_near", player_side)
        self._set_side_belief("path_blocked", blocked_side)

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

    def _void_towards(
        self, perception: GoombaPerception, direction: SightDirection
    ) -> bool:
        return self.ground_sensor.is_void_ahead(
            position=perception.position,
            width=perception.width,
            height=perception.height,
            direction=direction,
            obstacles=perception.obstacles,
        )

    def _blocked_towards(
        self, perception: GoombaPerception, direction: SightDirection
    ) -> bool:
        """True if walking towards ``direction`` hits a wall or a void."""
        return self._void_towards(perception, direction) or self.bump_sensor.obstacle_ahead(
            position=perception.center,
            direction=direction,
            obstacles=perception.obstacles,
        )

    def _set_side_belief(self, name: str, side: SightDirection | None) -> None:
        """Keep at most one ``name(left|right)`` belief, or none.

        ``remove_belief(name)`` alone would not match ``name(left)`` (different
        arity), so the side last set is remembered to remove it explicitly.
        """
        side_beliefs: dict[str, str | None] = self.__dict__.setdefault(
            "_side_beliefs", {}
        )
        previous = side_beliefs.get(name)
        current = side.value if side is not None else None
        if previous == current:
            return
        if previous is not None:
            self.bdi.remove_belief(name, previous)
        if current is not None:
            self.bdi.set_belief(name, current)
        side_beliefs[name] = current

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

            heading = str(asp.grounded(term.args[0], intention.scope))
            if heading == SightDirection.RIGHT.value:
                self._publish_action(EnemyAction.MOVE_LEFT)
            elif heading == SightDirection.LEFT.value:
                self._publish_action(EnemyAction.MOVE_RIGHT)
            else:
                LOGGER.warning("Goomba %s: unknown heading %r", self.goomba_id, heading)
            yield

        @actions.add(".chase", 1)
        def chase(agent, term, intention):
            # kill_mario behaviour: walk towards the player's side. A separate
            # action from .move_* only to tell the two behaviours apart.
            side = str(asp.grounded(term.args[0], intention.scope))
            LOGGER.debug("Goomba %s chases the player on the %s", self.goomba_id, side)
            if side == SightDirection.RIGHT.value:
                self._publish_action(EnemyAction.MOVE_RIGHT)
            elif side == SightDirection.LEFT.value:
                self._publish_action(EnemyAction.MOVE_LEFT)
            else:
                LOGGER.warning("Goomba %s: unknown side %r", self.goomba_id, side)
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
