"""SPADE agents and their asyncio event loop."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from queue import Empty, Queue
from typing import TYPE_CHECKING, TypeAlias

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.models import GoombaPerceptionMessage

if TYPE_CHECKING:
    from distributed_smb.agents.bdi.goomba import GoombaAgent

LOGGER = logging.getLogger(__name__)

PerceptionQueue: TypeAlias = dict[AgentType, Queue]
ActionQueue: TypeAlias = dict[AgentType, Queue]
GoombaFactory: TypeAlias = Callable[[str], "GoombaAgent"]

# How often the runtime moves perceptions from the game queue to the agents.
DISPATCH_INTERVAL = 0.01
GOOMBA_ASL_PATH = Path(__file__).parent / "asl" / "goomba.asl"


class SpadeRuntime:
    """Own SPADE setup, agent lifecycle, and shutdown on an asyncio loop.

    The game thread puts every Goomba's perception on the single GOOMBA queue.
    The runtime is its only reader: it routes each perception to the agent
    that owns that enemy, so agents never steal each other's perceptions.
    """

    def __init__(
        self,
        perception_queue: PerceptionQueue,
        action_queue: ActionQueue,
        *,
        enemy_ids: Iterable[str] = (),
        stop_event: threading.Event | None = None,
        goomba_factory: GoombaFactory | None = None,
    ) -> None:
        self.perception_queue = perception_queue
        self.action_queue = action_queue
        self.stop_event = stop_event or threading.Event()
        factory = goomba_factory or self._create_goomba_agent

        # Build every agent (and load its ASL) before the thread starts, and
        # inject the runtime so the agents can publish their actions.
        self.goomba_agents: dict[str, GoombaAgent] = {}
        for enemy_id in enemy_ids:
            agent = factory(enemy_id)
            agent.goomba_id = enemy_id
            agent.spade_runtime = self
            self.goomba_agents[enemy_id] = agent

    def request_stop(self) -> None:
        """Request graceful shutdown; safe to call from the game thread."""
        self.stop_event.set()

    async def run(self) -> None:
        """Start the agents, dispatch perceptions until stopped, then stop them."""
        try:
            for enemy_id, agent in self.goomba_agents.items():
                LOGGER.info("Starting SPADE agent for %s", enemy_id)
                await agent.start()

            while not self.stop_event.is_set():
                self.dispatch_perceptions()
                await asyncio.sleep(DISPATCH_INTERVAL)

        finally:
            for agent in self.goomba_agents.values():
                if agent.is_alive():
                    await agent.stop()

    def dispatch_perceptions(self) -> int:
        """Hand the latest queued perception of each enemy to its own agent.

        Older perceptions of the same enemy are superseded and dropped, so a
        slow agent always reasons on the freshest snapshot.
        """
        latest: dict[str, GoombaPerceptionMessage] = {}
        while True:
            try:
                message = self.get_perception(AgentType.GOOMBA)
            except Empty:
                break
            if not isinstance(message, GoombaPerceptionMessage):
                LOGGER.warning(
                    "Discarding GOOMBA perception of type %s", type(message).__name__
                )
                continue
            latest[message.perception.goomba_id] = message

        for goomba_id, message in latest.items():
            agent = self.goomba_agents.get(goomba_id)
            if agent is None:
                LOGGER.debug("No SPADE agent for %s, perception dropped", goomba_id)
                continue
            agent.perceive(message)
        return len(latest)

    @staticmethod
    def _create_goomba_agent(enemy_id: str) -> GoombaAgent:
        """Construct a Goomba BDI agent; SPADE is imported only when needed."""
        from distributed_smb.agents.bdi.goomba import GoombaAgent

        if not GOOMBA_ASL_PATH.is_file():
            raise FileNotFoundError(f"Goomba ASL file not found: {GOOMBA_ASL_PATH}")
        return GoombaAgent(
            f"goomba-{enemy_id}@localhost",
            f"goomba-{enemy_id}_password",
            str(GOOMBA_ASL_PATH),
        )

    def get_perception(
        self, agent_type: AgentType, timeout: float | None = 0
    ) -> object:
        """Read a perception from the queue belonging to an agent type."""
        if timeout == 0:
            return self.perception_queue[AgentType(agent_type)].get_nowait()
        return self.perception_queue[AgentType(agent_type)].get(timeout=timeout)

    def put_action(self, agent_type: AgentType, action: object) -> None:
        """Send an action to the queue belonging to an agent type."""
        self.action_queue[AgentType(agent_type)].put(action)
