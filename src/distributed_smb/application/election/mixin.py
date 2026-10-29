"""Election promotion mixin — handles role transition after winning the leader election."""

import json
import logging
import threading
import time

from distributed_smb.application.election import FollowingHost, SelfElected
from distributed_smb.network.transport.websocket import WsHandler, connect_with_retries
from distributed_smb.shared.config import (
    ELECTION_CLAIM_TIMEOUT_S,
    GAME_EVENT_WS_PORT,
    HOST_UDP_PORT,
    LOBBY_STARTUP_WAIT,
    LOBBY_WS_PORT,
)
from distributed_smb.shared.enums import PlayerRole
from distributed_smb.shared.messages.election import (
    ElectionAck,
    NewHostClaim,
    ReconnectionAck,
)
from distributed_smb.shared.messages.session import SessionCreated, SessionRecreate

LOGGER = logging.getLogger(__name__)


class ElectionMixin:
    # ------------------------------------------------------------------
    # Election handlers (override stubs in ClientGameplayMixin)
    # ------------------------------------------------------------------

    def _send_election_message_udp(
        self, message, *, only_hosts: set[str] | None = None
    ) -> None:
        """Send an election message over UDP too: the WS relay may have died with the host."""
        payload = self.serializer.encode_message(message)
        for entry in self.roster.get_all_players():
            if entry.player_id == self.local_player_id:
                continue
            if only_hosts is not None and entry.host not in only_hosts:
                continue
            self.udp_handler.send_packet_nowait(payload, entry.host, entry.udp_port)

    def _on_self_elected(self, event: SelfElected) -> None:
        peers = self._known_client_peers()
        if not peers:
            LOGGER.info("election: sole survivor — promoting immediately")
            self._promote_to_host()
            return

        claim = NewHostClaim(
            claimer_ip=self.local_ip,
            claimer_join_index=self.join_index,
            session_id=self.session_id,
        )
        payload = json.dumps(self.serializer.encode_ws_message(claim)).encode()
        self.game_event_broker.send(payload)
        self._send_election_message_udp(claim, only_hosts=peers)
        self._pending_election_acks = set(peers)
        self._election_claim_deadline = time.time() + ELECTION_CLAIM_TIMEOUT_S
        LOGGER.info(
            "election: NewHostClaim broadcast, waiting for %d ack(s) (deadline %.1fs)",
            len(peers),
            ELECTION_CLAIM_TIMEOUT_S,
        )

    def _on_election_ack(self, msg: ElectionAck) -> None:
        if self._promotion_done:
            return
        if msg.from_ip not in self._pending_election_acks:
            return
        self._pending_election_acks.discard(msg.from_ip)
        LOGGER.info(
            "election: ElectionAck from %s — %d remaining",
            msg.from_ip,
            len(self._pending_election_acks),
        )
        if not self._pending_election_acks:
            self._promote_to_host()

    def _on_new_host_claim(self, msg: NewHostClaim) -> None:
        if msg.claimer_join_index == self.join_index:
            return  # ignore echo of our own broadcast (join_index is unique per session)
        if self.election_coordinator is None:
            return
        event = self.election_coordinator.on_new_host_claim(
            claimer_join_index=msg.claimer_join_index,
            claimer_ip=msg.claimer_ip,
        )
        if isinstance(event, FollowingHost):
            LOGGER.info(
                "election: following %s (join_index=%d)",
                msg.claimer_ip,
                msg.claimer_join_index,
            )
            self._following_host_ip = msg.claimer_ip
            self._following_since = time.time()
            ack = ElectionAck(from_ip=self.local_ip, session_id=self.session_id)
            payload = json.dumps(self.serializer.encode_ws_message(ack)).encode()
            self.game_event_broker.send(payload)
            self._send_election_message_udp(ack, only_hosts={msg.claimer_ip})

    # ------------------------------------------------------------------
    # Claim deadline — called each frame from _tick_election_state
    # ------------------------------------------------------------------

    def _tick_claim_deadline(self, now: float) -> None:
        """Promote anyway once ELECTION_CLAIM_TIMEOUT_S expires, removing silent peers."""
        if self._promotion_done or not self._pending_election_acks:
            return
        if self._election_claim_deadline == 0.0 or now < self._election_claim_deadline:
            return
        for ip in list(self._pending_election_acks):
            LOGGER.warning("election: peer %s unresponsive — evicting", ip)
            entry = next(
                (
                    e
                    for e in self.roster.get_all_players()
                    if e.host == ip and e.player_id != self.local_player_id
                ),
                None,
            )
            if entry:
                self._evict_player(entry.player_id)  # removes from roster + world state
        self._pending_election_acks.clear()
        self._promote_to_host()

    # ------------------------------------------------------------------
    # Role promotion
    # ------------------------------------------------------------------

    def _promote_to_host(self) -> None:
        if self._promotion_done:
            return
        self._promotion_done = True
        LOGGER.info("election: promoting to host")

        # The engine was constructed as non-authoritative (CLIENT role at bootstrap).
        # Without this, handle_powerup_collisions/_handle_enemy_collisions/
        # handle_victory_condition/_process_respawns all silently no-op forever
        # after promotion, since they early-return on is_authoritative=False.
        self.engine.is_authoritative = True

        # Restore world state from last received snapshot
        if self.env_state_buffer is not None:
            last = self.env_state_buffer.get_last()
            if last is not None:
                self.bootstrap_from_snapshot(last)

        # Update roster: evict crashed host, mark self as new host
        crashed = self.roster.get_host()
        if crashed is not None:
            self._evict_player(crashed.player_id)  # removes from roster + world state
        self.roster.promote_host(self.local_player_id)

        # Rebuild UDP socket bound to the authoritative host port
        # (promoted client was on an OS-assigned ephemeral port)
        self._rebuild_udp_as_host()

        # Broadcast ReconnectionAck via relay and direct UDP — the relay may
        # already be down if it ran on the crashed host's own machine.
        surviving_peers = [
            e
            for e in self.roster.get_all_players()
            if e.player_id != self.local_player_id
        ]
        if surviving_peers:
            ack_msg = ReconnectionAck(
                new_host_ip=self.local_ip,
                udp_port=HOST_UDP_PORT,
                game_events_port=GAME_EVENT_WS_PORT,
                session_id=self.session_id,
            )
            payload = json.dumps(self.serializer.encode_ws_message(ack_msg)).encode()
            self.game_event_broker.send(payload)
            self._send_election_message_udp(ack_msg)
            LOGGER.info(
                "election: ReconnectionAck broadcast to %d peer(s)",
                len(surviving_peers),
            )

        # Own game event server: in-process mode launches the FastAPI server
        # synchronously here (fast); Docker/LAN mode brings up containers in
        # the background thread below (slow — must not freeze gameplay).
        self.game_event_broker.promote_to_server(GAME_EVENT_WS_PORT)

        # Switch role now, before the slow re-registration below, so the frame
        # loop starts draining UDP and broadcasting snapshots immediately.
        self._finish_promotion()

        self._relaunch_thread = threading.Thread(
            target=self._relaunch_lobby_for_rejoin, name="lobby-relaunch", daemon=True
        )
        self._relaunch_thread.start()

    def _relaunch_lobby_for_rejoin(self) -> None:
        """Bring lobby/game-events infrastructure back up so crashed nodes can rejoin."""
        self.lobby_container_manager.start()
        self.game_event_broker.reconnect("localhost", GAME_EVENT_WS_PORT)

        self.lobby_service.launch(port=LOBBY_WS_PORT)
        time.sleep(LOBBY_STARTUP_WAIT)
        try:
            lobby_ws = WsHandler(host="localhost", port=LOBBY_WS_PORT)
            connect_with_retries(lobby_ws, label="election: lobby")
            all_players = self.roster.get_all_players()
            next_ji = (max(e.join_index for e in all_players) + 1) if all_players else 0
            reserved_join_indices = {
                entry.join_index
                for entry in all_players
                if entry.player_id != self.local_player_id
            } | set(self.rejoin_states)
            lobby_ws.send(
                SessionRecreate(
                    session_id=self.session_id,
                    next_join_index=next_ji,
                    host_ip=self.local_ip,
                    host_udp_port=HOST_UDP_PORT,
                    host_join_index=self.join_index,
                    reserved_join_indices=sorted(reserved_join_indices),
                )
            )
            got_ack = False
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if isinstance(lobby_ws.poll(), SessionCreated):
                    LOGGER.info(
                        "election: lobby ready for rejoin (session=%s)", self.session_id
                    )
                    got_ack = True
                    break
                time.sleep(0.05)
            self.ws_handler = lobby_ws
            if got_ack:
                if self.use_discovery:
                    self.discovery_service.announce(self.session_id, LOBBY_WS_PORT)
                    LOGGER.info("election: discovery announce started for rejoin")
            else:
                LOGGER.warning(
                    "election: lobby did not ack SESSION_RECREATE (session=%s) — rejoin disabled",
                    self.session_id,
                )
        except Exception as exc:
            LOGGER.warning(
                "election: lobby registration failed (%s: %s) — rejoin disabled",
                type(exc).__name__,
                exc,
            )

    def _finish_promotion(self) -> None:
        # Give surviving peers a fresh grace period so _check_player_disconnections()
        # does not false-positive them out immediately (last_input_time was empty as client).
        now = time.time()
        for entry in self.roster.get_all_players():
            if entry.player_id != self.local_player_id:
                self.last_input_time[entry.player_id] = now

        # Switch role — next process_frame() routes to _process_host_frame()
        self.role = PlayerRole.HOST
        self._start_spade_runtime()
        LOGGER.info("election: promotion complete — role=HOST")
