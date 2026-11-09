"""Behavioral tests for the Goomba's real SPADE-BDI plans and actions."""

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.models import (
    EnemyAction,
    GoombaPerception,
    ObstacleObservation,
    Position,
    SightDirection,
)


def perception(*, direction=SightDirection.RIGHT, obstacles=()):
    return GoombaPerception(
        goomba_id="enemy-1",
        position=Position(100, 50),
        width=40,
        height=50,
        direction=direction,
        obstacles=tuple(obstacles),
    )


def drain_actions(runtime):
    actions = runtime.action_queue[AgentType.GOOMBA]
    results = []
    while not actions.empty():
        results.append(actions.get_nowait())
    return results


def test_asl_initial_patrol_plan_publishes_move_right(goomba_agent, run_bdi_steps):
    agent, runtime = goomba_agent
    agent._update_beliefs(
        perception(obstacles=(ObstacleObservation(0, 100, 400, 32),)),
        sequence=1,
    )
    run_bdi_steps(agent, count=4)
    results = drain_actions(runtime)
    assert len(results) == 1
    assert results[0].enemy_id == "enemy-1"
    assert results[0].action is EnemyAction.MOVE_RIGHT


def test_obstacle_belief_selects_turn_around_plan(goomba_agent, run_bdi_steps):
    agent, runtime = goomba_agent
    agent._update_beliefs(
        perception(
            obstacles=(
                ObstacleObservation(0, 100, 400, 32),
                ObstacleObservation(150, 0, 20, 100),
            )
        ),
        sequence=1,
    )
    run_bdi_steps(agent, count=12)
    results = drain_actions(runtime)
    assert len(results) == 1
    assert results[0].action is EnemyAction.MOVE_LEFT
    assert "direction(left)" in agent.bdi.get_beliefs()


def test_void_belief_selects_turn_around_plan(goomba_agent, run_bdi_steps):
    agent, runtime = goomba_agent
    agent._update_beliefs(
        perception(obstacles=(ObstacleObservation(0, 100, 140, 32),)),
        sequence=1,
    )
    run_bdi_steps(agent, count=12)
    results = drain_actions(runtime)
    assert len(results) == 1
    assert results[0].action is EnemyAction.MOVE_LEFT
    assert "direction(left)" in agent.bdi.get_beliefs()


def test_clear_perception_removes_stale_hazard_beliefs(goomba_agent, run_bdi_steps):
    agent, _runtime = goomba_agent
    agent._update_beliefs(
        perception(
            obstacles=(
                ObstacleObservation(0, 100, 400, 32),
                ObstacleObservation(150, 0, 20, 100),
            )
        ),
        sequence=1,
    )
    run_bdi_steps(agent, count=1)
    assert "obstacle_ahead" in agent.bdi.get_beliefs()
    agent._update_beliefs(
        perception(obstacles=(ObstacleObservation(0, 100, 400, 32),)),
        sequence=2,
    )
    run_bdi_steps(agent, count=1)
    assert "obstacle_ahead" not in agent.bdi.get_beliefs()
    assert "void_ahead" not in agent.bdi.get_beliefs()


def test_custom_action_publishes_action_result_to_goomba_queue(goomba_agent):
    agent, runtime = goomba_agent
    action = agent.bdi_actions.actions[(".move_left", 0)]
    list(action(agent, None, None))
    result = runtime.action_queue[AgentType.GOOMBA].get_nowait()
    assert result.enemy_id == "enemy-1"
    assert result.action is EnemyAction.MOVE_LEFT


def test_turn_around_is_an_absolute_move_so_repeated_turns_do_not_cancel_out(
    goomba_agent, run_bdi_steps
):
    agent, runtime = goomba_agent
    wall_ahead = perception(
        obstacles=(
            ObstacleObservation(0, 100, 400, 32),
            ObstacleObservation(150, 0, 20, 100),
        )
    )
    # Two snapshots taken before the first turn reached the game.
    agent._update_beliefs(wall_ahead, sequence=1)
    run_bdi_steps(agent, count=12)
    agent._update_beliefs(wall_ahead, sequence=2)
    run_bdi_steps(agent, count=12)

    assert [r.action for r in drain_actions(runtime)] == [
        EnemyAction.MOVE_LEFT,
        EnemyAction.MOVE_LEFT,
    ]


def test_bdi_behaviour_is_throttled():
    from distributed_smb.agents.bdi.goomba import ThrottledBDIBehaviour
    from distributed_smb.agents.spade_runtime import SpadeRuntime
    from distributed_smb.application.spade_agent_bridge import make_agent_queues

    runtime = SpadeRuntime(
        make_agent_queues(), make_agent_queues(), enemy_ids=["enemy-1"]
    )
    assert isinstance(runtime.goomba_agents["enemy-1"].bdi, ThrottledBDIBehaviour)
