"""Shared fixtures for isolated SPADE runtime and thread tests."""

import asyncio
import threading
import time
from queue import Queue

import pytest

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.spade_runtime import SpadeRuntime
from distributed_smb.agents.spade_thread import SpadeThread


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

    perceptions = {agent_type: Queue() for agent_type in AgentType}
    actions = {agent_type: Queue() for agent_type in AgentType}
    runtime = SpadeRuntime(perceptions, actions)
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
