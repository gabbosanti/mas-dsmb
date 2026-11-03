"""Tests for main.py: the composition root and the startup flow it drives."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from distributed_smb.main import _detect_local_ip, _try_recover_session, main
from distributed_smb.shared.config import DEFAULT_HOST, LOBBY_WS_PORT
from distributed_smb.shared.enums import PlayerRole
from distributed_smb.shared.session_metadata import CachedPeer, SessionMetadata


def test_main_resolves_the_local_ip_and_points_the_client_at_the_host():
    """The IP ends up in the roster every peer uses, so a wrong one makes the
    node unreachable for the whole session."""
    assert main(role=PlayerRole.HOST, local_ip="10.0.0.5").local_ip == "10.0.0.5"

    with patch("distributed_smb.main._detect_local_ip", return_value="192.168.1.42"):
        assert main(role=PlayerRole.HOST).local_ip == "192.168.1.42"

    unreachable = MagicMock()
    unreachable.__enter__.return_value = unreachable
    unreachable.connect.side_effect = OSError("network unreachable")
    with patch("distributed_smb.main.socket.socket", return_value=unreachable):
        assert _detect_local_ip() == DEFAULT_HOST

    client = main(role=PlayerRole.CLIENT, host_ip="192.168.1.10")
    assert (client.ws_handler.host, client.ws_handler.port) == (
        "192.168.1.10",
        LOBBY_WS_PORT,
    )
    assert client.remote_host == "192.168.1.10"


def test_main_restores_the_saved_player_slot_for_a_rejoining_client():
    metadata = SessionMetadata(
        session_id="session-abc",
        local_player_id="player1",
        peers=[],
        join_index=0,
    )
    with patch("distributed_smb.main.read_session_metadata", return_value=metadata):
        client = main(
            role=PlayerRole.CLIENT,
            host_ip="192.168.1.10",
            local_ip="192.168.1.20",
            session_id="session-abc",
        )

    assert client.rejoin_join_index == 0
    assert client.join_index == 0
    assert client.local_player_id == "player1"


class _FakeConnection:
    def __init__(self, calls):
        self._calls = calls

    def close(self):
        self._calls.append("close")

    def close_socket(self):
        self._calls.append("close_socket")


class _FakeController:
    """Stands in for the wired NodeController that main() drives."""

    def __init__(self, calls, *, use_discovery=False, outcomes=("quit",)):
        self.role = PlayerRole.HOST
        self.roster = object()
        self.ws_handler = _FakeConnection(calls)
        self.udp_handler = _FakeConnection(calls)
        self.lobby_container_manager = SimpleNamespace(
            stop=lambda: calls.append("containers_stop")
        )
        self.game_event_handler = SimpleNamespace(
            connect=lambda: calls.append("ws_connect")
        )
        self.use_discovery = use_discovery
        self.lobby_session_ids: list[str] = []
        self.start_requested_seen = None
        self._calls = calls
        self._outcomes = iter(outcomes)

    def lobby_phase(self, *, session_id, on_update, start_requested):
        self._calls.append("lobby")
        self.lobby_session_ids.append(session_id)
        self.start_requested_seen = start_requested()
        return self.roster

    def replay_lobby_phase(self, *, on_update, start_requested):
        self._calls.append("replay_lobby")
        return self.roster

    def run(self):
        self._calls.append("run")
        return next(self._outcomes)


def _fake_screen_class(calls, *, start_requested=True):
    class _FakeLobbyScreen:
        def __init__(self):
            self.start_requested = start_requested

        def prompt_session_id(self, *, initial_session_id):
            calls.append("prompt_session_id")
            return "abc123"

        def prompt_join_details(self, **kwargs):
            calls.append("prompt_join_details")
            return ("10.0.0.5", "abc123")

        def render(self, **kwargs):
            return True

        def play_game_start_transition(self, **kwargs):
            calls.append("transition")
            return True

        def close(self):
            calls.append("screen_close")

    return _FakeLobbyScreen


def _run_main(controller, calls, *, start_requested=True, **kwargs):
    with patch("distributed_smb.main.build_controller", return_value=controller):
        with patch(
            "distributed_smb.main.LobbyScreen",
            _fake_screen_class(calls, start_requested=start_requested),
        ):
            return main(run_app=True, **kwargs)


def test_recovery_is_skipped_without_metadata_or_if_the_player_closes_it():
    with patch("distributed_smb.main.read_session_metadata", return_value=None):
        assert _try_recover_session("127.0.0.1") is None

    metadata = SessionMetadata(
        session_id="session-abc",
        local_player_id="player-1",
        peers=[CachedPeer(player_id="player-2", ip="127.0.0.2", join_index=1)],
    )
    closed_by_user = MagicMock()
    closed_by_user.render.return_value = False

    with patch("distributed_smb.main.read_session_metadata", return_value=metadata):
        assert _try_recover_session("127.0.0.1", lobby_screen=closed_by_user) is None

    closed_by_user.close.assert_not_called()  # a screen we were given is not ours to close


def test_recovery_probes_the_cached_peers_and_returns_the_session_it_found():
    """The default prober is used when none is injected, and the screen the
    helper opened on its own must be closed again."""
    metadata = SessionMetadata(
        session_id="session-abc",
        local_player_id="player-1",
        peers=[CachedPeer(player_id="player-2", ip="127.0.0.2", join_index=1)],
    )

    with patch("distributed_smb.main.read_session_metadata", return_value=metadata):
        with patch("distributed_smb.main.LobbyScreen") as screen_cls:
            screen = MagicMock()
            screen.render.return_value = True
            screen_cls.return_value = screen

            with patch("distributed_smb.main.RecoveryProber") as prober_cls:
                prober = MagicMock()
                prober.find_current_host.return_value = "10.0.0.2"
                prober_cls.return_value = prober

                result = _try_recover_session("127.0.0.1")

    assert result == ("10.0.0.2", "session-abc")
    prober.find_current_host.assert_called_once_with(
        "session-abc", "127.0.0.1", metadata.peers, timeout_per_peer=0.5
    )
    screen.close.assert_called_once()


def test_prober_fails_cleans_up_and_returns_none():
    """When prober finds no host, delete metadata and return None."""
    metadata = SessionMetadata(
        session_id="session-abc",
        local_player_id="player-1",
        peers=[CachedPeer(player_id="player-2", ip="127.0.0.2", join_index=1)],
    )

    mock_screen = MagicMock()
    mock_screen.render.return_value = True

    mock_prober = MagicMock()
    mock_prober.find_current_host.return_value = None

    deleted = []

    def fake_delete():
        deleted.append(True)

    with patch("distributed_smb.main.read_session_metadata", return_value=metadata):
        with patch("distributed_smb.main.delete_session_metadata", fake_delete):
            result = _try_recover_session(
                "127.0.0.1",
                lobby_screen=mock_screen,
                prober=mock_prober,
            )

    assert result is None
    assert deleted == [True]
