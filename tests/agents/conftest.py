"""Shared fixtures for isolated SPADE runtime and thread tests."""

import asyncio
import threading
from pathlib import Path

import pytest

from distributed_smb.agents.bdi.goomba import GoombaAgent
from distributed_smb.agents.spade_runtime import SpadeRuntime
from distributed_smb.agents.spade_thread import SpadeThread
from distributed_smb.application.spade_agent_bridge import make_agent_queues


@pytest.fixture
def spade_runtime_thread(monkeypatch):
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
    runtime = SpadeRuntime(
        perception_queue=perceptions,
        action_queue=actions,
    )
    thread = SpadeThread(runtime.run)
    fake_agent = None
    agent_created = threading.Event()

    def make_fake_agent(_asl_path):
        nonlocal fake_agent
        fake_agent = FakeGoombaAgent()
        agent_created.set()
        return fake_agent

    monkeypatch.setattr(runtime, "_create_goomba_agent", make_fake_agent)
    thread.start()
    # Agent construction and its async start happen in the worker thread.
    if not agent_created.wait(timeout=2.0):
        runtime.request_stop()
        thread.stop(timeout=2.0)
        pytest.fail("SPADE runtime did not construct its agent")

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
    )
    asl_path = Path(__file__).parents[2] / "src/distributed_smb/agents/asl/goomba.asl"
    agent = GoombaAgent("goomba@test", "unused", str(asl_path))
    agent.spade_runtime = runtime
    agent.goomba_id = "enemy-1"
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
