"""Behavioral contract for the planned BDI agent subsystem.

The tests target ``distributed_smb.agents.bdi`` and define the expected public
API for the implementation: Agent, Goal, Plan, AgentMessage, ActionRequest,
ActionBoundary, and InvalidActionError.
"""

from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace

import pytest

from distributed_smb.domain.game_engine import GameEngine


@pytest.fixture
def bdi():
    try:
        module = import_module("distributed_smb.agents.bdi")
        return SimpleNamespace(
            Agent=module.Agent,
            Goal=module.Goal,
            Plan=module.Plan,
            AgentMessage=module.AgentMessage,
            ActionRequest=module.ActionRequest,
            ActionBoundary=module.ActionBoundary,
            InvalidActionError=module.InvalidActionError,
        )
    except (ImportError, AttributeError) as exc:
        pytest.fail(
            "Implement the documented distributed_smb.agents.bdi API before "
            f"running these behavioral tests: {exc}",
            pytrace=False,
        )


@pytest.fixture
def objectives(bdi):
    pursue_mario = bdi.Goal(
        name="pursue_mario",
        priority=10,
        condition=lambda beliefs: (
            beliefs.get("mario_visible", False) and beliefs.get("mario_reachable", True)
        ),
    )
    patrol = bdi.Goal(
        name="patrol",
        priority=1,
        condition=lambda beliefs: True,
    )
    plans = [
        bdi.Plan(
            goal_name="pursue_mario",
            precondition=lambda beliefs: beliefs.get("mario_visible", False),
            actions=[
                bdi.ActionRequest(name="move_toward", parameters={"target": "mario"})
            ],
        ),
        bdi.Plan(
            goal_name="patrol",
            precondition=lambda beliefs: True,
            actions=[bdi.ActionRequest(name="patrol", parameters={})],
        ),
    ]
    return [pursue_mario, patrol], plans


@pytest.mark.skip(reason="Feature not implemented yet")
def test_observations_initialise_and_refresh_beliefs(bdi, objectives):
    goals, plans = objectives
    agent = bdi.Agent(
        agent_id="goomba-1",
        goals=goals,
        plans=plans,
        relevant_beliefs={"mario_visible", "mario_x"},
        initial_observation={"mario_visible": True, "mario_x": 120},
    )

    assert agent.beliefs == {"mario_visible": True, "mario_x": 120}

    agent.observe({"mario_visible": False, "mario_x": 180})

    assert agent.beliefs["mario_visible"] is False
    assert agent.beliefs["mario_x"] == 180


@pytest.mark.skip(reason="Feature not implemented yet")
def test_deliberation_selects_a_goal_and_revises_the_intention(bdi, objectives):
    goals, plans = objectives
    agent = bdi.Agent(
        agent_id="goomba-1",
        goals=goals,
        plans=plans,
        relevant_beliefs={"mario_visible"},
        initial_observation={"mario_visible": True},
    )

    intention = agent.deliberate()
    assert intention.goal.name == "pursue_mario"

    agent.observe({"mario_visible": False})

    revised_intention = agent.deliberate()
    assert revised_intention.goal.name == "patrol"
    assert revised_intention is not intention


@pytest.mark.skip(reason="Feature not implemented yet")
@pytest.mark.parametrize(
    ("beliefs", "expected_goal"),
    [
        ({"mario_visible": False, "mario_reachable": True}, "patrol"),
        ({"mario_visible": True, "mario_reachable": False}, "patrol"),
    ],
)
def test_unreachable_or_irrelevant_goals_are_not_kept_as_intentions(
    bdi, objectives, beliefs, expected_goal
):
    goals, plans = objectives
    agent = bdi.Agent(
        agent_id="goomba-1",
        goals=goals,
        plans=plans,
        relevant_beliefs={"mario_visible", "mario_reachable"},
        initial_observation=beliefs,
    )

    assert agent.deliberate().goal.name == expected_goal


