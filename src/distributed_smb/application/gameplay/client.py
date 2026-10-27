"""Client-side frame synchronisation mixin."""

import logging
import time
from copy import deepcopy

from distributed_smb.application.election import (
    ElectionCoordinator,
    EnvironmentalStateBuffer,
    HostTimeoutWatcher,
    SelfElected,
)
from distributed_smb.shared.config import (
    GAME_EVENT_WS_PATH,
    GAME_EVENT_WS_PORT,
    HOST_TIMEOUT_S,
    HOST_UDP_PORT,
    HOST_VERIFY_RESEND_INTERVAL_S,
    HOST_VERIFY_TIMEOUT_S,
    PREDICTION_LEAD_CALIBRATION_FRAMES,
    PREDICTION_LEAD_DRIFT_TOLERANCE,
    PREDICTION_LEAD_EWMA_ALPHA,
    RECONCILE_GLIDE_RATE,
    RECONCILE_MAX_GLIDE_PX,
    RECONNECTION_FALLBACK_TIMEOUT_S,
    T_ELECTION_BASE_S,
    T_ELECTION_DELTA_S,
    TICK_INTERVAL,
)
from distributed_smb.shared.input import InputState
from distributed_smb.shared.messages.election import (
    ElectionAck,
    NewHostClaim,
    ReconnectionAck,
)
from distributed_smb.shared.messages.gameplay import PlayerInputPacket
from distributed_smb.shared.messages.recovery import (
    HostDiscoveryProbe,
    HostIdentityResponse,
)
from distributed_smb.shared.messages.sync import InitialStateSync, WorldStateSnapshot
from distributed_smb.shared.session_metadata import CachedPeer

LOGGER = logging.getLogger(__name__)

# Number of client frames between diagnostic frame-timing log lines.
CLIENT_DIAG_LOG_INTERVAL = 120


