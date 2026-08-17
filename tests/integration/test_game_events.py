"""Integration tests for game event propagation and player lifecycle (Milestone 4)."""

import json
import threading
import time
from unittest.mock import patch

import pytest

from distributed_smb.application.node_controller import NodeController
from distributed_smb.application.protocols import NoopGameEventBroker, NoopLobbyService
from distributed_smb.network.game_event_server import (
    launch_game_event_server,
    send_game_event,
)
from distributed_smb.network.game_event_server import (
    reset as reset_game_event_server,
)
from distributed_smb.network.lobby_service import launch_lobby_server, lobby_manager
from distributed_smb.network.serializer import Serializer
from distributed_smb.network.ws_handler import WsHandler
from distributed_smb.shared.config import (
    GAME_EVENT_WS_PATH,
    LOBBY_STARTUP_WAIT,
    UDP_INPUT_TIMEOUT,
)
from distributed_smb.shared.enums import PlayerRole
from distributed_smb.shared.input import InputState
from distributed_smb.shared.messages.gameplay import (
    BlockDestroyedMessage,
    LevelResetMessage,
    PlayerLeft,
    PowerUpCollectedMessage,
)
from distributed_smb.shared.roster import RosterEntry

TEST_LOBBY_PORT = 59410
TEST_GE_PORT = 59411
FRAME_DT = 1 / 60

_serializer = Serializer()


@pytest.fixture(scope="module", autouse=True)
def start_servers():
    lobby_manager.reset()
    launch_lobby_server(host="127.0.0.1", port=TEST_LOBBY_PORT)
    launch_game_event_server(host="127.0.0.1", port=TEST_GE_PORT)
    time.sleep(LOBBY_STARTUP_WAIT)


@pytest.fixture(autouse=True)
def reset_state():
    lobby_manager.reset()
    reset_game_event_server()
    yield
    lobby_manager.reset()
    reset_game_event_server()


