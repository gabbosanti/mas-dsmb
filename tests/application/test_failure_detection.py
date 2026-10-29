"""Failure detection on a client: is the host really gone, and what then."""

import time

import pytest

from distributed_smb.application.election import HostTimeoutWatcher
from distributed_smb.domain.entity import Player
from distributed_smb.network.transport.udp import UdpHandler
from distributed_smb.shared.config import (
    HOST_TIMEOUT_S,
    HOST_VERIFY_TIMEOUT_S,
    RECONNECTION_FALLBACK_TIMEOUT_S,
)
from distributed_smb.shared.enums import PlayerRole
from distributed_smb.shared.input import InputState
from distributed_smb.shared.messages.recovery import HostIdentityResponse
from tests.application.election_harness import FakeRecoveryProber, make_controller, peer

pytestmark = pytest.mark.usefixtures("no_real_lobby_relaunch")


def _host_looks_gone(monkeypatch, record: list | None = None):
    """A client whose host has been silent for longer than HOST_TIMEOUT_S."""
    nc, _ = make_controller()
    nc.timeout_watcher = HostTimeoutWatcher(timeout_s=HOST_TIMEOUT_S)
    nc.timeout_watcher.reset(time.time() - HOST_TIMEOUT_S - 0.1)
    monkeypatch.setattr(
        UdpHandler,
        "send_packet_nowait",
        lambda self, payload, host, port: record.append((host, port))
        if record is not None
        else None,
    )
    return nc


def test_a_snapshot_gap_probes_first_and_elects_only_if_nobody_answers(monkeypatch):
    """A gap can be a stall (GC, CPU contention) rather than a crash, so the
    host is probed first: an answer cancels the election, silence starts it."""
    probes: list[tuple[str, int]] = []
    answered = _host_looks_gone(monkeypatch, probes)

    answered._tick_election_state()

    assert answered.election_triggered is False
    assert answered._host_verify_deadline != 0.0
    assert probes == [(answered.remote_host, answered.remote_port)]

    answered._on_host_identity_response(
        HostIdentityResponse(
            session_id=answered.session_id, host_ip=answered.remote_host
        )
    )
    answered._tick_election_state()

    assert answered._host_verify_deadline == 0.0
    assert answered.election_triggered is False

    silent = _host_looks_gone(monkeypatch)
    silent._tick_election_state()
    silent._host_verify_deadline = time.time() - HOST_VERIFY_TIMEOUT_S - 0.1
    silent._tick_election_state()

    assert silent.election_triggered is True
    assert silent._host_verify_deadline == 0.0


def _following(since: float, found_ip: str | None = "10.0.0.2"):
    nc, _ = make_controller()
    nc.recovery_prober = FakeRecoveryProber(found_ip=found_ip)
    nc._following_host_ip = "10.0.0.2"
    nc._following_since = since
    return nc, nc.recovery_prober


def test_no_probe_unless_a_followed_host_stayed_silent_long_enough():
    """The probe is a last resort: only a pending follow whose
    ReconnectionAck never arrived may trigger it."""
    expired = time.time() - RECONNECTION_FALLBACK_TIMEOUT_S - 0.1

    too_early, prober = _following(since=time.time())
    too_early._tick_reconnection_fallback(time.time())
    assert not prober.calls
    assert too_early.reconnected is False

    already_back, prober = _following(since=expired)
    already_back.reconnected = True
    already_back._tick_reconnection_fallback(time.time())
    assert not prober.calls

    not_following, _ = make_controller()
    not_following.recovery_prober = FakeRecoveryProber(found_ip="10.0.0.2")
    not_following._tick_reconnection_fallback(time.time())
    assert not not_following.recovery_prober.calls


def test_the_probe_completes_the_reconnection_or_is_retried_next_tick():
    """The claimed host may not be ready yet, so a failed probe must keep the
    follow state alive instead of stalling there forever."""
    expired = time.time() - RECONNECTION_FALLBACK_TIMEOUT_S - 0.1

    found, prober = _following(since=expired)
    found._tick_reconnection_fallback(time.time())

    assert prober.calls
    assert found.reconnected is True
    assert found.remote_host == "10.0.0.2"
    assert found._following_host_ip is None

    not_ready, _ = _following(since=expired, found_ip=None)
    now = time.time()
    not_ready._tick_reconnection_fallback(now)

    assert not_ready.reconnected is False
    assert not_ready._following_host_ip == "10.0.0.2"
    assert not_ready._following_since == pytest.approx(now, abs=0.5)


def test_an_unanswered_claim_promotes_us_and_fully_evicts_the_silent_peer():
    """Evicted everywhere, not just from the roster: ghost entries used to
    survive in world_state and in the per-player input caches."""
    waiting, _ = make_controller()
    waiting._pending_election_acks = {"10.0.0.3"}
    waiting._election_claim_deadline = time.time() + 10.0

    waiting._tick_claim_deadline(time.time())

    assert waiting._promotion_done is False

    nc, _ = make_controller()
    nc.roster.add_player(peer("player3", "10.0.0.3", 50012, 2))
    nc.engine.world_state.add_player(Player(player_id="player3"))
    nc.cached_remote_inputs["player3"] = InputState()
    nc.last_remote_input_sequence["player3"] = 5
    nc.last_input_time["player3"] = time.time()
    nc._pending_election_acks = {"10.0.0.3"}
    nc._election_claim_deadline = time.time() - 0.1

    nc._tick_claim_deadline(time.time())

    assert nc._promotion_done is True
    assert nc.role is PlayerRole.HOST
    assert nc.roster.get_player("player3") is None
    assert nc.engine.world_state.get_player("player3") is None
    assert "player3" not in nc.cached_remote_inputs
    assert "player3" not in nc.last_remote_input_sequence
    assert "player3" not in nc.last_input_time
