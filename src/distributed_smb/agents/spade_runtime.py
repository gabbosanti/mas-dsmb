"""SPADE agents and their asyncio event loop."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING, TypeAlias

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.domain.entity import Enemy

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
        enemies: dict[str, Enemy]
    ) -> None:
        self.perception_queue = perception_queue
        self.action_queue = action_queue
        self.stop_event = stop_event or threading.Event()
        self.enemies = enemies
                
        # Configure the BDI agent before starting the runtime thread so its asl file is loaded before any perception can reach it.
        self.goomba_agents: list[GoombaAgent] = self._create_goomba_agents(self.enemies)
        # Each behaviour accesses the queues through its owning agent. Inject
        # the runtime before start(), so on_start()/run() always see it.
        for goomba_agent in self.goomba_agents:
            goomba_agent.spade_runtime = self
        
    def request_stop(self) -> None:
        """Request graceful shutdown; safe to call from the game thread."""
        self.stop_event.set()
    
    async def run(self) -> None:
        """Start configured agents, run until requested to stop, then stop them."""
        
        try:
            for goomba_agent in self.goomba_agents:
                print("[SPADE] Starting agent...")
                await goomba_agent.start()

            while not self.stop_event.is_set():
                await asyncio.sleep(0.1)

        finally:
            for goomba_agent in self.goomba_agents:
                if goomba_agent.is_alive():
                    await goomba_agent.stop()

    @staticmethod
    def _create_goomba_agents(agents: dict[str, Enemy]) -> list[GoombaAgent]:
        """Construct the Goomba and load its ASL configuration."""
        from distributed_smb.agents.bdi.goomba import GoombaAgent

        asl_path = Path(__file__).parent / "asl" / "goomba.asl"
        if not asl_path.is_file():
            raise FileNotFoundError(f"Goomba ASL file not found: {asl_path}")
        
        goomba_agents: list[GoombaAgent] = []
                
        for goomba_id in agents.keys():
            
            jid = f"goomba{goomba_id}@localhost"
            password =f"goomba{goomba_id}_password"
            goomba_agent = GoombaAgent(jid, password,str(asl_path))
            goomba_agents.append(goomba_agent)
        
        return goomba_agents

    def get_perception(
        self, agent_type: AgentType, timeout: float | None = 0
    ) -> object:
        """Read a perception from the queue belonging to an agent type."""
        return self.perception_queue[AgentType(agent_type)].get(timeout=timeout)

    def put_action(self, agent_type: AgentType, action: object) -> None:
        """Send an action to the queue belonging to an agent type."""
        self.action_queue[AgentType(agent_type)].put(action)
