"""Host-side frame synchronisation mixin."""

import logging
import time

from distributed_smb.shared.input import InputState
from distributed_smb.shared.messages.gameplay import PlayerInputPacket
from distributed_smb.shared.messages.recovery import HostDiscoveryProbe, HostIdentityResponse
from distributed_smb.shared.messages.session import RosterUpdate
from distributed_smb.shared.messages.sync import InitialStateSync, WorldStateSnapshot

LOGGER = logging.getLogger(__name__)

# Number of host frames between diagnostic frame-timing log lines.
HOST_DIAG_LOG_INTERVAL = 120


LOGGER = logging.getLogger(__name__)


class HostGameplayMixin:
    def bootstrap_from_snapshot(self, snapshot: WorldStateSnapshot) -> None:
        """Apply an incoming snapshot as the authoritative starting state.

        Called by ElectionMixin._promote_to_host() after the election, using
        the last snapshot stored in env_state_buffer. This preserves the
        EnvironmentalState (destroyed blocks, collected power-ups) from the
        moment before the old host crashed.

        The snapshot's world_state is already a decoded WorldState object
        (the serializer decodes it before constructing WorldStateSnapshot),
        so we assign it directly to the engine without re-decoding.
        """
        self.engine.world_state = snapshot.world_state
        self.last_snapshot_sequence = snapshot.sequence_number
        LOGGER.info("bootstrapped world state from snapshot seq=%d", snapshot.sequence_number)

    def _drain_remote_input_packets(self) -> int:
        """Poll incoming client input packets and cache the latest valid ones.

        Returns the number of input packets drained this frame, used for
        diagnostic logging of host-side socket backlog.
        """
        drained = 0
        while True:
            packet = self.udp_handler.receive_packet_nowait()
            if packet is None:
                return drained
            payload, _address = packet
            decoded = self.serializer.decode_message(payload)
            if isinstance(decoded, HostDiscoveryProbe):
                if decoded.session_id != self.session_id:
                    continue

                response = HostIdentityResponse(
                    session_id=self.session_id,
                    host_ip=self.local_ip,
                )
                self.udp_handler.send_packet_nowait(
                    self.serializer.encode_message(response),
                    _address[0],
                    _address[1],
                )
                continue

            if not isinstance(decoded, PlayerInputPacket):
                continue

            last_sequence = self.last_remote_input_sequence.get(decoded.player_id, -1)
            if decoded.sequence_number <= last_sequence:
                continue

            self.last_remote_input_sequence[decoded.player_id] = decoded.sequence_number
            self.cached_remote_inputs[decoded.player_id] = decoded.input_state
            self.last_input_time[decoded.player_id] = time.time()
            self.received_input_packets += 1
            drained += 1

    def _build_host_inputs(self, local_input: InputState) -> dict[str, InputState]:
        """Combine local and remote input streams into one authoritative map."""
        inputs = {self.local_player_id: local_input}
        for entry in self.roster.get_all_players():
            if entry.player_id != self.local_player_id:
                inputs[entry.player_id] = self.cached_remote_inputs.get(
                    entry.player_id, InputState()
                )
        return inputs

    def _send_world_state_snapshot(self) -> int:
        """Broadcast the authoritative world state to all remote peers.

        Returns the serialized payload size in bytes, used for diagnostic
        logging of snapshot growth over a session.
        """
        snapshot = WorldStateSnapshot(
            sequence_number=self.engine.world_state.sequence_number,
            world_state=self.engine.world_state.to_dict(),
        )
        payload = self.serializer.encode_message(snapshot)
        for entry in self.roster.get_all_players():
            if entry.player_id != self.local_player_id:
                self.udp_handler.send_packet_nowait(payload, entry.host, entry.udp_port)
        self.sent_snapshots += 1
        return len(payload)

    def _check_for_rejoining_players(self) -> None:
        """Detect players that rejoined via lobby WS (M9) and add them to the active game."""
        msg = self.ws_handler.poll()
        if not isinstance(msg, RosterUpdate):
            return
        known = {e.join_index for e in self.roster.get_all_players()}
        new_entries = []
        for entry in msg.roster.get_all_players():
            if entry.join_index not in known:
                x, y = self._spawn_position_for(entry.join_index)
                self.roster.add_player(entry)
                self.engine.spawn_player(entry.player_id, x=x, y=y, join_index=entry.join_index)
                self.last_input_time[entry.player_id] = time.time()
                LOGGER.info(
                    "rejoin: %s (join_index=%d) re-entered the game",
                    entry.player_id,
                    entry.join_index,
                )
                new_entries.append(entry)
        if new_entries:
            sync = InitialStateSync(world_state=self.engine.world_state.to_dict())
            self.ws_handler.send(sync)
            LOGGER.info("rejoin: sent InitialStateSync to %d rejoining player(s)", len(new_entries))

    def _process_host_frame(self, dt: float, local_input: InputState) -> object:
        """Run one authoritative host frame: drain inputs, tick, broadcast snapshot."""
        self._record_host_frame_interval()
        self._check_for_rejoining_players()
        self._check_player_disconnections()
        self.host_input_packets_window += self._drain_remote_input_packets()
        authoritative_inputs = self._build_host_inputs(local_input)
        self.engine.tick(dt, authoritative_inputs)
        for event in self.engine.events:
            self._send_game_event(event)
        self.engine.events.clear()
        self.host_last_payload_bytes = self._send_world_state_snapshot()
        self._maybe_log_host_diagnostics()
        return self.engine.world_state

    def _record_host_frame_interval(self) -> None:
        """Track wall-clock time between consecutive host frames."""
        now = time.monotonic()
        if self.host_last_frame_at is not None:
            self.host_frame_intervals.append(now - self.host_last_frame_at)
        self.host_last_frame_at = now

    def _maybe_log_host_diagnostics(self) -> None:
        """Periodically log host frame timing, payload size, and input backlog.

        Diagnostic for investigating session-long latency growth observed on
        the client (rtt_ms climbing from ~20ms to ~190ms over a session): if
        the host's own frame interval grows over time, the host loop itself
        is falling behind.
        """
        if len(self.host_frame_intervals) < HOST_DIAG_LOG_INTERVAL:
            return
        intervals = self.host_frame_intervals
        avg_ms = sum(intervals) / len(intervals) * 1000.0
        min_ms = min(intervals) * 1000.0
        max_ms = max(intervals) * 1000.0
        LOGGER.info(
            "host frame stats: interval_ms avg=%.2f min=%.2f max=%.2f "
            "input_packets=%d last_payload_bytes=%d",
            avg_ms,
            min_ms,
            max_ms,
            self.host_input_packets_window,
            self.host_last_payload_bytes,
        )
        self.host_frame_intervals.clear()
        self.host_input_packets_window = 0
