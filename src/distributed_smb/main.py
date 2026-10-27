"""Application entry point for a Distributed SMB node."""

import argparse
import logging
import socket
import threading
import time
from dataclasses import dataclass

from distributed_smb.application.lobby.coordinator import (
    SessionClosedError,
    SessionJoinRejectedError,
)
from distributed_smb.application.node_controller import (
    LobbyCancelledError,
    NodeController,
)
from distributed_smb.application.recovery.prober import RecoveryProber
from distributed_smb.network.discovery import DiscoveryService
from distributed_smb.network.game_events.broker import HttpGameEventBroker
from distributed_smb.network.game_events.server import GameEventBroker
from distributed_smb.network.lobby.container import LobbyContainerManager
from distributed_smb.network.lobby.service import LobbyService
from distributed_smb.network.transport.websocket import WsHandler
from distributed_smb.presentation.input_handler import InputHandler
from distributed_smb.presentation.renderer import Renderer
from distributed_smb.presentation.screens.lobby import LobbyScreen
from distributed_smb.presentation.screens.menu import MenuScreen
from distributed_smb.shared.config import (
    ARTIFICIAL_LATENCY_MS,
    DEFAULT_HOST,
    DEFAULT_PACKET_DROP_RATE,
    HOST_UDP_PORT,
    LOBBY_WS_PORT,
)
from distributed_smb.shared.enums import PlayerRole
from distributed_smb.shared.roster import GlobalRoster, RosterEntry
from distributed_smb.shared.session_metadata import (
    delete_session_metadata,
    read_session_metadata,
)


class ReturnToMenu(Exception):
    """Raised when the node leaves a lobby and should go back to the main menu."""


def build_controller(
    *,
    role: PlayerRole = PlayerRole.HOST,
    packet_drop_rate: float = DEFAULT_PACKET_DROP_RATE,
    artificial_latency_ms: int = ARTIFICIAL_LATENCY_MS,
    use_discovery: bool = False,
) -> NodeController:
    """Create and bootstrap the application's central controller."""
    if use_discovery:
        controller = NodeController(
            game_event_broker=HttpGameEventBroker(),
            discovery_service=DiscoveryService(),
            lobby_container_manager=LobbyContainerManager(),
            recovery_prober=RecoveryProber(),
            use_discovery=True,
            renderer=Renderer(),
            input_handler=InputHandler(),
        )
    else:
        controller = NodeController(
            game_event_broker=GameEventBroker(),
            lobby_service=LobbyService(),
            recovery_prober=RecoveryProber(),
            renderer=Renderer(),
            input_handler=InputHandler(),
        )
    controller.bootstrap(
        role=role,
        packet_drop_rate=packet_drop_rate,
        artificial_latency_ms=artificial_latency_ms,
    )
    return controller


def _detect_local_ip() -> str:
    """Best-effort detection of this machine's LAN-facing IP address."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except OSError:
            return DEFAULT_HOST


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line options for the loopback host/client runtime."""
    parser = argparse.ArgumentParser(description="Distributed SMB node")
    role_group = parser.add_mutually_exclusive_group()
    role_group.add_argument(
        "--host", action="store_true", help="Run the authoritative host"
    )
    role_group.add_argument(
        "--client", action="store_true", help="Run the remote client"
    )
    parser.add_argument(
        "--drop-rate",
        type=_drop_rate,
        default=DEFAULT_PACKET_DROP_RATE,
        help="Artificial UDP packet loss probability in [0.0, 1.0]",
    )
    parser.add_argument(
        "--host-ip",
        type=str,
        default=None,
        help="IP of the host machine (client). Omit to use UDP broadcast discovery.",
    )
    parser.add_argument(
        "--session-id",
        type=str,
        default="",
        help="Session ID to join (required when running as client)",
    )
    parser.add_argument(
        "--local-ip",
        type=str,
        default=None,
        help=(
            "This machine's IP on the LAN, advertised to peers via the lobby. "
            "Omit to auto-detect from the default network route."
        ),
    )
    parser.add_argument(
        "--latency",
        type=int,
        default=ARTIFICIAL_LATENCY_MS,
        metavar="MS",
        help="Artificial send-side latency in milliseconds (default: 0)",
    )
    return parser.parse_args(argv)


