import asyncio
import threading

from distributed_smb.agents.spade_runtime import SpadeRuntime


def test_runtime_starts_one_async_runner_and_stops_it_cleanly():
    runner_started = threading.Event()
    runner_stopped = threading.Event()
    observed = []

    async def runner(perceptions, actions, stop_event):
        observed.append(threading.current_thread().name)
        observed.append(asyncio.get_running_loop().is_running())
        runner_started.set()
        perception = await asyncio.to_thread(perceptions.get, True, 2.0)
        actions.put(("handled", perception))
        while not stop_event.is_set():
            await asyncio.sleep(0.01)
        runner_stopped.set()

    runtime = SpadeRuntime(runner)

    runtime.start()
    runtime.start()
    try:
        assert runner_started.wait(timeout=2.0)
        runtime.submit_perception({"enemy": "goomba-1"})
        assert runtime.get_action(timeout=2.0) == (
            "handled",
            {"enemy": "goomba-1"},
        )
        assert observed == ["spade-runtime", True]
    finally:
        runtime.stop()

    assert runner_stopped.is_set()
    assert runtime.is_running is False


def test_runtime_can_be_started_again_after_shutdown():
    runtime = SpadeRuntime()

    runtime.start()
    runtime.stop()
    runtime.start()
    runtime.stop()

    assert runtime.is_running is False
