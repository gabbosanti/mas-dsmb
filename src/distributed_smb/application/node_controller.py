"""Thin orchestrator: wires domain, network, and presentation components."""

import importlib
import logging
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from distributed_smb.application.client_gameplay import ClientGameplayMixin
from distributed_smb.application.dto import RenderFrame, build_render_frame
from distributed_smb.application.election import (
    ElectionCoordinator,
    EnvironmentalStateBuffer,
    HostTimeoutWatcher,
)
from distributed_smb.application.election_mixin import ElectionMixin
from distributed_smb.application.game_event_dispatcher import GameEventMixin
from distributed_smb.application.host_gameplay import HostGameplayMixin
from distributed_smb.application.lobby_coordinator import (
    LobbyCancelledError,
    LobbyMixin,
    LobbyUpdateCallback,
    StartRequestedCallback,
)
from distributed_smb.application.protocols import (
    DiscoveryServiceProtocol,
    GameEventBrokerProtocol,
    LobbyContainerManagerProtocol,
    LobbyServiceProtocol,
    NoopDiscoveryService,
    NoopGameEventBroker,
    NoopLobbyContainerManager,
    NoopLobbyService,
    NoopRecoveryProber,
    RecoveryProberProtocol,
)
from distributed_smb.application.reconciliation import (
    InterpolatedShadowCopy,
    NoopPredictionEngine,
    NoopShadowCopy,
    PredictionEngine,
    PredictionEngineProtocol,
    ShadowCopyProtocol,
)
from distributed_smb.application.recovery_mixin import RecoveryMixin
from distributed_smb.domain.game_engine import GameEngine
from distributed_smb.domain.lifecycle import NodeLifecycle
from distributed_smb.domain.world import CharacterState, WorldState
from distributed_smb.network.serializer import Serializer
from distributed_smb.network.udp_handler import UdpHandler
from distributed_smb.network.ws_handler import WsHandler
from distributed_smb.shared.config import (
    DEFAULT_HOST,
    DEFAULT_PACKET_DROP_RATE,
    DEFAULT_UDP_PORT,
    GAME_EVENT_WS_PATH,
    GAME_EVENT_WS_PORT,
    HOST_PLAYER_ID,
    HOST_UDP_PORT,
    INPUT_HISTORY_SIZE,
    LOBBY_WS_PORT,
    PREDICTION_LEAD_CALIBRATION_FRAMES,
    TICK_INTERVAL,
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
    player_id_for,
)
from distributed_smb.shared.enums import PlayerRole
from distributed_smb.shared.input import InputState
from distributed_smb.shared.roster import GlobalRoster

LOGGER = logging.getLogger(__name__)

__all__ = [
    "NodeController",
    "LobbyCancelledError",
    "LobbyUpdateCallback",
    "StartRequestedCallback",
]