def _try_recover_session(
    local_ip: str,
    *,
    lobby_screen: LobbyScreen | None = None,
    prober: RecoveryProber | None = None,
) -> tuple[str, str] | None:
    """Try to recover a previous client session from persisted metadata."""
    metadata = read_session_metadata()
    if metadata is None:
        return None

    # Build a display roster from cached peers so the lobby screen shows them.
    cached_roster = GlobalRoster()
    for peer in metadata.peers:
        cached_roster.add_player(
            RosterEntry(
                player_id=peer.player_id,
                host=peer.ip,
                udp_port=HOST_UDP_PORT,
                join_index=peer.join_index,
            )
        )

    screen = lobby_screen if lobby_screen is not None else LobbyScreen()
    try:
        # Show the recovery screen immediately (first frame) so the user sees the
        # session info and can cancel by closing the window.
        if not screen.render(
            role=PlayerRole.CLIENT,
            status="Rejoining the session… close the window to cancel",
            session_id=metadata.session_id,
            roster=cached_roster,
        ):
            return None

        # Run the UDP probe in a background thread so the lobby screen stays
        # responsive while waiting for peer responses.
        result: list[str | None] = [None]
        probe_done = threading.Event()

        def _probe() -> None:
            current_prober = prober or RecoveryProber()
            result[0] = current_prober.find_current_host(
                metadata.session_id,
                local_ip,
                metadata.peers,
                timeout_per_peer=0.5,
            )
            probe_done.set()

        threading.Thread(target=_probe, name="recovery-probe", daemon=True).start()

        while not probe_done.is_set():
            if not screen.render(
                role=PlayerRole.CLIENT,
                status="Rejoining the session… close the window to cancel",
                session_id=metadata.session_id,
                roster=cached_roster,
            ):
                return None
            time.sleep(0.033)

        host_ip = result[0]
        if host_ip is None:
            delete_session_metadata()
            return None

        return host_ip, metadata.session_id
    finally:
        if lobby_screen is None:
            screen.close()


@dataclass
class _LobbyRunner:
    """Drives the lobby screen and the transition into gameplay."""

    controller: NodeController
    screen: LobbyScreen
    session_id: str
    startup_role: PlayerRole

    def on_update(self, status: str, current_session_id: str, roster) -> bool:
        # self.controller.role, not the startup role: a node promoted mid-session
        # returns to the lobby as host, and only the host is offered Start.
        return self.screen.render(
            role=self.controller.role,
            status=status,
            session_id=current_session_id or self.session_id,
            roster=roster,
        )

    def teardown(self) -> None:
        self.controller.leave_lobby()
        self.controller.ws_handler.close()
        self.controller.game_event_handler.close()
        self.controller.udp_handler.close_socket()
        # Only the host owns the containers — a leaving client must not stop
        # the lobby/relay the others are still using (same machine, shared
        # container names; see LobbyContainerManager).
        if self.controller.role is PlayerRole.HOST:
            self.controller.lobby_container_manager.stop()
        delete_session_metadata()

    def enter_and_transition(self, *, is_replay: bool) -> bool:
        """Run lobby_phase()/replay_lobby_phase() plus the start transition."""
        try:
            if is_replay:
                self.controller.replay_lobby_phase(
                    on_update=self.on_update,
                    start_requested=lambda: self.screen.start_requested,
                )
            else:
                self.controller.lobby_phase(
                    session_id=self.session_id,
                    on_update=self.on_update,
                    start_requested=lambda: self.screen.start_requested,
                )
                if self.startup_role is PlayerRole.CLIENT:
                    self.controller.game_event_handler.connect()
            if not self.screen.play_game_start_transition(
                role=self.controller.role,
                roster=self.controller.roster,
            ):
                logging.info("Gameplay start cancelled during transition")
                self.teardown()
                return False
        except LobbyCancelledError:
            self.teardown()
            if self.screen.leave_requested and not self.screen.is_closed:
                logging.info("Left the lobby — returning to the main menu")
                raise ReturnToMenu from None
            logging.info("Lobby closed before game start")
            return False
        except SessionJoinRejectedError as exc:
            logging.info("Lobby refused the join: %s", exc)
            self.teardown()
            self.screen.show_error(title="Cannot join this session", message=str(exc))
            raise ReturnToMenu from None
        except SessionClosedError:
            logging.info("The host left the lobby — returning to the main menu")
            self.teardown()
            raise ReturnToMenu from None
        except Exception as exc:
            logging.exception("Lobby failed before game start")
            self.teardown()
            self.screen.show_error(title="Lobby connection failed", message=str(exc))
            return False
        finally:
            self.screen.close()
        return True


def _point_client_at(controller: NodeController, host_ip: str | None) -> None:
    """Without discovery the client is told the host address up front."""
    controller.remote_host = host_ip or DEFAULT_HOST
    controller.ws_handler = WsHandler(host=host_ip or DEFAULT_HOST, port=LOBBY_WS_PORT)


