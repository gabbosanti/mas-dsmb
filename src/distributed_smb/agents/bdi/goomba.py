import asyncio
from typing import TYPE_CHECKING

from spade.behaviour import CyclicBehaviour
from spade_bdi.bdi import BDIAgent

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.dto.goomba.goombaAction import ActionType, GoombaActionDTO
from distributed_smb.agents.dto.goomba.goombaPerception import GoombaPerceptionDTO

if TYPE_CHECKING:
    from distributed_smb.agents.spade_runtime import SpadeRuntime


class GoombaAgent(BDIAgent):
    # Injected by SpadeRuntime after construction to avoid a module import cycle.
    spade_runtime: "SpadeRuntime"

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

            goombaPerception = agent.spade_runtime.get_perception(AgentType.GOOMBA)
            if not isinstance(goombaPerception, GoombaPerceptionDTO):
                raise TypeError(
                    "The GOOMBA perception queue must contain "
                    "GoombaPerceptionDTO instances"
                )

            print(f"[Agent] Perception received: {goombaPerception}")

            goombaAction = GoombaActionDTO(action=None)

            if goombaPerception.obstacle_ahead:
                agent.bdi.set_belief("obstacle_ahead")
                goombaAction.__setattr__("action", ActionType.TURN_AROUND)

            elif goombaPerception.direction == "right":
                goombaAction.__setattr__("action", ActionType.MOVE_RIGHT)

            elif goombaPerception.direction == "left":
                goombaAction.__setattr__("action", ActionType.MOVE_LEFT)

            agent.spade_runtime.put_action(AgentType.GOOMBA, goombaAction)
            print("[Agent] Action sent")

        async def on_end(self):
            print(f"Behaviour finished with exit code {self.exit_code}.")

    async def setup(self):
        print("Agent starting . . .")
        self.patrol_behaviour = self.PatrolBehaviour()
        self.add_behaviour(self.patrol_behaviour)
        await asyncio.sleep(1)

    def add_custom_actions(self, actions):
        @actions.add(".move_right", 0)
        def move_right(agent, term, intention):
            print("Goomba moves right!")
            yield

        @actions.add(".move_left", 0)
        def move_left(agent, term, intention):
            print("Goomba moves left!")
            yield

        @actions.add(".turn_around", 0)
        def turn_around(agent, term, intention):
            self.bdi.remove_belief("obstacle_ahead")

            if self.bdi.get_belief("direction") == "right":
                self.bdi.set_belief("direction", "left")

            elif self.bdi.get_belief("direction") == "left":
                self.bdi.set_belief("direction", "right")

            yield
