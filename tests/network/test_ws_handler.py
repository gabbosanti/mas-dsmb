import time

import pytest

from distributed_smb.network.lobby_service import launch_lobby_server, lobby_manager
from distributed_smb.network.ws_handler import WsHandler
from distributed_smb.shared.config import LOBBY_STARTUP_WAIT
from distributed_smb.shared.messages.session import (
    GameStart,
    RosterUpdate,
    SessionCreate,
    SessionCreated,
    SessionJoin,
    SessionJoined,
)

TEST_PORT = 60000


def _poll_until(handler: WsHandler, timeout: float = 2.0):
    """Poll inbox until a message arrives or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = handler.poll()
        if msg is not None:
            return msg
        time.sleep(0.02)
    return None


def _drain(handler: WsHandler, count: int, timeout: float = 2.0) -> list:
    """Collect exactly `count` messages from handler."""
    msgs = []
    deadline = time.time() + timeout
    while len(msgs) < count and time.time() < deadline:
        msg = handler.poll()
        if msg is not None:
            msgs.append(msg)
        else:
            time.sleep(0.02)
    return msgs


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


# ---------------------------------------------------------------------------
# session_create
# ---------------------------------------------------------------------------


def test_connect_and_session_create():
    handler = WsHandler(host="127.0.0.1", port=TEST_PORT)
    handler.connect()

    handler.send(SessionCreate(player_id="host1", ip="127.0.0.1", udp_port=50010))

    created = _poll_until(handler)
    assert isinstance(created, SessionCreated)
    assert created.join_index == 0
    assert created.session_id != ""

    roster_msg = _poll_until(handler)
    assert isinstance(roster_msg, RosterUpdate)
    assert len(roster_msg.roster.players) == 1
    assert roster_msg.roster.players[0].player_id == "player1"

    handler.close()


# ---------------------------------------------------------------------------
# session_join
# ---------------------------------------------------------------------------


def test_session_join_assigns_join_index_1():
    host = WsHandler(host="127.0.0.1", port=TEST_PORT)
    host.connect()
    host.send(SessionCreate(player_id="host1", ip="127.0.0.1", udp_port=50010))

    created = _poll_until(host)
    session_id = created.session_id
    _poll_until(host)  # discard roster

    client = WsHandler(host="127.0.0.1", port=TEST_PORT)
    client.connect()
    client.send(SessionJoin(session_id=session_id, player_id="client1", ip="127.0.0.1", port=50011))

    joined = _poll_until(client)
    assert isinstance(joined, SessionJoined)
    assert joined.join_index == 1

    # both connections receive roster_update with 2 players
    roster_client = _poll_until(client)
    roster_host = _poll_until(host)
    assert isinstance(roster_client, RosterUpdate)
    assert isinstance(roster_host, RosterUpdate)
    assert len(roster_client.roster.players) == 2

    client.close()
    host.close()


# ---------------------------------------------------------------------------
# game_start
# ---------------------------------------------------------------------------


def test_game_start_received_by_all():
    host = WsHandler(host="127.0.0.1", port=TEST_PORT)
    host.connect()
    host.send(SessionCreate(player_id="host1", ip="127.0.0.1", udp_port=50010))
    created = _poll_until(host)
    session_id = created.session_id
    _poll_until(host)  # discard roster

    client = WsHandler(host="127.0.0.1", port=TEST_PORT)
    client.connect()
    client.send(SessionJoin(session_id=session_id, player_id="client1", ip="127.0.0.1", port=50011))
    _poll_until(client)  # joined
    _poll_until(client)  # roster
    _poll_until(host)  # roster

    host.send(GameStart(session_id=session_id))

    start_host = _poll_until(host)
    start_client = _poll_until(client)

    assert isinstance(start_host, GameStart)
    assert isinstance(start_client, GameStart)
    assert start_host.session_id == session_id

    client.close()
    host.close()


def test_poll_returns_none_when_empty():
    handler = WsHandler(host="127.0.0.1", port=TEST_PORT)
    handler.connect()
    assert handler.poll() is None
    handler.close()
