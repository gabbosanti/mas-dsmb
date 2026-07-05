from distributed_smb.domain.entity import ExclusivePowerUp
from distributed_smb.domain.game_engine import GameEngine
from distributed_smb.shared.config import WINDOW_HEIGHT, WINDOW_WIDTH
from distributed_smb.shared.input import InputState


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

    floor_y = engine.platforms[0].y

    assert player.y + player.height == floor_y, "Collision with floor is incorrect"


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
    objects = [*env.destructible_blocks, *env.power_ups.values(), *env.cooperative_gates.values()]

    assert len(env.destructible_blocks) >= 5
    assert len(engine.platforms) >= 8
    assert len(env.power_ups) >= 10
    assert "gate-test" in env.cooperative_gates
    assert any(powerup_id.startswith("coin-") for powerup_id in env.power_ups)
    assert any(powerup_id.startswith("flower-") for powerup_id in env.power_ups)
    assert any(powerup_id.startswith("mushroom-") for powerup_id in env.power_ups)
    assert any(powerup_id.startswith("star-") for powerup_id in env.power_ups)

    for obj in objects:
        assert 0 <= obj.x < WINDOW_WIDTH
        assert 0 <= obj.y < WINDOW_HEIGHT
        assert obj.x + obj.width <= WINDOW_WIDTH
        assert obj.y + obj.height <= WINDOW_HEIGHT

    for block in env.destructible_blocks:
        assert any(
            20 <= platform.y - (block.y + block.height) <= 150 for platform in engine.platforms
        )

    for obj in [*env.power_ups.values(), *env.cooperative_gates.values()]:
        assert any(abs((obj.y + obj.height) - platform.y) <= 10 for platform in engine.platforms)


def test_closed_gate_blocks_until_all_players_contribute():
    engine = GameEngine()
    engine.spawn_player("player1")
    engine.spawn_player("player2")
    gate = engine.world_state.get_gate("gate-test")
    player1 = engine.world_state.get_player("player1")
    player2 = engine.world_state.get_player("player2")

    player1.x = gate.x - player1.width + 5
    player1.y = gate.y + gate.height - player1.height
    player1.prev_x = player1.x
    player1.prev_y = player1.y
    player1.vx = 100
    player2.x = 100
    player2.y = player1.y

    engine.tick(1 / 60, {"player1": InputState(right=True), "player2": InputState()})

    assert gate.state == "closed"
    assert player1.x + player1.width <= gate.x


def test_gate_opens_after_all_players_touch_it():
    engine = GameEngine()
    engine.spawn_player("player1")
    engine.spawn_player("player2")
    gate = engine.world_state.get_gate("gate-test")

    for player in engine.world_state.characters.values():
        player.x = gate.x + 4
        player.y = gate.y + gate.height - player.height
        player.prev_x = player.x
        player.prev_y = player.y

    engine.tick(1 / 60, {"player1": InputState(), "player2": InputState()})

    assert gate.state == "open"
    assert any(
        getattr(event, "gate_id", None) == "gate-test"
        and getattr(event, "new_state", None) == "open"
        for event in engine.events
    )


def test_reaching_open_gate_triggers_victory():
    engine = GameEngine()
    engine.spawn_player("player1")
    gate = engine.world_state.get_gate("gate-test")
    player = engine.world_state.get_player("player1")

    gate.state = "open"
    engine.world_state.coins_collected = 5
    player.x = gate.x + 4
    player.y = gate.y + gate.height - player.height
    player.prev_x = player.x
    player.prev_y = player.y

    engine.tick(1 / 60, {"player1": InputState()})

    assert engine.world_state.victory is True
    assert engine.world_state.victory_player_id == "player1"


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


def test_gate_requires_coin_threshold_for_victory():
    engine = GameEngine()
    engine.spawn_player("player1")
    gate = engine.world_state.get_gate("gate-test")
    player = engine.world_state.get_player("player1")

    gate.state = "open"
    engine.world_state.environment.power_ups = {}
    engine.world_state.coins_collected = 4
    player.x = gate.x + 4
    player.y = gate.y + gate.height - player.height
    player.prev_x = player.x
    player.prev_y = player.y

    engine.tick(1 / 60, {"player1": InputState()})

    assert engine.world_state.victory is False


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


def test_jump_from_platform_destroys_reachable_block():
    engine = GameEngine()
    engine.spawn_player("player1")
    player = engine.world_state.get_player("player1")
    block = engine.world_state.environment.destructible_blocks[0]
    platform = engine.platforms[1]

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
