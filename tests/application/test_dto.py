from distributed_smb.application.dto import build_render_frame
from distributed_smb.domain.entity import (
    CooperativeGate,
    DestructibleBlock,
    Enemy,
    ExclusivePowerUp,
    Platform,
)
from distributed_smb.domain.level import Decoration
from distributed_smb.domain.world import CharacterState, EnvironmentalState, WorldState


def _world_state() -> WorldState:
    return WorldState(
        sequence_number=7,
        characters={
            "player1": CharacterState(
                player_id="player1", x=10.0, y=20.0, vx=1.0, vy=2.0, join_index=0
            )
        },
        environment=EnvironmentalState(
            destructible_blocks=[DestructibleBlock(x=1, y=2, destroyed=True)],
            power_ups={"pu-a": ExclusivePowerUp(x=3, y=4, powerup_id="pu-a", collected=True)},
            enemies={"enemy-a": Enemy(enemy_id="enemy-a", x=5.0, y=6.0)},
            cooperative_gates={"gate-a": CooperativeGate(x=7, y=8, gate_id="gate-a", state="open")},
        ),
        coins_collected=3,
        coins_to_win=5,
        victory=True,
        respawn_timers={"player2": 12.5},
    )


def test_build_render_frame_maps_every_field():
    world_state = _world_state()
    platforms = [Platform(x=0, y=0, width=32, height=32)]
    decorations = [Decoration(kind="cloud", x=50, y=60, width=37, height=26)]

    frame = build_render_frame(
        world_state=world_state,
        platforms=platforms,
        decorations=decorations,
        focus_player_id="player1",
        world_width=1920,
        world_height=960,
    )

    assert frame.sequence_number == 7
    assert frame.characters["player1"].x == 10.0
    assert frame.characters["player1"].y == 20.0
    assert frame.characters["player1"].vx == 1.0
    assert frame.characters["player1"].vy == 2.0
    assert frame.characters["player1"].join_index == 0

    assert len(frame.platforms) == 1
    assert frame.platforms[0].width == 32

    assert len(frame.blocks) == 1
    assert frame.blocks[0].destroyed is True

    assert frame.power_ups["pu-a"].collected is True
    assert frame.gates["gate-a"].state == "open"
    assert frame.enemies["enemy-a"].x == 5.0

    assert frame.coins_collected == 3
    assert frame.coins_to_win == 5
    assert frame.victory is True

    assert frame.focus_player_id == "player1"
    assert frame.world_width == 1920
    assert frame.world_height == 960

    assert len(frame.decorations) == 1
    assert frame.decorations[0].kind == "cloud"
    assert frame.decorations[0].x == 50


def test_build_render_frame_respawning_player_ids_uses_keys_not_timestamps():
    world_state = _world_state()

    frame = build_render_frame(
        world_state=world_state,
        platforms=[],
        decorations=[],
        focus_player_id=None,
        world_width=0,
        world_height=0,
    )

    assert frame.respawning_player_ids == frozenset({"player2"})
