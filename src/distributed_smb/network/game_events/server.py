"""Game event WebSocket server — reliable in-game event delivery from host to clients."""

import asyncio
import logging
import queue
import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, WebSocket

from distributed_smb.shared.config import (
    GAME_EVENT_HEARTBEAT_INTERVAL,
    GAME_EVENT_WS_PATH,
    GAME_EVENT_WS_PORT,
)

LOGGER = logging.getLogger(__name__)

_connections: list[WebSocket] = []
_ws_to_player: dict[int, str] = {}  # id(ws) → player_id
_ws_to_event: dict[int, asyncio.Event] = {}  # id(ws) → disconnect signal
_disconnect_queue: queue.Queue[str] = queue.Queue()
_loop: asyncio.AbstractEventLoop | None = None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    asyncio.create_task(_heartbeat_loop())
    yield


app = FastAPI(title="Distributed SMB Game Events", lifespan=_lifespan)


@app.websocket(GAME_EVENT_WS_PATH)
async def game_event_endpoint(ws: WebSocket, player_id: str = "") -> None:
    await ws.accept()
    _connections.append(ws)
    disconnect_event = asyncio.Event()
    _ws_to_event[id(ws)] = disconnect_event
    LOGGER.info(
        "Client connected to game event server: player_id=%r total=%d",
        player_id or "anonymous",
        len(_connections),
    )
    if player_id:
        _ws_to_player[id(ws)] = player_id
    try:
        await disconnect_event.wait()
    except Exception:
        pass
    finally:
        _ws_to_event.pop(id(ws), None)
        if ws in _connections:
            _connections.remove(ws)
        pid = _ws_to_player.pop(id(ws), None)
        LOGGER.info(
            "Client disconnected from game event server: player_id=%r total=%d",
            pid or "anonymous",
            len(_connections),
        )
        if pid:
            _disconnect_queue.put(pid)


async def _broadcast(payload: bytes) -> None:
    text = payload.decode("utf-8")
    LOGGER.info(
        "Broadcasting game event to %d connection(s): %s", len(_connections), text
    )
    dead: list[WebSocket] = []
    for ws in list(_connections):
        try:
            await ws.send_text(text)
        except Exception:
            dead.append(ws)
    for ws in dead:
        event = _ws_to_event.get(id(ws))
        if event is not None:
            event.set()


async def _heartbeat_loop() -> None:
    """Periodically ping all connections to detect stale WebSockets."""
    while True:
        await asyncio.sleep(GAME_EVENT_HEARTBEAT_INTERVAL)
        dead: list[WebSocket] = []
        for ws in list(_connections):
            try:
                await ws.send_text('{"type":"ping"}')
            except Exception:
                dead.append(ws)
        for ws in dead:
            event = _ws_to_event.get(id(ws))
            if event is not None:
                event.set()


@app.post("/events")
async def receive_event(request: Request) -> None:
    """Accept a game event from the host process and broadcast it to all clients."""
    payload = await request.body()
    await _broadcast(payload)


def send_game_event(payload: bytes) -> None:
    """Send an encoded game event to all connected clients (thread-safe)."""
    if _loop is None:
        return
    asyncio.run_coroutine_threadsafe(_broadcast(payload), _loop)


def get_disconnected_player() -> str | None:
    """Return a player_id that just disconnected, or None if the queue is empty."""
    try:
        return _disconnect_queue.get_nowait()
    except queue.Empty:
        return None


def reset() -> None:
    """Clear all state — used in tests."""
    _connections.clear()
    _ws_to_player.clear()
    _ws_to_event.clear()
    while not _disconnect_queue.empty():
        _disconnect_queue.get_nowait()


class GameEventBroker:
    """Concrete broker that delegates to the module-level game event server functions."""

    def send(self, payload: bytes) -> None:
        send_game_event(payload)

    def get_disconnected_player(self) -> str | None:
        return get_disconnected_player()

    def launch(self, host: str = "0.0.0.0", port: int = GAME_EVENT_WS_PORT) -> None:
        launch_game_event_server(host=host, port=port)

    def reconnect(self, host: str, port: int) -> None:
        # In-process server: clients reconnect via a new WsHandler on NodeController.
        pass

    def promote_to_server(self, port: int) -> None:
        launch_game_event_server(host="0.0.0.0", port=port)


def launch_game_event_server(
    host: str = "0.0.0.0", port: int = GAME_EVENT_WS_PORT
) -> threading.Thread:
    """Start the game event server in a background daemon thread."""

    async def _run_server() -> None:
        global _loop
        _loop = asyncio.get_running_loop()
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)
        await server.serve()

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run_server())

    thread = threading.Thread(target=_run, name="game-event-server", daemon=True)
    thread.start()
    LOGGER.info("Game event server started on port %d", port)
    return thread
