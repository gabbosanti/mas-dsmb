import time

from distributed_smb.domain.entity import Enemy, ExclusivePowerUp
from distributed_smb.domain.game_engine import VOID_DEATH_CAUSE, GameEngine
from distributed_smb.domain.physics import JUMP_FORCE
from distributed_smb.shared.input import InputState


def test_a_jump_rises_then_falls_and_lands_exactly_on_the_platform():
    """Euler integration with a fixed step: the whole prediction/replay scheme
    assumes this arc is reproducible tick by tick."""
    engine = GameEngine()
    engine.spawn_player("player1")
    player = engine.world_state.get_player("player1")
    player.y = 0
    player.vy = 0
    player.on_ground = True

    engine.tick(1 / 60, {"player1": InputState(jump=True)})
    assert player.vy < 0, "Jump does not set upward velocity"

    engine.tick(1 / 60, {"player1": InputState()})
    assert player.vy > -abs(JUMP_FORCE), "Gravity does not pull the jump back down"

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

    assert player.on_ground is True, "Player did not land"
    assert player.vy == 0, "Vertical velocity did not reset on landing"
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


def test_falling_into_the_void_kills_only_on_the_authoritative_engine():
    """A client predicting a death would remove a player the host still has."""
    host = GameEngine()
    host.spawn_player("player1")
    host.world_state.get_player("player1").y = host.world_height + 1
    host.world_state.get_player("player1").prev_y = host.world_height + 1

    host.tick(0.016, {"player1": InputState()})

    assert host.world_state.get_player("player1") is None
    assert "player1" in host.world_state.respawn_timers
    assert any(
        event.player_id == "player1" and event.enemy_id == VOID_DEATH_CAUSE
        for event in host.events
    )

    client = GameEngine(is_authoritative=False)
    client.spawn_player("player1")
    client.world_state.get_player("player1").y = client.world_height + 1
    client.world_state.get_player("player1").prev_y = client.world_height + 1

    client.tick(0.016, {"player1": InputState()})

    assert client.world_state.get_player("player1") is not None
    assert "player1" not in client.world_state.respawn_timers


def test_landing_on_an_enemy_kills_it_and_touching_it_sideways_kills_the_player():
    stomping = GameEngine()
    stomping.spawn_player("player1")
    enemy = next(iter(stomping.world_state.environment.enemies.values()))
    player = stomping.world_state.get_player("player1")
    player.x = enemy.x
    player.width, player.height = enemy.width, enemy.height
    player.y = enemy.y - player.height + 2
    player.prev_y = enemy.y - player.height
    player.vy = 80

    stomping._handle_enemy_collisions()

    assert enemy.enemy_id not in stomping.world_state.environment.enemies
    assert "player1" in stomping.world_state.characters
    assert player.vy < 0

    walking_into = GameEngine()
    walking_into.spawn_player("player1", join_index=2)
    enemy = next(iter(walking_into.world_state.environment.enemies.values()))
    player = walking_into.world_state.get_player("player1")
    player.x = enemy.x
    player.y = enemy.y
    player.prev_y = enemy.y
    player.vy = 0

    walking_into._handle_enemy_collisions()

    assert enemy.enemy_id in walking_into.world_state.environment.enemies
    assert "player1" not in walking_into.world_state.characters
    assert "player1" in walking_into.world_state.respawn_timers


def test_a_dead_player_respawns_at_the_spawn_point_of_its_join_index():
    engine = GameEngine()
    engine.spawn_player("player1", join_index=1)
    enemy = next(iter(engine.world_state.environment.enemies.values()))
    player = engine.world_state.get_player("player1")
    player.x = enemy.x
    player.y = enemy.y
    player.prev_y = enemy.y
    player.vy = 0
    engine._handle_enemy_collisions()
    engine.world_state.respawn_timers["player1"] = 0.0

    engine._process_respawns()

    respawned = engine.world_state.get_player("player1")
    assert (respawned.x, respawned.y) == engine.spawn_position_for(1)
    assert respawned.join_index == 1
    assert "player1" not in engine.world_state.respawn_timers


def test_a_star_lets_the_player_kill_enemies_by_touch_for_a_while():
    engine = GameEngine()
    engine.spawn_player("player1")
    player = engine.world_state.get_player("player1")
    star = ExclusivePowerUp(powerup_id="star-custom", x=player.x + 10, y=player.y - 10)
    engine.world_state.add_power_up(star)
    player.x, player.y = star.x, star.y
    player.prev_x, player.prev_y = player.x, player.y

    engine.handle_powerup_collisions()

    assert player.powerup_effect_expires_at is not None
    assert player.powerup_effect_expires_at >= time.time() + 9.5

    enemy = Enemy(enemy_id="enemy-1", x=player.x, y=player.y, width=10, height=10)
    engine.world_state.environment.enemies[enemy.enemy_id] = enemy

    engine._handle_enemy_collisions()

    assert enemy.enemy_id not in engine.world_state.environment.enemies
    assert engine.world_state.get_player("player1") is player
