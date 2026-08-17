"""World state definitions."""

from copy import deepcopy
from dataclasses import asdict, dataclass, field

from distributed_smb.domain.entity import (
    CooperativeGate,
    DestructibleBlock,
    Enemy,
    ExclusivePowerUp,
)
from distributed_smb.domain.level import Level
from distributed_smb.shared.config import PLAYER_HEIGHT, PLAYER_WIDTH


@dataclass(slots=True)
class CharacterState:
    """Minimal dynamic state for a controllable character."""

    player_id: str
    x: float = 0.0
    y: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    width: int = PLAYER_WIDTH
    height: int = PLAYER_HEIGHT
    on_ground: bool = False
    is_crouching: bool = False
    prev_x: float = 0.0
    prev_y: float = 0.0
    join_index: int = 0
    powerup_effect_expires_at: float | None = None


@dataclass(slots=True)
class EnvironmentalState:
    destructible_blocks: list[DestructibleBlock] = field(default_factory=list)
    power_ups: dict[str, ExclusivePowerUp] = field(default_factory=dict)
    enemies: dict[str, Enemy] = field(default_factory=dict)
    cooperative_gates: dict[str, CooperativeGate] = field(default_factory=dict)


@dataclass(slots=True)
class WorldState:
    """Authoritative world snapshot stored locally."""

    sequence_number: int = 0
    characters: dict[str, CharacterState] = field(default_factory=dict)
    environment: EnvironmentalState = field(default_factory=EnvironmentalState)
    coins_collected: int = 0
    coins_to_win: int = 5
    blocks_destroyed: int = 0
    blocks_to_win: int = 0
    enemies_defeated: int = 0
    enemies_to_win: int = 0
    initial_enemy_count: int = 0
    victory: bool = False
    victory_player_id: str | None = None
    victory_at: float | None = None
    respawn_timers: dict[str, float] = field(default_factory=dict)

    def load_level(self, level: Level) -> None:
        """Populate the environment from a level template.

        Deep-copies every entity: level.blocks/powerups/enemies/gates is a
        reusable template (kept alive on GameEngine._level for mid-session
        resets), and gameplay mutates entity state in place (block.destroyed,
        power_up.collected, ...) — aliasing the template's objects here would
        let a run's mutations leak into the "fresh" state of the next one.
        """
        self.environment.destructible_blocks = deepcopy(level.blocks)
        self.environment.power_ups = {
            powerup.powerup_id: powerup for powerup in deepcopy(level.powerups)
        }
        self.environment.enemies = {enemy.enemy_id: enemy for enemy in deepcopy(level.enemies)}
        self.environment.cooperative_gates = {gate.gate_id: gate for gate in deepcopy(level.gates)}
        self.coins_collected = 0
        self.coins_to_win = level.coins_to_win
        self.blocks_destroyed = 0
        self.blocks_to_win = level.blocks_to_win
        self.enemies_defeated = 0
        self.enemies_to_win = level.enemies_to_win
        self.initial_enemy_count = len(level.enemies)
        self.victory = False
        self.victory_player_id = None
        self.victory_at = None

    def add_player(self, character: CharacterState):
        self.characters[character.player_id] = character

    def remove_player(self, player_id: str):
        if player_id in self.characters:
            del self.characters[player_id]

    def get_player(self, player_id: str) -> CharacterState | None:
        return self.characters[player_id] if player_id in self.characters else None

    def get_all_players(self):
        return list(self.characters.values())

    def get_all_players_dict(self):
        return self.characters

    def add_block(self, block: DestructibleBlock) -> None:
        self.environment.destructible_blocks.append(block)

    def get_block(self, position: tuple[int, int]) -> DestructibleBlock | None:
        for block in self.environment.destructible_blocks:
            if (block.x, block.y) == position:
                return block
        return None

    def add_power_up(self, power_up: ExclusivePowerUp) -> None:
        self.environment.power_ups[power_up.powerup_id] = power_up

    def get_power_up(self, powerup_id: str) -> ExclusivePowerUp | None:
        return self.environment.power_ups.get(powerup_id)

    def add_gate(self, gate: CooperativeGate) -> None:
        self.environment.cooperative_gates[gate.gate_id] = gate

    def get_gate(self, gate_id: str) -> CooperativeGate | None:
        return self.environment.cooperative_gates.get(gate_id)

    def add_enemy(self, enemy: Enemy) -> None:
        self.environment.enemies[enemy.enemy_id] = enemy

    def get_enemy(self, enemy_id: str) -> Enemy | None:
        return self.environment.enemies.get(enemy_id)

    def to_dict(self) -> dict:
        """Serialize WorldState in dict for messages."""
        d = asdict(self)
        for gate in d["environment"]["cooperative_gates"].values():
            gate["contributions"] = list(gate["contributions"])
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "WorldState":
        """Deserialize a dict into a WorldState."""
        characters = {k: CharacterState(**v) for k, v in data["characters"].items()}
        destructible_blocks = [
            DestructibleBlock(**b) for b in data["environment"]["destructible_blocks"]
        ]
        power_ups = {k: ExclusivePowerUp(**v) for k, v in data["environment"]["power_ups"].items()}
        cooperative_gates = {
            k: CooperativeGate(**{**v, "contributions": set(v["contributions"])})
            for k, v in data["environment"]["cooperative_gates"].items()
        }
        enemies = {k: Enemy(**v) for k, v in data["environment"].get("enemies", {}).items()}
        environment = EnvironmentalState(
            destructible_blocks=destructible_blocks,
            power_ups=power_ups,
            enemies=enemies,
            cooperative_gates=cooperative_gates,
        )
        return cls(
            sequence_number=data["sequence_number"],
            characters=characters,
            environment=environment,
            coins_collected=data.get("coins_collected", 0),
            coins_to_win=data.get("coins_to_win", 5),
            blocks_destroyed=data.get("blocks_destroyed", 0),
            blocks_to_win=data.get("blocks_to_win", 0),
            enemies_defeated=data.get("enemies_defeated", 0),
            enemies_to_win=data.get("enemies_to_win", 0),
            initial_enemy_count=data.get("initial_enemy_count", len(enemies)),
            victory=data.get("victory", False),
            victory_player_id=data.get("victory_player_id"),
            victory_at=data.get("victory_at"),
            respawn_timers=data.get("respawn_timers", {}),
        )
