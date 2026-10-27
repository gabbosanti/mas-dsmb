"""Election state machine: staggered timer-based distributed host election."""

from dataclasses import dataclass
from enum import Enum


class ElectionState(Enum):
    """State of the election process."""

    IDLE = "idle"
    """No election in progress; waiting for host timeout."""

    ELECTION_PENDING = "election_pending"
    """Election started; timer counting down before self-election."""

    CLAIMED = "claimed"
    """Self-elected as host; awaiting acknowledgment from peers."""

    FOLLOWER = "follower"
    """Following a host elected by another peer."""


@dataclass(slots=True)
class ElectionEvent:
    """Base class for election events emitted by ElectionCoordinator.tick()."""

    pass


@dataclass(slots=True)
class SelfElected(ElectionEvent):
    """Emitted when self-election timer expires without override from lower-indexed peer."""

    my_ip: str


@dataclass(slots=True)
class FollowingHost(ElectionEvent):
    """Emitted when a valid NewHostClaim received from lower-indexed peer."""

    claimer_ip: str
    claimer_join_index: int


class ElectionCoordinator:
    """Staggered timer election coordinator."""

    def __init__(
        self, join_index: int, my_ip: str, timeout_base_s: float, timeout_delta_s: float
    ):
        """Initialize the election coordinator."""
        self.join_index = join_index
        self.my_ip = my_ip
        self.timeout_base_s = timeout_base_s
        self.timeout_delta_s = timeout_delta_s

        self.state = ElectionState.IDLE
        self.current_host_ip: str | None = None
        self.current_host_join_index: int | None = None
        self.known_peers: set[str] = set()
        self.election_timer_expiry: float | None = None

    def start_election(self, known_peers: set[str]) -> None:
        """Begin staggered election timer."""
        self.state = ElectionState.ELECTION_PENDING
        self.known_peers = known_peers.copy()
        # Timer fires at: current_time + (T_ELECTION_BASE_S + join_index * T_ELECTION_DELTA_S)
        # Caller is responsible for setting election_timer_expiry before next tick().
        self.current_host_ip = None
        self.current_host_join_index = None

    def set_election_timer(self, current_time: float) -> None:
        """Set the election timer to fire after the staggered delay."""
        delay = self.timeout_base_s + self.join_index * self.timeout_delta_s
        self.election_timer_expiry = current_time + delay

    def tick(self, current_time: float) -> ElectionEvent | None:
        """Process election state machine tick."""
        if self.state == ElectionState.ELECTION_PENDING:
            if self.election_timer_expiry is None:
                raise ValueError(
                    """election_timer_expiry must be set before calling tick()
                    in ELECTION_PENDING state"""
                )
            if current_time >= self.election_timer_expiry:
                self.state = ElectionState.CLAIMED
                return SelfElected(my_ip=self.my_ip)
        return None

    def on_new_host_claim(
        self, claimer_join_index: int, claimer_ip: str
    ) -> ElectionEvent | None:
        """Process incoming NewHostClaim from a peer."""
        if claimer_join_index >= self.join_index:
            # Claimer has higher or equal JoinIndex: we are the rightful host, ignore.
            return None

        # Claimer has lower JoinIndex: they win.
        self.state = ElectionState.FOLLOWER
        self.current_host_ip = claimer_ip
        self.current_host_join_index = claimer_join_index
        self.election_timer_expiry = None
        return FollowingHost(
            claimer_ip=claimer_ip, claimer_join_index=claimer_join_index
        )
