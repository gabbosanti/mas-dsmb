"""SPADE agents and their asyncio event loop."""

import asyncio
import threading
from collections.abc import Mapping
from pathlib import Path
from queue import Queue
from typing import TypeAlias

import spade

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.bdi.goomba import GoombaAgent
from distributed_smb.agents.dto.goombaAction import GoombaActionDTO
from distributed_smb.agents.dto.goombaPerception import GoombaPerceptionDTO

PerceptionQueue: TypeAlias = dict[AgentType, Queue[GoombaPerceptionDTO]]
ActionQueue: TypeAlias = dict[AgentType, Queue[GoombaActionDTO]]


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
        asl_path = Path(__file__).with_name("goomba.asl")
        
        goombaAgent = GoombaAgent(
                    "goomba@localhost",
                    "goomba_password",
                    str(asl_path)
        )
                        
        try:
            print("[SPADE] Starting agent...")
            await goombaAgent.start()
            
            print("Wait until user interrupts with ctrl+C")
            await spade.wait_until_finished(goombaAgent)
            
            while not self.stop_event.is_set():
                await asyncio.sleep(0.1)
                
        finally:
            if goombaAgent.is_alive():
                await goombaAgent.stop()

