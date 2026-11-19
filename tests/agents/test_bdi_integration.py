"""Exercise the BDI decision through the shared queues and game adapter."""

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.models import (
    PlayerAction,
    PlayerActionResult,
)
from distributed_smb.application.agent_integration import AgentGameIntegration
from distributed_smb.application.spade_agent_bridge import SpadeAgentBridge
from distributed_smb.domain.game_engine import GameEngine


def test_bdi_action_moves_the_controlled_goomba(goomba_agent, run_bdi_steps):
    agent, runtime = goomba_agent
    engine = GameEngine()
    integration = AgentGameIntegration(
        engine,
        enabled=True,
        perception_interval_ticks=1,
        bridge=SpadeAgentBridge(
            perceptions=runtime.perception_queue,
            actions=runtime.action_queue,
        ),
    )
    integration.control_enemy_with_spade("enemy-1")

    # Game thread publishes the first snapshot into the shared perception map.
    integration.tick(0, {})
    assert runtime.dispatch_perceptions() == 1
    run_bdi_steps(agent, count=4)

    result = runtime.action_queue[AgentType.GOOMBA].get_nowait()
    integration.bridge.submit_action(AgentType.GOOMBA, result)
    enemy = engine.world_state.environment.enemies["enemy-1"]
    initial_x = enemy.x
    integration.tick(0.1, {})

    assert enemy.x > initial_x


def test_player_action_queue_moves_a_spade_replaced_player():
    engine = GameEngine()
    engine.spawn_player("player2", x=100, y=100, join_index=1)
    integration = AgentGameIntegration(
        engine,
        enabled=True,
        perception_interval_ticks=1,
    )
    integration.control_player_with_spade("player2")

    integration.bridge.submit_action(
        AgentType.PLAYER,
        PlayerActionResult(
            player_id="player2",
            action=PlayerAction.MOVE_RIGHT,
        ),
    )
    initial_x = engine.world_state.characters["player2"].x
    integration.tick(0.1, {})

    assert engine.world_state.characters["player2"].x > initial_x
