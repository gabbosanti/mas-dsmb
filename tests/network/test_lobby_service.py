import json

import pytest
from fastapi.testclient import TestClient

from distributed_smb.network.lobby.service import app, lobby_manager
from distributed_smb.shared.enums import MessageType
from distributed_smb.shared.roster import GlobalRoster, RosterEntry

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_lobby():
    lobby_manager.reset()
    yield
    lobby_manager.reset()


def _send(ws, message_type: str, **fields) -> None:
    ws.send_text(json.dumps({"message_type": message_type, **fields}))


def _next(ws) -> dict:
    return json.loads(ws.receive_text())


def _create_session(ws, player_id: str = "host1") -> str:
    """Host side of the handshake: returns the session id, drops the roster echo."""
    _send(ws, "session_create", player_id=player_id, ip="127.0.0.1", udp_port=50010)
    session_id = _next(ws)["session_id"]
    _next(ws)  # roster_update
    return session_id


def _join_session(
    ws,
    session_id: str,
    player_id: str = "client1",
    port: int = 50011,
    rejoin_join_index: int | None = None,
) -> int:
    """Client side of the handshake: returns the assigned join_index."""
    _send(
        ws,
        "session_join",
        session_id=session_id,
        player_id=player_id,
        ip="127.0.0.1",
        port=port,
        rejoin_join_index=rejoin_join_index,
    )
    join_index = _next(ws)["join_index"]
    _next(ws)  # roster_update
    return join_index


def _active_session(session_id: str = "active-1") -> None:
    """A session already in game, as left behind by a host migration."""
    roster = GlobalRoster()
    roster.add_player(
        RosterEntry(player_id="player2", host="10.0.0.2", udp_port=50010, join_index=1)
    )
    lobby_manager.register_active_session(session_id, roster, next_join_index=2)


def test_the_handshake_creates_the_session_and_numbers_the_joiners():
    """join_index is the total order the election later relies on."""
    with client.websocket_connect("/lobby") as host_ws:
        _send(
            host_ws, "session_create", player_id="host1", ip="127.0.0.1", udp_port=50010
        )

        created = _next(host_ws)
        assert created["message_type"] == MessageType.SESSION_CREATED
        assert created["session_id"]
        assert created["join_index"] == 0

        players = _next(host_ws)["roster"]["players"]
        assert [p["player_id"] for p in players] == ["player1"]
        assert players[0]["is_host"] is True

        with client.websocket_connect("/lobby") as first:
            assert _join_session(first, created["session_id"], player_id="c1") == 1
            assert len(_next(host_ws)["roster"]["players"]) == 2

            with client.websocket_connect("/lobby") as second:
                assert (
                    _join_session(
                        second, created["session_id"], player_id="c2", port=50012
                    )
                    == 2
                )


def test_a_session_refuses_the_fifth_player():
    """The roster, the sprites and the staggered election timers are all built
    for four: the lobby must turn the fifth away instead of half-admitting it."""
    with client.websocket_connect("/lobby") as host_ws:
        session_id = _create_session(host_ws)

        with (
            client.websocket_connect("/lobby") as c1,
            client.websocket_connect("/lobby") as c2,
        ):
            with client.websocket_connect("/lobby") as c3:
                for index, ws in enumerate((c1, c2, c3), start=1):
                    assert _join_session(
                        ws, session_id, player_id=f"c{index}", port=50010 + index
                    )

                with client.websocket_connect("/lobby") as too_many:
                    _send(
                        too_many,
                        "session_join",
                        session_id=session_id,
                        player_id="c4",
                        ip="127.0.0.1",
                        port=50020,
                    )
                    refusal = _next(too_many)

    assert refusal["message_type"] == MessageType.SESSION_JOIN_REJECTED
    assert "4" in refusal["reason"]
    assert len(lobby_manager.get_roster(session_id).get_all_players()) == 4


def test_game_start_is_broadcast_and_marks_the_session_active():
    with client.websocket_connect("/lobby") as host_ws:
        session_id = _create_session(host_ws)

        with client.websocket_connect("/lobby") as client_ws:
            _join_session(client_ws, session_id)
            _next(host_ws)  # roster_update caused by the join

            assert not lobby_manager.is_active(session_id)
            _send(host_ws, "game_start", session_id=session_id)

            for ws in (host_ws, client_ws):
                assert _next(ws)["message_type"] == MessageType.GAME_START
            assert lobby_manager.is_active(session_id)


