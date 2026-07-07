"""Lobby WebSocket service — session registry and coordination server."""

import asyncio
import json
import logging
import threading
import uuid
from dataclasses import dataclass, field

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from distributed_smb.network.serializer import Serializer
from distributed_smb.shared.config import LOBBY_WS_PATH, LOBBY_WS_PORT, player_id_for
from distributed_smb.shared.enums import ConnectionStatus
from distributed_smb.shared.messages.session import (
    GameStart,
    MessageType,
    RosterUpdate,
    SessionCreate,
    SessionCreated,
    SessionJoin,
    SessionJoined,
    SessionRecreate,
)
from distributed_smb.shared.roster import GlobalRoster, RosterEntry

LOGGER = logging.getLogger(__name__)


@dataclass
class _SessionRecord:
    session_id: str
    entries: list[dict] = field(default_factory=list)
    connections: list[WebSocket] = field(default_factory=list)
    is_active: bool = False
    next_join_index: int = 0


class LobbyManager:
    """In-memory session registry; all methods run inside the asyncio event loop."""

    def __init__(self):
        self._sessions: dict[str, _SessionRecord] = {}

    def reset(self) -> None:
        self._sessions.clear()

    def create_session(self, player_id: str, host: str, udp_port: int) -> tuple[str, int]:
        """Register the host as the first participant. Returns (session_id, join_index=0)."""
        session_id = uuid.uuid4().hex[:8]
        entry = {
            "player_id": player_id_for(0),
            "host": host,
            "udp_port": udp_port,
            "join_index": 0,
            "status": ConnectionStatus.CONNECTED.value,
            "is_host": True,
        }
        self._sessions[session_id] = _SessionRecord(
            session_id=session_id,
            entries=[entry],
            next_join_index=1,
        )
        return session_id, 0

    def join_session(self, session_id: str, player_id: str, host: str, udp_port: int) -> int:
        """Add a joining player. Returns the assigned join_index."""
        record = self._sessions[session_id]
        join_index = record.next_join_index
        entry = {
            "player_id": player_id_for(join_index),
            "host": host,
            "udp_port": udp_port,
            "join_index": join_index,
            "status": ConnectionStatus.CONNECTED.value,
            "is_host": False,
        }
        record.entries.append(entry)
        record.next_join_index += 1
        return join_index

    def add_connection(self, session_id: str, ws: WebSocket) -> None:
        self._sessions[session_id].connections.append(ws)

    def remove_connection(self, session_id: str, ws: WebSocket) -> None:
        record = self._sessions.get(session_id)
        if record:
            record.connections = [c for c in record.connections if c is not ws]

    def get_roster(self, session_id: str) -> GlobalRoster:
        roster = GlobalRoster()
        for e in self._sessions[session_id].entries:
            roster.add_player(
                RosterEntry(
                    player_id=e["player_id"],
                    host=e["host"],
                    udp_port=e["udp_port"],
                    join_index=e["join_index"],
                    status=ConnectionStatus(e["status"]),
                    is_host=e["is_host"],
                )
            )
        return roster

    def mark_active(self, session_id: str) -> None:
        self._sessions[session_id].is_active = True

    def is_active(self, session_id: str) -> bool:
        record = self._sessions.get(session_id)
        return record.is_active if record else False

    def register_active_session(
        self, session_id: str, roster: GlobalRoster, next_join_index: int
    ) -> None:
        """Pre-seed an already-running session so rejoining nodes can connect (M9)."""
        entries = [
            {
                "player_id": e.player_id,
                "host": e.host,
                "udp_port": e.udp_port,
                "join_index": e.join_index,
                "status": e.status.value,
                "is_host": e.is_host,
            }
            for e in roster.get_all_players()
        ]
        self._sessions[session_id] = _SessionRecord(
            session_id=session_id,
            entries=entries,
            is_active=True,
            next_join_index=next_join_index,
        )

    def poll_new_joiners(self, session_id: str, known_join_indices: set[int]) -> list[dict]:
        """Return roster entries whose join_index is not in known_join_indices."""
        record = self._sessions.get(session_id)
        if not record:
            return []
        return [e for e in record.entries if e["join_index"] not in known_join_indices]

    async def broadcast(self, session_id: str, payload: dict) -> None:
        """Send JSON to every WebSocket connected in the session."""
        record = self._sessions.get(session_id)
        if not record:
            return
        data = json.dumps(payload)
        for ws in list(record.connections):
            try:
                await ws.send_text(data)
            except Exception:
                pass


