"""Failure detector and leader election, without any networking."""

import time

import pytest

from distributed_smb.application.election import (
    ElectionCoordinator,
    ElectionState,
    FollowingHost,
    HostTimeoutWatcher,
    SelfElected,
)
from distributed_smb.shared.config import (
    T_ELECTION_BASE_S,
    T_ELECTION_DELTA_S,
)


def test_the_watcher_only_fires_past_the_interval_and_any_snapshot_clears_it():
    watcher = HostTimeoutWatcher(timeout_s=5.0)
    t0 = time.time()

    assert watcher.tick(t0) is False  # nothing received yet

    watcher.reset(t0)
    assert watcher.tick(t0 + 2.0) is False
    assert watcher.tick(t0 + 5.0) is False  # exactly at the boundary
    assert watcher.tick(t0 + 5.001) is True

    watcher.reset(t0 + 5.5)  # a snapshot finally arrived
    assert watcher.tick(t0 + 8.0) is False


def test_start_election_transitions_from_idle_to_pending():
    coordinator = ElectionCoordinator(
        join_index=0,
        my_ip="127.0.0.1",
        timeout_base_s=T_ELECTION_BASE_S,
        timeout_delta_s=T_ELECTION_DELTA_S,
    )
    assert coordinator.state == ElectionState.IDLE

    coordinator.start_election({"127.0.0.2", "127.0.0.3"})

    assert coordinator.state == ElectionState.ELECTION_PENDING
    assert coordinator.known_peers == {"127.0.0.2", "127.0.0.3"}


def test_the_staggered_timer_fires_after_base_plus_join_index_delta():
    coordinator = ElectionCoordinator(
        join_index=2, my_ip="192.168.1.1", timeout_base_s=0.5, timeout_delta_s=0.3
    )
    t0 = 1000.0
    coordinator.start_election(set())
    coordinator.set_election_timer(t0)

    assert coordinator.election_timer_expiry == pytest.approx(t0 + 1.1)
    assert coordinator.tick(t0 + 1.0) is None

    event = coordinator.tick(t0 + 1.1)

    assert isinstance(event, SelfElected)
    assert event.my_ip == "192.168.1.1"
    assert coordinator.state == ElectionState.CLAIMED


def test_lower_join_index_wins():
    """Both survivors start at the same instant: only the lower index
    self-elects, the other is still waiting when it does."""
    first = ElectionCoordinator(
        join_index=0, my_ip="10.0.0.1", timeout_base_s=0.5, timeout_delta_s=0.3
    )
    second = ElectionCoordinator(
        join_index=1, my_ip="10.0.0.2", timeout_base_s=0.5, timeout_delta_s=0.3
    )
    t0 = 1000.0
    for coordinator, peer_ip in ((first, "10.0.0.2"), (second, "10.0.0.1")):
        coordinator.start_election({peer_ip})
        coordinator.set_election_timer(t0)

    # first fires at 0.5, second only at 0.8
    assert isinstance(first.tick(t0 + 0.5), SelfElected)
    assert first.state == ElectionState.CLAIMED
    assert second.tick(t0 + 0.5) is None
    assert second.state == ElectionState.ELECTION_PENDING


def _node(join_index: int, my_ip: str) -> ElectionCoordinator:
    return ElectionCoordinator(
        join_index=join_index, my_ip=my_ip, timeout_base_s=0.5, timeout_delta_s=0.3
    )


def test_a_claim_wins_only_if_it_comes_from_a_lower_join_index():
    """Lower index wins, even against a node that already claimed: that is
    what keeps two survivors from both believing they are the host."""
    follower = _node(join_index=2, my_ip="10.0.0.3")
    follower.start_election({"10.0.0.1", "10.0.0.2"})

    event = follower.on_new_host_claim(claimer_join_index=0, claimer_ip="10.0.0.1")
    assert isinstance(event, FollowingHost)
    assert (event.claimer_ip, event.claimer_join_index) == ("10.0.0.1", 0)
    assert follower.state == ElectionState.FOLLOWER
    assert follower.current_host_ip == "10.0.0.1"

    claimer = _node(join_index=1, my_ip="10.0.0.2")
    claimer.state = ElectionState.CLAIMED
    claimer.current_host_join_index = 1

    assert (
        claimer.on_new_host_claim(claimer_join_index=2, claimer_ip="10.0.0.3") is None
    )
    assert claimer.state == ElectionState.CLAIMED

    event = claimer.on_new_host_claim(claimer_join_index=0, claimer_ip="10.0.0.1")
    assert isinstance(event, FollowingHost)
    assert claimer.state == ElectionState.FOLLOWER
    assert claimer.current_host_ip == "10.0.0.1"


def test_a_claim_arriving_before_our_timer_prevents_a_second_election():
    """Without this, the slower candidate would self-elect anyway and the
    session would end up with two hosts."""
    coordinator = _node(join_index=1, my_ip="10.0.0.2")
    t0 = 1000.0
    coordinator.start_election({"10.0.0.1"})
    coordinator.set_election_timer(t0)

    event = coordinator.on_new_host_claim(claimer_join_index=0, claimer_ip="10.0.0.1")
    assert isinstance(event, FollowingHost)
    assert coordinator.state == ElectionState.FOLLOWER

    assert coordinator.tick(t0 + 0.8) is None  # past our own expiry
    assert coordinator.state == ElectionState.FOLLOWER
