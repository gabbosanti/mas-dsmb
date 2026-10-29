"""Own the thread that runs SPADE's asyncio event loop."""

import logging
import threading
from collections.abc import Callable
from queue import Queue

LOGGER = logging.getLogger(__name__)

SpadeRunner = Callable[
    [dict[str, Queue[object]], dict[str, Queue[object]], threading.Event],
    None,
]


class SpadeRuntime:
    """Run one synchronous SPADE entry point on a dedicated background thread.

    The runner is expected to call ``spade.run(main(), embedded_xmpp_server=False)``
    and stop its agents when ``stop_event`` is set.
    """

    def __init__(self, runner: SpadeRunner | None = None) -> None:
        self.perception_queues: dict[str, Queue[object]] = {}
        self.action_queues: dict[str, Queue[object]] = {}
        self.stop_event = threading.Event()
        self._runner = runner
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: Exception | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def error(self) -> Exception | None:
        """Return an exception raised by the SPADE runner, if any."""
        return self._error

    def register_agent(self, agent_id: str) -> tuple[Queue[object], Queue[object]]:
        """Create or return the dedicated perception and action queues."""
        if not agent_id:
            raise ValueError("agent_id must not be empty")
        with self._lock:
            if self.is_running:
                raise RuntimeError("Cannot register agents while SPADE is running")
            perception_queue = self.perception_queues.setdefault(agent_id, Queue())
            action_queue = self.action_queues.setdefault(agent_id, Queue())
            return perception_queue, action_queue

    def start(self, timeout: float = 5.0) -> None:
        """Start the runner once; repeated calls while it is running are safe."""
        with self._lock:
            if self.is_running:
                return

            self.stop_event = threading.Event()
            self._ready = threading.Event()
            self._error = None
            self._thread = threading.Thread(
                target=self._thread_main,
                name="spade-runtime",
                daemon=True,
            )
            self._thread.start()
            thread = self._thread

        if not self._ready.wait(timeout):
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
        if thread.is_alive():
            raise TimeoutError("The SPADE runtime thread did not stop in time")
        if self._error is not None:
            raise RuntimeError("The SPADE runtime failed") from self._error

    def submit_perception(self, agent_id: str, perception: object) -> None:
        """Publish an observation to the selected agent's queue."""
        self._queue_for(self.perception_queues, agent_id).put(perception)

    def get_action(self, agent_id: str, timeout: float | None = 0) -> object:
        """Read an action from the selected agent's queue."""
        return self._queue_for(self.action_queues, agent_id).get(timeout=timeout)

    @staticmethod
    def _queue_for(queues: dict[str, Queue[object]], agent_id: str) -> Queue[object]:
        try:
            return queues[agent_id]
        except KeyError as exc:
            raise KeyError(f"No queues registered for agent {agent_id!r}") from exc

    def _thread_main(self) -> None:
        self._ready.set()
        try:
            if self._runner is not None:
                self._runner(
                    self.perception_queues, self.action_queues, self.stop_event
                )
            else:
                self.stop_event.wait()
        except Exception as exc:
            self._error = exc
            LOGGER.exception("SPADE runtime stopped with an error")
        finally:
            self.stop_event.set()
