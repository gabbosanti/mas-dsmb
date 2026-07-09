from copy import deepcopy

import pytest

from distributed_smb.application.reconciliation import PredictionEngine
from distributed_smb.domain.game_engine import GameEngine
from distributed_smb.domain.world import WorldState
from distributed_smb.shared.config import TICK_INTERVAL
from distributed_smb.shared.input import InputState
from distributed_smb.shared.messages.sync import WorldStateSnapshot


def _make_snapshot(world_state: WorldState) -> WorldStateSnapshot:
    return WorldStateSnapshot(
        sequence_number=world_state.sequence_number,
        world_state=world_state,
    )


def test_prediction_engine_predict_does_not_tick_engine():
    """predict() must not advance the engine — the caller is responsible for ticking."""
    engine = GameEngine()
    engine.spawn_player("player1")
    pe = PredictionEngine(engine=engine, local_player_id="player1")

    seq_before = engine.world_state.sequence_number
    pe.predict(InputState(right=True))

    assert engine.world_state.sequence_number == seq_before


def test_prediction_engine_predict_records_input_in_buffer():
    """predict() must push an entry into the buffer so reconcile can replay it."""
    engine = GameEngine()
    engine.spawn_player("player1")
    pe = PredictionEngine(engine=engine, local_player_id="player1")

    assert pe.buffer.get_unacknowledged() == []
    pe.predict(InputState(right=True))
    assert len(pe.buffer.get_unacknowledged()) == 1


def test_prediction_engine_reconcile_applies_authoritative_state():
    """reconcile() must set engine.world_state to the authoritative snapshot."""
    engine = GameEngine()
    engine.spawn_player("player1")
    pe = PredictionEngine(engine=engine, local_player_id="player1")

    pe.predict(InputState(right=True))
    engine.tick(TICK_INTERVAL, {"player1": InputState(right=True)})

    authoritative = deepcopy(engine.world_state)
    authoritative.get_player("player1").x = 999.0
    snapshot = _make_snapshot(authoritative)

    pe.reconcile(snapshot)

    assert engine.world_state.get_player("player1").x == 999.0


def test_prediction_engine_reconcile_applies_authoritative_enemy_position():
    """Enemies have no dedicated WS event, so reconcile() must take their
    position from the authoritative snapshot — otherwise client and host
    diverge forever (M6-1)."""
    engine = GameEngine()
    engine.spawn_player("player1")
    pe = PredictionEngine(engine=engine, local_player_id="player1")

    local_enemy = next(iter(engine.world_state.environment.enemies.values()))
    local_enemy.x = 111.0

    authoritative = deepcopy(engine.world_state)
    authoritative_enemy = next(iter(authoritative.environment.enemies.values()))
    authoritative_enemy.x = 500.0
    snapshot = _make_snapshot(authoritative)

    pe.reconcile(snapshot)

    reconciled_enemy = next(iter(engine.world_state.environment.enemies.values()))
    assert reconciled_enemy.x == 500.0


def test_prediction_engine_reconcile_still_preserves_local_blocks_powerups_gates():
    """Blocks/power-ups/gates stay local — they are synced via WS events, not
    the UDP snapshot (M4 behavior, must not regress when fixing M6-1)."""
    engine = GameEngine()
    engine.spawn_player("player1")
    pe = PredictionEngine(engine=engine, local_player_id="player1")

    local_block = engine.world_state.environment.destructible_blocks[0]
    local_block.destroyed = True

    authoritative = deepcopy(engine.world_state)
    authoritative.environment.destructible_blocks[0].destroyed = False
    snapshot = _make_snapshot(authoritative)

    pe.reconcile(snapshot)

    assert engine.world_state.environment.destructible_blocks[0].destroyed is True


def test_prediction_engine_reconcile_replays_pending_inputs():
    """After reconcile, unacknowledged inputs must be replayed on top of the authoritative state."""
    engine = GameEngine()
    engine.spawn_player("player1")
    pe = PredictionEngine(engine=engine, local_player_id="player1")

    # Two predict+tick cycles
    pe.predict(InputState(right=True))
    engine.tick(TICK_INTERVAL, {"player1": InputState(right=True)})
    pe.predict(InputState(right=True))
    engine.tick(TICK_INTERVAL, {"player1": InputState(right=True)})

    # Server acknowledges tick 1 only (sequence_number=1)
    authoritative_engine = GameEngine()
    authoritative_engine.spawn_player("player1")
    authoritative_engine.tick(TICK_INTERVAL, {"player1": InputState(right=True)})
    authoritative_snapshot = _make_snapshot(deepcopy(authoritative_engine.world_state))

    pe.reconcile(authoritative_snapshot)

    # Input 2 must have been replayed: expected = authoritative + one more right tick
    expected_engine = GameEngine()
    expected_engine.spawn_player("player1")
    expected_engine.world_state = deepcopy(authoritative_engine.world_state)
    expected_engine.tick(TICK_INTERVAL, {"player1": InputState(right=True)})

    expected_x = expected_engine.world_state.get_player("player1").x
    assert engine.world_state.get_player("player1").x == pytest.approx(expected_x)


def test_prediction_engine_acknowledge_clears_confirmed_inputs():
    """reconcile() must remove acknowledged inputs from the buffer."""
    engine = GameEngine()
    engine.spawn_player("player1")
    pe = PredictionEngine(engine=engine, local_player_id="player1")

    pe.predict(InputState(right=True))
    engine.tick(TICK_INTERVAL, {"player1": InputState(right=True)})
    pe.predict(InputState(right=True))
    engine.tick(TICK_INTERVAL, {"player1": InputState(right=True)})

    assert len(pe.buffer.get_unacknowledged()) == 2

    authoritative = deepcopy(engine.world_state)
    authoritative.sequence_number = 1
    pe.reconcile(_make_snapshot(authoritative))

    # Only input 2 (seq=2) remains pending
    assert len(pe.buffer.get_unacknowledged()) == 1
