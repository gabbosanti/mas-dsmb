"""Lobby coordination mixin — session creation, joining, and start synchronisation."""

import logging
import time
from collections.abc import Callable
from typing import Any

from distributed_smb.shared.config import (
    GAME_EVENT_WS_PATH,
    GAME_EVENT_WS_PORT,
    LOBBY_STARTUP_WAIT,
    LOBBY_TIMEOUT,
    LOBBY_WS_PORT,
    player_id_for,
)
from distributed_smb.shared.enums import PlayerRole
from distributed_smb.shared.messages.session import (
    GameStart,
    RosterUpdate,
    SessionCreate,
    SessionCreated,
    SessionJoin,
    SessionJoined,
)
from distributed_smb.shared.roster import GlobalRoster

LOGGER = logging.getLogger(__name__)

LobbyUpdateCallback = Callable[[str, str, GlobalRoster], bool | None]
StartRequestedCallback = Callable[[], bool]


class LobbyCancelledError(RuntimeError):
    """Raised when the user closes the lobby UI before game start."""


class LobbyMixin:
    def lobby_phase(
        self,
        *,
        session_id: str = "",
        on_update: LobbyUpdateCallback | None = None,
        start_requested: StartRequestedCallback | None = None,
    ) -> GlobalRoster:
        self.lifecycle.move_to_lobby()
        self._notify_lobby_update("Entering lobby", on_update)
        if self.role is PlayerRole.HOST:
            self._host_lobby_phase(
                on_update=on_update,
                start_requested=start_requested,
            )
        else:
            self._client_lobby_phase(session_id=session_id, on_update=on_update)
        self._rebuild_world_from_roster()
        self._init_shadow_copies()
        self.lifecycle.move_to_game()
        if hasattr(self, "_write_session_metadata"):
            self._write_session_metadata()
        self._notify_lobby_update("Starting game", on_update)
        return self.roster

    def _notify_lobby_update(
        self,
        status: str,
        on_update: LobbyUpdateCallback | None,
    ) -> None:
        if on_update is not None:
            should_continue = on_update(status, self.session_id, self.roster)
            if should_continue is False:
                raise LobbyCancelledError("Lobby was closed before game start")

    def _discovery_allowed_ips(self) -> set[str]:
        allowed_ips = {entry.host for entry in self.roster.get_all_players() if entry.host}
        if self.local_ip:
            allowed_ips.add(self.local_ip)
        return allowed_ips

    def _host_lobby_phase(
        self,
        *,
        on_update: LobbyUpdateCallback | None = None,
        start_requested: StartRequestedCallback | None = None,
    ) -> None:
        self._notify_lobby_update("Creating lobby server", on_update)
        if self.use_discovery:
            self.lobby_container_manager.start()
        else:
            self.lobby_service.launch(port=LOBBY_WS_PORT)
            time.sleep(LOBBY_STARTUP_WAIT)

        self._notify_lobby_update("Connecting to lobby", on_update)
        for attempt in range(10):
            try:
                self.ws_handler.connect(timeout=2.0)
                break
            except (ConnectionError, TimeoutError):
                if attempt < 9:
                    LOGGER.info("lobby not ready yet (attempt %d/10), retrying in 1s…", attempt + 1)
                    time.sleep(1.0)
                else:
                    raise
        self.ws_handler.send(
            SessionCreate(
                player_id=self.local_player_id,
                ip=self.local_ip,
                udp_port=self.udp_handler.port,
            )
        )

        created: SessionCreated = self._poll_lobby(SessionCreated)
        self.session_id = created.session_id
        LOGGER.info("Session created: %s", self.session_id)
        if self.use_discovery:
            self.discovery_service.announce(self.session_id, LOBBY_WS_PORT)
        self._notify_lobby_update("Waiting for players", on_update)

        deadline = time.time() + LOBBY_TIMEOUT
        while time.time() < deadline:
            msg = self.ws_handler.poll()
            if isinstance(msg, RosterUpdate):
                self.roster = msg.roster
            self._notify_lobby_update("Waiting for players", on_update)
            if start_requested is not None and start_requested() and self.roster.players:
                LOGGER.info("Host manually requested game start")
                break
            time.sleep(0.05)

        if not self.use_discovery:
            self.game_event_broker.launch()
            time.sleep(LOBBY_STARTUP_WAIT)
        if self.use_discovery and hasattr(self.discovery_service, "set_allowed_ips"):
            self.discovery_service.set_allowed_ips(self._discovery_allowed_ips())
        self._notify_lobby_update("Broadcasting game start", on_update)
        self.ws_handler.send(GameStart(session_id=self.session_id))
        self._poll_lobby(GameStart)
        # Drain any RosterUpdate messages that arrived concurrently with GameStart
        # (e.g., a player joined at the exact moment the host sent GameStart).
        _drain_until = time.time() + 0.1
        while time.time() < _drain_until:
            msg = self.ws_handler.poll()
            if msg is None:
                time.sleep(0.01)
            elif isinstance(msg, RosterUpdate):
                self.roster = msg.roster
        LOGGER.info("Lobby phase complete: %d players", len(self.roster.players))

    def _client_lobby_phase(
        self,
        *,
        session_id: str,
        on_update: LobbyUpdateCallback | None = None,
    ) -> None:
        self._notify_lobby_update("Connecting to lobby", on_update)
        if self.use_discovery:
            host_ip, lobby_port = self.discovery_service.discover(session_id)
            self._make_lobby_ws_client(host_ip, lobby_port)
        self.udp_handler.open_socket()  # bind early so actual port is known before announcing
        for attempt in range(10):
            try:
                self.ws_handler.connect(timeout=2.0)
                break
            except (ConnectionError, TimeoutError):
                if attempt < 9:
                    LOGGER.info("lobby not ready yet (attempt %d/10), retrying in 1s…", attempt + 1)
                    time.sleep(1.0)
                else:
                    raise
        self.ws_handler.send(
            SessionJoin(
                session_id=session_id,
                player_id=self.local_player_id,
                ip=self.local_ip,
                port=self.udp_handler.port,
            )
        )

        joined: SessionJoined = self._poll_lobby(SessionJoined)
        self.local_player_id = player_id_for(joined.join_index)
        if hasattr(self.prediction_engine, "local_player_id"):
            self.prediction_engine.local_player_id = self.local_player_id
        self._notify_lobby_update("Joined session", on_update)

        deadline = time.time() + LOBBY_TIMEOUT
        while time.time() < deadline:
            msg = self.ws_handler.poll()
            if isinstance(msg, RosterUpdate):
                self.roster = msg.roster
                self._notify_lobby_update("Waiting for game start", on_update)
            elif isinstance(msg, GameStart):
                self.session_id = msg.session_id
                self._notify_lobby_update("Starting game", on_update)
                break
            else:
                self._notify_lobby_update("Waiting for game start", on_update)
            time.sleep(0.05)

        LOGGER.info("Lobby phase complete: session=%s", self.session_id)

    def _poll_lobby(self, expected_type: type, timeout: float = LOBBY_TIMEOUT) -> Any:
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self.ws_handler.poll()
            if msg is None:
                time.sleep(0.05)
                continue
            if isinstance(msg, RosterUpdate):
                self.roster = msg.roster
            if isinstance(msg, expected_type):
                return msg
        raise TimeoutError(f"Lobby timeout waiting for {expected_type.__name__}")

    def _rebuild_world_from_roster(self) -> None:
        LOGGER.info(
            "Rebuilding world from roster (%d players): %s",
            len(self.roster.get_all_players()),
            [e.player_id for e in self.roster.get_all_players()],
        )
        self.engine.world_state.characters.clear()
        if hasattr(self, "shadow_copies"):
            self.shadow_copies.clear()
        for entry in self.roster.get_all_players():
            x, y = self._spawn_position_for(entry.join_index)
            self.engine.spawn_player(entry.player_id, x=x, y=y, join_index=entry.join_index)
        now = time.time()
        self.last_input_time = {
            entry.player_id: now
            for entry in self.roster.get_all_players()
            if entry.player_id != self.local_player_id
        }
        others = [e for e in self.roster.get_all_players() if e.player_id != self.local_player_id]
        if others:
            self.remote_player_id = others[0].player_id
        host_entry = next((e for e in self.roster.get_all_players() if e.is_host), None)
        if host_entry and self.role is PlayerRole.CLIENT:
            self.remote_host = host_entry.host
            self.remote_port = host_entry.udp_port
            path = f"{GAME_EVENT_WS_PATH}?player_id={self.local_player_id}"
            self._make_ws_client(host_entry.host, GAME_EVENT_WS_PORT, path)
