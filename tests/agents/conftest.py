"""Shared fixtures for isolated SPADE runtime and thread tests."""

import asyncio
import threading

import pytest

from distributed_smb.agents.spade_runtime import SpadeRuntime
from distributed_smb.agents.spade_thread import SpadeThread
from distributed_smb.application.spade_agent_bridge import make_agent_queues


@pytest.fixture
def spade_runtime_thread():
    """Create and start a runtime/thread pair without connecting to XMPP.

    The SPADE Agent is replaced with a small lifecycle double. Tests using this
    fixture still exercise SpadeRuntime.run and the real SpadeThread, while
    keeping the suite independent from a live XMPP server.
    """

    class FakeGoombaAgent:
        def __init__(self, *_args, **_kwargs):
            self.started = threading.Event()
            self.stopped = False
            self.thread_name = None
            self.event_loop_running = False
            self._alive = False
            self.bdi_agent = object()
            self.spade_runtime = None
            self.goomba_id = None
            self.perceived = []

        def perceive(self, message):
            self.perceived.append(message)

        async def start(self):
            self._alive = True
            self.thread_name = threading.current_thread().name
            self.event_loop_running = asyncio.get_running_loop().is_running()
            self.started.set()

        def is_alive(self):
            return self._alive

        async def stop(self):
            self._alive = False
            self.stopped = True

    perceptions = make_agent_queues()
    actions = make_agent_queues()
    fake_agent = FakeGoombaAgent()
    runtime = SpadeRuntime(
        perception_queue=perceptions,
        action_queue=actions,
        enemy_ids=["enemy-1"],
        goomba_factory=lambda _enemy_id: fake_agent,
    )
    thread = SpadeThread(runtime.run)
    thread.start()

    try:
        yield runtime, thread, fake_agent
    finally:
        runtime.request_stop()
        thread.stop(timeout=2.0)


@pytest.fixture
def goomba_agent():
    """Build the real Goomba BDI agent without starting its XMPP connection."""
    perceptions = make_agent_queues()
    actions = make_agent_queues()
    runtime = SpadeRuntime(
        perception_queue=perceptions,
        action_queue=actions,
        enemy_ids=["enemy-1"],
    )
    agent = runtime.goomba_agents["enemy-1"]
    return agent, runtime


@pytest.fixture
def run_bdi_steps():
    """Advance the real SPADE-BDI interpreter without starting an XMPP agent."""

    def run(agent, count=1):
        async def advance():
            for _ in range(count):
                await agent.bdi.run()

        asyncio.run(advance())

    return run
