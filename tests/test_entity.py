import pytest

from distributed_smb.domain.entity import (
    CooperativeGate,
    DestructibleBlock,
    ExclusivePowerUp,
)
from distributed_smb.domain.events import (
    BlockDestroyedEvent,
    GateStateChangedEvent,
    PowerUpCollectedEvent,
)


def test_destructible_block_destroy_emits_event():
    block = DestructibleBlock(x=100, y=100)

    event = block.destroy()

    assert block.destroyed is True
    assert isinstance(event, BlockDestroyedEvent)
    assert event.position == (100, 100)

    with pytest.raises(ValueError):
        block.destroy()


def test_exclusive_powerup_collects_only_once():
    power_up = ExclusivePowerUp(x=0, y=0, powerup_id="power1")
    event = power_up.collect("player1")

    assert power_up.collected is True
    assert power_up.owner == "player1"
    assert isinstance(event, PowerUpCollectedEvent)
    assert event.player_id == "player1"

    with pytest.raises(ValueError):
        power_up.collect("player2")


def test_cooperative_gate_opens_when_requirements_are_met():
    gate = CooperativeGate(x=0, y=0, gate_id="gate1")

    event = gate.update_state(True)

    assert isinstance(event, GateStateChangedEvent)
    assert gate.state == "open"
    assert event.new_state == "open"


def test_cooperative_gate_stays_closed_when_requirements_are_unmet():
    gate = CooperativeGate(x=0, y=0, gate_id="gate1")

    event = gate.update_state(False)

    assert event is None
    assert gate.state == "closed"
