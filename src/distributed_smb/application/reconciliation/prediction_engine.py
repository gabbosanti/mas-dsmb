import logging
import math
import time
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Callable, Deque

from distributed_smb.domain.game_engine import GameEngine
from distributed_smb.shared.config import TICK_INTERVAL
from distributed_smb.shared.input import InputState
from distributed_smb.shared.messages.sync import WorldStateSnapshot

LOGGER = logging.getLogger(__name__)

RECONCILE_LOG_THRESHOLD = 1.0

RECONCILE_LARGE_CORRECTION_THRESHOLD = 25.0


def _fmt_rtt(rtt_ms: float | None) -> str:
    return "n/a" if rtt_ms is None else f"{rtt_ms:.1f}"


@dataclass(slots=True)
class InputHistoryEntry:
    sequence_number: int
    input_state: InputState
    dt: float = TICK_INTERVAL
    sent_at: float = 0.0


class InputHistoryBuffer:
    def __init__(self, capacity: int = 64) -> None:
        self.capacity = capacity
        self._entries: Deque[InputHistoryEntry] = deque(maxlen=capacity)

    def push(
        self,
        sequence_number: int,
        input_state: InputState,
        dt: float = TICK_INTERVAL,
        sent_at: float = 0.0,
    ) -> None:
        self._entries.append(
            InputHistoryEntry(
                sequence_number=sequence_number,
                input_state=input_state,
                dt=dt,
                sent_at=sent_at,
            )
        )

    def acknowledge(self, sequence_number: int) -> InputHistoryEntry | None:
        """Drop acknowledged entries, returning the most recently acknowledged one."""
        last_acknowledged: InputHistoryEntry | None = None
        while self._entries and self._entries[0].sequence_number <= sequence_number:
            last_acknowledged = self._entries.popleft()
        return last_acknowledged

    def get_unacknowledged(self) -> list[InputHistoryEntry]:
        return list(self._entries)

    def clear(self) -> None:
        self._entries.clear()


class PredictionEngine:
    def __init__(
        self,
        engine: GameEngine,
        local_player_id: str,
        history_capacity: int = 64,
        time_provider: Callable[[], float] = time.monotonic,
    ) -> None:
        self.engine = engine
        self.local_player_id = local_player_id
        self.buffer = InputHistoryBuffer(capacity=history_capacity)
        self.time_provider = time_provider

    def predict(self, input_state: InputState, dt: float = TICK_INTERVAL) -> None:
        next_seq = self.engine.world_state.sequence_number + 1
        self.buffer.push(next_seq, input_state, dt, sent_at=self.time_provider())

    def reconcile(self, authoritative_snapshot: WorldStateSnapshot) -> None:
        world_state = authoritative_snapshot.world_state
        client_seq_before = self.engine.world_state.sequence_number
        predicted_player = self.engine.world_state.get_player(self.local_player_id)
        predicted_pos = (
            (predicted_player.x, predicted_player.y) if predicted_player else None
        )
        predicted_vy = predicted_player.vy if predicted_player else None
        authoritative_player = world_state.get_player(self.local_player_id)
        authoritative_vy = authoritative_player.vy if authoritative_player else None

        last_acknowledged = self.buffer.acknowledge(world_state.sequence_number)
        pending_inputs = self.buffer.get_unacknowledged()
        if client_seq_before > world_state.sequence_number + self.buffer.capacity:
            # Buffered inputs belong to a timeline the authoritative sequence can
            # no longer reach (a new host resumes from an older snapshot): they
            # would never be acknowledged, pinning the client a full buffer ahead.
            self.buffer.clear()
            pending_inputs = []
            LOGGER.info(
                "reconcile: dropped stale input history (client_seq=%d host_seq=%d)",
                client_seq_before,
                world_state.sequence_number,
            )
        # Blocks/power-ups/gates are managed exclusively by WS events, so the
        # UDP snapshot must not override the client's local prediction of
        # them. Enemies have no dedicated WS event (they move continuously)
        # so they must be taken from the authoritative snapshot, or client
        # and host would diverge forever with no resync path.
        local_env = self.engine.world_state.environment
        # reconcile() runs once per received snapshot (up to 60/s, more when
        # draining a backlog after a stall). deepcopy()-ing the *entire*
        # authoritative WorldState here — including destructible_blocks/
        # power_ups/cooperative_gates — used to pay for a full deep copy of
        # those collections only to immediately discard them three lines
        # below in favor of the client's own locally-predicted copies. Only
        # characters and enemies are actually kept from the snapshot *and*
        # get mutated during the replay below, so only those need copying;
        # everything else can be shared by reference.
        self.engine.world_state = replace(
            world_state,
            characters=deepcopy(world_state.characters),
            environment=replace(
                world_state.environment,
                enemies=deepcopy(world_state.environment.enemies),
            ),
        )
        self.engine.world_state.environment.destructible_blocks = (
            local_env.destructible_blocks
        )
        self.engine.world_state.environment.power_ups = local_env.power_ups
        self.engine.world_state.environment.cooperative_gates = (
            local_env.cooperative_gates
        )
        if pending_inputs:
            self._replay_pending(pending_inputs)

        rtt_ms = None
        if last_acknowledged is not None and last_acknowledged.sent_at:
            rtt_ms = (self.time_provider() - last_acknowledged.sent_at) * 1000.0

        self._log_correction(
            client_seq_before,
            world_state.sequence_number,
            pending_inputs,
            predicted_pos,
            predicted_vy,
            authoritative_vy,
            rtt_ms,
        )

    def _log_correction(
        self,
        client_seq_before: int,
        host_seq: int,
        pending_inputs: list[InputHistoryEntry],
        predicted_pos: tuple[float, float] | None,
        predicted_vy: float | None,
        authoritative_vy: float | None,
        rtt_ms: float | None,
    ) -> None:
        if predicted_pos is None:
            return
        reconciled_player = self.engine.world_state.get_player(self.local_player_id)
        if reconciled_player is None:
            return
        delta_x = reconciled_player.x - predicted_pos[0]
        delta_y = reconciled_player.y - predicted_pos[1]
        distance = math.hypot(delta_x, delta_y)
        if distance < RECONCILE_LOG_THRESHOLD:
            return
        if distance >= RECONCILE_LARGE_CORRECTION_THRESHOLD:
            jump_pending = any(entry.input_state.jump for entry in pending_inputs)
            LOGGER.info(
                "reconcile correction: %.1fpx (dx=%.1f dy=%.1f) "
                "client_seq=%d host_seq=%d pending_replayed=%d rtt_ms=%s "
                "predicted_vy=%s authoritative_vy=%s jump_in_replay=%s",
                distance,
                delta_x,
                delta_y,
                client_seq_before,
                host_seq,
                len(pending_inputs),
                _fmt_rtt(rtt_ms),
                predicted_vy,
                authoritative_vy,
                jump_pending,
            )
            return
        LOGGER.info(
            "reconcile correction: %.1fpx (dx=%.1f dy=%.1f) "
            "client_seq=%d host_seq=%d pending_replayed=%d rtt_ms=%s",
            distance,
            delta_x,
            delta_y,
            client_seq_before,
            host_seq,
            len(pending_inputs),
            _fmt_rtt(rtt_ms),
        )

    def pending_count(self) -> int:
        return len(self.buffer.get_unacknowledged())

    def _replay_pending(self, pending_inputs: list[InputHistoryEntry]) -> None:
        for entry in pending_inputs:
            self.engine.tick(entry.dt, {self.local_player_id: entry.input_state})
