DEFAULT_HOST = "127.0.0.1"
DEFAULT_UDP_PORT = 50000
HOST_UDP_PORT = 50010
UDP_MAX_PACKET_SIZE = 65535
DEFAULT_PACKET_DROP_RATE = 0.0
HOST_PLAYER_ID = "player1"


def player_id_for(join_index: int) -> str:
    """Return the canonical player ID for the given lobby join order (0-based)."""
    return f"player{join_index + 1}"


# Lobby WebSocket server
LOBBY_WS_PORT = 50002
LOBBY_WS_PATH = "/lobby"
LOBBY_WS_URL_TEMPLATE = "ws://{host}:{port}{path}"

# Game event WebSocket server (host → clients, reliable in-game events)
GAME_EVENT_WS_PORT = 50003
GAME_EVENT_WS_PATH = "/game-events"

# Session discovery (UDP broadcast)
DISCOVERY_UDP_PORT = 59099

# Player disconnect detection
UDP_INPUT_TIMEOUT = 10.0
"""Seconds without a UDP input packet before the host considers a peer gone
and evicts it (_check_player_disconnections). Kept well above the ~5.6s
worst-case stall observed on a single machine running 3 game processes: the
peer's own process can freeze for that long (CPU contention, not an actual
disconnect), during which it sends nothing — evicting it that fast turns a
transient stall into a permanent, unrecoverable loss of that player from the
authoritative simulation, since nothing re-adds it short of a full rejoin."""
GAME_EVENT_HEARTBEAT_INTERVAL = 5.0  # seconds between WebSocket heartbeat pings

# Lobby coordination timings
LOBBY_STARTUP_WAIT = 0.5  # seconds to wait for uvicorn to bind before connecting
LOBBY_TIMEOUT = 30.0  # seconds to wait for a lobby handshake reply before giving up

TICK_RATE = 60
TICK_INTERVAL = 1.0 / TICK_RATE

# Remote snapshot smoothing and loss-tolerance timings.
SNAPSHOT_TIMEOUT = 0.15
MAX_EXTRAPOLATION_TIME = 0.35

# Base resolution used as logical reference.
BASE_WINDOW_WIDTH = 640
BASE_WINDOW_HEIGHT = 480

# Global world/presentation scale factor.
WORLD_SCALE = 1.5

# === Election and Host Failure Detection ===
# Eventually perfect failure detector: timeout-based detection in asynchronous systems.
# False positives possible under high latency, but rare in normal conditions.
HOST_TIMEOUT_S = 5.0
"""Seconds without WorldStateSnapshot (UDP) before declaring host lost."""

# Staggered timer election: break ties without central coordinator.
# Nodes with lower JoinIndex win (deterministic total ordering via JoinIndex ≈ Lamport clock).
T_ELECTION_BASE_S = 0.5
"""Base delay (seconds) before node self-elects as host candidate."""

T_ELECTION_DELTA_S = 0.3
"""Additional delay per JoinIndex unit: T = T_ELECTION_BASE_S + join_index * T_ELECTION_DELTA_S."""

ELECTION_CLAIM_TIMEOUT_S = 2.0
"""Timeout (seconds) waiting for NewHostClaim from lower-indexed nodes."""

HOST_VERIFY_TIMEOUT_S = 10.0
"""Seconds to wait for a HostIdentityResponse before trusting a HOST_TIMEOUT_S
expiry and starting an election. A snapshot gap can come from a brief stall
(GC pause, CPU contention from other peers/containers on the same machine,
a network blip) rather than an actual crash — a direct probe confirms the
host is really gone before paying the cost of a full election. Set this high
(observed stalls on a single machine running 3 game processes reached ~5.6s)
rather than tuned tight, since a missed genuine crash just costs a few extra
seconds of detection, while a too-short window still lets stalls cascade
into a false, conflicting election — the far more disruptive failure mode."""

HOST_VERIFY_RESEND_INTERVAL_S = 0.5
"""Resend the verify probe at this cadence in case the probe or its reply
is lost — UDP is unreliable and we only get one HOST_VERIFY_TIMEOUT_S window."""

