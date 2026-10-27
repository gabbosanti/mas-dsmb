"""Manages the lifecycle of the lobby and game-event Docker containers."""

import logging
import subprocess
import time

from distributed_smb.shared.config import (
    GAME_EVENT_WS_PORT,
    LOBBY_STARTUP_WAIT,
    LOBBY_WS_PORT,
)

LOGGER = logging.getLogger(__name__)

_IMAGE_LOBBY = "distributed-smb-lobby"
_IMAGE_GAMEEVENTS = "distributed-smb-gameevents"
_NAME_LOBBY = "smb-lobby"
_NAME_GAMEEVENTS = "smb-gameevents"

_CONTAINERS = [
    (_NAME_LOBBY, _IMAGE_LOBBY, LOBBY_WS_PORT),
    (_NAME_GAMEEVENTS, _IMAGE_GAMEEVENTS, GAME_EVENT_WS_PORT),
]

_REMOVAL_POLL_INTERVAL_S = 0.2
_REMOVAL_TIMEOUT_S = 5.0


class LobbyContainerManager:
    """Starts/stops the lobby and game-events containers on this machine."""

    def start(self) -> None:
        for name, image, port in _CONTAINERS:
            if self._is_running(name):
                LOGGER.info("container %s already running — reusing", name)
                continue
            self._wait_until_removed(name)
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--rm",
                    "-p",
                    f"{port}:{port}",
                    "--name",
                    name,
                    image,
                ],
                capture_output=True,
            )
            if result.returncode != 0:
                stderr = result.stderr.decode(errors="replace")
                LOGGER.error("failed to start container %s: %s", name, stderr)
                raise subprocess.CalledProcessError(
                    result.returncode, result.args, result.stderr
                )
        time.sleep(LOBBY_STARTUP_WAIT)

    def stop(self) -> None:
        for name in (_NAME_LOBBY, _NAME_GAMEEVENTS):
            subprocess.run(["docker", "stop", name], check=False, capture_output=True)

    def _is_running(self, name: str) -> bool:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True,
        )
        return result.returncode == 0 and result.stdout.strip() == b"true"

    def _wait_until_removed(self, name: str) -> None:
        """Block until `name` is fully gone (container removed, not just stopped)."""
        deadline = time.time() + _REMOVAL_TIMEOUT_S
        while time.time() < deadline:
            result = subprocess.run(["docker", "inspect", name], capture_output=True)
            if result.returncode != 0:
                return
            time.sleep(_REMOVAL_POLL_INTERVAL_S)
        LOGGER.warning(
            "container %s did not disappear within %.1fs — starting anyway",
            name,
            _REMOVAL_TIMEOUT_S,
        )
