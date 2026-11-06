from dataclasses import FrozenInstanceError

import pytest

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.models import (
    ActionResult,
    EnemyAction,
    GoombaPerception,
    GoombaPerceptionMessage,
    ObstacleKind,
    SightDirection,
)
from distributed_smb.application.agent_integration import (
    AgentGameIntegration,
    EnemyNotFoundError,
    SpadeAgentBridge,
)
from distributed_smb.domain.entity import DestructibleBlock, Platform
from distributed_smb.domain.game_engine import GameEngine


def spade_integration(engine: GameEngine, *enemy_ids: str) -> AgentGameIntegration:
    integration = AgentGameIntegration(engine, enabled=True)
    for enemy_id in enemy_ids:
        integration.control_enemy_with_spade(enemy_id)
    return integration


def send(integration: AgentGameIntegration, enemy_id: str, action: object) -> None:
    """Act as the SPADE thread: put an agent decision on the GOOMBA action queue."""
    integration.bridge.submit_action(AgentType.GOOMBA, ActionResult(enemy_id, action))


def test_observation_is_an_immutable_raw_snapshot_without_sight_filtering():
    engine = GameEngine()
    engine.platforms = [
        Platform(x=150, y=100, width=50, height=10),
        Platform(x=5000, y=100, width=50, height=10),  # far away: still reported
    ]
    engine.world_state.environment.destructible_blocks = [
        DestructibleBlock(x=180, y=145),
        DestructibleBlock(x=110, y=130, destroyed=True),
    ]
    enemy = engine.world_state.environment.enemies["enemy-1"]
    enemy.x, enemy.y, enemy.vx = 100, 100, -50
    engine.spawn_player("player-b", x=3000, y=100, join_index=1)
    engine.spawn_player("player-a", x=20, y=100, join_index=0)  # behind the Goomba

    observation = AgentGameIntegration(engine).observe("enemy-1")

    assert isinstance(observation, GoombaPerception)
    assert observation.goomba_id == "enemy-1"
    assert (observation.position.x, observation.position.y) == (100, 100)
    assert (observation.width, observation.height) == (enemy.width, enemy.height)
    assert observation.direction is SightDirection.LEFT
    assert [player.player_id for player in observation.players] == [
        "player-a",
        "player-b",
    ]
    assert [(obstacle.kind, obstacle.x) for obstacle in observation.obstacles] == [
        (ObstacleKind.PLATFORM, 150),
        (ObstacleKind.PLATFORM, 5000),
        (ObstacleKind.BLOCK, 180),
    ]
    with pytest.raises(FrozenInstanceError):
        observation.position.x = 0


def test_observation_rejects_an_unknown_enemy():
    integration = AgentGameIntegration(GameEngine())

    with pytest.raises(EnemyNotFoundError):
        integration.observe("missing-enemy")


def test_spade_action_moves_only_the_selected_enemy():
    engine = GameEngine()
    controlled = engine.world_state.environment.enemies["enemy-1"]
    uncontrolled = engine.world_state.environment.enemies["enemy-2"]
    controlled.x = 520
    uncontrolled.x = 1120
    initial_uncontrolled_x = uncontrolled.x
    initial_uncontrolled_vx = uncontrolled.vx
    integration = spade_integration(engine, "enemy-1")

    send(integration, "enemy-1", EnemyAction.MOVE_LEFT)
    integration.tick(0.1, {})

    assert controlled.x == pytest.approx(515)
    assert uncontrolled.x == pytest.approx(
        initial_uncontrolled_x + initial_uncontrolled_vx * 0.1
    )


def test_a_move_persists_until_the_agent_decides_otherwise():
    engine = GameEngine()
    enemy = engine.world_state.environment.enemies["enemy-1"]
    enemy.x = 520
    integration = spade_integration(engine, "enemy-1")

    send(integration, "enemy-1", EnemyAction.MOVE_LEFT)
    integration.tick(0.1, {})
    integration.tick(0.1, {})  # no new action: the engine keeps the velocity

    assert enemy.x == pytest.approx(510)


def test_turn_around_reverses_the_current_heading():
    engine = GameEngine()
    enemy = engine.world_state.environment.enemies["enemy-1"]
    enemy.x, enemy.vx = 520, 50
    integration = spade_integration(engine, "enemy-1")

    send(integration, "enemy-1", EnemyAction.TURN_AROUND)
    integration.tick(0.1, {})
    assert enemy.x == pytest.approx(515)

    send(integration, "enemy-1", EnemyAction.TURN_AROUND)
    integration.tick(0.1, {})
    assert enemy.x == pytest.approx(520)


