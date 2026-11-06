"""Thread lifecycle wrapper for an asynchronous SPADE runtime."""

import asyncio
import logging
import threading
from collections.abc import Callable, Coroutine
from typing import Any

LOGGER = logging.getLogger(__name__)

type AsyncRuntime = Callable[[], Coroutine[Any, Any, None]]


class SpadeThread:
    """Run an async runtime on a dedicated thread and own its lifecycle."""

    def __init__(self, runtime: AsyncRuntime) -> None:
        self._runtime = runtime
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def error(self) -> BaseException | None:
        return self._error

    def start(self, timeout: float = 5.0) -> None:
        """Start the runtime once; wait until its worker thread is initialized."""
        with self._lock:
            if self.is_running:
                return
            self._error = None
            started = threading.Event()
            self._thread = threading.Thread(
                target=self._run,
                args=(started,),
                name="spade-runtime",
                daemon=True,
            )
            self._thread.start()
        if not started.wait(timeout):
            raise TimeoutError("Timed out while starting the SPADE thread")
        if self._error is not None:
            raise RuntimeError("The SPADE runtime failed") from self._error

    def stop(self, timeout: float = 5.0) -> None:
        """Wait for the runtime to finish after its owner requests shutdown."""
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout)
        if thread.is_alive():
            raise TimeoutError("Timed out while stopping the SPADE thread")
        if self._error is not None:
            raise RuntimeError("The SPADE runtime failed") from self._error

    def _run(self, started: threading.Event) -> None:
        started.set()
        try:
            asyncio.run(self._runtime())
        except BaseException as exc:
            self._error = exc
            LOGGER.exception("SPADE thread terminated with an error")
