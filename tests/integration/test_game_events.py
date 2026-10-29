"""Game event propagation over the real WebSocket relay, plus player lifecycle."""

import json
import time

import pytest

from distributed_smb.application.node_controller import NodeController
from distributed_smb.application.protocols import NoopGameEventBroker, NoopLobbyService
from distributed_smb.network.game_events.server import (
    launch_game_event_server,
    send_game_event,
)
from distributed_smb.network.game_events.server import (
    reset as reset_game_event_server,
)
from distributed_smb.network.serializer import Serializer
from distributed_smb.network.transport.websocket import WsHandler
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

TEST_GE_PORT = 59411

_serializer = Serializer()


@pytest.fixture(scope="module", autouse=True)
def start_server():
    launch_game_event_server(host="127.0.0.1", port=TEST_GE_PORT)
    time.sleep(LOBBY_STARTUP_WAIT)


@pytest.fixture(autouse=True)
def reset_state():
    reset_game_event_server()
    yield
    reset_game_event_server()


def _node(role: PlayerRole) -> NodeController:
    """A node in post-lobby state: host and client in roster and world."""
    node = NodeController(
        game_event_broker=NoopGameEventBroker(),
        lobby_service=NoopLobbyService(),
    ).bootstrap(role=role)
    node.roster.add_player(
        RosterEntry(
            player_id="player1",
            host="127.0.0.1",
            udp_port=50000,
            join_index=0,
            is_host=True,
        )
    )
    node.roster.add_player(
        RosterEntry(player_id="player2", host="127.0.0.1", udp_port=50001, join_index=1)
    )
    node._rebuild_world_from_roster()
    if role is PlayerRole.HOST:
        node.cached_remote_inputs["player2"] = InputState()
        node.last_remote_input_sequence["player2"] = 0
    return node


def _subscriber(player_id: str) -> WsHandler:
    path = f"{GAME_EVENT_WS_PATH}?player_id={player_id}"
    handler = WsHandler("127.0.0.1", TEST_GE_PORT, path=path)
    handler.connect()
    time.sleep(0.1)
    return handler


def _publish_to(node: NodeController, message) -> None:
    """Send an event over the real relay and let the node apply it."""
    node.game_event_handler = _subscriber(node.local_player_id)
    send_game_event(json.dumps(_serializer.encode_ws_message(message)).encode())
    time.sleep(0.2)
    node._drain_game_events()
    node.game_event_handler.close()


def _poll_until(handler: WsHandler, expected_type: type, timeout: float = 2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = handler.poll()
        if isinstance(msg, expected_type):
            return msg
        time.sleep(0.05)
    return None


def test_one_event_reaches_every_connected_client():
    first, second = _subscriber("receiver-1"), _subscriber("receiver-2")

    send_game_event(
        json.dumps(
            _serializer.encode_ws_message(BlockDestroyedMessage(position=(50, 50)))
        ).encode()
    )

    received = [
        _poll_until(handler, BlockDestroyedMessage) for handler in (first, second)
    ]
    first.close()
    second.close()

    assert all(msg is not None for msg in received)
    assert [msg.position for msg in received] == [(50, 50), (50, 50)]


def test_host_evicts_a_silent_player_everywhere_and_tells_the_others():
    """A peer that stops sending input is dropped from world, roster and every
    per-player cache, and the others are told so they drop it too."""
    host = _node(PlayerRole.HOST)
    host.last_input_time["player2"] = time.time() - UDP_INPUT_TIMEOUT - 1.0

    sent: list[bytes] = []

    class _SpyBroker(NoopGameEventBroker):
        def send(self, payload: bytes) -> None:
            sent.append(payload)

    host.game_event_broker = _SpyBroker()

    host._check_player_disconnections()

    assert "player2" not in host.engine.world_state.characters
    assert host.roster.get_player("player2") is None
    assert "player2" not in host.cached_remote_inputs
    assert "player2" not in host.last_remote_input_sequence
    assert "player2" not in host.last_input_time

    assert len(sent) == 1
    msg = _serializer.decode_ws_message(json.loads(sent[0]))
    assert isinstance(msg, PlayerLeft)
    assert msg.player_id == "player2"


def test_client_applies_player_left_unless_it_names_itself():
    """A false eviction — e.g. a UDP timeout misfiring right after a migration —
    would otherwise make a live node remove itself from its own world."""
    client = _node(PlayerRole.CLIENT)
    assert "player1" in client.engine.world_state.characters

    _publish_to(client, PlayerLeft(player_id="player1"))

    assert "player1" not in client.engine.world_state.characters
    assert client.roster.get_player("player1") is None

    _publish_to(client, PlayerLeft(player_id=client.local_player_id))

    assert client.local_player_id in client.engine.world_state.characters
    assert client.roster.get_player(client.local_player_id) is not None


def test_client_applies_environment_events_and_the_level_reset():
    """Blocks and power-ups never come from the UDP snapshot: these events are
    the only way a client learns about them, reset included."""
    client = _node(PlayerRole.CLIENT)
    block = client.engine.world_state.environment.destructible_blocks[0]
    power_up = next(iter(client.engine.world_state.environment.power_ups.values()))

    _publish_to(client, BlockDestroyedMessage(position=(block.x, block.y)))
    _publish_to(
        client,
        PowerUpCollectedMessage(powerup_id=power_up.powerup_id, player_id="player1"),
    )

    assert block.destroyed
    assert power_up.collected
    assert power_up.owner == "player1"

    player = client.engine.world_state.get_player("player1")
    player.x, player.y = 999, 999

    _publish_to(client, LevelResetMessage())

    environment = client.engine.world_state.environment
    assert environment.destructible_blocks[0].destroyed is False
    assert next(iter(environment.power_ups.values())).collected is False
    reset_player = client.engine.world_state.get_player("player1")
    assert (reset_player.x, reset_player.y) == client.engine.spawn_position_for(0)
