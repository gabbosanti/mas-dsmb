"""Election promotion mixin — handles role transition after winning the leader election."""

import json
import logging
import time

from distributed_smb.application.election import FollowingHost, SelfElected
from distributed_smb.shared.config import (
    ELECTION_CLAIM_TIMEOUT_S,
    GAME_EVENT_WS_PORT,
    HOST_UDP_PORT,
    LOBBY_STARTUP_WAIT,
    LOBBY_WS_PORT,
)
from distributed_smb.shared.enums import PlayerRole
from distributed_smb.shared.messages.election import ElectionAck, NewHostClaim, ReconnectionAck
from distributed_smb.shared.messages.session import SessionCreated, SessionRecreate

LOGGER = logging.getLogger(__name__)


class ElectionMixin:
    # ------------------------------------------------------------------
    # Election handlers (override stubs in ClientGameplayMixin)
    # ------------------------------------------------------------------

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
            ack = ElectionAck(from_ip=self.local_ip, session_id=self.session_id)
            payload = json.dumps(self.serializer.encode_ws_message(ack)).encode()
            self.game_event_broker.send(payload)

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

        # Broadcast ReconnectionAck via relay while old server is still reachable
        surviving_peers = [
            e for e in self.roster.get_all_players() if e.player_id != self.local_player_id
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
            LOGGER.info("election: ReconnectionAck broadcast to %d peer(s)", len(surviving_peers))

        # Start own game event server:
        # - in-process mode: promote_to_server() launches the FastAPI server
        # - Docker/LAN mode: lobby_container_manager.start() brings up the containers,
        #   then we redirect the HTTP broker at the local instance
        self.game_event_broker.promote_to_server(GAME_EVENT_WS_PORT)
        self.lobby_container_manager.start()
        self.game_event_broker.reconnect("localhost", GAME_EVENT_WS_PORT)

        # Start lobby and re-register the existing session so recovering nodes can rejoin (M9).
        # Works in both Docker mode (LobbyContainerManager starts the container, then we
        # connect via WS) and in-process mode (LobbyService starts uvicorn, same WS path).
        self.lobby_service.launch(port=LOBBY_WS_PORT)
        time.sleep(LOBBY_STARTUP_WAIT)
        try:
            self._make_lobby_ws_client("localhost", LOBBY_WS_PORT)
            for attempt in range(10):
                try:
                    self.ws_handler.connect(timeout=2.0)
                    break
                except (ConnectionError, TimeoutError, OSError):
                    if attempt < 9:
                        LOGGER.info(
                            "election: lobby not ready yet (attempt %d/10), retrying in 1s…",
                            attempt + 1,
                        )
                        time.sleep(1.0)
                    else:
                        raise
            all_players = self.roster.get_all_players()
            next_ji = (max(e.join_index for e in all_players) + 1) if all_players else 0
            self.ws_handler.send(
                SessionRecreate(session_id=self.session_id, next_join_index=next_ji)
            )
            got_ack = False
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if isinstance(self.ws_handler.poll(), SessionCreated):
                    LOGGER.info("election: lobby ready for rejoin (session=%s)", self.session_id)
                    got_ack = True
                    break
                time.sleep(0.05)
            if not got_ack:
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

        # Give surviving peers a fresh grace period so _check_player_disconnections()
        # does not false-positive them out immediately (last_input_time was empty as client).
        now = time.time()
        for entry in self.roster.get_all_players():
            if entry.player_id != self.local_player_id:
                self.last_input_time[entry.player_id] = now

        # Switch role — next process_frame() routes to _process_host_frame()
        self.role = PlayerRole.HOST
        LOGGER.info("election: promotion complete — role=HOST")