def test_a_recreated_session_keeps_its_id_and_lets_a_node_rejoin_mid_game():
    """A crashed node comes back with its cached id and must land straight
    in the running game, without waiting for a start that already happened."""
    with client.websocket_connect("/lobby") as host_ws:
        _send(
            host_ws,
            "session_recreate",
            session_id="restored-session-abc",
            next_join_index=2,
            host_ip="192.168.1.10",
            host_udp_port=50010,
            host_join_index=1,
            reserved_join_indices=[2],
        )

        ack = _next(host_ws)
        assert ack["message_type"] == MessageType.SESSION_CREATED
        assert ack["session_id"] == "restored-session-abc"

        host_entry = lobby_manager.get_roster("restored-session-abc").get_host()
        assert (host_entry.host, host_entry.join_index) == ("192.168.1.10", 1)

        with client.websocket_connect("/lobby") as rejoining:
            assert (
                _join_session(
                    rejoining,
                    "restored-session-abc",
                    player_id="player3",
                    port=49500,
                    rejoin_join_index=2,
                )
                == 2
            )

            game_start = _next(rejoining)
            assert game_start["message_type"] == MessageType.GAME_START
            assert game_start["session_id"] == "restored-session-abc"


def test_rejoins_reclaim_reserved_player_slots_without_exceeding_the_player_limit():
    with client.websocket_connect("/lobby") as host_ws:
        _send(
            host_ws,
            "session_recreate",
            session_id="migrated-session",
            next_join_index=3,
            host_ip="192.168.1.10",
            host_udp_port=50010,
            host_join_index=1,
            reserved_join_indices=[0, 2],
        )
        _next(host_ws)

        with client.websocket_connect("/lobby") as former_host:
            assert (
                _join_session(
                    former_host,
                    "migrated-session",
                    player_id="player1",
                    port=49500,
                    rejoin_join_index=0,
                )
                == 0
            )
            _next(former_host)  # game_start

            with client.websocket_connect("/lobby") as other_rejoin:
                assert (
                    _join_session(
                        other_rejoin,
                        "migrated-session",
                        player_id="player3",
                        port=49501,
                        rejoin_join_index=2,
                    )
                    == 2
                )
                _next(other_rejoin)  # game_start

                with client.websocket_connect("/lobby") as fourth_player:
                    assert (
                        _join_session(
                            fourth_player,
                            "migrated-session",
                            player_id="new-player",
                            port=49502,
                        )
                        == 3
                    )
                    _next(fourth_player)  # game_start

                    with client.websocket_connect("/lobby") as too_many:
                        _send(
                            too_many,
                            "session_join",
                            session_id="migrated-session",
                            player_id="fifth",
                            ip="127.0.0.1",
                            port=49503,
                        )
                        refusal = _next(too_many)

    assert refusal["message_type"] == MessageType.SESSION_JOIN_REJECTED
    roster = lobby_manager.get_roster("migrated-session")
    assert [
        (entry.player_id, entry.join_index) for entry in roster.get_all_players()
    ] == [
        ("player1", 0),
        ("player2", 1),
        ("player3", 2),
        ("player4", 3),
    ]


def test_a_client_that_leaves_disappears_from_everyone_else_roster():
    """Also once the game has started: the post-victory lobby runs on a
    session already marked active."""
    with client.websocket_connect("/lobby") as host_ws:
        session_id = _create_session(host_ws)

        with client.websocket_connect("/lobby") as client_ws:
            join_index = _join_session(client_ws, session_id)
            assert len(_next(host_ws)["roster"]["players"]) == 2

            _send(
                client_ws, "session_leave", session_id=session_id, join_index=join_index
            )
            roster = _next(host_ws)
            _next(client_ws)  # the leaver gets the broadcast too

    assert roster["message_type"] == MessageType.ROSTER_UPDATE
    assert [p["player_id"] for p in roster["roster"]["players"]] == ["player1"]

    _active_session()
    with client.websocket_connect("/lobby") as ws:
        join_index = _join_session(ws, "active-1", player_id="player3", port=49500)
        _next(ws)  # game_start

        _send(ws, "session_leave", session_id="active-1", join_index=join_index)
        roster = _next(ws)

    assert [p["player_id"] for p in roster["roster"]["players"]] == ["player2"]


def test_the_host_leaving_closes_the_room_for_everyone():
    """The room has no meaning without its host: the others are sent back to
    the menu and the session is dropped entirely."""
    with client.websocket_connect("/lobby") as host_ws:
        session_id = _create_session(host_ws)

        with client.websocket_connect("/lobby") as client_ws:
            _join_session(client_ws, session_id)
            _next(host_ws)  # roster_update caused by the join

            _send(host_ws, "session_leave", session_id=session_id, join_index=0)
            closed = _next(client_ws)
            _next(host_ws)  # the host gets its own broadcast too

    assert closed["message_type"] == MessageType.SESSION_CLOSED
    assert closed["session_id"] == session_id
    assert not lobby_manager.has_session(session_id)


def test_a_dropped_connection_does_not_evict_anyone():
    """A socket dying is not a departure: it also happens on a crash or during
    a host migration, where membership belongs to the host."""
    with client.websocket_connect("/lobby") as host_ws:
        session_id = _create_session(host_ws)

        with client.websocket_connect("/lobby") as client_ws:
            _join_session(client_ws, session_id)
            _next(host_ws)  # roster_update caused by the join

    players = {
        e.player_id for e in lobby_manager.get_roster(session_id).get_all_players()
    }
    assert players == {"player1", "player2"}
