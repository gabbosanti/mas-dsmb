"""Own the thread that runs SPADE's asyncio event loop."""

import asyncio
import json
import logging
from pathlib import Path
import threading
from collections.abc import Awaitable, Callable
from enum import Enum
from queue import Queue
from typing import TypeAlias
from distributed_smb.agents.bdi.goomba import GoombaAgent
from distributed_smb.agents.dto.goombaPerception import GoombaPerceptionDTO
import spade

LOGGER = logging.getLogger(__name__)

class AgentType(Enum):
    #Supported autonomous-agent categories

    GOOMBA = "goomba"
    KOOPA = "koopa"
    PLAYER = "player"


PerceptionQueue: TypeAlias = dict[AgentType, Queue[GoombaPerceptionDTO]]
ActionQueue: TypeAlias = dict[AgentType, Queue[GoombaActionDTO]]

SpadeRunner = Callable[[PerceptionQueue, ActionQueue, threading.Event], Awaitable[None]]

class SpadeRuntime:
    """Run one asynchronous SPADE runner on a dedicated background thread.

    Each agent category has its own perception and action queue. 
    The runner is expected to stop its SPADE agents when stop_event is set.
    """

    def __init__(self, runner: SpadeRunner | None = None) -> None:
        self.perception_queue: PerceptionQueue = {
            agent_type: Queue() for agent_type in AgentType
        }
        self.action_queue: ActionQueue = {
            agent_type: Queue() for agent_type in AgentType
        }
        self.stop_event = threading.Event()
        self._runner = runner
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._eventReady = threading.Event()
        self._error: Exception | None = None

    @property # Is the thread running ? (True/False)
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property # Return an exception raised by the SPADE runner, if any
    def error(self) -> Exception | None:
        return self._error

    def start(self, timeout: float = 5.0) -> None:
        """Start the SPADE event loop once; repeated calls while running are safe."""
        with self._lock:
            if self.is_running:
                return

            self._error = None
            self._thread = threading.Thread(
                target=self._thread_main,
                name="spade-runtime",
                daemon=True,
            )
            self._thread.start()
            thread = self._thread

        if not self._eventReady.wait(timeout):
            raise TimeoutError("Timed out while starting the SPADE runtime")
        if not thread.is_alive() and self._error is not None:
            raise RuntimeError(
                "The SPADE runtime failed during startup"
            ) from self._error

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the runner to stop and wait for SPADE to shut down."""
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            if thread.is_alive():
                self.stop_event.set()

        if thread.is_alive():
            thread.join(timeout)

        if self._error is not None:
            raise RuntimeError("The SPADE runtime failed") from self._error
        
    def get_perception(self, agent_type: AgentType, timeout: float | None = 0) -> GoombaPerceptionDTO :
        # Read a perception from the queue for an agent type
        self.perception_queue[AgentType(agent_type)].get(timeout=timeout)

    def put_action(self, agent_type: AgentType, action: GoombaActionDTO) -> None :
        # Send an action to the queue for his agent type
        return self.action_queue[AgentType(agent_type)].put(action)

    async def _main(self) -> None:
        self._eventReady.set()
        if self._runner is None:
            while not self.stop_event.is_set():
                await asyncio.sleep(0.1)
            return
        
        asl_path = Path(__file__).with_name("goomba.asl")

        goombaAgent = GoombaAgent(
            "goomba@localhost",
            "goomba_password",
            str(asl_path)
        )

        print("[SPADE] Starting agent...")

        await goombaAgent.start()
        
        print("Wait until user interrupts with ctrl+C")
        await spade.wait_until_finished(goombaAgent)
        
        #ricevo la percezione in formato json string
        goombaPerception = self.get_perception(AgentType.GOOMBA)
        print(f"[Agent] Perception received: {goombaPerception}")
        
        if (goombaPerception.obstacle_ahead == True) : #Se c'è un ostacolo davanti
            goombaAgent.bdi.set_belief("obstacle_ahead")  #aggiorno la belief

        #asl ragiona ed elabora un piano....
                
        self.put_action(AgentType.GOOMBA, action)
        print("[Agent] Action sent")

        print("[SPADE] Agent started")

        '''        
        await self._runner(
            self.perception_queues,
            self.action_queues,
            self.stop_event,
        )
        '''       

    def _thread_main(self) -> None:
        try:
            spade.run(self._main(), embedded_xmpp_server=False)
        except Exception as exc:
            self._error = exc
            LOGGER.exception("SPADE runtime stopped with an error")
            self._eventReady.set()
        finally:
            self.stop_event.set()
