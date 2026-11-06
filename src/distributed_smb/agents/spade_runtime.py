"""SPADE agents and their asyncio event loop."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING

from distributed_smb.agents.agent_type import AgentType

if TYPE_CHECKING:
    from distributed_smb.agents.bdi.goomba import GoombaAgent

type PerceptionQueue = dict[AgentType, Queue]
type ActionQueue = dict[AgentType, Queue]


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

        # Goomba configuration
        asl_path = Path(__file__).with_name("goomba.asl")

        goombaAgent = self._create_goomba_agent(asl_path)
        goombaAgent.spade_runtime = self

        try:
            print("[SPADE] Starting agent...")
            await goombaAgent.start()

            while not self.stop_event.is_set():
                await asyncio.sleep(0.1)

        finally:
            if goombaAgent.is_alive():
                await goombaAgent.stop()

    @staticmethod
    def _create_goomba_agent(asl_path: Path) -> GoombaAgent:
        """Import SPADE only when the real agent runtime is actually started."""
        from distributed_smb.agents.bdi.goomba import GoombaAgent

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
