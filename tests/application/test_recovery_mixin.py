from distributed_smb.application.node_controller import NodeController
from distributed_smb.shared.enums import NodeState
from distributed_smb.shared.roster import RosterEntry
from distributed_smb.shared.session_metadata import CachedPeer, SessionMetadata


class _SpyRecoveryProber:
    def __init__(self, host_ip=None):
        self.calls = []
        self.host_ip = host_ip

    def find_current_host(self, session_id, requester_ip, peers, timeout_per_peer):
        self.calls.append((session_id, requester_ip, peers, timeout_per_peer))
        return self.host_ip


def test_recovery_gives_up_and_cleans_up_when_it_cannot_find_the_session(monkeypatch):
    """No metadata, no peers to ask, or nobody answering: all three must leave
    the node in RECOVERING with no stale file behind."""
    deleted: list[bool] = []
    monkeypatch.setattr(
        "distributed_smb.application.recovery.mixin.delete_session_metadata",
        lambda: deleted.append(True),
    )

    def attempt(metadata, host_ip=None):
        controller = NodeController()
        controller.recovery_prober = _SpyRecoveryProber(host_ip=host_ip)
        monkeypatch.setattr(
            "distributed_smb.application.recovery.mixin.read_session_metadata",
            lambda: metadata,
        )
        return controller, controller.attempt_recovery()

    controller, result = attempt(metadata=None)
    assert result is None
    assert controller.lifecycle.state is NodeState.RECOVERING
    assert controller.recovery_prober.calls == []  # nothing to probe
    assert deleted == []  # no file to remove either

    without_peers = SessionMetadata(
        session_id="session-abc", local_player_id="player-1", peers=[]
    )
    controller, result = attempt(metadata=without_peers)
    assert result is None
    assert controller.recovery_prober.calls == []
    assert deleted == [True]

    with_peers = SessionMetadata(
        session_id="session-abc",
        local_player_id="player-1",
        peers=[CachedPeer(player_id="player-2", ip="127.0.0.2", join_index=1)],
    )
    controller, result = attempt(metadata=with_peers)
    assert result is None
    assert controller.recovery_prober.calls  # this time it did probe
    assert deleted == [True, True]


def test_attempt_recovery_success_sets_remote_host_and_session_id(monkeypatch):
    controller = NodeController()
    controller.recovery_prober = _SpyRecoveryProber(host_ip="10.0.0.2")
    controller.local_ip = "127.0.0.5"

    metadata = SessionMetadata(
        session_id="session-abc",
        local_player_id="player-1",
        peers=[CachedPeer(player_id="player-2", ip="127.0.0.2", join_index=1)],
    )
    monkeypatch.setattr(
        "distributed_smb.application.recovery.mixin.read_session_metadata",
        lambda: metadata,
    )

    result = controller.attempt_recovery()

    assert result == "10.0.0.2"
    assert controller.remote_host == "10.0.0.2"
    assert controller.session_id == "session-abc"


def test_write_session_metadata_excludes_self(monkeypatch):
    controller = NodeController()
    controller.session_id = "session-abc"
    controller.local_player_id = "player-1"
    controller.roster.players = [
        RosterEntry("player-1", "127.0.0.1", 50010, 0, is_host=True),
        RosterEntry("player-2", "127.0.0.2", 50011, 1, is_host=False),
    ]

    written = []

    def fake_write(metadata):
        written.append(metadata)

    monkeypatch.setattr(
        "distributed_smb.application.recovery.mixin.write_session_metadata", fake_write
    )

    controller._write_session_metadata()

    assert len(written) == 1
    metadata = written[0]
    assert metadata.session_id == "session-abc"
    assert metadata.local_player_id == "player-1"
    assert metadata.peers == [
        CachedPeer(player_id="player-2", ip="127.0.0.2", join_index=1)
    ]
