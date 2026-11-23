"""SPADE agents and their asyncio event loop."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from queue import Empty, Queue
from typing import TYPE_CHECKING

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.models import EnemyPerceptionMessage

if TYPE_CHECKING:
    from distributed_smb.agents.bdi.enemy_agent import EnemyAgent

LOGGER = logging.getLogger(__name__)

type PerceptionQueue = dict[AgentType, Queue]
type ActionQueue = dict[AgentType, Queue]
type AgentFactory = Callable[[str], "EnemyAgent"]
type DummyPlayerFactory = Callable[[str], object]

# How often the runtime moves perceptions from the game queue to the agents.
DISPATCH_INTERVAL = 0.01
ASL_DIR = Path(__file__).parent / "asl"
XMPP_DOMAIN = "localhost"


class SpadeRuntime:
    """Own SPADE setup, agent lifecycle, and shutdown on an asyncio loop.

    The game thread puts each enemy's perception on the queue of its agent
    type (GOOMBA, KOOPA, ...). The runtime is the only reader of those queues:
    it routes each perception to the agent that owns that enemy, so agents
    never steal each other's perceptions.
    """

    def __init__(
        self,
        perception_queue: PerceptionQueue,
        action_queue: ActionQueue,
        *,
        enemies: Mapping[str, AgentType] | None = None,
        stop_event: threading.Event | None = None,
        goomba_factory: AgentFactory | None = None,
        koopa_factory: AgentFactory | None = None,
        dummy_player_factory: DummyPlayerFactory | None = None,
    ) -> None:
        self.perception_queue = perception_queue
        self.action_queue = action_queue
        self.stop_event = stop_event or threading.Event()
        factories: dict[AgentType, AgentFactory] = {
            AgentType.GOOMBA: goomba_factory or self._create_goomba_agent,
            AgentType.KOOPA: koopa_factory or self._create_koopa_agent,
        }
        self.dummy_player_factory = dummy_player_factory or self._create_dummy_player
        self.player_agents: dict[str, object] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

        # Build every agent (and load its ASL) before the thread starts, and
        # inject the runtime so the agents can publish their actions.
        self.agent_types: dict[str, AgentType] = {}
        self.agents: dict[str, EnemyAgent] = {}
        for enemy_id, agent_type in (enemies or {}).items():
            agent_type = AgentType(agent_type)
            if agent_type not in factories:
                raise ValueError(f"No SPADE agent for {agent_type.value} {enemy_id!r}")
            agent = factories[agent_type](enemy_id)
            agent.enemy_id = enemy_id
            agent.spade_runtime = self
            self.agents[enemy_id] = agent
            self.agent_types[enemy_id] = agent_type

        # Agents interact through SPADE messages: each one learns the
        # addresses of all the others, grouped by kind.
        for enemy_id, agent in self.agents.items():
            peers: dict[AgentType, list[str]] = {}
            for other_id, other_type in self.agent_types.items():
                if other_id != enemy_id:
                    peers.setdefault(other_type, []).append(
                        self.jid_for(other_id, other_type)
                    )
            agent.set_peers(peers)

    def agents_of(self, agent_type: AgentType) -> dict[str, EnemyAgent]:
        return {
            enemy_id: agent
            for enemy_id, agent in self.agents.items()
            if self.agent_types[enemy_id] is agent_type
        }

    @property
    def goomba_agents(self) -> dict[str, EnemyAgent]:
        return self.agents_of(AgentType.GOOMBA)

    @property
    def koopa_agents(self) -> dict[str, EnemyAgent]:
        return self.agents_of(AgentType.KOOPA)

    def request_stop(self) -> None:
        """Request graceful shutdown; safe to call from the game thread."""
        self.stop_event.set()

    async def run(self) -> None:
        """Start the agents, dispatch perceptions until stopped, then stop them."""
        try:
            for enemy_id, agent in self.agents.items():
                agent_type = self.agent_types[enemy_id].value
                LOGGER.info("Starting SPADE %s agent for %s", agent_type, enemy_id)
                start_started = time.monotonic()
                await agent.start()
                LOGGER.info(
                    "Started SPADE %s agent for %s in %.2f seconds",
                    agent_type,
                    enemy_id,
                    time.monotonic() - start_started,
                )
            for player_id, agent in list(self.player_agents.items()):
                if getattr(agent, "is_alive", lambda: False)():
                    continue
                LOGGER.info("Starting SPADE dummy agent for %s", player_id)
                await agent.start()

            while not self.stop_event.is_set():
                self.dispatch_perceptions()
                await asyncio.sleep(DISPATCH_INTERVAL)

        finally:
            for enemy_id, agent in self.agents.items():
                if agent.is_alive():
                    LOGGER.info("Stopping SPADE agent for %s", enemy_id)
                    stop_started = time.monotonic()
                    try:
                        await agent.stop()
                    except Exception:
                        LOGGER.exception("Failed to stop SPADE agent for %s", enemy_id)
                        raise
                    LOGGER.info(
                        "Stopped SPADE agent for %s in %.2f seconds",
                        enemy_id,
                        time.monotonic() - stop_started,
                    )
            for player_id, agent in self.player_agents.items():
                if getattr(agent, "is_alive", lambda: False)():
                    await agent.stop()

    def dispatch_perceptions(self) -> int:
        """Hand the latest queued perception of each enemy to its own agent.

        Each agent type's queue is drained on its own. Older perceptions of
        the same enemy are superseded and dropped, so a slow agent always
        reasons on the freshest snapshot. Returns how many were delivered.
        """
        delivered = 0
        for agent_type in sorted(set(self.agent_types.values()), key=lambda t: t.value):
            latest: dict[str, EnemyPerceptionMessage] = {}
            while True:
                try:
                    message = self.get_perception(agent_type)
                except Empty:
                    break
                if not isinstance(message, EnemyPerceptionMessage):
                    LOGGER.warning(
                        "Discarding %s perception of type %s",
                        agent_type.value,
                        type(message).__name__,
                    )
                    continue
                latest[message.perception.enemy_id] = message

            for enemy_id, message in latest.items():
                agent = self.agents.get(enemy_id)
                if agent is None or self.agent_types[enemy_id] is not agent_type:
                    LOGGER.debug(
                        "No SPADE %s agent for %s, perception dropped",
                        agent_type.value,
                        enemy_id,
                    )
                    continue
                agent.perceive(message)
                delivered += 1
        return delivered
        latest_players: dict[str, PlayerPerceptionMessage] = {}
        while True:
            try:
                player_perception = self.get_perception(AgentType.PLAYER)
            except Empty:
                break
            if not isinstance(player_perception, PlayerPerceptionMessage):
                LOGGER.warning(
                    "Discarding PLAYER perception of type %s",
                    type(player_perception).__name__,
                )
                continue
            latest_players[player_perception.perception.player_id] = player_perception

        for player_id, player_perception in latest_players.items():
            agent = self.player_agents.get(player_id)
            if agent is None:
                LOGGER.debug(
                    "No SPADE dummy agent for %s, perception dropped", player_id
                )
                continue
            agent.perceive(player_perception)
        return len(latest) + len(latest_players)

    def add_player_agent(self, player_id: str) -> None:
        """Create and register a dummy SPADE agent for a crashed player."""
        if player_id in self.player_agents:
            return
        agent = self.dummy_player_factory(player_id)
        agent.player_id = player_id
        agent.spade_runtime = self
        self.player_agents[player_id] = agent
        if self._loop is not None:
            self._loop.call_soon_threadsafe(
                lambda: asyncio.create_task(agent.start())
            )

    def remove_player_agent(self, player_id: str) -> None:
        """Release a player ID back to normal network input."""
        agent = self.player_agents.pop(player_id, None)
        if agent is not None and getattr(agent, "is_alive", lambda: False)():
            if self._loop is not None:
                self._loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(agent.stop())
                )

    @staticmethod
    def _create_goomba_agent(enemy_id: str) -> EnemyAgent:
        """Construct a Goomba BDI agent; SPADE is imported only when needed."""
        from distributed_smb.agents.bdi.goomba import GoombaAgent

        return SpadeRuntime._create_agent(GoombaAgent, AgentType.GOOMBA, enemy_id)

    @staticmethod
    def _create_koopa_agent(enemy_id: str) -> EnemyAgent:
        """Construct a Koopa BDI agent; SPADE is imported only when needed."""
        from distributed_smb.agents.bdi.koopa import KoopaAgent

        return SpadeRuntime._create_agent(KoopaAgent, AgentType.KOOPA, enemy_id)

    @staticmethod
    def _create_agent(agent_cls, agent_type: AgentType, enemy_id: str) -> EnemyAgent:
        asl_path = ASL_DIR / f"{agent_type.value}.asl"
        if not asl_path.is_file():
            raise FileNotFoundError(
                f"{agent_type.value} ASL file not found: {asl_path}"
            )
        return agent_cls(
            SpadeRuntime.jid_for(enemy_id, agent_type),
            f"{agent_type.value}-{enemy_id}_password",
            str(asl_path),
        )

    def _create_dummy_player(self, player_id: str) -> object:
        from distributed_smb.agents.dummy_player import DummyPlayerAgent

        return DummyPlayerAgent(
            jid=f"dummy-{player_id}@{XMPP_DOMAIN}",
            password=f"dummy-{player_id}_password",
            player_id=player_id,
        )

    @staticmethod
    def jid_for(enemy_id: str, agent_type: AgentType = AgentType.GOOMBA) -> str:
        """XMPP address of the agent that controls ``enemy_id``."""
        return f"{AgentType(agent_type).value}-{enemy_id}@{XMPP_DOMAIN}"

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