def _prompt_client_join(
    lobby_screen: LobbyScreen,
    *,
    use_discovery: bool,
    host_ip: str | None,
    session_id: str,
) -> tuple[str, str | None] | None:
    """Ask what to join: (session_id, host_ip), or None if the player cancelled."""
    if use_discovery:
        joined_session_id = lobby_screen.prompt_session_id(
            initial_session_id=session_id
        )
        return None if joined_session_id is None else (joined_session_id, None)

    join_result = lobby_screen.prompt_join_details(
        initial_host_ip=host_ip or "",
        initial_session_id=session_id,
    )
    if join_result is None:
        return None
    joined_host_ip, joined_session_id = join_result
    return joined_session_id, joined_host_ip or None


def main(
    *,
    run_app: bool = False,
    role: PlayerRole = PlayerRole.HOST,
    packet_drop_rate: float = DEFAULT_PACKET_DROP_RATE,
    artificial_latency_ms: int = ARTIFICIAL_LATENCY_MS,
    host_ip: str | None = None,
    local_ip: str | None = None,
    session_id: str = "",
) -> NodeController:
    """Bootstrap the local node and start the graphical application."""
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    use_discovery = host_ip is None
    logging.info(
        "Starting Distributed SMB in %s mode (discovery=%s)", role, use_discovery
    )
    controller = build_controller(
        role=role,
        packet_drop_rate=packet_drop_rate,
        artificial_latency_ms=artificial_latency_ms,
        use_discovery=use_discovery,
    )
    controller.local_ip = local_ip or _detect_local_ip()
    logging.info("Advertising local IP %s to peers", controller.local_ip)

    if run_app:
        lobby_screen = LobbyScreen()
        if role is PlayerRole.CLIENT and not session_id:
            joined = _prompt_client_join(
                lobby_screen,
                use_discovery=use_discovery,
                host_ip=host_ip,
                session_id=session_id,
            )
            if joined is None:
                logging.info("Client join cancelled before connecting to lobby")
                controller.udp_handler.close_socket()
                lobby_screen.close()
                delete_session_metadata()
                return controller

            session_id, joined_host_ip = joined
            if joined_host_ip:
                host_ip = joined_host_ip
                use_discovery = False
                controller.use_discovery = False

        if role is PlayerRole.CLIENT and not use_discovery:
            _point_client_at(controller, host_ip)

        lobby_screen.render(
            role=controller.role,
            status="Preparing lobby",
            session_id=session_id,
            roster=controller.roster,
        )
        runner = _LobbyRunner(
            controller=controller,
            screen=lobby_screen,
            session_id=session_id,
            startup_role=role,
        )

        if not runner.enter_and_transition(is_replay=False):
            return controller

        outcome = controller.run()
        while outcome == "victory":
            runner.screen = LobbyScreen()
            if not runner.enter_and_transition(is_replay=True):
                return controller
            outcome = controller.run()

        # Only reached on a clean quit (no exception) — a KeyboardInterrupt/crash
        # here must NOT stop the containers: they need to survive so a promoted
        # host can find them still running and reuse them (see
        # LobbyContainerManager.start()). Stopping them unconditionally in a
        # finally block raced the next host's own startup.
        controller.lobby_container_manager.stop()
        delete_session_metadata()
    elif role is PlayerRole.CLIENT and not use_discovery:
        _point_client_at(controller, host_ip)
    return controller


def _drop_rate(value: str) -> float:
    """Validate that the drop rate is a float in [0.0, 1.0]."""
    try:
        f = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid float value: {value!r}")
    if not (0.0 <= f <= 1.0):
        raise argparse.ArgumentTypeError(f"must be in [0.0, 1.0], got {f}")
    return f


if __name__ == "__main__":
    args = parse_args()
    local_ip = args.local_ip or _detect_local_ip()

    while True:
        selected_role = None
        host_ip = args.host_ip
        session_id = args.session_id

        if args.host:
            selected_role = PlayerRole.HOST
        elif args.client:
            selected_role = PlayerRole.CLIENT
        else:
            lobby_screen = LobbyScreen()
            try:
                recovered = _try_recover_session(local_ip, lobby_screen=lobby_screen)
            finally:
                lobby_screen.close()

            if recovered is not None:
                host_ip, session_id = recovered
                selected_role = PlayerRole.CLIENT
            else:
                menu = MenuScreen()
                selected_role = menu.prompt_role_selection()
                menu.close()
                if selected_role is None:
                    raise SystemExit(0)

        try:
            main(
                run_app=True,
                role=selected_role,
                packet_drop_rate=args.drop_rate,
                artificial_latency_ms=args.latency,
                host_ip=host_ip,
                local_ip=local_ip,
                session_id=session_id,
            )
        except ReturnToMenu:
            if args.host or args.client:
                # CLI shortcuts pin the role, so there is no menu to go back to.
                raise SystemExit(0) from None
            continue
        break
