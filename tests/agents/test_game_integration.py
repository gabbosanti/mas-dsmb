from dataclasses import FrozenInstanceError

import pytest

from distributed_smb.agents.models import (
    ActionRequest,
    ActionStatus,
    EnemyAction,
    EnemyObservation,
    MovementCommand,
    ObstacleKind,
)
from distributed_smb.agents.controller import PursuitController
from distributed_smb.application.agent_integration import (
    AgentGameIntegration,
    EnemyNotFoundError,
)
from distributed_smb.domain.entity import DestructibleBlock, Platform
from distributed_smb.domain.game_engine import GameEngine


class FixedActionController:
    def __init__(self, action: EnemyAction) -> None:
        self.action = action

    def decide(self, observation: EnemyObservation) -> ActionRequest:
        return ActionRequest(observation.enemy_id, self.action)


def test_observation_is_immutable_and_contains_only_nearby_world_facts():
    engine = GameEngine()
    engine.platforms = [
        Platform(x=150, y=100, width=50, height=10),
        Platform(x=1000, y=100, width=50, height=10),
    ]
    engine.world_state.environment.destructible_blocks = [
        DestructibleBlock(x=100, y=130),
        DestructibleBlock(x=110, y=130, destroyed=True),
    ]
    enemy = engine.world_state.environment.enemies["enemy-1"]
    enemy.x, enemy.y = 100, 100
    engine.spawn_player("far-player", x=300, y=100, join_index=1)
    engine.spawn_player("near-player", x=140, y=100, join_index=0)

    observation = AgentGameIntegration(engine).observe("enemy-1")

    assert observation.enemy_id == "enemy-1"
    assert (observation.enemy_position.x, observation.enemy_position.y) == (100, 100)
    assert observation.target_player_id == "near-player"
    assert observation.mario_position.x == 140
    assert [obstacle.kind for obstacle in observation.nearby_obstacles] == [
        ObstacleKind.PLATFORM,
        ObstacleKind.BLOCK,
    ]
    with pytest.raises(FrozenInstanceError):
        observation.enemy_position.x = 0


def test_observation_rejects_an_unknown_enemy():
    integration = AgentGameIntegration(GameEngine())

    with pytest.raises(EnemyNotFoundError):
        integration.observe("missing-enemy")


def test_agent_control_is_selective_and_other_enemies_keep_patrolling():
    engine = GameEngine()
    controlled = engine.world_state.environment.enemies["enemy-1"]
    uncontrolled = engine.world_state.environment.enemies["enemy-2"]
    controlled.x = 520
    uncontrolled.x = 1120
    initial_uncontrolled_x = uncontrolled.x
    initial_uncontrolled_vx = uncontrolled.vx

    integration = AgentGameIntegration(engine, enabled=True)
    integration.control_enemy("enemy-1", FixedActionController(EnemyAction.MOVE_LEFT))
    results = integration.tick(0.1, {})

    assert controlled.x == pytest.approx(515)
    assert uncontrolled.x == pytest.approx(
        initial_uncontrolled_x + initial_uncontrolled_vx * 0.1
    )
    assert results["enemy-1"].status is ActionStatus.APPLIED
    assert results["enemy-1"].engine_command is MovementCommand.MOVE_LEFT
    assert results["enemy-1"].position_after.x == pytest.approx(controlled.x)


def test_fixed_action_controls_selected_enemy_without_a_controller():
    engine = GameEngine()
    enemy = engine.world_state.environment.enemies["enemy-1"]
    enemy.x = 520
    integration = AgentGameIntegration(engine, enabled=True)
    integration.control_enemy("enemy-1", EnemyAction.MOVE_LEFT)

    results = integration.tick(0.1, {})

    assert enemy.x == pytest.approx(515)
    assert results["enemy-1"].status is ActionStatus.APPLIED
    assert results["enemy-1"].requested_action is EnemyAction.MOVE_LEFT


def test_unsupported_jump_stops_only_the_selected_enemy_for_one_tick():
    engine = GameEngine()
    controlled = engine.world_state.environment.enemies["enemy-1"]
    uncontrolled = engine.world_state.environment.enemies["enemy-2"]
    controlled.x = 520
    uncontrolled.x = 1120
    initial_uncontrolled_x = uncontrolled.x
    initial_uncontrolled_vx = uncontrolled.vx

    integration = AgentGameIntegration(engine, enabled=True)
    integration.control_enemy("enemy-1", FixedActionController(EnemyAction.JUMP))
    results = integration.tick(0.1, {})

    assert controlled.x == 520
    assert controlled.vx != 0
    assert uncontrolled.x == pytest.approx(
        initial_uncontrolled_x + initial_uncontrolled_vx * 0.1
    )
    assert results["enemy-1"].status is ActionStatus.UNSUPPORTED
    assert results["enemy-1"].engine_command is MovementCommand.STOP

    integration.control_enemy("enemy-1", FixedActionController(EnemyAction.MOVE_RIGHT))
    integration.tick(0.1, {})
    assert controlled.x == pytest.approx(525)
    integration.release_enemy("enemy-1")
    integration.enabled = False
    integration.tick(0.1, {})
    assert controlled.x == pytest.approx(530)


def test_pursuit_controller_uses_observation_and_produces_stop_without_a_target():
    engine = GameEngine()
    enemy = engine.world_state.environment.enemies["enemy-1"]
    enemy.x, enemy.y = 520, 800
    engine.spawn_player("player1", x=600, y=800)
    integration = AgentGameIntegration(engine)
    observation = integration.observe("enemy-1")

    request = PursuitController().decide(observation)
    assert request == ActionRequest("enemy-1", EnemyAction.MOVE_RIGHT)

    engine.world_state.characters.clear()
    no_target = PursuitController().decide(integration.observe("enemy-1"))
    assert no_target == ActionRequest("enemy-1", EnemyAction.STOP)


def test_action_requests_cannot_take_over_an_unselected_enemy():
    engine = GameEngine()
    enemy = engine.world_state.environment.enemies["enemy-2"]
    initial_x = enemy.x
    initial_vx = enemy.vx
    integration = AgentGameIntegration(engine, enabled=True)

    results = integration.tick(
        0.1,
        {},
        {"enemy-2": ActionRequest("enemy-2", EnemyAction.STOP)},
    )

    assert results["enemy-2"].status is ActionStatus.INVALID_TARGET
    assert enemy.x == pytest.approx(initial_x + initial_vx * 0.1)