def _poll_until(handler: WsHandler, expected_type: type, timeout: float = 2.0):
    """Poll a WsHandler until a message of expected_type arrives."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = handler.poll()
        if isinstance(msg, expected_type):
            return msg
        time.sleep(0.05)
    return None


def _run_lobby(host: NodeController, client: NodeController) -> list:
    """Run lobby phase for both nodes concurrently, suppressing server launches."""
    errors = []

    def run_host():
        try:
            with patch("distributed_smb.application.lobby_coordinator.time.sleep"):
                host.lobby_phase(start_requested=lambda: len(host.roster.players) >= 2)
        except Exception as exc:
            errors.append(exc)

    def run_client():
        deadline = time.time() + 5.0
        while not host.session_id and time.time() < deadline:
            time.sleep(0.05)
        try:
            client.lobby_phase(session_id=host.session_id)
        except Exception as exc:
            errors.append(exc)

    t_host = threading.Thread(target=run_host)
    t_client = threading.Thread(target=run_client)
    t_host.start()
    t_client.start()
    t_host.join(timeout=10.0)
    t_client.join(timeout=10.0)
    return errors


def _make_post_lobby_host() -> NodeController:
    """Build a host NodeController in post-lobby state without running lobby."""
    nc = NodeController(
        game_event_broker=NoopGameEventBroker(),
        lobby_service=NoopLobbyService(),
    ).bootstrap(role=PlayerRole.HOST)
    nc.roster.add_player(
        RosterEntry(
            player_id="player1", host="127.0.0.1", udp_port=50000, join_index=0, is_host=True
        )
    )
    nc.roster.add_player(
        RosterEntry(player_id="player2", host="127.0.0.1", udp_port=50001, join_index=1)
    )
    nc._rebuild_world_from_roster()
    nc.cached_remote_inputs["player2"] = InputState()
    nc.last_remote_input_sequence["player2"] = 0
    return nc


def _make_post_lobby_client() -> NodeController:
    """Build a client NodeController in post-lobby state without running lobby."""
    nc = NodeController().bootstrap(role=PlayerRole.CLIENT)
    nc.roster.add_player(
        RosterEntry(
            player_id="player1", host="127.0.0.1", udp_port=50000, join_index=0, is_host=True
        )
    )
    nc.roster.add_player(
        RosterEntry(player_id="player2", host="127.0.0.1", udp_port=50001, join_index=1)
    )
    nc._rebuild_world_from_roster()
    return nc


def _ge_handler(player_id: str) -> WsHandler:
    """Connect a WsHandler to the test game event server and return it."""
    path = f"{GAME_EVENT_WS_PATH}?player_id={player_id}"
    handler = WsHandler("127.0.0.1", TEST_GE_PORT, path=path)
    handler.connect()
    time.sleep(0.1)
    return handler


def test_block_destroyed_event_reaches_client():
    """BlockDestroyedMessage sent via send_game_event() must arrive at all connected clients."""
    receiver = _ge_handler("test-receiver")

    payload = json.dumps(
        _serializer.encode_ws_message(BlockDestroyedMessage(position=(100, 200)))
    ).encode()
    send_game_event(payload)

    received = _poll_until(receiver, BlockDestroyedMessage)
    receiver.close()

    assert received is not None, "Client did not receive BlockDestroyedMessage"
    assert received.position == (100, 200)


def test_powerup_collected_event_reaches_client():
    """PowerUpCollectedMessage is delivered to connected WebSocket clients."""
    receiver = _ge_handler("test-receiver")

    payload = json.dumps(
        _serializer.encode_ws_message(
            PowerUpCollectedMessage(powerup_id="pu-1", player_id="player1")
        )
    ).encode()
    send_game_event(payload)

    received = _poll_until(receiver, PowerUpCollectedMessage)
    receiver.close()

    assert received is not None, "Client did not receive PowerUpCollectedMessage"
    assert received.powerup_id == "pu-1"
    assert received.player_id == "player1"


def test_player_left_event_reaches_client():
    """PlayerLeft is delivered to connected WebSocket clients."""
    receiver = _ge_handler("test-receiver")

    payload = json.dumps(_serializer.encode_ws_message(PlayerLeft(player_id="player2"))).encode()
    send_game_event(payload)

    received = _poll_until(receiver, PlayerLeft)
    receiver.close()

    assert received is not None, "Client did not receive PlayerLeft"
    assert received.player_id == "player2"


def test_event_reaches_multiple_clients():
    """A single broadcast reaches every connected client simultaneously."""
    r1 = _ge_handler("receiver-1")
    r2 = _ge_handler("receiver-2")

    payload = json.dumps(
        _serializer.encode_ws_message(BlockDestroyedMessage(position=(50, 50)))
    ).encode()
    send_game_event(payload)

    msg1 = _poll_until(r1, BlockDestroyedMessage)
    msg2 = _poll_until(r2, BlockDestroyedMessage)

    r1.close()
    r2.close()

    assert msg1 is not None, "receiver-1 did not receive the event"
    assert msg2 is not None, "receiver-2 did not receive the event"


def test_host_evicts_player_on_udp_timeout():
    """When a client's last input timestamp is past UDP_INPUT_TIMEOUT the host removes it."""
    host = _make_post_lobby_host()
    assert "player2" in host.engine.world_state.characters

    host.last_input_time["player2"] = time.time() - UDP_INPUT_TIMEOUT - 1.0
    host._check_player_disconnections()

    assert "player2" not in host.engine.world_state.characters
    assert host.roster.get_player("player2") is None


def test_evict_player_clears_all_associated_state():
    """_evict_player removes the player from world, roster, input cache, and input timers."""
    host = _make_post_lobby_host()

    host._evict_player("player2")

    assert "player2" not in host.engine.world_state.characters
    assert host.roster.get_player("player2") is None
    assert "player2" not in host.cached_remote_inputs
    assert "player2" not in host.last_remote_input_sequence
    assert "player2" not in host.last_input_time