lobby_manager = LobbyManager()
_serializer = Serializer()
app = FastAPI(title="Distributed SMB Lobby")


@app.websocket(LOBBY_WS_PATH)
async def lobby_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    session_id: str | None = None
    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            message_type = data.get("message_type")

            if message_type == MessageType.SESSION_CREATE:
                msg: SessionCreate = _serializer.decode_ws_message(data)
                session_id, join_index = lobby_manager.create_session(
                    msg.player_id, msg.ip, msg.udp_port
                )
                lobby_manager.add_connection(session_id, ws)
                created = SessionCreated(session_id=session_id, join_index=join_index)
                await ws.send_text(json.dumps(_serializer.encode_ws_message(created)))
                roster = lobby_manager.get_roster(session_id)
                await lobby_manager.broadcast(
                    session_id, _serializer.encode_ws_message(RosterUpdate(roster=roster))
                )

            elif message_type == MessageType.SESSION_JOIN:
                msg: SessionJoin = _serializer.decode_ws_message(data)
                session_id = msg.session_id
                join_index = lobby_manager.join_session(session_id, msg.player_id, msg.ip, msg.port)
                lobby_manager.add_connection(session_id, ws)
                joined = SessionJoined(join_index=join_index)
                await ws.send_text(json.dumps(_serializer.encode_ws_message(joined)))
                roster = lobby_manager.get_roster(session_id)
                await lobby_manager.broadcast(
                    session_id, _serializer.encode_ws_message(RosterUpdate(roster=roster))
                )
                # Game already running (rejoin after host migration): send GameStart immediately
                # so _client_lobby_phase() can proceed without waiting for a host trigger.
                if lobby_manager.is_active(session_id):
                    game_start = GameStart(session_id=session_id)
                    await ws.send_text(json.dumps(_serializer.encode_ws_message(game_start)))

            elif message_type == MessageType.SESSION_RECREATE:
                # Promoted host re-registers an existing session after M8 migration (M9).
                # Preserves the session_id so recovering nodes can join with their cached ID.
                msg: SessionRecreate = _serializer.decode_ws_message(data)
                session_id = msg.session_id
                LOGGER.info(
                    "lobby: SESSION_RECREATE received (session=%s, next_join_index=%d)",
                    session_id,
                    msg.next_join_index,
                )
                host_roster = GlobalRoster()
                host_roster.add_player(
                    RosterEntry(
                        player_id=player_id_for(msg.host_join_index),
                        host=msg.host_ip,
                        udp_port=msg.host_udp_port,
                        join_index=msg.host_join_index,
                        status=ConnectionStatus.CONNECTED,
                        is_host=True,
                    )
                )
                lobby_manager.register_active_session(session_id, host_roster, msg.next_join_index)
                lobby_manager.add_connection(session_id, ws)
                LOGGER.info("lobby: session %s registered, sending SessionCreated ack", session_id)
                ack = SessionCreated(session_id=session_id, join_index=0)
                await ws.send_text(json.dumps(_serializer.encode_ws_message(ack)))

            elif message_type == MessageType.INITIAL_STATE_SYNC:
                if session_id:
                    await lobby_manager.broadcast(session_id, data)

            elif message_type == MessageType.GAME_START:
                msg: GameStart = _serializer.decode_ws_message(data)
                lobby_manager.mark_active(msg.session_id)
                await lobby_manager.broadcast(msg.session_id, _serializer.encode_ws_message(msg))

    except WebSocketDisconnect:
        if session_id:
            lobby_manager.remove_connection(session_id, ws)
    except Exception as exc:
        LOGGER.error(
            "lobby: unhandled exception in WebSocket handler: %s: %s", type(exc).__name__, exc
        )
        if session_id:
            lobby_manager.remove_connection(session_id, ws)


class LobbyService:
    """Concrete service that delegates to the module-level lobby server function."""

    def launch(self, host: str = "0.0.0.0", port: int = LOBBY_WS_PORT) -> None:
        launch_lobby_server(host=host, port=port)


def launch_lobby_server(host: str = "0.0.0.0", port: int = LOBBY_WS_PORT) -> threading.Thread:
    """Start uvicorn in a background daemon thread and return it."""

    def _run() -> None:
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)
        asyncio.run(server.serve())

    thread = threading.Thread(target=_run, name="lobby-server", daemon=True)
    thread.start()
    return thread
