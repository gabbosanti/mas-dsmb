"""Unit tests for the SPADE runtime/thread integration."""

import time

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
    # The runtime's dispatcher, not the test, consumes the shared queue.
    deadline = time.monotonic() + 2.0
    while not _fake_agent.perceived and time.monotonic() < deadline:
        time.sleep(0.01)
    assert _fake_agent.perceived == [perception]

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


def make_message(goomba_id, sequence):
    return GoombaPerceptionMessage(
        simulation_sequence=sequence,
        perception=GoombaPerception(
            goomba_id=goomba_id,
            position=Position(10, 20),
            width=40,
            height=50,
            direction=SightDirection.LEFT,
        ),
    )


def test_dispatch_routes_each_perception_to_its_own_agent_only():
    from distributed_smb.agents.spade_runtime import SpadeRuntime
    from distributed_smb.application.spade_agent_bridge import make_agent_queues

    class Recorder:
        def __init__(self):
            self.perceived = []

        def perceive(self, message):
            self.perceived.append(message)

    runtime = SpadeRuntime(
        make_agent_queues(),
        make_agent_queues(),
        enemy_ids=["enemy-1", "enemy-2"],
        goomba_factory=lambda _enemy_id: Recorder(),
    )
    queue = runtime.perception_queue[AgentType.GOOMBA]
    for message in (
        make_message("enemy-1", 5),
        make_message("enemy-2", 5),
        make_message("enemy-1", 10),  # supersedes enemy-1's older snapshot
        make_message("enemy-9", 10),  # no agent for this enemy
    ):
        queue.put(message)

    runtime.dispatch_perceptions()

    first, second = runtime.goomba_agents["enemy-1"], runtime.goomba_agents["enemy-2"]
    assert first.goomba_id == "enemy-1" and second.goomba_id == "enemy-2"
    assert [m.simulation_sequence for m in first.perceived] == [10]
    assert [m.perception.goomba_id for m in second.perceived] == ["enemy-2"]
    assert queue.empty()
