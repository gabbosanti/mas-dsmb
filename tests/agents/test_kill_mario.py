"""Behavioural tests for the Goomba's kill_mario plans (goomba.asl)."""

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.models import (
    EnemyAction,
    GoombaPerception,
    ObstacleObservation,
    PlayerPerception,
    Position,
    SightDirection,
)

# The Goomba is 40x50 at (100, 50): center (120, 75), feet on y=100.
FLOOR = ObstacleObservation(0, 100, 400, 32)


def player(center_x: float, center_y: float = 75) -> PlayerPerception:
    return PlayerPerception("player1", Position(center_x - 10, center_y - 20), 20, 40)


def perception(*, direction=SightDirection.RIGHT, players=(), obstacles=(FLOOR,)):
    return GoombaPerception(
        goomba_id="enemy-1",
        position=Position(100, 50),
        width=40,
        height=50,
        direction=direction,
        players=tuple(players),
        obstacles=tuple(obstacles),
    )


def decide(agent, runtime, run_bdi_steps, snapshot, sequence=1):
    agent._update_beliefs(snapshot, sequence=sequence)
    run_bdi_steps(agent, count=1)
    queue = runtime.action_queue[AgentType.GOOMBA]
    actions = []
    while not queue.empty():
        actions.append(queue.get_nowait().action)
    return actions


def test_goomba_turns_towards_a_player_behind_it(goomba_agent, run_bdi_steps):
    agent, runtime = goomba_agent

    actions = decide(agent, runtime, run_bdi_steps, perception(players=[player(40)]))

    assert actions == [EnemyAction.MOVE_LEFT]
    assert "player_near(left)" in agent.bdi.get_beliefs()


def test_goomba_keeps_walking_towards_a_player_ahead(goomba_agent, run_bdi_steps):
    agent, runtime = goomba_agent

    snapshot = perception(direction=SightDirection.LEFT, players=[player(220)])
    assert decide(agent, runtime, run_bdi_steps, snapshot) == [EnemyAction.MOVE_RIGHT]


def test_player_at_another_height_or_too_far_is_ignored(goomba_agent, run_bdi_steps):
    agent, runtime = goomba_agent

    snapshot = perception(
        direction=SightDirection.LEFT,
        players=[player(200, center_y=-20), player(400)],
    )
    # Plain patrol: keep the current heading.
    assert decide(agent, runtime, run_bdi_steps, snapshot) == [EnemyAction.MOVE_LEFT]


def test_goomba_does_not_chase_a_player_beyond_a_void(goomba_agent, run_bdi_steps):
    agent, runtime = goomba_agent
    # The floor starts exactly under the Goomba: there is a void on its left.
    floor = ObstacleObservation(100, 100, 300, 32)

    snapshot = perception(players=[player(60)], obstacles=(floor,))

    assert decide(agent, runtime, run_bdi_steps, snapshot) == [EnemyAction.MOVE_RIGHT]
    assert "path_blocked(left)" in agent.bdi.get_beliefs()


def test_safety_wins_over_chasing(goomba_agent, run_bdi_steps):
    agent, runtime = goomba_agent
    short_floor = ObstacleObservation(0, 100, 150, 32)  # void just ahead

    snapshot = perception(players=[player(200)], obstacles=(short_floor,))

    assert decide(agent, runtime, run_bdi_steps, snapshot) == [EnemyAction.MOVE_LEFT]


def test_goomba_resumes_patrol_when_the_player_leaves(goomba_agent, run_bdi_steps):
    agent, runtime = goomba_agent

    decide(agent, runtime, run_bdi_steps, perception(players=[player(40)]), sequence=1)
    actions = decide(
        agent,
        runtime,
        run_bdi_steps,
        perception(direction=SightDirection.LEFT),
        sequence=2,
    )

    assert actions == [EnemyAction.MOVE_LEFT]
    beliefs = agent.bdi.get_beliefs()
    assert not any(belief.startswith("player_near") for belief in beliefs)
