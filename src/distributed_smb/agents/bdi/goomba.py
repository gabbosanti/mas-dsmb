import asyncio
from spade_bdi.bdi import BDIAgent
from spade.behaviour import CyclicBehaviour

from distributed_smb.agents.agent_type import AgentType

class GoombaAgent(BDIAgent):
    class PatrolBehaviour(CyclicBehaviour):
        async def on_start(self):
            print("Starting behaviour . . .")

        async def run(self):
            await asyncio.sleep(1)
            #ricevo la percezione in formato json string
            goombaPerception = self.get_perception(AgentType.GOOMBA)
            print(f"[Agent] Perception received: {goombaPerception}")
        
            if (goombaPerception.obstacle_ahead == True) : #Se c'è un ostacolo davanti
                self.bdi.set_belief("obstacle_ahead")  #aggiorno la belief

            #asl ragiona ed elabora un piano....
        
            self.put_action(AgentType.GOOMBA, action)
            print("[Agent] Action sent")

            print("[SPADE] Agent started")


        async def on_end(self):
            print("Behaviour finished with exit code {}.".format(self.exit_code))

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
            
            if(self.bdi.get_belief("direction") == "right"):
                self.bdi.set_belief("direction", "left")

            elif(self.bdi.get_belief("direction") == "left"):
                self.bdi.set_belief("direction", "right")

            yield