def test_stop_holds_the_enemy_until_a_new_action_arrives():
    engine = GameEngine()
    enemy = engine.world_state.environment.enemies["enemy-1"]
    enemy.x = 520
    integration = spade_integration(engine, "enemy-1")

    send(integration, "enemy-1", EnemyAction.STOP)
    integration.tick(0.1, {})
    integration.tick(0.1, {})
    assert enemy.x == 520

    send(integration, "enemy-1", EnemyAction.MOVE_RIGHT)
    integration.tick(0.1, {})
    assert enemy.x == pytest.approx(525)


def test_invalid_agent_messages_never_reach_the_engine():
    engine = GameEngine()
    controlled = engine.world_state.environment.enemies["enemy-1"]
    uncontrolled = engine.world_state.environment.enemies["enemy-2"]
    controlled.x, controlled.vx = 520, 50
    initial_uncontrolled_x = uncontrolled.x
    initial_uncontrolled_vx = uncontrolled.vx
    integration = spade_integration(engine, "enemy-1")

    integration.bridge.submit_action(AgentType.GOOMBA, "not an ActionResult")
    send(integration, "enemy-1", EnemyAction.JUMP)  # unsupported by the engine
    send(integration, "enemy-2", EnemyAction.STOP)  # not handed to SPADE
    send(integration, "missing-enemy", EnemyAction.STOP)
    integration.tick(0.1, {})

    assert controlled.x == pytest.approx(525)
    assert uncontrolled.x == pytest.approx(
        initial_uncontrolled_x + initial_uncontrolled_vx * 0.1
    )
    assert integration.bridge.poll_action(AgentType.GOOMBA) is None


def test_disabled_integration_discards_agent_traffic_and_patrols():
    engine = GameEngine()
    enemy = engine.world_state.environment.enemies["enemy-1"]
    enemy.x, enemy.vx = 520, 50
    integration = spade_integration(engine, "enemy-1")
    integration.enabled = False

    send(integration, "enemy-1", EnemyAction.STOP)
    integration.bridge.publish(AgentType.GOOMBA, "stale perception")
    integration.tick(0.1, {})

    assert enemy.x == pytest.approx(525)
    assert integration.bridge.poll_action(AgentType.GOOMBA) is None
    assert integration.bridge.poll_perception(AgentType.GOOMBA) is None


def test_released_enemy_ignores_its_agent_and_resumes_patrolling():
    engine = GameEngine()
    enemy = engine.world_state.environment.enemies["enemy-1"]
    enemy.x, enemy.vx = 520, 50
    integration = spade_integration(engine, "enemy-1")

    send(integration, "enemy-1", EnemyAction.STOP)
    integration.tick(0.1, {})
    integration.release_enemy("enemy-1")
    send(integration, "enemy-1", EnemyAction.STOP)
    integration.tick(0.1, {})

    assert enemy.x == pytest.approx(525)


def test_spade_bridge_publishes_only_at_configured_tick_cadence():
    engine = GameEngine()
    integration = AgentGameIntegration(engine, enabled=True)
    integration.control_enemy_with_spade("enemy-1")

    for _ in range(4):
        integration.tick(1 / 60, {})

    assert integration.bridge.poll_perception(AgentType.GOOMBA) is None

    integration.tick(1 / 60, {})
    message = integration.bridge.poll_perception(AgentType.GOOMBA)

    assert isinstance(message, GoombaPerceptionMessage)
    assert message.simulation_sequence == 5
    assert message.perception.goomba_id == "enemy-1"


def test_spade_bridge_drops_oldest_messages_when_a_queue_is_full():
    from distributed_smb.application.spade_agent_bridge import make_agent_queues

    engine = GameEngine()
    perception = AgentGameIntegration(engine).observe("enemy-1")
    bridge = SpadeAgentBridge(perceptions=make_agent_queues(capacity=1))
    bridge.publish(AgentType.GOOMBA, GoombaPerceptionMessage(1, perception))
    bridge.publish(AgentType.GOOMBA, GoombaPerceptionMessage(2, perception))

    assert bridge.dropped_perceptions == 1
    assert bridge.poll_perception(AgentType.GOOMBA).simulation_sequence == 2


def test_spade_bridge_rejects_queue_maps_missing_an_agent_type():
    from queue import Queue

    with pytest.raises(ValueError):
        SpadeAgentBridge(perceptions={AgentType.GOOMBA: Queue()})


def test_game_and_spade_runtime_share_the_same_queues():
    from distributed_smb.agents.spade_runtime import SpadeRuntime
    from distributed_smb.application.spade_agent_bridge import make_agent_queues

    perceptions, actions = make_agent_queues(), make_agent_queues()
    runtime = SpadeRuntime(perceptions, actions)
    bridge = SpadeAgentBridge(perceptions=perceptions, actions=actions)

    bridge.publish(AgentType.GOOMBA, "perception")
    assert runtime.get_perception(AgentType.GOOMBA) == "perception"

    runtime.put_action(AgentType.GOOMBA, "action")
    assert bridge.poll_action(AgentType.GOOMBA) == "action"