class ClientGameplayMixin:
    def _process_client_frame(self, local_input: InputState) -> object:
        """Run one client frame: send input, predict, tick, reconcile, drain events."""
        self._ensure_election_components()
        self._record_client_frame_interval()
        self._drain_lobby_messages()
        self._send_input_packet(local_input)
        self._run_predicted_ticks(local_input)
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

        if self._host_verify_deadline == 0.0:
            if not self.election_triggered and self.timeout_watcher.tick(now):
                LOGGER.warning(
                    "host timeout suspected — verifying before starting election"
                )
                self._send_host_verify_probe(now)
        else:
            self._tick_host_verify(now)

        if self.election_triggered:
            event = self.election_coordinator.tick(now)
            if isinstance(event, SelfElected):
                LOGGER.info("election: self-elected as new host (ip=%s)", event.my_ip)
                self._on_self_elected(event)
            self._tick_claim_deadline(now)
            self._tick_reconnection_fallback(now)

    def _send_host_verify_probe(self, now: float) -> None:
        """Probe the suspected-dead host directly before trusting the local timeout."""
        self._host_verify_deadline = now + HOST_VERIFY_TIMEOUT_S
        self._host_verify_next_probe = now
        self._resend_host_verify_probe(now)

    def _resend_host_verify_probe(self, now: float) -> None:
        self._host_verify_next_probe = now + HOST_VERIFY_RESEND_INTERVAL_S
        probe = HostDiscoveryProbe(
            session_id=self.session_id, requester_ip=self.local_ip
        )
        self.udp_handler.send_packet_nowait(
            self.serializer.encode_message(probe), self.remote_host, self.remote_port
        )

    def _tick_host_verify(self, now: float) -> None:
        if not self.timeout_watcher.tick(now):
            # A fresh snapshot (or a verify response, see _on_host_identity_response)
            # arrived while we were waiting — the host is alive after all.
            self._host_verify_deadline = 0.0
            return
        if now >= self._host_verify_deadline:
            LOGGER.warning("host timeout confirmed — starting election")
            self._host_verify_deadline = 0.0
            self.election_triggered = True
            peers = self._known_client_peers()
            self.election_coordinator.start_election(peers)
            self.election_coordinator.set_election_timer(now)
            return
        if now >= self._host_verify_next_probe:
            self._resend_host_verify_probe(now)

    def _on_host_identity_response(self, msg: HostIdentityResponse) -> None:
        if self._host_verify_deadline == 0.0:
            return
        if msg.session_id != self.session_id:
            return
        LOGGER.info(
            "election: host verify probe answered — host alive, election cancelled"
        )
        self._host_verify_deadline = 0.0
        if self.timeout_watcher is not None:
            self.timeout_watcher.reset(time.time())

    def _tick_reconnection_fallback(self, now: float) -> None:
        """Fall back to a direct UDP probe if ReconnectionAck never arrives."""
        if self.reconnected or self._following_host_ip is None:
            return
        if now - self._following_since < RECONNECTION_FALLBACK_TIMEOUT_S:
            return

        candidate_ip = self._following_host_ip
        # The candidate's own promotion (Docker containers, WS retries) can take
        # much longer than one probe timeout — retry every interval instead of
        # giving up after a single miss.
        self._following_since = now
        LOGGER.warning(
            "election: no ReconnectionAck from %s after %.1fs — probing directly",
            candidate_ip,
            RECONNECTION_FALLBACK_TIMEOUT_S,
        )
        found_ip = self.recovery_prober.find_current_host(
            self.session_id,
            self.local_ip,
            [CachedPeer(player_id="", ip=candidate_ip, join_index=-1)],
            timeout_per_peer=1.5,
        )
        if found_ip is None:
            LOGGER.warning(
                "election: direct probe to %s got no response — will retry",
                candidate_ip,
            )
            return
        self._following_host_ip = None
        self._on_reconnection_ack(
            ReconnectionAck(
                new_host_ip=found_ip,
                udp_port=HOST_UDP_PORT,
                game_events_port=GAME_EVENT_WS_PORT,
                session_id=self.session_id,
            )
        )

    # ------------------------------------------------------------------
    # Election event handlers (called from _drain_game_events)
    # ------------------------------------------------------------------

    def _on_reconnection_ack(self, ack: ReconnectionAck) -> None:
        """Follow the newly promoted host: reconnect, realign, start fresh."""
        if self.reconnected or self._promotion_done:
            return
        self.reconnected = True
        self.remote_host = ack.new_host_ip
        self.remote_port = ack.udp_port
        self._reconnect_game_event_handler(
            ack.new_host_ip, ack.game_events_port, GAME_EVENT_WS_PATH
        )
        self.game_event_broker.reconnect(ack.new_host_ip, ack.game_events_port)
        self._reconnect_lobby_ws_handler(ack.new_host_ip)
        LOGGER.info(
            "reconnected to new host %s (udp_port=%d, game_events_port=%d)",
            ack.new_host_ip,
            ack.udp_port,
            ack.game_events_port,
        )

        self._resync_environment_from_buffer()
        # The new host resumes from its own last buffered snapshot, whose sequence
        # can be lower than the last one this client saw — without this reset every
        # snapshot from it would be discarded as stale and never reconciled.
        self.last_snapshot_sequence = 0
        self._repoint_roster_at(ack.new_host_ip)
        self._reset_election_state()
        self._recalibrate_prediction_lead()

    def _resync_environment_from_buffer(self) -> None:
        """Realign blocks, power-ups and gates with the rest of the survivors."""
        if self.env_state_buffer is None:
            return
        last = self.env_state_buffer.get_last()
        if last is None:
            return
        environment = self.engine.world_state.environment
        environment.destructible_blocks = (
            last.world_state.environment.destructible_blocks
        )
        environment.power_ups = last.world_state.environment.power_ups
        environment.cooperative_gates = last.world_state.environment.cooperative_gates

    def _repoint_roster_at(self, new_host_ip: str) -> None:
        """Evict the crashed host and flag the elected one as host."""
        old_host = self.roster.get_host()
        if old_host is not None:
            self._evict_player(old_host.player_id)
        new_host = next(
            (
                entry
                for entry in self.roster.get_all_players()
                if entry.host == new_host_ip
            ),
            None,
        )
        if new_host is not None:
            self.roster.promote_host(new_host.player_id)

    def _reset_election_state(self) -> None:
        """Re-arm the failure detector so a second host crash is noticed as well."""
        self.election_triggered = False
        self._pending_election_acks = set()
        self._election_claim_deadline = 0.0
        self.election_coordinator = (
            None  # lazily re-created in _ensure_election_components
        )
        self.timeout_watcher = HostTimeoutWatcher(timeout_s=HOST_TIMEOUT_S)
        self._host_verify_deadline = 0.0

    def _recalibrate_prediction_lead(self) -> None:
        """Restart the RTT calibration: the new host may sit on another machine."""
        self.prediction_lead_baseline = 0.0
        self.prediction_lead_calibration_remaining = PREDICTION_LEAD_CALIBRATION_FRAMES
        self.visual_correction_offset = (0.0, 0.0)

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

    def _run_predicted_ticks(self, local_input: InputState) -> None:
        """Predict and tick the engine, applying any pending drift correction."""
        ticks = 1 + self.pending_tick_adjustment
        self.pending_tick_adjustment = 0
        # Fixed simulation step, not the real (variable) frame dt: apply_physics()
        # integrates gravity/velocity as GRAVITY*dt and vy*dt. _replay_pending()
        # later re-ticks these same buffered inputs against the authoritative
        # host's own ticks for the same sequence numbers — if this client used
        # a different dt than the host did for "the same" tick (near-certain
        # with two independently wall-clock-timed frame loops, especially
        # under CPU contention), the two diverge, compounding over a jump arc
        # into the large, frequent reconciliation corrections seen in testing.
        for _ in range(ticks):
            self.prediction_engine.predict(local_input, TICK_INTERVAL)
            self.engine.tick(TICK_INTERVAL, {self.local_player_id: local_input})

    def _adjust_prediction_lead(self) -> None:
        """Track the prediction lead and correct clock drift against the host."""
        pending = self.prediction_engine.pending_count()
        baseline = self.prediction_lead_baseline
        deviation = pending - baseline

        # The baseline is never moved by a correction: it is the calibrated
        # lead this connection needs, and a correction exists precisely to pull
        # `pending` back to it. Letting it follow the correction (baseline += 1)
        # made the target chase the drift instead of opposing it: each cycle
        # recovered one tick and conceded one, so lead and baseline climbed
        # together without bound (observed on a real LAN rejoin: pending 3 -> 29,
        # apparent RTT 80ms -> 419ms, with the frame time perfectly stable).
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

    def _smoothed_local_visual_state(
        self, pre_reconcile_pos: tuple[float, float] | None
    ):
        """Absorb the reconciliation correction gradually instead of snapping."""
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

            if isinstance(decoded, NewHostClaim):
                self._on_new_host_claim(decoded)
                continue
            if isinstance(decoded, ElectionAck):
                self._on_election_ack(decoded)
                continue
            if isinstance(decoded, ReconnectionAck):
                self._on_reconnection_ack(decoded)
                continue
            if isinstance(decoded, HostIdentityResponse):
                self._on_host_identity_response(decoded)
                continue
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
            shadow.update(char_state, snapshot.sequence_number)

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
