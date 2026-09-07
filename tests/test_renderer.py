import pygame

from distributed_smb.application.dto import (
    RenderBlock,
    RenderCharacter,
    RenderFrame,
    RenderGate,
    RenderPowerUp,
)
from distributed_smb.presentation.renderer import Renderer
from distributed_smb.presentation.renderer_ui import UiRenderer


def test_renderer_loads_mario_asset_pack():
    renderer = Renderer()

    assert renderer._get_asset_sprite("Mario.png", (8 * 32, 0, 32, 32), 50, 50) is not None
    assert renderer._get_asset_sprite("OverWorld.png", (16, 0, 16, 16), 32, 32) is not None
    assert renderer._get_asset_sprite("Items.png", (48, 0, 16, 16), 32, 32) is not None
    assert renderer._get_asset_sprite("Castle.png", (0, 0, 80, 80), 54, 96) is not None


def test_renderer_draws_sprite_instead_of_flat_background(monkeypatch):
    monkeypatch.setattr(pygame.time, "get_ticks", lambda: 0)
    screen = pygame.display.set_mode((200, 200))
    renderer = Renderer(width=200, height=200)
    frame = RenderFrame(
        characters={
            "player1": RenderCharacter(
                player_id="player1",
                x=40,
                y=30,
                width=50,
                height=50,
                on_ground=True,
            )
        }
    )

    renderer.render(screen=screen, frame=frame)

    sprite_pixels = [screen.get_at((x, y))[:3] for x in range(40, 90) for y in range(30, 80)]
    assert any(pixel != renderer.background_color for pixel in sprite_pixels)


def test_renderer_preserves_last_facing_direction_when_player_stops(monkeypatch):
    monkeypatch.setattr(pygame.time, "get_ticks", lambda: 0)
    screen = pygame.display.set_mode((200, 200))
    renderer = Renderer(width=200, height=200)
    character = RenderCharacter(
        player_id="player1",
        x=40,
        y=30,
        width=50,
        height=50,
        vx=-10,
        on_ground=True,
    )
    frame = RenderFrame(characters={"player1": character})

    renderer.render(screen=screen, frame=frame)
    assert renderer._facing_by_player["player1"] == -1

    character.vx = 0
    renderer.render(screen=screen, frame=frame)
    assert renderer._facing_by_player["player1"] == -1


def test_walk_animation_uses_distinct_frames(monkeypatch):
    renderer = Renderer()
    character = RenderCharacter(
        player_id="player1",
        width=50,
        height=50,
        vx=10,
        on_ground=True,
    )

    monkeypatch.setattr(pygame.time, "get_ticks", lambda: 0)
    frame_a = pygame.image.tobytes(renderer._get_player_sprite(character), "RGBA")

    monkeypatch.setattr(pygame.time, "get_ticks", lambda: 160)
    frame_b = pygame.image.tobytes(renderer._get_player_sprite(character), "RGBA")

    assert frame_a != frame_b


def test_powerup_ids_use_distinct_item_sprites():
    renderer = Renderer()

    coin = pygame.image.tobytes(renderer._get_environment_sprite("powerup", "coin", 32, 32), "RGBA")
    flower = pygame.image.tobytes(
        renderer._get_environment_sprite("powerup", "flower", 32, 32), "RGBA"
    )
    mushroom = pygame.image.tobytes(
        renderer._get_environment_sprite("powerup", "mushroom", 32, 32), "RGBA"
    )
    star = pygame.image.tobytes(renderer._get_environment_sprite("powerup", "star", 32, 32), "RGBA")

    assert len({coin, flower, mushroom, star}) == 4


def test_renderer_hides_destroyed_blocks_and_collected_powerups(monkeypatch):
    monkeypatch.setattr(pygame.time, "get_ticks", lambda: 0)
    screen = pygame.display.set_mode((400, 200))
    renderer = Renderer(width=400, height=200)
    frame = RenderFrame(
        blocks=[
            RenderBlock(x=20, y=20, width=32, height=32),
            RenderBlock(x=60, y=20, width=32, height=32, destroyed=True),
        ],
        power_ups={
            "visible": RenderPowerUp(x=20, y=70, width=32, height=32, powerup_id="visible"),
            "collected": RenderPowerUp(
                x=60, y=70, width=32, height=32, powerup_id="collected", collected=True
            ),
        },
    )

    renderer.render(screen=screen, frame=frame)

    assert screen.get_at((36, 36))[:3] != renderer.background_color
    assert screen.get_at((76, 36))[:3] == renderer.background_color
    assert screen.get_at((36, 86))[:3] != renderer.background_color
    assert screen.get_at((76, 86))[:3] == renderer.background_color


def test_renderer_animates_powerup_collection_transition(monkeypatch):
    tick = 0
    monkeypatch.setattr(pygame.time, "get_ticks", lambda: tick)
    screen = pygame.display.set_mode((200, 200))
    renderer = Renderer(width=200, height=200)
    power_up = RenderPowerUp(x=50, y=70, width=32, height=32, powerup_id="coin-1")
    frame = RenderFrame(power_ups={power_up.powerup_id: power_up})

    renderer.render(screen=screen, frame=frame)
    power_up.collected = True
    renderer.render(screen=screen, frame=frame)

    assert screen.get_at((66, 86))[:3] != renderer.background_color

    tick = 500
    renderer.render(screen=screen, frame=frame)

    assert screen.get_at((66, 86))[:3] == renderer.background_color


