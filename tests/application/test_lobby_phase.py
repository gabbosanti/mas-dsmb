"""Integration tests for NodeController.lobby_phase()."""

import threading
import time
from unittest.mock import patch

import pytest

from distributed_smb.application.node_controller import NodeController
from distributed_smb.application.protocols import NoopGameEventBroker, NoopLobbyService
from distributed_smb.network.lobby.service import launch_lobby_server, lobby_manager
from distributed_smb.network.transport.websocket import WsHandler
from distributed_smb.shared.config import LOBBY_STARTUP_WAIT
from distributed_smb.shared.enums import PlayerRole

TEST_PORT = 59200


@pytest.fixture(scope="module", autouse=True)
def start_server():
    lobby_manager.reset()
    launch_lobby_server(host="127.0.0.1", port=TEST_PORT)
    time.sleep(LOBBY_STARTUP_WAIT)


@pytest.fixture(autouse=True)
def reset_lobby():
    lobby_manager.reset()
    yield
    lobby_manager.reset()


def _make_host() -> NodeController:
    ctrl = NodeController(
        game_event_broker=NoopGameEventBroker(),
        lobby_service=NoopLobbyService(),
    ).bootstrap(role=PlayerRole.HOST)
    ctrl.ws_handler = WsHandler(host="127.0.0.1", port=TEST_PORT)
    return ctrl


def _make_client() -> NodeController:
    ctrl = NodeController(
        game_event_broker=NoopGameEventBroker(),
        lobby_service=NoopLobbyService(),
    ).bootstrap(role=PlayerRole.CLIENT)
    ctrl.ws_handler = WsHandler(host="127.0.0.1", port=TEST_PORT)
    return ctrl


def _join_or_cancel(stop: threading.Event, *threads: threading.Thread) -> None:
    """Join the lobby threads, cancelling whatever is still waiting: lobby
    waits have no deadline, so a stuck thread would hang the whole run."""
    for t in threads:
        t.join(timeout=10.0)
    stop.set()
    for t in threads:
        t.join(timeout=2.0)


def test_host_lobby_phase_single_player():
    host = _make_host()

    with patch("distributed_smb.application.lobby.coordinator.time.sleep"):
        roster = host.lobby_phase(start_requested=lambda: True)

    assert host.session_id != ""
    assert len(roster.players) == 1
    assert roster.players[0].player_id == "player1"
    assert roster.players[0].is_host is True
    assert set(host.engine.world_state.characters) == {"player1"}


def test_host_and_client_lobby_phase(run_lobby_pair):
    host = _make_host()
    client = _make_client()

    errors = run_lobby_pair(host, client)

    assert not errors, errors
    assert len(host.roster.players) == 2
    assert len(client.roster.players) == 2
    assert host.session_id == client.session_id
    # World contains exactly the players from the roster
    assert set(host.engine.world_state.characters) == {"player1", "player2"}
    assert set(client.engine.world_state.characters) == {"player1", "player2"}


def test_replay_lobby_phase_resets_engine_without_new_session(run_lobby_pair):
    host = _make_host()
    client = _make_client()

    errors = run_lobby_pair(host, client)
    assert not errors, errors

    original_session_id = host.session_id
    host.engine.world_state.victory = True
    host.engine.world_state.victory_player_id = "player1"
    host.engine.world_state.environment.destructible_blocks[0].destroyed = True

    stop_replay = threading.Event()

    def replay_host():
        try:
            with patch("distributed_smb.application.lobby.coordinator.time.sleep"):
                host.replay_lobby_phase(
                    start_requested=lambda: True,
                    on_update=lambda *a: not stop_replay.is_set(),
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def replay_client():
        try:
            client.replay_lobby_phase(on_update=lambda *a: not stop_replay.is_set())
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t_host = threading.Thread(target=replay_host, daemon=True)
    t_client = threading.Thread(target=replay_client, daemon=True)
    t_host.start()
    t_client.start()
    _join_or_cancel(stop_replay, t_host, t_client)

    assert not errors, errors
    assert host.session_id == original_session_id
    assert client.session_id == original_session_id
    assert host.engine.world_state.victory is False
    assert host.engine.world_state.environment.destructible_blocks[0].destroyed is False
    assert set(host.engine.world_state.characters) == {"player1", "player2"}


def _run_lobby_until_cancelled(ctrl, errors, **kwargs):
    from distributed_smb.application.lobby.coordinator import (
        LobbyCancelledError,
        SessionClosedError,
    )

    try:
        ctrl.lobby_phase(**kwargs)
    except (LobbyCancelledError, SessionClosedError) as exc:
        errors.append(exc)


def test_a_client_leaving_disappears_from_the_hosts_roster():
    """Real controllers and sockets: the leave message is only correct if
    session_id and join_index were recorded when the join was acked."""
    host = _make_host()
    client = _make_client()
    stop_host = threading.Event()
    outcomes: list = []

    def host_update(status, session_id, roster):
        return not stop_host.is_set()

    t_host = threading.Thread(
        target=_run_lobby_until_cancelled,
        args=(host, outcomes),
        kwargs={"on_update": host_update, "start_requested": lambda: False},
        daemon=True,
    )
    t_host.start()
    deadline = time.time() + 5.0
    while not host.session_id and time.time() < deadline:
        time.sleep(0.05)

    stop_client = threading.Event()
    t_client = threading.Thread(
        target=_run_lobby_until_cancelled,
        args=(client, outcomes),
        kwargs={
            "session_id": host.session_id,
            "on_update": lambda *a: not stop_client.is_set(),
        },
        daemon=True,
    )
    t_client.start()

    deadline = time.time() + 5.0
    while (
        len(host.roster.players) < 2 or len(client.roster.players) < 2
    ) and time.time() < deadline:
        time.sleep(0.05)
    assert len(host.roster.players) == 2
    assert len(client.roster.players) == 2

    client.leave_lobby()
    stop_client.set()

    deadline = time.time() + 5.0
    while len(host.roster.players) != 1 and time.time() < deadline:
        time.sleep(0.05)

    stop_host.set()
    t_host.join(timeout=3.0)
    t_client.join(timeout=3.0)

    assert [p.player_id for p in host.roster.players] == ["player1"]


def test_the_host_leaving_sends_every_client_back_to_the_menu():
    """End-to-end: when the host leaves, a waiting client's lobby phase must
    end with SessionClosedError instead of waiting forever."""
    from distributed_smb.application.lobby.coordinator import SessionClosedError

    host = _make_host()
    client = _make_client()
    stop_host = threading.Event()
    outcomes: list = []

    t_host = threading.Thread(
        target=_run_lobby_until_cancelled,
        args=(host, outcomes),
        kwargs={
            "on_update": lambda *a: not stop_host.is_set(),
            "start_requested": lambda: False,
        },
        daemon=True,
    )
    t_host.start()
    deadline = time.time() + 5.0
    while not host.session_id and time.time() < deadline:
        time.sleep(0.05)

    t_client = threading.Thread(
        target=_run_lobby_until_cancelled,
        args=(client, outcomes),
        kwargs={"session_id": host.session_id},
        daemon=True,
    )
    t_client.start()

    deadline = time.time() + 5.0
    while len(client.roster.players) < 2 and time.time() < deadline:
        time.sleep(0.05)

    host.leave_lobby()
    stop_host.set()

    t_client.join(timeout=5.0)
    t_host.join(timeout=3.0)

    assert not t_client.is_alive()
    assert any(isinstance(o, SessionClosedError) for o in outcomes)
