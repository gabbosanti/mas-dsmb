"""Game event dispatch and player eviction mixin."""

import json
import logging
import time

from distributed_smb.shared.config import UDP_INPUT_TIMEOUT
from distributed_smb.shared.mappers.gameplay_mapper import event_to_message
from distributed_smb.shared.messages.election import (
    ElectionAck,
    ElectionNack,
    NewHostClaim,
    ReconnectionAck,
)
from distributed_smb.shared.messages.gameplay import (
    BlockDestroyedMessage,
    GateStateChangedMessage,
    LevelResetMessage,
    PlayerDeathMessage,
    PlayerLeft,
    PowerUpCollectedMessage,
)

LOGGER = logging.getLogger(__name__)


class GameEventMixin:
    def _send_game_event(self, event: object) -> None:
        """Convert a domain event to a network message and broadcast to all clients."""
        msg = event_to_message(event)
        if msg is None:
            LOGGER.warning("No message mapping for event: %s", type(event).__name__)
            return
        payload = json.dumps(self.serializer.encode_ws_message(msg)).encode()
        self.game_event_broker.send(payload)
        LOGGER.info("game event sent: %s", msg)

    def _evict_player(self, pid: str) -> None:
        """Remove a player from the local world and clean up associated state."""
        self.engine.world_state.remove_player(pid)
        self.roster.remove_player(pid)
        self.cached_remote_inputs.pop(pid, None)
        self.last_remote_input_sequence.pop(pid, None)
        self.last_input_time.pop(pid, None)
        if hasattr(self, "shadow_copies"):
            self.shadow_copies.pop(pid, None)

    def _send_player_left(self, pid: str) -> None:
        """Broadcast a PlayerLeft message to all remaining clients."""
        msg = PlayerLeft(player_id=pid)
        payload = json.dumps(self.serializer.encode_ws_message(msg)).encode()
        self.game_event_broker.send(payload)
        LOGGER.info("PlayerLeft broadcast: %s", pid)

    def _check_player_disconnections(self) -> None:
        """Detect players that dropped their connection and evict them."""
        while True:
            pid = self.game_event_broker.get_disconnected_player()
            if pid is None:
                break
            LOGGER.info("Player left the game (WebSocket): %s", pid)
            self._evict_player(pid)
            self._send_player_left(pid)

        now = time.time()
        for pid in list(self.last_input_time):
            if now - self.last_input_time[pid] > UDP_INPUT_TIMEOUT:
                LOGGER.info("Player left the game (UDP timeout): %s", pid)
                self._evict_player(pid)
                self._send_player_left(pid)

    def _drain_game_events(self) -> None:
        """Poll incoming game events and apply them to the local world state."""
        while True:
            msg = self.game_event_handler.poll()
            if msg is None:
                return
            if isinstance(msg, BlockDestroyedMessage):
                LOGGER.info("game event applied: %s", msg)
                block = self.engine.world_state.get_block(msg.position)
                if block and not block.destroyed:
                    block.destroyed = True
            elif isinstance(msg, PowerUpCollectedMessage):
                LOGGER.info("game event applied: %s", msg)
                pu = self.engine.world_state.get_power_up(msg.powerup_id)
                if pu:
                    pu.collected = True
                    pu.owner = msg.player_id
            elif isinstance(msg, GateStateChangedMessage):
                LOGGER.info("game event applied: %s", msg)
                gate = self.engine.world_state.get_gate(msg.gate_id)
                if gate:
                    gate.state = msg.new_state
            elif isinstance(msg, PlayerLeft):
                LOGGER.info("Player left (received): %s", msg.player_id)
                self._evict_player(msg.player_id)
            elif isinstance(msg, PlayerDeathMessage):
                LOGGER.info("Player died (received): %s by enemy %s", msg.player_id, msg.enemy_id)
                self.engine.world_state.remove_player(msg.player_id)
            elif isinstance(msg, LevelResetMessage):
                LOGGER.info("game event applied: level reset (new run)")
                self.engine.reset_for_new_run()
            elif isinstance(msg, NewHostClaim):
                LOGGER.info(
                    "election: NewHostClaim from %s (join_index=%d)",
                    msg.claimer_ip,
                    msg.claimer_join_index,
                )
                self._on_new_host_claim(msg)
            elif isinstance(msg, ElectionAck):
                LOGGER.info("election: ElectionAck from %s", msg.from_ip)
                self._on_election_ack(msg)
            elif isinstance(msg, ElectionNack):
                LOGGER.info("election: ElectionNack from %s (%s)", msg.from_ip, msg.reason)
                self._on_election_nack(msg)
            elif isinstance(msg, ReconnectionAck):
                LOGGER.info("election: ReconnectionAck from new host %s", msg.new_host_ip)
                self._on_reconnection_ack(msg)
