"""SPADE agents and their asyncio event loop."""

import asyncio
import threading
from pathlib import Path
from queue import Queue
from typing import TypeAlias

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.bdi.goomba import GoombaAgent
from distributed_smb.agents.dto.goomba.goombaAction import GoombaActionDTO
from distributed_smb.agents.dto.goomba.goombaPerception import GoombaPerceptionDTO

PerceptionQueue: TypeAlias = dict[AgentType, Queue]
ActionQueue: TypeAlias = dict[AgentType, Queue]


class SpadeRuntime:
    """Own SPADE setup, agent lifecycle, and shutdown on an asyncio loop."""

    def __init__(
        self,
        perception_queue: PerceptionQueue,
        action_queue: ActionQueue,
        *,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.perception_queue = perception_queue
        self.action_queue = action_queue
        self.stop_event = stop_event or threading.Event()

    def request_stop(self) -> None:
        """Request graceful shutdown; safe to call from the game thread."""
        self.stop_event.set()
    
    async def run(self) -> None:
        """Start configured agents, run until requested to stop, then stop them."""
        
        #Goomba configuration
        asl_path = Path(__file__).with_name("goomba.asl")
        
        goombaAgent = GoombaAgent(
                    "goomba@localhost",
            "goomba_password",
            str(asl_path),
        )
        goombaAgent.spade_runtime = self
                        
        try:
            print("[SPADE] Starting agent...")
            await goombaAgent.start()
            
            while not self.stop_event.is_set():
                await asyncio.sleep(0.1)
                
        finally:
            if goombaAgent.is_alive():
                await goombaAgent.stop()

    def get_perception(
        self, agent_type: AgentType, timeout: float | None = 0
    ) -> object:
        """Read a perception from the queue belonging to an agent type."""
        return self.perception_queue[AgentType(agent_type)].get(timeout=timeout)

    def put_action(self, agent_type: AgentType, action: object) -> None:
        """Send an action to the queue belonging to an agent type."""
        self.action_queue[AgentType(agent_type)].put(action)
