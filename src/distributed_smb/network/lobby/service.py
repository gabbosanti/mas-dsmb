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
from distributed_smb.shared.config import (
    LOBBY_WS_PATH,
    LOBBY_WS_PORT,
    MAX_PLAYERS,
    player_id_for,
)
from distributed_smb.shared.enums import ConnectionStatus
from distributed_smb.shared.messages.session import (
    GameStart,
    MessageType,
    RosterUpdate,
    SessionClosed,
    SessionCreate,
    SessionCreated,
    SessionJoin,
    SessionJoined,
    SessionJoinRejected,
    SessionLeave,
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
    reserved_join_indices: set[int] = field(default_factory=set)


class LobbyManager:
    """In-memory session registry; all methods run inside the asyncio event loop."""

    def __init__(self):
        self._sessions: dict[str, _SessionRecord] = {}

    def reset(self) -> None:
        self._sessions.clear()

    def create_session(
        self, player_id: str, host: str, udp_port: int
    ) -> tuple[str, int]:
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

    def join_session(
        self,
        session_id: str,
        player_id: str,
        host: str,
        udp_port: int,
        rejoin_join_index: int | None = None,
    ) -> int | None:
        """Add a player in a free slot, or reclaim its reserved slot after a crash."""
        record = self._sessions.get(session_id)
        if record is None:
            return None
        current_indices = {entry["join_index"] for entry in record.entries}

        if rejoin_join_index is not None:
            existing = next(
                (entry for entry in record.entries if entry["join_index"] == rejoin_join_index),
                None,
            )
            if existing is not None:
                if existing["player_id"] != player_id:
                    return None
                existing["host"] = host
                existing["udp_port"] = udp_port
                record.reserved_join_indices.discard(rejoin_join_index)
                return rejoin_join_index
            if rejoin_join_index not in record.reserved_join_indices:
                return None
            join_index = rejoin_join_index
            record.reserved_join_indices.remove(join_index)
        else:
            occupied_indices = current_indices | record.reserved_join_indices
            if len(occupied_indices) >= MAX_PLAYERS:
                return None
            join_index = next(
                (index for index in range(MAX_PLAYERS) if index not in occupied_indices),
                None,
            )
            if join_index is None:
                return None

        entry = {
            "player_id": player_id_for(join_index),
            "host": host,
            "udp_port": udp_port,
            "join_index": join_index,
            "status": ConnectionStatus.CONNECTED.value,
            "is_host": False,
        }
        record.entries.append(entry)
        record.next_join_index = max(record.next_join_index, join_index + 1)
        return join_index

    def add_connection(self, session_id: str, ws: WebSocket) -> None:
        self._sessions[session_id].connections.append(ws)

    def remove_connection(self, session_id: str, ws: WebSocket) -> None:
        record = self._sessions.get(session_id)
        if record:
            record.connections = [c for c in record.connections if c is not ws]

    def leave_session(self, session_id: str, join_index: int) -> bool:
        """Drop a participant. Returns True if it was the host (room is over)."""
        record = self._sessions.get(session_id)
        if record is None:
            return False
        leaving = next(
            (e for e in record.entries if e["join_index"] == join_index), None
        )
        if leaving is None:
            return False
        record.entries = [e for e in record.entries if e["join_index"] != join_index]
        return bool(leaving["is_host"])

    def is_full(self, session_id: str, rejoin_join_index: int | None = None) -> bool:
        """A session holds at most MAX_PLAYERS participants, host included."""
        record = self._sessions.get(session_id)
        if record is None:
            return False
        if rejoin_join_index is not None:
            return rejoin_join_index not in record.reserved_join_indices and not any(
                entry["join_index"] == rejoin_join_index
                and entry["player_id"] != player_id_for(rejoin_join_index)
                for entry in record.entries
            )
        return (
            len({entry["join_index"] for entry in record.entries} | record.reserved_join_indices)
            >= MAX_PLAYERS
        )

    def has_session(self, session_id: str) -> bool:
        return session_id in self._sessions

    def drop_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

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
        self,
        session_id: str,
        roster: GlobalRoster,
        next_join_index: int,
        reserved_join_indices: set[int] | None = None,
    ) -> None:
        """Pre-seed an already-running session so rejoining nodes can connect."""
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
            reserved_join_indices=set(reserved_join_indices or ()),
        )

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
                    session_id,
                    _serializer.encode_ws_message(RosterUpdate(roster=roster)),
                )

            elif message_type == MessageType.SESSION_JOIN:
                msg: SessionJoin = _serializer.decode_ws_message(data)
                session_id = msg.session_id
                if lobby_manager.is_full(session_id, msg.rejoin_join_index):
                    LOGGER.info("lobby: session %s is full, refusing a join", session_id)
                    rejected = SessionJoinRejected(
                        session_id=session_id,
                        reason=f"The session already has {MAX_PLAYERS} players",
                    )
                    await ws.send_text(
                        json.dumps(_serializer.encode_ws_message(rejected))
                    )
                    session_id = None
                    continue
                join_index = lobby_manager.join_session(
                    session_id,
                    msg.player_id,
                    msg.ip,
                    msg.port,
                    msg.rejoin_join_index,
                )
                if join_index is None:
                    rejected = SessionJoinRejected(
                        session_id=session_id,
                        reason="The requested player slot is unavailable",
                    )
                    await ws.send_text(json.dumps(_serializer.encode_ws_message(rejected)))
                    session_id = None
                    continue
                lobby_manager.add_connection(session_id, ws)
                joined = SessionJoined(join_index=join_index)
                await ws.send_text(json.dumps(_serializer.encode_ws_message(joined)))
                roster = lobby_manager.get_roster(session_id)
                await lobby_manager.broadcast(
                    session_id,
                    _serializer.encode_ws_message(RosterUpdate(roster=roster)),
                )
                # Game already running (rejoin after host migration): send GameStart immediately
                # so _client_lobby_phase() can proceed without waiting for a host trigger.
                if lobby_manager.is_active(session_id):
                    game_start = GameStart(session_id=session_id)
                    await ws.send_text(
                        json.dumps(_serializer.encode_ws_message(game_start))
                    )

            elif message_type == MessageType.SESSION_RECREATE:
                # The promoted host re-registers an existing session after a migration.
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
                lobby_manager.register_active_session(
                    session_id,
                    host_roster,
                    msg.next_join_index,
                    set(msg.reserved_join_indices),
                )
                lobby_manager.add_connection(session_id, ws)
                LOGGER.info(
                    "lobby: session %s registered, sending SessionCreated ack",
                    session_id,
                )
                ack = SessionCreated(session_id=session_id, join_index=0)
                await ws.send_text(json.dumps(_serializer.encode_ws_message(ack)))

            elif message_type == MessageType.SESSION_LEAVE:
                msg: SessionLeave = _serializer.decode_ws_message(data)
                await _handle_leave(msg.session_id, msg.join_index)

            elif message_type == MessageType.INITIAL_STATE_SYNC:
                if session_id:
                    await lobby_manager.broadcast(session_id, data)

            elif message_type == MessageType.GAME_START:
                msg: GameStart = _serializer.decode_ws_message(data)
                lobby_manager.mark_active(msg.session_id)
                await lobby_manager.broadcast(
                    msg.session_id, _serializer.encode_ws_message(msg)
                )

    except WebSocketDisconnect:
        if session_id:
            lobby_manager.remove_connection(session_id, ws)
    except Exception as exc:
        LOGGER.error(
            "lobby: unhandled exception in WebSocket handler: %s: %s",
            type(exc).__name__,
            exc,
        )
        if session_id:
            lobby_manager.remove_connection(session_id, ws)


