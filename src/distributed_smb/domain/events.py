# domain/events.py
from dataclasses import dataclass


@dataclass(slots=True)
class BlockDestroyedEvent:
    position: tuple[int, int]


@dataclass(slots=True)
class PowerUpCollectedEvent:
    powerup_id: str
    player_id: str


@dataclass(slots=True)
class GateStateChangedEvent:
    gate_id: str
    new_state: str


@dataclass(slots=True)
class PlayerDeathEvent:
    player_id: str
    enemy_id: str
