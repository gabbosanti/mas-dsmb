from distributed_smb.domain.entity import ExclusivePowerUp
from distributed_smb.domain.game_engine import GameEngine
from distributed_smb.shared.input import InputState
from src.distributed_smb.domain.entity import CooperativeGate, DestructibleBlock
from src.distributed_smb.domain.world import EnvironmentalState, WorldState


def test_environmental_state_contains_entities():
    env = EnvironmentalState()
    env.destructible_blocks.append(DestructibleBlock(x=10, y=10))
    env.power_ups["p1"] = ExclusivePowerUp(x=20, y=20, powerup_id="p1")
    env.cooperative_gates["g1"] = CooperativeGate(x=30, y=30, gate_id="g1")

    assert len(env.destructible_blocks) == 1
    assert "p1" in env.power_ups
    assert "g1" in env.cooperative_gates


def test_worldstate_preserves_environmental_state():
    world = WorldState()
    block = DestructibleBlock(x=10, y=10)
    world.environment.destructible_blocks.append(block)

    assert world.environment.destructible_blocks[0] is block


def test_move_right():
    engine = GameEngine()
    engine.spawn_player("player1")
    players = engine.world_state.get_all_players()
    player = players[0]
    initial_x = player.x

    input_state = InputState(right=True)

    for _ in range(60):
        engine.tick(1 / 60, {"player1": input_state})

    assert player.x > initial_x, "Player did not move to the right"


def test_gravity():
    engine = GameEngine()
    engine.spawn_player("player1")
    players = engine.world_state.get_all_players()
    player = players[0]

    initial_y = player.y

    for _ in range(60):
        engine.tick(1 / 60, {"player1": InputState()})

    assert player.y > initial_y, "Gravity does not work"


def test_jump():
    engine = GameEngine()
    engine.spawn_player("player1")
    players = engine.world_state.get_all_players()
    player = players[0]

    player.on_ground = True

    input_state = InputState(jump=True)

    engine.tick(1 / 60, {"player1": input_state})

    assert player.vy < 0, "Jump does not set upward velocity"


def test_landing():
    engine = GameEngine()
    engine.spawn_player("player1")
    players = engine.world_state.get_all_players()
    player = players[0]

    player.y = 300
    player.vy = 100

    for _ in range(120):
        engine.tick(1 / 60, {"player1": InputState()})

    assert player.on_ground is True, "Player did not land"
    assert player.vy == 0, "Vertical velocity did not reset on landing"


def test_no_input():
    engine = GameEngine()
    engine.spawn_player("player1")
    players = engine.world_state.get_all_players()
    player = players[0]

    initial_x = player.x

    for _ in range(60):
        engine.tick(1 / 60, {"player1": InputState()})

    assert player.x == initial_x, "Player did not stay in place without input"


def test_collision_floor():
    engine = GameEngine()
    engine.spawn_player("player1")
    players = engine.world_state.get_all_players()
    player = players[0]

    player.y = 0
    player.vy = 0

    for _ in range(300):
        engine.tick(1 / 60, {"player1": InputState()})

    player_bottom = player.y + player.height
    player_center = player.x + player.width / 2

    platform = min(
        (
            p
            for p in engine.platforms
            if p.x <= player_center <= p.x + p.width and p.y >= player_bottom
        ),
        key=lambda p: p.y,
    )

    assert player.y + player.height == platform.y, "Collision with floor is incorrect"


def test_multiplayer_inputs():
    engine = GameEngine()
    engine.spawn_player("p1")
    engine.spawn_player("p2")

    inputs = {"p1": InputState(right=True), "p2": InputState(left=True)}

    engine.tick(0.016, inputs)

    p1 = engine.world_state.get_player("p1")
    p2 = engine.world_state.get_player("p2")

    assert p1.vx > 0
    assert p2.vx < 0


def test_default_level_contains_reachable_world_objects():
    engine = GameEngine()
    env = engine.world_state.environment
    assert len(env.destructible_blocks) >= 4
    assert len(engine.platforms) >= 8
    assert len(env.power_ups) >= 10
    assert "gate-1" in env.cooperative_gates
    assert any(powerup_id.startswith("coin-") for powerup_id in env.power_ups)
    assert any(powerup_id.startswith("star-") for powerup_id in env.power_ups)

    for block in env.destructible_blocks:
        assert any(
            20 <= platform.y - (block.y + block.height) <= 150 for platform in engine.platforms
        )


def test_collecting_coins_updates_shared_counter():
    engine = GameEngine()
    engine.spawn_player("player1")
    player = engine.world_state.get_player("player1")
    power_up = ExclusivePowerUp(powerup_id="coin-custom", x=player.x + 10, y=player.y - 10)
    engine.world_state.add_power_up(power_up)

    player.x = power_up.x
    player.y = power_up.y
    player.prev_x = player.x
    player.prev_y = player.y

    engine.handle_powerup_collisions()

    assert power_up.collected is True
    assert engine.world_state.coins_collected == 1


def test_non_authoritative_engine_does_not_mutate_coin_counter():
    engine = GameEngine(is_authoritative=False)
    engine.spawn_player("player1")
    player = engine.world_state.get_player("player1")
    power_up = ExclusivePowerUp(powerup_id="coin-custom", x=player.x + 10, y=player.y - 10)
    engine.world_state.add_power_up(power_up)

    player.x = power_up.x
    player.y = power_up.y
    player.prev_x = player.x
    player.prev_y = player.y

    engine.handle_powerup_collisions()

    assert power_up.collected is False
    assert engine.world_state.coins_collected == 0


def test_head_bump_destroys_destructible_block():
    engine = GameEngine()
    engine.spawn_player("player1")
    player = engine.world_state.get_player("player1")
    block = engine.world_state.environment.destructible_blocks[0]

    player.x = block.x + 4
    player.y = block.y + block.height - 2
    player.prev_x = player.x
    player.prev_y = block.y + block.height + 8
    player.vy = -120

    engine.handle_block_collisions()

    assert block.destroyed is True
    assert any(event.position == (block.x, block.y) for event in engine.events)


def test_lateral_block_collision_does_not_destroy_block():
    engine = GameEngine()
    engine.spawn_player("player1")
    player = engine.world_state.get_player("player1")
    block = engine.world_state.environment.destructible_blocks[0]

    player.x = block.x - player.width + 2
    player.y = block.y
    player.prev_x = player.x - 8
    player.prev_y = player.y
    player.vx = 120

    engine.handle_block_collisions()

    assert block.destroyed is False


"""
def test_jump_from_platform_destroys_reachable_block():
    engine = GameEngine()
    engine.spawn_player("player1")
    player = engine.world_state.get_player("player1")
    block = engine.world_state.environment.destructible_blocks[0]
    platform = engine.platforms[2]

    player.x = block.x + 4
    player.y = platform.y - player.height
    player.prev_x = player.x
    player.prev_y = player.y
    player.on_ground = True

    engine.tick(1 / 60, {"player1": InputState(jump=True)})
    for _ in range(30):
        if block.destroyed:
            break
        engine.tick(1 / 60, {"player1": InputState()})

    assert block.destroyed is True
"""


def test_level_dimensions_match_tiled_map():
    engine = GameEngine()

    assert engine.world_width == 1920
    assert engine.world_height == 960


def test_spawn_player_clamps_to_world_bounds():
    engine = GameEngine()
    engine.spawn_player("player1", x=9999, y=-50)
    player = engine.world_state.get_player("player1")

    assert player.x == engine.world_width - player.width
    assert player.y == 0
