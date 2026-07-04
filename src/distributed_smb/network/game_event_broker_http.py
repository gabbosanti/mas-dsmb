"""HTTP-based GameEventBroker — forwards events to the containerised game-event server."""

import logging
import queue
import threading

import httpx

from distributed_smb.shared.config import GAME_EVENT_WS_PORT

LOGGER = logging.getLogger(__name__)


class HttpGameEventBroker:
    """Sends game events via HTTP POST to the game-event container."""

    def __init__(self, host: str = "localhost", port: int = GAME_EVENT_WS_PORT) -> None:
        self._url = f"http://{host}:{port}/events"
        self._queue: queue.Queue[bytes] = queue.Queue()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def send(self, payload: bytes) -> None:
        # Queued and posted from a background thread so a slow/blocked HTTP
        # round-trip to the container never stalls the 60Hz game loop.
        self._queue.put(payload)

    def _run(self) -> None:
        while True:
            payload = self._queue.get()
            try:
                httpx.post(self._url, content=payload, timeout=1.0)
                LOGGER.debug("HTTP POST ok → %s (%d bytes)", self._url, len(payload))
            except Exception as exc:
                LOGGER.warning("HTTP POST failed → %s: %s", self._url, exc)

    def get_disconnected_player(self) -> str | None:
        return None

    def launch(self, host: str = "0.0.0.0", port: int = GAME_EVENT_WS_PORT) -> None:
        pass  # container lifecycle managed by LobbyContainerManager

    def reconnect(self, host: str, port: int) -> None:
        self._url = f"http://{host}:{port}/events"

    def promote_to_server(self, port: int) -> None:
        pass  # new host's container is started by LobbyContainerManager externally