@pytest.mark.skip(reason="Feature not implemented yet")
def test_execution_uses_the_plan_for_the_current_intention(bdi, objectives):
    goals, plans = objectives
    submitted = []
    agent = bdi.Agent(
        agent_id="goomba-1",
        goals=goals,
        plans=plans,
        relevant_beliefs={"mario_visible"},
        initial_observation={"mario_visible": True},
        action_sink=submitted.append,
    )
    agent.deliberate()

    requests = agent.execute_intention()

    expected = bdi.ActionRequest(name="move_toward", parameters={"target": "mario"})
    assert requests == [expected]
    assert submitted == [expected]


@pytest.mark.skip(reason="Feature not implemented yet")
def test_execution_reports_when_the_intention_has_no_executable_plan(bdi):
    unreachable_goal = bdi.Goal(
        name="attack",
        priority=5,
        condition=lambda beliefs: True,
    )
    blocked_plan = bdi.Plan(
        goal_name="attack",
        precondition=lambda beliefs: beliefs.get("path_clear", False),
        actions=[bdi.ActionRequest(name="attack", parameters={})],
    )
    agent = bdi.Agent(
        agent_id="goomba-1",
        goals=[unreachable_goal],
        plans=[blocked_plan],
        relevant_beliefs={"path_clear"},
        initial_observation={"path_clear": False},
    )
    agent.deliberate()

    assert agent.execute_intention() == []


@pytest.mark.skip(reason="Feature not implemented yet")
def test_action_boundary_rejects_unsupported_actions_without_changing_game_state(bdi):
    engine = GameEngine()
    engine.spawn_player("player1")
    original_state = deepcopy(engine.world_state)
    boundary = bdi.ActionBoundary(
        engine=engine,
        player_id="player1",
        supported_actions={"move_left", "move_right", "jump"},
    )
    unsupported = bdi.ActionRequest(name="delete_world", parameters={})

    with pytest.raises(bdi.InvalidActionError):
        boundary.submit(unsupported)

    assert engine.world_state == original_state


@pytest.mark.skip(reason="Feature not implemented yet")
def test_received_messages_update_relevant_beliefs_once(bdi, objectives):
    goals, plans = objectives
    agent = bdi.Agent(
        agent_id="goomba-1",
        goals=goals,
        plans=plans,
        relevant_beliefs={"mario_visible", "mario_x"},
        initial_observation={"mario_visible": False, "mario_x": 10},
    )
    message = bdi.AgentMessage(
        sender_id="goomba-2",
        recipient_id="goomba-1",
        message_id="sighting-1",
        information={"mario_visible": True, "mario_x": 200},
    )

    agent.receive(message)
    agent.receive(message)

    assert agent.beliefs["mario_visible"] is True
    assert agent.beliefs["mario_x"] == 200
    assert agent.processed_message_ids == {"sighting-1"}


@pytest.mark.skip(reason="Feature not implemented yet")
def test_missing_or_irrelevant_message_information_does_not_corrupt_beliefs(
    bdi, objectives
):
    goals, plans = objectives
    agent = bdi.Agent(
        agent_id="goomba-1",
        goals=goals,
        plans=plans,
        relevant_beliefs={"mario_visible", "mario_x"},
        initial_observation={"mario_visible": False, "mario_x": 10},
    )
    missing = bdi.AgentMessage(
        sender_id="goomba-2",
        recipient_id="goomba-1",
        message_id="empty-1",
        information={},
    )
    irrelevant = bdi.AgentMessage(
        sender_id="goomba-2",
        recipient_id="goomba-1",
        message_id="irrelevant-1",
        information={"weather": "sunny"},
    )

    agent.receive(missing)
    agent.receive(irrelevant)

    assert agent.beliefs == {"mario_visible": False, "mario_x": 10}


@pytest.mark.skip(reason="Feature not implemented yet")
def test_revoking_autonomous_control_stops_future_action_submissions(bdi, objectives):
    goals, plans = objectives
    submitted = []
    agent = bdi.Agent(
        agent_id="goomba-1",
        goals=goals,
        plans=plans,
        relevant_beliefs={"mario_visible"},
        initial_observation={"mario_visible": True},
        action_sink=submitted.append,
    )

    agent.set_autonomous_control(True)
    agent.step()
    assert submitted

    submitted.clear()
    agent.set_autonomous_control(False)

    assert agent.step() == []
    assert submitted == []