RECONNECTION_FALLBACK_TIMEOUT_S = 4.0
"""Seconds a following peer waits for ReconnectionAck (via the old host's relay)
before falling back to a direct UDP probe of the claimed new host — the relay
dies with the crashed host if its containers were on the same machine."""

WINDOW_WIDTH = int(BASE_WINDOW_WIDTH * WORLD_SCALE)
WINDOW_HEIGHT = int(BASE_WINDOW_HEIGHT * WORLD_SCALE)

PLAYER_WIDTH = int(50 * WORLD_SCALE)
PLAYER_HEIGHT = int(50 * WORLD_SCALE)

ENEMY_HEIGHT = int(34 * WORLD_SCALE)
ENEMY_WIDTH = int(34 * WORLD_SCALE)

# Enemies driven by a SPADE-BDI agent; the others keep the engine's patrol.
# SPADE_ENEMY_IDS: tuple[str, ...] = ("enemy-1", "enemy-2")

MAX_PLAYERS = 4

RESPAWN_DELAY_S = 3.0

# How long the victory overlay stays on screen before returning to the lobby.
# Host and client each time this independently off their own frame.victory.
VICTORY_OVERLAY_DURATION_S = 3.0

# INPUT_HISTORY_SIZE: number of frames kept in the circular input buffer for
# post-rollback replay. At 60 fps this covers 1 second of history, which is
# more than enough for any realistic LAN round-trip time — and, being a
# bounded buffer, it is also the hard cap on how many frames a single
# reconciliation can replay.
INPUT_HISTORY_SIZE: int = 60

# PREDICTION_LEAD_EWMA_ALPHA: smoothing factor used during the calibration
# window (see PREDICTION_LEAD_CALIBRATION_FRAMES) to settle the prediction
# lead baseline onto the connection's true round-trip latency in ticks.
PREDICTION_LEAD_EWMA_ALPHA: float = 0.01

# PREDICTION_LEAD_CALIBRATION_FRAMES: number of frames during which the
# prediction lead baseline is still adjusted via EWMA. After this window the
# baseline is frozen. Freezing is required because client and host tick
# loops run at very slightly different real-world rates (~1% in LAN tests);
# if the baseline kept tracking the instantaneous lead via EWMA, it would
# drift upward together with the lead instead of correcting it, leaving
# `pending` to grow unbounded over a long session.
PREDICTION_LEAD_CALIBRATION_FRAMES: int = 120

# PREDICTION_LEAD_DRIFT_TOLERANCE: how many ticks the instantaneous prediction
# lead may deviate from its (frozen, post-calibration) baseline before the
# client adjusts its tick rate by one tick (skip a tick if running ahead,
# double-tick if running behind). This corrects clock drift between client
# and host, keeping the lead bounded over long sessions instead of drifting
# away from the baseline established during calibration.
PREDICTION_LEAD_DRIFT_TOLERANCE: float = 3.0

# RECONCILE_GLIDE_RATE: fraction of the outstanding visual reconciliation
# error absorbed per frame. Combined with RECONCILE_MAX_GLIDE_PX below, small
# errors (a few px, the normal one-tick prediction jitter) resolve in 1-2
# frames, while large errors (a jump-timing mismatch, tens of px) resolve in
# a handful of frames at the capped rate instead of lingering for ~1s.
RECONCILE_GLIDE_RATE: float = 0.3

# RECONCILE_MAX_GLIDE_PX: hard cap, in pixels per frame, on how much of the
# outstanding error is absorbed in a single frame. Bounds the worst-case
# visible jolt for very large corrections regardless of their size.
RECONCILE_MAX_GLIDE_PX: float = 8.0

# ARTIFICIAL_LATENCY_MS: one-way delay injected by UdpHandler on outgoing
# packets. Use only for local testing of reconciliation behaviour; must be
# 0 in production.
ARTIFICIAL_LATENCY_MS: int = 0

# DISCOVERY_TIMEOUT: time to wait for a discovery response from a host before giving up.
# Used in the lobby discovery process to avoid waiting indefinitely for a response
# from a host that may not be available
DISCOVERY_TIMEOUT: float = 10.0
