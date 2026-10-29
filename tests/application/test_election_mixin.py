"""ElectionMixin: claiming the host role, collecting the quorum, promoting."""

import json
import time

import pytest

from distributed_smb.application.election import SelfElected
from distributed_smb.application.node_controller import NodeController
from distributed_smb.domain.entity import DestructibleBlock, Player
from distributed_smb.domain.world import EnvironmentalState, WorldState
from distributed_smb.network.transport.udp import UdpHandler
from distributed_smb.shared.config import (
    ELECTION_CLAIM_TIMEOUT_S,
    GAME_EVENT_WS_PORT,
    HOST_UDP_PORT,
)
from distributed_smb.shared.enums import MessageType, PlayerRole
from distributed_smb.shared.messages.election import NewHostClaim, ReconnectionAck
from distributed_smb.shared.messages.session import SessionRecreate
from distributed_smb.shared.messages.sync import WorldStateSnapshot
from distributed_smb.shared.roster import GlobalRoster
from tests.application.election_harness import (
    RECONNECTION_ACK,
    SESSION,
    FakeWsHandler,
    SpyLobbyService,
    SpyUdpHandler,
    ack,
    awaiting_acks,
    claim,
    make_controller,
    peer,
)

pytestmark = pytest.mark.usefixtures("no_real_lobby_relaunch")


def test_a_sole_survivor_promotes_immediately_without_claiming():
    """With nobody left to ack there is no quorum to wait for, so the election
    ends in the same state a direct promotion would reach."""
    nc, broker = make_controller(with_host_in_roster=True)

    try:
        nc._on_self_elected(SelfElected(my_ip="10.0.0.2"))

        assert nc._promotion_done is True
        assert nc.role is PlayerRole.HOST
        assert nc.engine.is_authoritative is True
        assert broker.promoted_port == GAME_EVENT_WS_PORT
        assert nc.roster.get_player("player1") is None  # the crashed host is gone
        assert nc.roster.get_host().player_id == "player2"

        broker.promoted_port = None
        nc._promote_to_host()
        assert broker.promoted_port is None  # promoting twice does nothing
    finally:
        nc.udp_handler.close_socket()


def test_with_surviving_peers_the_claim_is_broadcast_and_the_promotion_deferred():
    nc, broker = make_controller()
    nc.roster.add_player(peer("player3", "10.0.0.3", 50012, 2))

    before = time.time()
    nc._on_self_elected(SelfElected(my_ip="10.0.0.2"))

    assert nc._promotion_done is False
    assert nc.role is PlayerRole.CLIENT
    assert broker.last_message_type() == MessageType.NEW_HOST_CLAIM.value
    assert nc._pending_election_acks == {"10.0.0.3"}
    # Without the deadline an unresponsive peer would block the promotion forever.
    assert nc._election_claim_deadline >= before + ELECTION_CLAIM_TIMEOUT_S - 0.1


def test_promotion_waits_for_every_pending_ack_and_ignores_spurious_ones():
    """Promoting on a partial quorum leaves a peer following the old host; an
    unknown sender or a relay echo must not count towards it."""
    nc, _ = make_controller()
    awaiting_acks(nc, "10.0.0.3", "10.0.0.4")

    try:
        nc._on_election_ack(ack("10.0.0.99"))
        nc._on_election_ack(ack("10.0.0.3"))

        assert nc._promotion_done is False
        assert nc.role is PlayerRole.CLIENT
        assert nc._pending_election_acks == {"10.0.0.4"}

        nc._on_election_ack(ack("10.0.0.4"))

        assert nc._promotion_done is True
        assert nc.role is PlayerRole.HOST
        assert nc.engine.is_authoritative is True

        promoted_role = nc.role
        nc._on_election_ack(ack("10.0.0.4"))  # relay echo, after the fact
        assert nc.role is promoted_role
    finally:
        nc.udp_handler.close_socket()


def test_the_claim_goes_out_over_udp_to_every_surviving_peer():
    """The relay can die with the crashed host, so UDP must carry it too."""
    nc, _ = make_controller()
    nc.roster.add_player(peer("player3", "10.0.0.3", 50013, 2))
    nc.udp_handler = spy = SpyUdpHandler()

    nc._on_self_elected(SelfElected(my_ip="10.0.0.2"))

    # The crashed host is deliberately excluded from _known_client_peers().
    assert {(host, port) for _, host, port in spy.sent} == {("10.0.0.3", 50013)}
    for payload, _, _ in spy.sent:
        decoded = nc.serializer.decode_message(payload)
        assert isinstance(decoded, NewHostClaim)
        assert decoded.claimer_ip == "10.0.0.2"


def test_following_a_claim_acks_it_on_both_transports_and_arms_the_fallback():
    """The claimer needs the ack for its quorum — over the relay and directly
    over UDP, since the relay may have died with the crashed host — and the
    follower needs the deadline in case the ReconnectionAck never arrives."""
    nc, broker = make_controller(
        local_ip="10.0.0.3", local_player_id="player3", join_index=2
    )
    nc.roster.add_player(peer("player2", "10.0.0.2", 50012, 1))
    nc.election_coordinator.start_election({"10.0.0.2"})
    nc.election_coordinator.set_election_timer(time.time())
    nc.udp_handler = spy = SpyUdpHandler()

    before = time.time()
    nc._on_new_host_claim(claim())

    assert broker.last_message_type() == MessageType.ELECTION_ACK.value

    assert len(spy.sent) == 1
    payload, host, port = spy.sent[0]
    assert (host, port) == ("10.0.0.2", 50012)
    assert nc.serializer.decode_message(payload).from_ip == "10.0.0.3"

    assert nc._following_host_ip == "10.0.0.2"
    assert nc._following_since >= before


