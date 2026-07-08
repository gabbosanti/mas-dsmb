import pygame

from distributed_smb.domain.entity import CooperativeGate, DestructibleBlock, ExclusivePowerUp
from distributed_smb.domain.world import CharacterState, WorldState
from distributed_smb.presentation.renderer import Renderer


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
    world = WorldState(
        characters={
            "player1": CharacterState(
                player_id="player1",
                x=40,
                y=30,
                width=50,
                height=50,
                on_ground=True,
            )
        }
    )

    renderer.render(screen=screen, world_state=world, platforms=[])

    sprite_pixels = [screen.get_at((x, y))[:3] for x in range(40, 90) for y in range(30, 80)]
    assert any(pixel != renderer.background_color for pixel in sprite_pixels)


def test_renderer_preserves_last_facing_direction_when_player_stops(monkeypatch):
    monkeypatch.setattr(pygame.time, "get_ticks", lambda: 0)
    screen = pygame.display.set_mode((200, 200))
    renderer = Renderer(width=200, height=200)
    character = CharacterState(
        player_id="player1",
        x=40,
        y=30,
        width=50,
        height=50,
        vx=-10,
        on_ground=True,
    )
    world = WorldState(characters={"player1": character})

    renderer.render(screen=screen, world_state=world, platforms=[])
    assert renderer._facing_by_player["player1"] == -1

    character.vx = 0
    renderer.render(screen=screen, world_state=world, platforms=[])
    assert renderer._facing_by_player["player1"] == -1


def test_walk_animation_uses_distinct_frames(monkeypatch):
    renderer = Renderer()
    character = CharacterState(
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
    screen = pygame.display.set_mode((200, 200))
    renderer = Renderer(width=200, height=200)
    world = WorldState()
    world.environment.destructible_blocks.append(DestructibleBlock(x=20, y=20, width=32, height=32))
    world.environment.destructible_blocks.append(
        DestructibleBlock(x=60, y=20, width=32, height=32, destroyed=True)
    )
    world.environment.power_ups["visible"] = ExclusivePowerUp(
        x=20, y=70, width=32, height=32, powerup_id="visible"
    )
    world.environment.power_ups["collected"] = ExclusivePowerUp(
        x=60, y=70, width=32, height=32, powerup_id="collected", collected=True
    )

    renderer.render(screen=screen, world_state=world, platforms=[])

    assert screen.get_at((36, 36))[:3] != renderer.background_color
    assert screen.get_at((76, 36))[:3] == renderer.background_color
    assert screen.get_at((36, 86))[:3] != renderer.background_color
    assert screen.get_at((76, 86))[:3] == renderer.background_color


def test_renderer_animates_powerup_collection_transition(monkeypatch):
    tick = 0
    monkeypatch.setattr(pygame.time, "get_ticks", lambda: tick)
    screen = pygame.display.set_mode((200, 200))
    renderer = Renderer(width=200, height=200)
    world = WorldState()
    power_up = ExclusivePowerUp(x=50, y=70, width=32, height=32, powerup_id="coin-1")
    world.environment.power_ups[power_up.powerup_id] = power_up

    renderer.render(screen=screen, world_state=world, platforms=[])
    power_up.collected = True
    renderer.render(screen=screen, world_state=world, platforms=[])

    assert screen.get_at((66, 86))[:3] != renderer.background_color

    tick = 500
    renderer.render(screen=screen, world_state=world, platforms=[])

    assert screen.get_at((66, 86))[:3] == renderer.background_color


def test_gate_sprite_changes_between_closed_and_open(monkeypatch):
    monkeypatch.setattr(pygame.time, "get_ticks", lambda: 0)
    screen = pygame.display.set_mode((200, 200))
    renderer = Renderer(width=200, height=200)
    world = WorldState()
    gate = CooperativeGate(x=40, y=40, width=40, height=48, gate_id="gate-a", state="closed")
    world.environment.cooperative_gates["gate-a"] = gate

    renderer.render(screen=screen, world_state=world, platforms=[])
    closed_pixels = pygame.image.tobytes(screen.subsurface(pygame.Rect(40, 40, 40, 48)), "RGBA")

    gate.state = "open"
    renderer.render(screen=screen, world_state=world, platforms=[])
    open_pixels = pygame.image.tobytes(screen.subsurface(pygame.Rect(40, 40, 40, 48)), "RGBA")

    assert closed_pixels != open_pixels


def test_renderer_camera_centers_on_focus_player():
    renderer = Renderer(width=200, height=150)
    world = WorldState(
        characters={
            "player1": CharacterState(
                player_id="player1",
                x=300,
                y=120,
                width=50,
                height=50,
                on_ground=True,
            )
        }
    )

    camera_x, camera_y = renderer._camera_offset(
        world_state=world,
        platforms=[],
        focus_player_id="player1",
        world_size=(600, 400),
    )

    assert (camera_x, camera_y) == (225, 70)


def test_renderer_camera_clamps_at_world_edge():
    renderer = Renderer(width=200, height=150)
    world = WorldState(
        characters={
            "player1": CharacterState(
                player_id="player1",
                x=10,
                y=20,
                width=50,
                height=50,
                on_ground=True,
            )
        }
    )

    camera_x, camera_y = renderer._camera_offset(
        world_state=world,
        platforms=[],
        focus_player_id="player1",
        world_size=(600, 400),
    )

    assert (camera_x, camera_y) == (0, 0)