@dataclass(slots=True)
class NodeController(
    ElectionMixin,
    LobbyMixin,
    HostGameplayMixin,
    ClientGameplayMixin,
    GameEventMixin,
    RecoveryMixin,
):
    """Coordinates the node runtime without embedding domain logic."""

    lifecycle: NodeLifecycle = field(default_factory=NodeLifecycle)
    roster: GlobalRoster = field(default_factory=GlobalRoster)
    engine: GameEngine = field(default_factory=GameEngine)
    # Presentation objects: application never calls methods on these, it only
    # holds them to hand off to GameApp in run() — keeping the type unknown
    # here avoids an application -> presentation import. main.py (composition
    # root) injects the real Renderer/InputHandler instances.
    renderer: object | None = None
    input_handler: object | None = None
    udp_handler: UdpHandler = field(
        default_factory=lambda: UdpHandler(host=DEFAULT_HOST, port=DEFAULT_UDP_PORT)
    )
    ws_handler: WsHandler = field(
        default_factory=lambda: WsHandler(host=DEFAULT_HOST, port=LOBBY_WS_PORT)
    )
    game_event_handler: WsHandler = field(
        default_factory=lambda: WsHandler(
            host=DEFAULT_HOST, port=GAME_EVENT_WS_PORT, path=GAME_EVENT_WS_PATH
        )
    )
    session_id: str = ""
    local_ip: str = DEFAULT_HOST
    serializer: Serializer = field(default_factory=Serializer)
    tick_interval: float = TICK_INTERVAL
    is_bootstrapped: bool = False
    role: PlayerRole = PlayerRole.HOST
    local_player_id: str = HOST_PLAYER_ID
    remote_player_id: str = ""
    remote_host: str = DEFAULT_HOST
    remote_port: int = HOST_UDP_PORT
    input_sequence_number: int = 0
    last_remote_input_sequence: dict[str, int] = field(default_factory=dict)
    last_snapshot_sequence: int = 0
    cached_remote_inputs: dict[str, InputState] = field(default_factory=dict)
    sent_input_packets: int = 0
    received_input_packets: int = 0
    sent_snapshots: int = 0
    received_snapshots: int = 0
    last_input_time: dict[str, float] = field(default_factory=dict)
    prediction_engine: PredictionEngineProtocol = field(default_factory=NoopPredictionEngine)
    shadow_copies: dict[str, ShadowCopyProtocol] = field(default_factory=dict)
    shadow_copy_factory: Callable[[], ShadowCopyProtocol] = field(default=NoopShadowCopy)
    game_event_broker: GameEventBrokerProtocol = field(default_factory=NoopGameEventBroker)
    lobby_service: LobbyServiceProtocol = field(default_factory=NoopLobbyService)
    discovery_service: DiscoveryServiceProtocol = field(default_factory=NoopDiscoveryService)
    lobby_container_manager: LobbyContainerManagerProtocol = field(
        default_factory=NoopLobbyContainerManager
    )
    recovery_prober: RecoveryProberProtocol = field(default_factory=NoopRecoveryProber)
    use_discovery: bool = False
    time_provider: Callable[[], float] = field(default_factory=lambda: time.monotonic)
    visual_correction_offset: tuple[float, float] = (0.0, 0.0)
    prediction_lead_baseline: float = 0.0
    prediction_lead_calibration_remaining: int = PREDICTION_LEAD_CALIBRATION_FRAMES
    pending_tick_adjustment: int = 0
    host_last_frame_at: float | None = None
    host_frame_intervals: list[float] = field(default_factory=list)
    host_input_packets_window: int = 0
    host_last_payload_bytes: int = 0
    client_last_frame_at: float | None = None
    client_frame_intervals: list[float] = field(default_factory=list)
    # --- M8: fault tolerance ---
    join_index: int = 0
    election_coordinator: ElectionCoordinator | None = None
    timeout_watcher: HostTimeoutWatcher | None = None
    env_state_buffer: EnvironmentalStateBuffer | None = None
    election_triggered: bool = False
    reconnected: bool = False
    # --- M8: election claim tracking ---
    _pending_election_acks: set = field(default_factory=set)
    _election_claim_deadline: float = 0.0
    _promotion_done: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.prediction_engine, NoopPredictionEngine):
            self.prediction_engine._engine = self.engine

    def _make_ws_client(self, host: str, port: int, path: str) -> None:
        """Create a WebSocket client handler and assign it as the game event receiver."""
        self.game_event_handler = WsHandler(host=host, port=port, path=path)

    def _make_lobby_ws_client(self, host: str, port: int) -> None:
        self.ws_handler = WsHandler(host=host, port=port)

    def _init_shadow_copies(self) -> None:
        """Initialise one ShadowCopy per remote player (CLIENT only, called after lobby)."""
        if self.role is not PlayerRole.CLIENT:
            self.shadow_copies = {}
            return
        self.shadow_copies = {
            pid: self.shadow_copy_factory()
            for pid in self.engine.world_state.characters
            if pid != self.local_player_id
        }

    def bootstrap(
        self,
        *,
        role: PlayerRole = PlayerRole.HOST,
        packet_drop_rate: float = DEFAULT_PACKET_DROP_RATE,
        artificial_latency_ms: int = 0,
    ) -> "NodeController":
        """Prepare the application components for the future runtime loop."""
        LOGGER.info("Bootstrapping node controller")
        self.role = role
        self._configure_role(
            packet_drop_rate=packet_drop_rate,
            artificial_latency_ms=artificial_latency_ms,
        )
        self._bootstrap_world()
        if role is PlayerRole.CLIENT:
            if isinstance(self.prediction_engine, NoopPredictionEngine):
                self.prediction_engine = PredictionEngine(
                    engine=self.engine,
                    local_player_id=self.local_player_id,
                    history_capacity=INPUT_HISTORY_SIZE,
                    time_provider=self.time_provider,
                )
            if self.shadow_copy_factory is NoopShadowCopy:
                self.shadow_copy_factory = lambda: InterpolatedShadowCopy(
                    time_provider=self.time_provider
                )
        self.lifecycle.move_to_idle()
        self.is_bootstrapped = True
        LOGGER.info(
            "Bootstrap completed: state=%s, role=%s, tick_interval=%.4f, window=%sx%s",
            self.lifecycle.state,
            self.role,
            self.tick_interval,
            WINDOW_WIDTH,
            WINDOW_HEIGHT,
        )
        return self

    def build_runtime_context(self) -> dict[str, object]:
        """Expose the components wired by the controller."""
        context = {
            "engine": self.engine,
            "renderer": self.renderer,
            "input_handler": self.input_handler,
            "tick_interval": self.tick_interval,
            "role": self.role,
            "local_player_id": self.local_player_id,
            "remote_player_id": self.remote_player_id,
        }
        LOGGER.info("Runtime context ready: %s", ", ".join(sorted(context)))
        return context

    def process_frame(self, dt: float, local_input: InputState) -> RenderFrame:
        """Advance one frame according to the current runtime role."""
        if not self.lifecycle.is_started:
            self.lifecycle.move_to_game()
        self.udp_handler.open_socket()
        if self.role is PlayerRole.HOST:
            world_state = self._process_host_frame(dt, local_input)
        else:
            world_state = self._process_client_frame(dt, local_input)
        return build_render_frame(
            world_state=world_state,
            platforms=self.engine.platforms,
            focus_player_id=self.local_player_id,
            world_width=self.engine.world_width,
            world_height=self.engine.world_height,
        )

    def run(self) -> bool:
        """Run the application if the presentation runtime is available."""
        if not self.is_bootstrapped:
            self.bootstrap()

        game_app_class = self._load_game_app_class()
        if game_app_class is None:
            LOGGER.info(
                "Application bootstrap completed, waiting for presentation runtime integration"
            )
            return False

        LOGGER.info("Delegating execution to presentation runtime")
        app = game_app_class(
            frame_handler=self.process_frame,
            input_handler=self.input_handler,
            renderer=self.renderer,
            local_player_id=self.local_player_id,
        )
        app.run()
        self.udp_handler.close_socket()
        return True

    def _configure_role(self, *, packet_drop_rate: float, artificial_latency_ms: int = 0) -> None:
        """Configure ports and player identities for host or client mode."""
        if self.role is PlayerRole.HOST:
            self.local_player_id = HOST_PLAYER_ID
            self.engine.is_authoritative = True
            self.udp_handler = UdpHandler(
                host="0.0.0.0",
                port=HOST_UDP_PORT,
                packet_drop_rate=packet_drop_rate,
                artificial_latency_ms=artificial_latency_ms,
            )
        else:
            self.local_player_id = player_id_for(1)  # placeholder, overwritten after lobby join
            self.engine.is_authoritative = False
            self.udp_handler = UdpHandler(
                host="0.0.0.0",
                port=0,  # OS assigns a unique port, avoiding same-machine collisions
                packet_drop_rate=packet_drop_rate,
                artificial_latency_ms=artificial_latency_ms,
            )
            self.remote_port = HOST_UDP_PORT

    def _bootstrap_world(self) -> None:
        """Ensure the local player exists before lobby assigns the full roster."""
        if self.engine.world_state.get_player(self.local_player_id) is None:
            self.engine.spawn_player(self.local_player_id, x=100, y=100)

    def _build_visual_world_state(
        self,
        *,
        local_visual_state: CharacterState | None = None,
    ) -> WorldState:
        """Build a render-only snapshot separated from the authoritative world state."""
        visual_characters: dict[str, CharacterState] = {}
        local_player_respawning = self.local_player_id in self.engine.world_state.respawn_timers

        for player_id, character in self.engine.world_state.characters.items():
            if (
                player_id == self.local_player_id
                and local_visual_state is not None
                and not local_player_respawning
            ):
                visual_characters[player_id] = local_visual_state
                continue

            shadow_copy = self.shadow_copies.get(player_id)
            if shadow_copy is not None:
                visual_state = shadow_copy.get_display_state()
                if visual_state is not None:
                    visual_characters[player_id] = visual_state
                    continue

            visual_characters[player_id] = character

        # Reconcile may temporarily remove the local player before a later snapshot
        # restores them. Keep the pre-reconcile capture only while they are not dead.
        if (
            local_visual_state is not None
            and not local_player_respawning
            and self.local_player_id not in visual_characters
        ):
            visual_characters[self.local_player_id] = local_visual_state

        return WorldState(
            sequence_number=self.engine.world_state.sequence_number,
            characters=visual_characters,
            environment=deepcopy(self.engine.world_state.environment),
            coins_collected=self.engine.world_state.coins_collected,
            coins_to_win=self.engine.world_state.coins_to_win,
            blocks_destroyed=self.engine.world_state.blocks_destroyed,
            blocks_to_win=self.engine.world_state.blocks_to_win,
            enemies_defeated=self.engine.world_state.enemies_defeated,
            enemies_to_win=self.engine.world_state.enemies_to_win,
            initial_enemy_count=self.engine.world_state.initial_enemy_count,
            victory=self.engine.world_state.victory,
            victory_player_id=self.engine.world_state.victory_player_id,
            respawn_timers=deepcopy(self.engine.world_state.respawn_timers),
        )

    def _spawn_position_for(self, join_index: int) -> tuple[int, int]:
        """Return a stable spawn point determined by join order (0-based)."""
        return 100 + join_index * 140, 100

    def _rebuild_udp_as_host(self) -> None:
        """Rebind the UDP socket to the authoritative host port after promotion."""
        self.udp_handler.close_socket()
        self.udp_handler = UdpHandler(host="0.0.0.0", port=HOST_UDP_PORT)

    def _reconnect_game_event_handler(self, host: str, port: int, path: str) -> None:
        """Replace the WebSocket event handler with a new connection to the given endpoint."""
        self.game_event_handler.close()
        self.game_event_handler = WsHandler(host=host, port=port, path=path)
        for attempt in range(6):
            try:
                self.game_event_handler.connect(timeout=2.0)
                return
            except (ConnectionError, TimeoutError):
                if attempt < 5:
                    LOGGER.info(
                        "game event relay not ready (attempt %d/6), retrying in 1s…", attempt + 1
                    )
                    time.sleep(1.0)
        LOGGER.warning(
            "could not reconnect game event relay at %s:%d — continuing without relay", host, port
        )

    def _load_game_app_class(self) -> type[Any] | None:
        """Load the optional Pygame runtime provided by the presentation layer."""
        try:
            module = importlib.import_module("distributed_smb.presentation.app")
        except ModuleNotFoundError:
            LOGGER.info("Presentation runtime not available yet: expected presentation.app.GameApp")
            return None

        game_app_class = getattr(module, "GameApp", None)
        if game_app_class is None:
            LOGGER.warning("presentation.app found, but GameApp is missing")
            return None
        return game_app_class
