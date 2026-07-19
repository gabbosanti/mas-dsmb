"""Render-only DTOs decoupling presentation from domain."""

from dataclasses import dataclass, field

from distributed_smb.domain.entity import Platform
from distributed_smb.domain.level import Decoration
from distributed_smb.domain.world import WorldState
from distributed_smb.shared.config import PLAYER_HEIGHT, PLAYER_WIDTH


@dataclass(slots=True)
class RenderCharacter:
    player_id: str
    x: float = 0.0
    y: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    width: int = PLAYER_WIDTH
    height: int = PLAYER_HEIGHT
    on_ground: bool = False
    is_crouching: bool = False
    join_index: int = 0


@dataclass(slots=True)
class RenderBlock:
    x: int
    y: int
    width: int
    height: int
    destroyed: bool = False


@dataclass(slots=True)
class RenderPowerUp:
    powerup_id: str
    x: int
    y: int
    width: int
    height: int
    collected: bool = False


@dataclass(slots=True)
class RenderGate:
    gate_id: str
    x: int
    y: int
    width: int
    height: int
    state: str = "closed"
    is_final: bool = True


@dataclass(slots=True)
class RenderEnemy:
    enemy_id: str
    x: float
    y: float
    width: int
    height: int


@dataclass(slots=True)
class RenderPlatform:
    x: int
    y: int
    width: int
    height: int


@dataclass(slots=True)
class RenderDecoration:
    kind: str
    x: int
    y: int
    width: int
    height: int


@dataclass(slots=True)
class RenderFrame:
    sequence_number: int = 0
    characters: dict[str, RenderCharacter] = field(default_factory=dict)
    respawning_player_ids: frozenset[str] = frozenset()
    platforms: list[RenderPlatform] = field(default_factory=list)
    decorations: list[RenderDecoration] = field(default_factory=list)
    blocks: list[RenderBlock] = field(default_factory=list)
    power_ups: dict[str, RenderPowerUp] = field(default_factory=dict)
    gates: dict[str, RenderGate] = field(default_factory=dict)
    enemies: dict[str, RenderEnemy] = field(default_factory=dict)
    coins_collected: int = 0
    coins_to_win: int = 5
    blocks_destroyed: int = 0
    blocks_to_win: int = 0
    enemies_defeated: int = 0
    enemies_to_win: int = 0
    victory: bool = False
    focus_player_id: str | None = None
    world_width: int = 0
    world_height: int = 0


def build_render_frame(
    world_state: WorldState,
    platforms: list[Platform],
    decorations: list[Decoration],
    focus_player_id: str | None,
    world_width: int,
    world_height: int,
) -> RenderFrame:
    """Build a render-only snapshot from the authoritative or visual WorldState."""
    return RenderFrame(
        sequence_number=world_state.sequence_number,
        characters={
            player_id: RenderCharacter(
                player_id=character.player_id,
                x=character.x,
                y=character.y,
                vx=character.vx,
                vy=character.vy,
                width=character.width,
                height=character.height,
                on_ground=character.on_ground,
                is_crouching=character.is_crouching,
                join_index=character.join_index,
            )
            for player_id, character in world_state.characters.items()
        },
        respawning_player_ids=frozenset(world_state.respawn_timers),
        platforms=[RenderPlatform(x=p.x, y=p.y, width=p.width, height=p.height) for p in platforms],
        decorations=[
            RenderDecoration(kind=d.kind, x=d.x, y=d.y, width=d.width, height=d.height)
            for d in decorations
        ],
        blocks=[
            RenderBlock(x=b.x, y=b.y, width=b.width, height=b.height, destroyed=b.destroyed)
            for b in world_state.environment.destructible_blocks
        ],
        power_ups={
            powerup_id: RenderPowerUp(
                powerup_id=power_up.powerup_id,
                x=power_up.x,
                y=power_up.y,
                width=power_up.width,
                height=power_up.height,
                collected=power_up.collected,
            )
            for powerup_id, power_up in world_state.environment.power_ups.items()
        },
        gates={
            gate_id: RenderGate(
                gate_id=gate.gate_id,
                x=gate.x,
                y=gate.y,
                width=gate.width,
                height=gate.height,
                state=gate.state,
                is_final=gate.is_final,
            )
            for gate_id, gate in world_state.environment.cooperative_gates.items()
        },
        enemies={
            enemy_id: RenderEnemy(
                enemy_id=enemy.enemy_id,
                x=enemy.x,
                y=enemy.y,
                width=enemy.width,
                height=enemy.height,
            )
            for enemy_id, enemy in world_state.environment.enemies.items()
        },
        coins_collected=world_state.coins_collected,
        coins_to_win=world_state.coins_to_win,
        blocks_destroyed=world_state.blocks_destroyed,
        blocks_to_win=world_state.blocks_to_win,
        enemies_defeated=world_state.enemies_defeated,
        enemies_to_win=world_state.enemies_to_win,
        victory=world_state.victory,
        focus_player_id=focus_player_id,
        world_width=world_width,
        world_height=world_height,
    )