def test_the_reconnection_ack_reaches_the_survivors_and_nobody_else(monkeypatch):
    sent: list[tuple[bytes, str]] = []
    monkeypatch.setattr(
        UdpHandler,
        "send_packet_nowait",
        lambda self, payload, host, port: sent.append((payload, host)),
    )
    nc, broker = make_controller()
    nc.roster.add_player(peer("player3", "10.0.0.3", 50013, 2))

    nc._promote_to_host()

    to_survivor = [
        nc.serializer.decode_message(p) for p, host in sent if host == "10.0.0.3"
    ]
    acks = [msg for msg in to_survivor if isinstance(msg, ReconnectionAck)]
    assert len(acks) == 1
    assert acks[0].new_host_ip == "10.0.0.2"
    relayed = [json.loads(payload).get("message_type") for payload in broker.sent]
    assert MessageType.RECONNECTION_ACK.value in relayed

    alone, alone_broker = make_controller()
    alone._promote_to_host()
    relayed = [json.loads(payload).get("message_type") for payload in alone_broker.sent]
    assert MessageType.RECONNECTION_ACK.value not in relayed


def test_our_own_claim_echoed_back_by_the_relay_is_ignored():
    nc, broker = make_controller(local_ip="10.0.0.2", join_index=1)

    nc._on_new_host_claim(claim(ip="10.0.0.2"))

    assert not broker.sent


def test_the_promoted_host_re_registers_the_session_for_rejoining_nodes(monkeypatch):
    """Runs on a background thread, so join it before asserting."""
    nc, _ = make_controller()
    nc.roster.add_player(peer("player3", "10.0.0.3", 50012, 2))
    nc.engine.world_state.add_player(Player(player_id="player1", x=250, y=100, join_index=0))
    nc.lobby_service = SpyLobbyService()
    fake_ws = FakeWsHandler()
    monkeypatch.setattr(
        "distributed_smb.application.election.mixin.WsHandler", lambda *a, **kw: fake_ws
    )

    nc._promote_to_host()
    nc._relaunch_thread.join(timeout=2.0)

    assert nc.lobby_service.launched
    assert nc.ws_handler is fake_ws
    assert len(fake_ws.sent) == 1
    message = fake_ws.sent[0]
    assert isinstance(message, SessionRecreate)
    assert message.session_id == SESSION
    assert message.next_join_index == 3
    assert message.reserved_join_indices == [0, 2]
    nc.udp_handler.close_socket()


def _following_a_new_host() -> NodeController:
    """Node C, after A crashed and B claimed: B is still is_host=False here."""
    nc, _ = make_controller(
        local_ip="10.0.0.3", local_player_id="player3", join_index=2
    )
    nc.roster.add_player(peer("player2", "10.0.0.2", 50011, 1))
    nc.election_triggered = True
    return nc


def test_reconnection_ack_resets_the_election_and_repoints_the_roster():
    """A stale election state would blind the node to the next crash.
    The relay echoes the ack back, so a second one must change nothing."""
    nc = _following_a_new_host()
    assert nc.roster.get_host().player_id == "player1"  # stale view: the old host

    nc._on_reconnection_ack(RECONNECTION_ACK)

    assert nc.election_triggered is False
    assert nc.election_coordinator is None
    assert nc.timeout_watcher is not None
    assert nc.roster.get_player("player1") is None
    assert nc.roster.get_host().player_id == "player2"

    nc._on_reconnection_ack(RECONNECTION_ACK)

    assert nc.roster.get_player("player1") is None
    assert nc.roster.get_host().player_id == "player2"


def test_reconnection_ack_resyncs_environment_from_buffered_snapshot():
    """reconcile() never touches blocks/power-ups/gates, so an event
    lost while the old host died would diverge forever without this."""
    nc = _following_a_new_host()
    nc.engine.world_state.environment.destructible_blocks = [
        DestructibleBlock(x=100, y=200, destroyed=True)  # predicted locally, and wrong
    ]
    last_good = EnvironmentalState(
        destructible_blocks=[DestructibleBlock(x=100, y=200, destroyed=False)]
    )
    nc.env_state_buffer.update(
        WorldStateSnapshot(
            sequence_number=42,
            world_state=WorldState(sequence_number=42, environment=last_good),
        )
    )

    nc._on_reconnection_ack(RECONNECTION_ACK)

    assert nc.engine.world_state.environment.destructible_blocks[0].destroyed is False


def test_merging_a_roster_broadcast_adds_unknown_peers_and_nothing_else():
    """A peer nobody knows about makes every survivor a sole survivor: on the
    next crash they would all promote themselves."""
    nc, _ = make_controller()  # roster: player1 (host), player2 (self)
    assert nc._known_client_peers() == set()
    player1 = nc.roster.get_player("player1")

    broadcast = GlobalRoster()
    broadcast.add_player(peer("player1", "10.0.0.1", HOST_UDP_PORT, 0, is_host=True))
    broadcast.add_player(peer("player2", "10.0.0.2", 50011, 1))
    broadcast.add_player(peer("player4", "10.0.0.4", 50013, 3))

    nc._merge_roster(broadcast)
    nc._merge_roster(broadcast)

    assert nc._known_client_peers() == {"10.0.0.4"}
    assert len(nc.roster.get_all_players()) == 3
    assert nc.roster.get_player("player1") == player1