def test_host_broadcasts_player_left_on_udp_timeout():
    """Host calls game_event_broker.send() with PlayerLeft when evicting via UDP timeout."""
    host = _make_post_lobby_host()
    host.last_input_time["player2"] = time.time() - UDP_INPUT_TIMEOUT - 1.0

    sent_payloads: list[bytes] = []

    class _SpyBroker(NoopGameEventBroker):
        def send(self, payload: bytes) -> None:
            sent_payloads.append(payload)

    host.game_event_broker = _SpyBroker()
    host._check_player_disconnections()

    assert len(sent_payloads) == 1
    data = json.loads(sent_payloads[0])
    msg = _serializer.decode_ws_message(data)
    assert isinstance(msg, PlayerLeft)
    assert msg.player_id == "player2"


def test_client_applies_player_left_event():
    """Client's _drain_game_events() removes a player from world state on PlayerLeft."""
    client = _make_post_lobby_client()
    client.game_event_handler = _ge_handler(client.local_player_id)

    assert "player1" in client.engine.world_state.characters

    payload = json.dumps(_serializer.encode_ws_message(PlayerLeft(player_id="player1"))).encode()
    send_game_event(payload)
    time.sleep(0.2)

    client._drain_game_events()
    client.game_event_handler.close()

    assert "player1" not in client.engine.world_state.characters
    assert client.roster.get_player("player1") is None


def test_client_applies_block_destroyed_event():
    """Client marks a block as destroyed when it receives a BlockDestroyedMessage."""
    client = _make_post_lobby_client()
    client.game_event_handler = _ge_handler(client.local_player_id)

    block = client.engine.world_state.environment.destructible_blocks[0]
    block_pos = (block.x, block.y)
    assert block is not None, "Test block not found in world state"
    assert not block.destroyed

    payload = json.dumps(
        _serializer.encode_ws_message(BlockDestroyedMessage(position=block_pos))
    ).encode()
    send_game_event(payload)
    time.sleep(0.2)

    client._drain_game_events()
    client.game_event_handler.close()

    assert block.destroyed


def test_client_applies_powerup_collected_event():
    """Client marks a power-up as collected and sets owner on PowerUpCollectedMessage."""
    client = _make_post_lobby_client()
    client.game_event_handler = _ge_handler(client.local_player_id)

    pu = next(iter(client.engine.world_state.environment.power_ups.values()), None)
    assert pu is not None, "Test power-up not found in world state"
    assert not pu.collected

    payload = json.dumps(
        _serializer.encode_ws_message(
            PowerUpCollectedMessage(powerup_id=pu.powerup_id, player_id="player1")
        )
    ).encode()
    send_game_event(payload)
    time.sleep(0.2)

    client._drain_game_events()
    client.game_event_handler.close()

    assert pu.collected
    assert pu.owner == "player1"


def test_client_applies_level_reset_event():
    """Client restores its local blocks/power-ups and respawns players on LevelResetMessage."""
    client = _make_post_lobby_client()
    client.game_event_handler = _ge_handler(client.local_player_id)

    client.engine.world_state.environment.destructible_blocks[0].destroyed = True
    next(iter(client.engine.world_state.environment.power_ups.values())).collected = True
    player = client.engine.world_state.get_player("player1")
    player.x, player.y = 999, 999

    payload = json.dumps(_serializer.encode_ws_message(LevelResetMessage())).encode()
    send_game_event(payload)
    time.sleep(0.2)

    client._drain_game_events()
    client.game_event_handler.close()

    # reset_for_new_run() rebuilds the environment from the level template,
    # so the pre-reset block/power_up objects are stale; re-fetch them.
    assert client.engine.world_state.environment.destructible_blocks[0].destroyed is False
    assert next(iter(client.engine.world_state.environment.power_ups.values())).collected is False
    reset_player = client.engine.world_state.get_player("player1")
    assert (reset_player.x, reset_player.y) == client.engine.spawn_position_for(0)
