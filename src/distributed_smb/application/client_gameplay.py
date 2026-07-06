"""Client-side frame synchronisation mixin."""

import logging
import time
from copy import deepcopy

from distributed_smb.application.election import (
    ElectionCoordinator,
    EnvironmentalStateBuffer,
    FollowingHost,
    HostTimeoutWatcher,
    SelfElected,
)
from distributed_smb.shared.config import (
    GAME_EVENT_WS_PATH,
    HOST_TIMEOUT_S,
    PREDICTION_LEAD_DRIFT_TOLERANCE,
    PREDICTION_LEAD_EWMA_ALPHA,
    RECONCILE_GLIDE_RATE,
    RECONCILE_MAX_GLIDE_PX,
    T_ELECTION_BASE_S,
    T_ELECTION_DELTA_S,
)
from distributed_smb.shared.input import InputState
from distributed_smb.shared.messages.election import ElectionAck, ElectionNack, ReconnectionAck
from distributed_smb.shared.messages.gameplay import PlayerInputPacket
from distributed_smb.shared.messages.sync import InitialStateSync, WorldStateSnapshot

LOGGER = logging.getLogger(__name__)

# Number of client frames between diagnostic frame-timing log lines.
CLIENT_DIAG_LOG_INTERVAL = 120


class ClientGameplayMixin:
    def _process_client_frame(self, dt: float, local_input: InputState) -> object:
        """Run one client frame: send input, predict, tick, reconcile, drain events."""
        self._ensure_election_components()
        self._record_client_frame_interval()
        self._drain_lobby_messages()
        self._send_input_packet(local_input)
        self._run_predicted_ticks(dt, local_input)
        pre_reconcile_player = self.engine.world_state.get_player(self.local_player_id)
        pre_reconcile_pos = (
            (pre_reconcile_player.x, pre_reconcile_player.y)
            if pre_reconcile_player is not None
            else None
        )
        self.engine.events.clear()
        self._drain_snapshot_packets()
        self._drain_game_events()
        self._tick_election_state()
        self._adjust_prediction_lead()
        local_visual_state = self._smoothed_local_visual_state(pre_reconcile_pos)
        return self._build_visual_world_state(local_visual_state=local_visual_state)

    # ------------------------------------------------------------------
    # Election lifecycle
    # ------------------------------------------------------------------

    def _ensure_election_components(self) -> None:
        """Lazy-init election components on first client frame (roster available here)."""
        if self.election_coordinator is not None:
            return
        entry = self.roster.get_player(self.local_player_id)
        if entry is not None:
            self.join_index = entry.join_index
        self.election_coordinator = ElectionCoordinator(
            join_index=self.join_index,
            my_ip=self.local_ip,
            timeout_base_s=T_ELECTION_BASE_S,
            timeout_delta_s=T_ELECTION_DELTA_S,
        )
        self.timeout_watcher = HostTimeoutWatcher(timeout_s=HOST_TIMEOUT_S)
        self.env_state_buffer = EnvironmentalStateBuffer()

    def _known_client_peers(self) -> set[str]:
        """IPs of all surviving peers (excluding the crashed host and self)."""
        return {
            entry.host
            for entry in self.roster.get_all_players()
            if not entry.is_host and entry.player_id != self.local_player_id
        }

    def _tick_election_state(self) -> None:
        """Check host timeout and advance the election state machine."""
        if self.election_coordinator is None or self.timeout_watcher is None:
            return

        now = time.time()

        if not self.election_triggered and self.timeout_watcher.tick(now):
            LOGGER.warning("host timeout detected — starting election")
            self.election_triggered = True
            peers = self._known_client_peers()
            self.election_coordinator.start_election(peers)
            self.election_coordinator.set_election_timer(now)

        if self.election_triggered:
            event = self.election_coordinator.tick(now)
            if isinstance(event, SelfElected):
                LOGGER.info("election: self-elected as new host (ip=%s)", event.my_ip)
                self._on_self_elected(event)
            self._tick_claim_deadline(now)

    # ------------------------------------------------------------------
    # Election event handlers (called from _drain_game_events)
    # ------------------------------------------------------------------

    def _on_new_host_claim(self, msg) -> None:
        """Handle an incoming NewHostClaim from a peer."""
        if self.election_coordinator is None:
            return
        event = self.election_coordinator.on_new_host_claim(
            claimer_join_index=msg.claimer_join_index,
            claimer_ip=msg.claimer_ip,
        )
        if isinstance(event, FollowingHost):
            LOGGER.info(
                "election: following new host %s (join_index=%d)",
                event.claimer_ip,
                event.claimer_join_index,
            )

    def _on_election_ack(self, msg: ElectionAck) -> None:
        pass

    def _on_election_nack(self, msg: ElectionNack) -> None:
        pass

    def _on_reconnection_ack(self, ack: ReconnectionAck) -> None:
        if self.reconnected or self._promotion_done:
            return
        self.reconnected = True
        self.remote_host = ack.new_host_ip
        self.remote_port = ack.udp_port
        self._reconnect_game_event_handler(
            ack.new_host_ip, ack.game_events_port, GAME_EVENT_WS_PATH
        )
        self.game_event_broker.reconnect(ack.new_host_ip, ack.game_events_port)
        LOGGER.info(
            "reconnected to new host %s (udp_port=%d, game_events_port=%d)",
            ack.new_host_ip,
            ack.udp_port,
            ack.game_events_port,
        )

        # Sync local roster: evict the crashed host and promote the newly elected one.
        # Without this, _known_client_peers() would still see the old host as a peer
        # and _promote_to_host() would evict the wrong entry in any future election.
        old_host = self.roster.get_host()
        if old_host is not None:
            self._evict_player(old_host.player_id)
        new_host_entry = next(
            (e for e in self.roster.get_all_players() if e.host == ack.new_host_ip), None
        )
        if new_host_entry is not None:
            self.roster.promote_host(new_host_entry.player_id)

        # Reset the election machinery so a future host crash triggers a fresh election.
        # election_triggered stays True after a follow, which silently swallows the second
        # timeout check and prevents the node from ever detecting a subsequent crash.
        self.election_triggered = False
        self._pending_election_acks = set()
        self._election_claim_deadline = 0.0
        self.election_coordinator = None  # lazily re-created in _ensure_election_components
        self.timeout_watcher = HostTimeoutWatcher(timeout_s=HOST_TIMEOUT_S)

    def _on_self_elected(self, event: SelfElected) -> None:
        pass

    def _drain_lobby_messages(self) -> None:
        """Drain lobby WS messages during gameplay — handles InitialStateSync on rejoin."""
        while True:
            msg = self.ws_handler.poll()
            if msg is None:
                break
            if isinstance(msg, InitialStateSync):
                self.engine.world_state = msg.world_state
                self.last_snapshot_sequence = self.engine.world_state.sequence_number
                LOGGER.info(
                    "rejoin: applied InitialStateSync (seq=%d)",
                    self.engine.world_state.sequence_number,
                )

    # ------------------------------------------------------------------
    # Prediction and reconciliation
    # ------------------------------------------------------------------

    def _run_predicted_ticks(self, dt: float, local_input: InputState) -> None:
        """Predict and tick the engine, applying any pending drift correction."""
        ticks = 1 + self.pending_tick_adjustment
        self.pending_tick_adjustment = 0
        for _ in range(ticks):
            self.prediction_engine.predict(local_input, dt)
            self.engine.tick(dt, {self.local_player_id: local_input})

    def _adjust_prediction_lead(self) -> None:
        """Track the prediction lead and correct clock drift against the host.

        The prediction lead (number of unacknowledged predicted ticks) tracks
        the round-trip latency in ticks and is expected to stay roughly
        constant. Client and host tick loops run at very slightly different
        real-world rates, so the lead drifts steadily if left uncorrected.

        During the first PREDICTION_LEAD_CALIBRATION_FRAMES frames, the
        baseline is settled onto the connection's true lead via EWMA. After
        that it is frozen: deviations from this fixed baseline trigger a
        one-tick correction (skip a tick if running ahead, double-tick if
        running behind) for the next frame, which cancels the clock-rate
        mismatch instead of letting the baseline drift along with it.
        """
        pending = self.prediction_engine.pending_count()
        baseline = self.prediction_lead_baseline or float(pending)
        deviation = pending - baseline

        if deviation > PREDICTION_LEAD_DRIFT_TOLERANCE:
            self.pending_tick_adjustment = -1
            LOGGER.debug(
                "prediction lead drift: pending=%d baseline=%.2f -> skipping next tick",
                pending,
                baseline,
            )
        elif deviation < -PREDICTION_LEAD_DRIFT_TOLERANCE:
            self.pending_tick_adjustment = 1
            LOGGER.debug(
                "prediction lead drift: pending=%d baseline=%.2f -> double-ticking next frame",
                pending,
                baseline,
            )
        elif self.prediction_lead_calibration_remaining > 0:
            baseline += (pending - baseline) * PREDICTION_LEAD_EWMA_ALPHA

        if self.prediction_lead_calibration_remaining > 0:
            self.prediction_lead_calibration_remaining -= 1

        self.prediction_lead_baseline = baseline

    def _smoothed_local_visual_state(self, pre_reconcile_pos: tuple[float, float] | None):
        """Absorb the reconciliation correction gradually instead of snapping.

        reconcile() may have moved the local player (rollback + replay). Render
        a position that continues smoothly from last frame and closes the
        accumulated error toward the authoritative position at a rate
        proportional to the outstanding error (RECONCILE_GLIDE_RATE), capped
        at RECONCILE_MAX_GLIDE_PX per frame. Small errors (a few px of normal
        prediction jitter) resolve in 1-2 frames; large errors (a jump-timing
        mismatch, tens of px) resolve in a handful of frames at the capped
        rate instead of lingering for a full second, which would let the
        backlog pile up if corrections recur faster than that.
        """
        player = self.engine.world_state.get_player(self.local_player_id)
        if player is None or pre_reconcile_pos is None:
            return player

        correction_x = player.x - pre_reconcile_pos[0]
        correction_y = player.y - pre_reconcile_pos[1]
        offset_x, offset_y = self.visual_correction_offset
        offset_x += correction_x
        offset_y += correction_y

        glide_x = self._glide_step(offset_x)
        glide_y = self._glide_step(offset_y)
        offset_x -= glide_x
        offset_y -= glide_y
        self.visual_correction_offset = (offset_x, offset_y)

        visual_player = deepcopy(player)
        visual_player.x -= offset_x
        visual_player.y -= offset_y
        return visual_player

    @staticmethod
    def _glide_step(offset: float) -> float:
        step = offset * RECONCILE_GLIDE_RATE
        return max(-RECONCILE_MAX_GLIDE_PX, min(RECONCILE_MAX_GLIDE_PX, step))

    def _record_client_frame_interval(self) -> None:
        """Track wall-clock time between consecutive client frames."""
        now = time.monotonic()
        if self.client_last_frame_at is not None:
            self.client_frame_intervals.append(now - self.client_last_frame_at)
        self.client_last_frame_at = now

        if len(self.client_frame_intervals) < CLIENT_DIAG_LOG_INTERVAL:
            return
        intervals = self.client_frame_intervals
        avg_ms = sum(intervals) / len(intervals) * 1000.0
        min_ms = min(intervals) * 1000.0
        max_ms = max(intervals) * 1000.0
        LOGGER.info(
            "client frame stats: interval_ms avg=%.2f min=%.2f max=%.2f pending=%d baseline=%.2f",
            avg_ms,
            min_ms,
            max_ms,
            self.prediction_engine.pending_count(),
            self.prediction_lead_baseline,
        )
        self.client_frame_intervals.clear()

    def _drain_snapshot_packets(self) -> None:
        """Poll incoming snapshots, reconcile predicted state, update shadow copies."""
        now = time.time()
        while True:
            packet = self.udp_handler.receive_packet_nowait()
            if packet is None:
                return
            payload, _address = packet
            decoded = self.serializer.decode_message(payload)
            if not isinstance(decoded, WorldStateSnapshot):
                continue
            if decoded.sequence_number <= self.last_snapshot_sequence:
                continue

            self.last_snapshot_sequence = decoded.sequence_number
            self.prediction_engine.reconcile(decoded)
            self._update_shadow_copies(decoded)
            self.received_snapshots += 1
            if self.timeout_watcher is not None:
                self.timeout_watcher.reset(now)
            if self.env_state_buffer is not None:
                self.env_state_buffer.update(decoded)

    def _update_shadow_copies(self, snapshot: WorldStateSnapshot) -> None:
        """Push new authoritative states to shadow copies for all remote players."""
        for pid, char_state in snapshot.world_state.characters.items():
            if pid == self.local_player_id:
                continue
            shadow = self.shadow_copies.get(pid)
            if shadow is None:
                shadow = self.shadow_copy_factory()
                self.shadow_copies[pid] = shadow
            shadow.update(char_state)

    def _send_input_packet(self, local_input: InputState) -> None:
        """Send the local client's input packet to the authoritative host."""
        self.input_sequence_number += 1
        packet = PlayerInputPacket(
            player_id=self.local_player_id,
            sequence_number=self.input_sequence_number,
            input_state=local_input,
        )
        payload = self.serializer.encode_message(packet)
        self.udp_handler.send_packet_nowait(payload, self.remote_host, self.remote_port)
        self.sent_input_packets += 1
