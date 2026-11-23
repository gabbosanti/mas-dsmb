"""Unit tests for the SPADE runtime/thread integration."""

import asyncio
import threading
import time

import pytest

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.models import (
    ActionResult,
    EnemyAction,
    EnemyPerceptionMessage,
    GoombaPerception,
    PlayerPerceptionMessage,
    Position,
    SightDirection,
)
from distributed_smb.agents.spade_thread import SpadeThread


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

    perception = EnemyPerceptionMessage(
        simulation_sequence=7,
        perception=GoombaPerception(
            enemy_id="enemy-1",
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


def test_spade_thread_timeout_logs_worker_stack(caplog):
    entered_runtime = threading.Event()
    release_runtime = threading.Event()

    async def blocked_runtime():
        entered_runtime.set()
        while not release_runtime.is_set():
            await asyncio.sleep(0.01)

    thread = SpadeThread(blocked_runtime)
    thread.start()
    assert entered_runtime.wait(timeout=2.0)

    try:
        with pytest.raises(TimeoutError, match="Timed out while stopping"):
            thread.stop(timeout=0.01)
        assert "Worker stack:" in caplog.text
        assert "blocked_runtime" in caplog.text
    finally:
        release_runtime.set()
        thread.stop(timeout=2.0)


def make_message(goomba_id, sequence):
    return EnemyPerceptionMessage(
        simulation_sequence=sequence,
        perception=GoombaPerception(
            enemy_id=goomba_id,
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

        def set_peers(self, jids):
            self.peers = list(jids)

    runtime = SpadeRuntime(
        make_agent_queues(),
        make_agent_queues(),
        enemies={"enemy-1": AgentType.GOOMBA, "enemy-2": AgentType.GOOMBA},
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
    assert first.enemy_id == "enemy-1" and second.enemy_id == "enemy-2"
    assert [m.simulation_sequence for m in first.perceived] == [10]
    assert [m.perception.enemy_id for m in second.perceived] == ["enemy-2"]
    assert queue.empty()


def test_player_perception_is_routed_to_the_agent_of_that_player():
    from distributed_smb.agents.models import PlayerAgentPerception
    from distributed_smb.agents.spade_runtime import SpadeRuntime
    from distributed_smb.application.spade_agent_bridge import make_agent_queues

    class Recorder:
        def __init__(self, player_id):
            self.player_id = player_id
            self.perceived = []

        def perceive(self, message):
            self.perceived.append(message)

    runtime = SpadeRuntime(
        make_agent_queues(), make_agent_queues(), player_factory=Recorder
    )
    runtime.add_player_agent("player2")
    runtime.add_player_agent("player2")  # idempotent
    message = PlayerPerceptionMessage(
        simulation_sequence=11,
        perception=PlayerAgentPerception(
            "player2", Position(10, 20), 50, 75, on_ground=True, goal_x=None
        ),
    )
    runtime.perception_queue[AgentType.PLAYER].put(message)

    assert runtime.dispatch_perceptions() == 1
    assert runtime.player_agents["player2"].perceived == [message]

    runtime.remove_player_agent("player2")
    runtime.perception_queue[AgentType.PLAYER].put(message)
    assert runtime.dispatch_perceptions() == 0