def test_gate_sprite_changes_between_closed_and_open(monkeypatch):
    monkeypatch.setattr(pygame.time, "get_ticks", lambda: 0)
    screen = pygame.display.set_mode((200, 200))
    renderer = Renderer(width=200, height=200)
    gate = RenderGate(x=40, y=40, width=40, height=48, gate_id="gate-a", state="closed")
    frame = RenderFrame(gates={"gate-a": gate})

    renderer.render(screen=screen, frame=frame)
    closed_pixels = pygame.image.tobytes(screen.subsurface(pygame.Rect(40, 40, 40, 48)), "RGBA")

    gate.state = "open"
    renderer.render(screen=screen, frame=frame)
    open_pixels = pygame.image.tobytes(screen.subsurface(pygame.Rect(40, 40, 40, 48)), "RGBA")

    assert closed_pixels != open_pixels


def test_renderer_camera_centers_on_focus_player():
    renderer = Renderer(width=200, height=150)
    frame = RenderFrame(
        characters={
            "player1": RenderCharacter(
                player_id="player1",
                x=300,
                y=120,
                width=50,
                height=50,
                on_ground=True,
            )
        },
        focus_player_id="player1",
        world_width=600,
        world_height=400,
    )

    camera_x, camera_y = renderer._camera_offset(frame, platforms=[])

    assert (camera_x, camera_y) == (225, 70)


def test_renderer_camera_clamps_at_world_edge():
    renderer = Renderer(width=200, height=150)
    frame = RenderFrame(
        characters={
            "player1": RenderCharacter(
                player_id="player1",
                x=10,
                y=20,
                width=50,
                height=50,
                on_ground=True,
            )
        },
        focus_player_id="player1",
        world_width=600,
        world_height=400,
    )

    camera_x, camera_y = renderer._camera_offset(frame, platforms=[])

    assert (camera_x, camera_y) == (0, 0)


def test_renderer_starts_death_effect_when_player_enters_respawn(monkeypatch):
    tick = 0
    monkeypatch.setattr(pygame.time, "get_ticks", lambda: tick)
    screen = pygame.display.set_mode((200, 200))
    renderer = Renderer(width=200, height=200)
    alive_frame = RenderFrame(
        characters={
            "player1": RenderCharacter(
                player_id="player1",
                x=40,
                y=30,
                width=50,
                height=50,
                on_ground=True,
            )
        },
        focus_player_id="player1",
    )

    renderer.render(screen=screen, frame=alive_frame)
    assert renderer._death_effects == {}

    tick = 50
    dead_frame = RenderFrame(
        respawning_player_ids=frozenset({"player1"}), focus_player_id="player1"
    )
    renderer.render(screen=screen, frame=dead_frame)

    assert "player1" in renderer._death_effects


def test_renderer_keeps_last_camera_offset_during_death_effect(monkeypatch):
    tick = 0
    monkeypatch.setattr(pygame.time, "get_ticks", lambda: tick)
    screen = pygame.display.set_mode((200, 150))
    renderer = Renderer(width=200, height=150)
    alive_frame = RenderFrame(
        characters={
            "player1": RenderCharacter(
                player_id="player1",
                x=300,
                y=120,
                width=50,
                height=50,
                on_ground=True,
            )
        },
        focus_player_id="player1",
        world_width=600,
        world_height=400,
    )

    renderer.render(screen=screen, frame=alive_frame)
    assert renderer._last_camera_offset == (225, 70)

    tick = 50
    dead_frame = RenderFrame(
        respawning_player_ids=frozenset({"player1"}),
        focus_player_id="player1",
        world_width=600,
        world_height=400,
    )
    renderer.render(screen=screen, frame=dead_frame)

    assert renderer._last_camera_offset == (225, 70)


def test_character_touches_gate_requires_aabb_overlap():
    gate = RenderGate(x=100, y=100, width=40, height=60, gate_id="checkpoint-1", is_final=False)
    far_character = RenderCharacter(player_id="player1", x=0, y=0, width=50, height=50)
    overlapping_character = RenderCharacter(player_id="player1", x=110, y=110, width=50, height=50)

    assert UiRenderer._character_touches_gate(far_character, gate) is False
    assert UiRenderer._character_touches_gate(overlapping_character, gate) is True


def test_checkpoint_toast_triggers_only_on_physical_touch():
    renderer = Renderer(width=200, height=150)
    gate = RenderGate(
        x=500, y=500, width=40, height=60, gate_id="checkpoint-1", state="open", is_final=False
    )

    far_frame = RenderFrame(
        characters={"player1": RenderCharacter(player_id="player1", x=0, y=0)},
        gates={"checkpoint-1": gate},
    )
    renderer._ui_renderer.sync_checkpoint_toasts(far_frame, now_ms=0)
    assert "checkpoint-1" not in renderer._checkpoint_toasts

    touching_frame = RenderFrame(
        characters={"player1": RenderCharacter(player_id="player1", x=505, y=505)},
        gates={"checkpoint-1": gate},
    )
    renderer._ui_renderer.sync_checkpoint_toasts(touching_frame, now_ms=100)
    assert "checkpoint-1" in renderer._checkpoint_toasts
