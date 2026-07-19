"""Application entry point for a Distributed SMB node."""

import argparse
import logging
import socket
import threading
import time

from distributed_smb.application.node_controller import LobbyCancelledError, NodeController
from distributed_smb.application.recovery.prober import RecoveryProber
from distributed_smb.network.discovery import DiscoveryService
from distributed_smb.network.game_event_broker_http import HttpGameEventBroker
from distributed_smb.network.game_event_server import GameEventBroker
from distributed_smb.network.lobby_container import LobbyContainerManager
from distributed_smb.network.lobby_service import LobbyService
from distributed_smb.network.ws_handler import WsHandler
from distributed_smb.presentation.input_handler import InputHandler
from distributed_smb.presentation.lobby_screen import LobbyScreen
from distributed_smb.presentation.menu_screen import MenuScreen
from distributed_smb.presentation.renderer import Renderer
from distributed_smb.shared.config import (
    ARTIFICIAL_LATENCY_MS,
    DEFAULT_HOST,
    DEFAULT_PACKET_DROP_RATE,
    HOST_UDP_PORT,
    LOBBY_WS_PORT,
)
from distributed_smb.shared.enums import PlayerRole
from distributed_smb.shared.roster import GlobalRoster, RosterEntry
from distributed_smb.shared.session_metadata import delete_session_metadata, read_session_metadata


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
            use_discovery=True,
            renderer=Renderer(),
            input_handler=InputHandler(),
        )
    else:
        controller = NodeController(
            game_event_broker=GameEventBroker(),
            lobby_service=LobbyService(),
            renderer=Renderer(),
            input_handler=InputHandler(),
        )
    controller.bootstrap(
        role=role,
        packet_drop_rate=packet_drop_rate,
        artificial_latency_ms=artificial_latency_ms,
    )
    controller.build_runtime_context()
    return controller


def get_controller(
    *,
    role: PlayerRole = PlayerRole.HOST,
    packet_drop_rate: float = DEFAULT_PACKET_DROP_RATE,
    artificial_latency_ms: int = ARTIFICIAL_LATENCY_MS,
    use_discovery: bool = False,
) -> NodeController:
    """Get a bootstrapped controller without starting the GUI (for testing)."""
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    return build_controller(
        role=role,
        packet_drop_rate=packet_drop_rate,
        artificial_latency_ms=artificial_latency_ms,
        use_discovery=use_discovery,
    )


def _detect_local_ip() -> str:
    """Best-effort detection of this machine's LAN-facing IP address.

    Opens a UDP socket toward a public address — no packet is actually sent,
    the OS just resolves which local interface the route would use. Falls
    back to DEFAULT_HOST on machines with no route (e.g. fully offline).
    """
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
    role_group.add_argument("--host", action="store_true", help="Run the authoritative host")
    role_group.add_argument("--client", action="store_true", help="Run the remote client")
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
            status="Rientro nella sessione… Chiudi per annullare",
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
                status="Rientro nella sessione… Chiudi per annullare",
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
    logging.info("Starting Distributed SMB in %s mode (discovery=%s)", role, use_discovery)
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
            if use_discovery:
                join_result = lobby_screen.prompt_session_id(initial_session_id=session_id)
            else:
                join_result = lobby_screen.prompt_join_details(
                    initial_host_ip=host_ip or "",
                    initial_session_id=session_id,
                )
            if join_result is None:
                logging.info("Client join cancelled before connecting to lobby")
                controller.udp_handler.close_socket()
                lobby_screen.close()
                delete_session_metadata()
                return controller

            if use_discovery:
                session_id = join_result
            else:
                joined_host_ip, session_id = join_result
                if joined_host_ip:
                    host_ip = joined_host_ip
                    use_discovery = False
                    controller.use_discovery = False

        if role is PlayerRole.CLIENT and not use_discovery:
            controller.remote_host = host_ip or DEFAULT_HOST
            controller.ws_handler = WsHandler(host=host_ip or DEFAULT_HOST, port=LOBBY_WS_PORT)

        lobby_screen.render(
            role=role,
            status="Preparing lobby",
            session_id=session_id,
            roster=controller.roster,
        )

        def update_lobby_screen(status: str, current_session_id: str, roster) -> bool:
            return lobby_screen.render(
                role=role,
                status=status,
                session_id=current_session_id or session_id,
                roster=roster,
            )

        try:
            controller.lobby_phase(
                session_id=session_id,
                on_update=update_lobby_screen,
                start_requested=lambda: lobby_screen.start_requested,
            )
            if role is PlayerRole.CLIENT:
                controller.game_event_handler.connect()
            if not lobby_screen.play_game_start_transition(
                role=role,
                roster=controller.roster,
            ):
                logging.info("Gameplay start cancelled during transition")
                controller.ws_handler.close()
                controller.udp_handler.close_socket()
                controller.lobby_container_manager.stop()
                delete_session_metadata()
                return controller
        except LobbyCancelledError:
            logging.info("Lobby closed before game start")
            controller.ws_handler.close()
            controller.udp_handler.close_socket()
            controller.lobby_container_manager.stop()
            delete_session_metadata()
            return controller
        except Exception as exc:
            logging.exception("Lobby failed before game start")
            controller.ws_handler.close()
            controller.udp_handler.close_socket()
            controller.lobby_container_manager.stop()
            delete_session_metadata()
            lobby_screen.show_error(
                title="Lobby connection failed",
                message=str(exc),
            )
            return controller
        finally:
            lobby_screen.close()

        try:
            controller.run()
        finally:
            controller.lobby_container_manager.stop()
            delete_session_metadata()
    elif role is PlayerRole.CLIENT and not use_discovery:
        controller.remote_host = host_ip or DEFAULT_HOST
        controller.ws_handler = WsHandler(host=host_ip or DEFAULT_HOST, port=LOBBY_WS_PORT)
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
    selected_role = None
    host_ip = args.host_ip
    session_id = args.session_id
    local_ip = args.local_ip or _detect_local_ip()

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

    main(
        run_app=True,
        role=selected_role,
        packet_drop_rate=args.drop_rate,
        artificial_latency_ms=args.latency,
        host_ip=host_ip,
        local_ip=local_ip,
        session_id=session_id,
    )
