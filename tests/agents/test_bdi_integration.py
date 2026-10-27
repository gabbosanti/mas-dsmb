"""Integration contract for connecting BDI agents to the game.

These tests define the expected API of the planned
``distributed_smb.agents.integration`` module.
"""

import importlib
from types import SimpleNamespace

import pytest

from distributed_smb.domain.game_engine import GameEngine
from distributed_smb.shared.input import InputState


@pytest.fixture
def integration_api():
    module = importlib.import_module("distributed_smb.agents.integration")
    return SimpleNamespace(
        AgentGameIntegration=module.AgentGameIntegration,
        SimulationLoop=module.SimulationLoop,
        AutonomousPlayerController=module.AutonomousPlayerController,
    )


@pytest.mark.skip(reason="Feature not implemented yet")
def test_observation_to_action_pipeline_executes_agent_request(integration_api):
    engine = GameEngine()
    engine.spawn_player("player1")
    integration = integration_api.AgentGameIntegration(engine=engine)
    integration.add_agent(
        agent_id="goomba-1",
        goals=[{"name": "move_right", "priority": 1}],
        plans=[{"goal": "move_right", "action": "move_right"}],
    )
    initial_x = engine.world_state.get_player("player1").x

    integration.step(dt=1 / 60)

    assert engine.world_state.get_player("player1").x > initial_x


@pytest.mark.skip(reason="Feature not implemented yet")
def test_environment_changes_update_observations_and_revise_intentions(integration_api):
    engine = GameEngine()
    integration = integration_api.AgentGameIntegration(engine=engine)
    agent = integration.add_agent(agent_id="goomba-1")

    integration.step(dt=1 / 60)
    initial_intention = agent.intention
    integration.set_environment_fact("mario_visible", False)
    integration.step(dt=1 / 60)

    assert agent.beliefs["mario_visible"] is False
    assert agent.intention != initial_intention


@pytest.mark.skip(reason="Feature not implemented yet")
def test_information_shared_between_agents_changes_their_decisions(integration_api):
    engine = GameEngine()
    integration = integration_api.AgentGameIntegration(engine=engine)
    first = integration.add_agent(agent_id="goomba-1")
    second = integration.add_agent(agent_id="goomba-2")
    first.set_belief("mario_x", 200)

    integration.step(dt=1 / 60)

    assert second.beliefs["mario_x"] == 200
    assert second.intention.name == "intercept_mario"

    integration.set_environment_fact("mario_reachable", False)
    integration.step(dt=1 / 60)

    assert second.intention.name != "intercept_mario"


@pytest.mark.skip(reason="Feature not implemented yet")
def test_unresponsive_agent_does_not_block_the_simulation_loop(integration_api):
    engine = GameEngine()
    integration = integration_api.AgentGameIntegration(engine=engine)
    integration.add_agent(agent_id="slow-agent", decision_delay=10)
    loop = integration_api.SimulationLoop(
        engine=engine,
        integration=integration,
        decision_timeout=0.01,
    )

    result = loop.step(dt=1 / 60)

    assert result.completed is True
    assert result.used_fallback is True


@pytest.mark.skip(reason="Feature not implemented yet")
def test_autonomous_substitution_uses_character_state_and_excludes_human_input(
    integration_api,
):
    engine = GameEngine()
    engine.spawn_player("player1")
    controller = integration_api.AutonomousPlayerController(engine=engine)
    controller.activate("player1")

    result = controller.step("player1", dt=1 / 60, human_input=InputState(right=True))

    assert result.control_source == "autonomous"
    assert result.human_input_accepted is False
    assert result.character_state == engine.world_state.get_player("player1")


@pytest.mark.skip(reason="Feature not implemented yet")
def test_human_input_is_accepted_only_after_autonomous_control_is_restored(
    integration_api,
):
    engine = GameEngine()
    engine.spawn_player("player1")
    controller = integration_api.AutonomousPlayerController(engine=engine)
    controller.activate("player1")
    controller.deactivate("player1")

    result = controller.step("player1", dt=1 / 60, human_input=InputState(right=True))

    assert result.control_source == "human"
    assert result.human_input_accepted is True
