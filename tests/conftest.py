import os
import threading
import time

import pygame
import pytest

from distributed_smb.shared.messages.session import SessionCreated, SessionRecreate


@pytest.fixture(autouse=True, scope="session")
def initialize_pygame():
    """Initialize pygame for tests to avoid 'video system not initialized'."""
    # Set dummy display driver to avoid opening actual windows
    os.environ["SDL_VIDEODRIVER"] = "dummy"

    # Initialize pygame
    pygame.init()

    yield

    # Clean up
    pygame.quit()


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path, monkeypatch):
    """Lobby phases write session_metadata.json into the working directory:
    keep it out of the repo root, where the real game would find it."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def run_lobby_pair():
    """Run a host and a client lobby phase concurrently until the game starts,
    cancelling after 10s: a stuck thread must fail the test, not hang the run."""
    stop = threading.Event()

    def run(host, client) -> list[Exception]:
        errors: list[Exception] = []

        def keep_waiting(*_):
            return not stop.is_set()

        def run_host():
            try:
                host.lobby_phase(
                    start_requested=lambda: len(host.roster.players) >= 2,
                    on_update=keep_waiting,
                )
            except Exception as exc:
                errors.append(exc)

        def run_client():
            deadline = time.time() + 5.0
            while not host.session_id and time.time() < deadline:
                time.sleep(0.05)
            try:
                client.lobby_phase(session_id=host.session_id, on_update=keep_waiting)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=run_host, daemon=True),
            threading.Thread(target=run_client, daemon=True),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)
        stop.set()
        for t in threads:
            t.join(timeout=2.0)
        return errors

    return run


class _AckingLobbyWs:
    """Stands in for the lobby a promoted host re-registers with."""

    def __init__(self, *_, **__):
        self._inbox: list = []

    def connect(self, timeout: float = 10.0) -> None:
        pass

    def send(self, message) -> None:
        if isinstance(message, SessionRecreate):
            self._inbox.append(
                SessionCreated(session_id=message.session_id, join_index=0)
            )

    def poll(self):
        return self._inbox.pop(0) if self._inbox else None

    def close(self) -> None:
        pass


@pytest.fixture
def no_real_lobby_relaunch(monkeypatch):
    """Promotion re-registers with the lobby on localhost from a background
    thread: fake it, and join the thread before the fake goes away."""
    monkeypatch.setattr(
        "distributed_smb.application.election.mixin.time.sleep", lambda s: None
    )
    monkeypatch.setattr(
        "distributed_smb.application.election.mixin.WsHandler", _AckingLobbyWs
    )
    yield
    for t in threading.enumerate():
        if t.name == "lobby-relaunch":
            t.join(timeout=2.0)
