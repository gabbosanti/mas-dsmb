import asyncio
from typing import TYPE_CHECKING

from spade_bdi.bdi import BDIAgent
from spade.behaviour import CyclicBehaviour

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.models import (
    ActionResult,
    EnemyAction,
    GoombaPerceptionMessage,
)
from distributed_smb.agents.perception import GroundSensor, Vision
if TYPE_CHECKING:
    from distributed_smb.agents.spade_runtime import SpadeRuntime

class GoombaAgent(BDIAgent):
    spade_runtime: "SpadeRuntime"
    vision = Vision()
    ground_sensor = GroundSensor()

    class PatrolBehaviour(CyclicBehaviour):
        async def on_start(self):
            print("Starting behaviour . . .")
            agent = self.agent
            if not isinstance(agent, GoombaAgent):
                raise RuntimeError("PatrolBehaviour is not attached to a GoombaAgent")
            self.goomba_agent = agent
            
        async def run(self):
            await asyncio.sleep(1)

            agent = self.goomba_agent
            
            goombaPerceptionMessage = agent.spade_runtime.get_perception(AgentType.GOOMBA)
            if not isinstance(goombaPerceptionMessage, GoombaPerceptionMessage):
                raise TypeError(
                    "The GOOMBA perception queue must contain "
                    "GoombaPerceptionMessage instances"
                )

            print(f"[Agent] Perception received: {goombaPerceptionMessage.perception}")
            goombaPerception = goombaPerceptionMessage.perception
            agent.goomba_id = goombaPerception.goomba_id

            agent._update_beliefs(goombaPerception)

            print("[Agent] Beliefs updated; ASL plans choose the next action")


        async def on_end(self):
            print("Behaviour finished with exit code {}.".format(self.exit_code))

    async def setup(self):
        print("Agent starting . . .")
        self.goomba_id = None
        self.patrol_behaviour = self.PatrolBehaviour()
        self.add_behaviour(self.patrol_behaviour)
        await asyncio.sleep(1)

    def _update_beliefs(self, perception) -> None:
        """Translate a game snapshot into facts consumed by the ASL plans."""
        obstacle_ahead = self.vision.obstacle_ahead(
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
        ):
            if present:
                self.bdi.set_belief(belief)
            else:
                self.bdi.remove_belief(belief)
        
    def add_custom_actions(self, actions):

        @actions.add(".move_right", 0)
        def move_right(agent, term, intention):
            print("Goomba moves right!")
            self._publish_action(EnemyAction.MOVE_RIGHT)
            yield

        @actions.add(".move_left", 0)
        def move_left(agent, term, intention):
            print("Goomba moves left!")
            self._publish_action(EnemyAction.MOVE_LEFT)
            yield

        @actions.add(".turn_around", 0)
        def turn_around(agent, term, intention):
            self._publish_action(EnemyAction.TURN_AROUND)
            self.bdi.remove_belief("obstacle_ahead")
            self.bdi.remove_belief("void_ahead")
            
            direction = self.bdi.get_belief_value("direction")
            if direction == ("right",):
                self.bdi.set_belief("direction", "left")

            elif direction == ("left",):
                self.bdi.set_belief("direction", "right")

            yield

    def _publish_action(self, action: EnemyAction) -> None:
        """Publish the action selected by the ASL plan to the game thread."""
        goomba_id = getattr(self, "goomba_id", None)
        if goomba_id is None:
            # The initial ASL goal can run before the first game perception.
            return
        self.spade_runtime.put_action(
            AgentType.GOOMBA,
            ActionResult(enemy_id=goomba_id, action=action),
        )
            
