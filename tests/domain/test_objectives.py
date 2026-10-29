"""Cooperative objectives: coins, destructible blocks, gates and victory."""

from distributed_smb.domain.entity import CooperativeGate, ExclusivePowerUp
from distributed_smb.domain.game_engine import GameEngine


def test_only_the_authoritative_engine_moves_the_shared_coin_counter():
    """The counter is shared progress: a client counting it locally too would
    double it once the host's snapshot arrives."""

    def collect_a_coin(engine: GameEngine) -> ExclusivePowerUp:
        engine.spawn_player("player1")
        player = engine.world_state.get_player("player1")
        coin = ExclusivePowerUp(
            powerup_id="coin-custom", x=player.x + 10, y=player.y - 10
        )
        engine.world_state.add_power_up(coin)
        player.x, player.y = coin.x, coin.y
        player.prev_x, player.prev_y = player.x, player.y

        engine.handle_powerup_collisions()
        engine._sync_objective_progress_from_environment()
        return coin

    host = GameEngine()
    coin = collect_a_coin(host)
    assert coin.collected is True
    assert host.world_state.coins_collected == 1

    client = GameEngine(is_authoritative=False)
    coin = collect_a_coin(client)
    assert coin.collected is False
    assert client.world_state.coins_collected == 0


def test_gate_opens_only_once_every_level_requirement_is_met():
    engine = GameEngine()
    gate = engine.world_state.get_gate("gate-1")

    engine.handle_gate_collisions()
    assert gate.state == "closed"

    for block in engine.world_state.environment.destructible_blocks[
        : engine.world_state.blocks_to_win
    ]:
        block.destroyed = True
    coin_targets = [
        power_up
        for power_up in engine.world_state.environment.power_ups.values()
        if power_up.powerup_id.startswith("coin-")
    ][: engine.world_state.coins_to_win]
    for power_up in coin_targets:
        power_up.collected = True
    enemies = list(engine.world_state.environment.enemies)
    for enemy_id in enemies[: engine.world_state.enemies_to_win]:
        del engine.world_state.environment.enemies[enemy_id]

    engine._sync_objective_progress_from_environment()
    engine.handle_gate_collisions()

    assert gate.state == "open"


def test_a_block_breaks_from_below_and_not_from_the_side():
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

    player.x = block.x + 4
    player.y = block.y + block.height - 2
    player.prev_x = player.x
    player.prev_y = block.y + block.height + 8
    player.vy = -120

    engine.handle_block_collisions()

    assert block.destroyed is True
    assert any(event.position == (block.x, block.y) for event in engine.events)


def test_only_the_final_gate_triggers_victory_and_then_the_world_stops():
    """The checkpoint castle opens the same way but must not end the run, and
    once the run is over the engine just holds state."""

    def touch(gate_id: str, is_final: bool) -> GameEngine:
        engine = GameEngine()
        gate = CooperativeGate(
            x=100, y=100, gate_id=gate_id, state="open", is_final=is_final
        )
        engine.world_state.environment.cooperative_gates = {gate_id: gate}
        engine.spawn_player("player1", x=100, y=100)
        engine.handle_victory_condition()
        return engine

    assert touch("checkpoint", is_final=False).world_state.victory is False

    finished = touch("final", is_final=True)
    assert finished.world_state.victory is True
    assert finished.world_state.victory_player_id == "player1"

    finished.events.clear()
    for _ in range(120):
        finished.tick(1 / 60, {})

    assert finished.world_state.victory is True
    assert finished.events == []
