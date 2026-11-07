"""Exercise the BDI decision through the shared queues and game adapter."""

from distributed_smb.agents.agent_type import AgentType
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
    message = runtime.get_perception(AgentType.GOOMBA)
    agent.goomba_id = message.perception.goomba_id
    agent._update_beliefs(message.perception)
    run_bdi_steps(agent, count=4)

    result = runtime.action_queue[AgentType.GOOMBA].get_nowait()
    integration.bridge.submit_action(AgentType.GOOMBA, result)
    enemy = engine.world_state.environment.enemies["enemy-1"]
    initial_x = enemy.x
    integration.tick(0.1, {})

    assert enemy.x > initial_x
