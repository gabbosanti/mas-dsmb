"""SPADE agents and their asyncio event loop."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING, TypeAlias

from distributed_smb.agents.agent_type import AgentType

if TYPE_CHECKING:
    from distributed_smb.agents.bdi.goomba import GoombaAgent

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
        goomba_agent: GoombaAgent | None = None,
    ) -> None:
        self.perception_queue = perception_queue
        self.action_queue = action_queue
        self.stop_event = stop_event or threading.Event()

        # Configure the BDI agent before starting the runtime thread so its
        # ASL file is loaded before any perception can reach it.
        self.goomba_agent = (
            self._create_goomba_agent() if goomba_agent is None else goomba_agent
        )
        if self.goomba_agent.bdi_agent is None:
            raise RuntimeError("The Goomba ASL file could not be loaded; BDI is disabled")
        self.goomba_agent.spade_runtime = self

    def request_stop(self) -> None:
        """Request graceful shutdown; safe to call from the game thread."""
        self.stop_event.set()
    
    async def run(self) -> None:
        """Start configured agents, run until requested to stop, then stop them."""
        
        goomba_agent = self.goomba_agent
        try:
            print("[SPADE] Starting agent...")
            await goomba_agent.start()
            
            while not self.stop_event.is_set():
                await asyncio.sleep(0.1)
                
        finally:
            if goomba_agent.is_alive():
                await goomba_agent.stop()

    @staticmethod
    def _create_goomba_agent() -> GoombaAgent:
        """Construct the Goomba and load its ASL configuration."""
        from distributed_smb.agents.bdi.goomba import GoombaAgent

        asl_path = Path(__file__).parent / "asl" / "goomba.asl"
        if not asl_path.is_file():
            raise FileNotFoundError(f"Goomba ASL file not found: {asl_path}")
        return GoombaAgent(
            "goomba@localhost",
            "goomba_password",
            str(asl_path),
        )

    def get_perception(
        self, agent_type: AgentType, timeout: float | None = 0
    ) -> object:
        """Read a perception from the queue belonging to an agent type."""
        return self.perception_queue[AgentType(agent_type)].get(timeout=timeout)

    def put_action(self, agent_type: AgentType, action: object) -> None:
        """Send an action to the queue belonging to an agent type."""
        self.action_queue[AgentType(agent_type)].put(action)
