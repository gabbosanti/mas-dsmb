"""Unit tests for the SPADE runtime/thread integration."""

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.models import (
    ActionResult,
    EnemyAction,
    GoombaPerception,
    GoombaPerceptionMessage,
    Position,
    SightDirection,
)


def test_spade_runtime_runs_on_dedicated_thread(spade_runtime_thread):
    runtime, thread, fake_agent = spade_runtime_thread

    thread.start()
    thread.start()  # Starting an already-running runtime is idempotent.

    assert fake_agent.started.wait(timeout=2.0)
    assert thread.is_running
    assert fake_agent.thread_name == "spade-runtime"
    assert fake_agent.event_loop_running is True
    assert runtime.stop_event.is_set() is False


def test_runtime_routes_perceptions_and_actions_through_agent_queues(
    spade_runtime_thread,
):
    runtime, _thread, _fake_agent = spade_runtime_thread
    
    perception = GoombaPerceptionMessage(
        simulation_sequence=7,
        perception=GoombaPerception(
            goomba_id="enemy-1",
            position=Position(10, 20),
            width=40,
            height=50,
            direction=SightDirection.LEFT,
        ),
    )
    runtime.perception_queue[AgentType.GOOMBA].put(perception)
    assert runtime.get_perception(AgentType.GOOMBA) is perception

    action = ActionResult("enemy-1", EnemyAction.MOVE_LEFT)
    runtime.put_action(AgentType.GOOMBA, action)
    assert runtime.action_queue[AgentType.GOOMBA].get_nowait() is action


def test_spade_thread_stops_runtime_cleanly(spade_runtime_thread):
    runtime, thread, fake_agent = spade_runtime_thread

    thread.start()
    assert fake_agent.started.wait(timeout=2.0)

    runtime.request_stop()
    thread.stop(timeout=2.0)

    assert thread.is_running is False
    assert fake_agent.stopped is True