async def _handle_leave(session_id: str, join_index: int) -> None:
    """Drop a participant that deliberately left and tell the others."""
    if not lobby_manager.has_session(session_id):
        return

    if lobby_manager.leave_session(session_id, join_index):
        LOGGER.info("lobby: host left session %s — closing the room", session_id)
        await lobby_manager.broadcast(
            session_id,
            _serializer.encode_ws_message(SessionClosed(session_id=session_id)),
        )
        lobby_manager.drop_session(session_id)
        return

    LOGGER.info("lobby: participant %d left session %s", join_index, session_id)
    roster = lobby_manager.get_roster(session_id)
    await lobby_manager.broadcast(
        session_id, _serializer.encode_ws_message(RosterUpdate(roster=roster))
    )


class LobbyService:
    """Concrete service that delegates to the module-level lobby server function."""

    def launch(self, host: str = "0.0.0.0", port: int = LOBBY_WS_PORT) -> None:
        launch_lobby_server(host=host, port=port)


def launch_lobby_server(
    host: str = "0.0.0.0", port: int = LOBBY_WS_PORT
) -> threading.Thread:
    """Start uvicorn in a background daemon thread and return it."""

    def _run() -> None:
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)
        asyncio.run(server.serve())

    thread = threading.Thread(target=_run, name="lobby-server", daemon=True)
    thread.start()
    return thread
