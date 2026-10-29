"""Pygame lobby screen shown before the gameplay loop starts."""

import subprocess
import time
from dataclasses import dataclass, field

import pygame
import pygame.scrap

from distributed_smb.shared.config import WINDOW_HEIGHT, WINDOW_WIDTH
from distributed_smb.shared.enums import PlayerRole
from distributed_smb.shared.roster import GlobalRoster


@dataclass(slots=True)
class LobbyScreen:
    """Render lobby status, session id, and roster while WebSocket setup runs."""

    width: int = WINDOW_WIDTH
    height: int = WINDOW_HEIGHT
    background_color: tuple[int, int, int] = (24, 28, 36)
    panel_color: tuple[int, int, int] = (38, 45, 58)
    text_color: tuple[int, int, int] = (235, 239, 245)
    muted_text_color: tuple[int, int, int] = (160, 170, 185)
    accent_color: tuple[int, int, int] = (96, 180, 140)
    error_color: tuple[int, int, int] = (235, 105, 105)
    _screen: pygame.Surface = field(init=False, repr=False)
    _title_font: pygame.font.Font = field(init=False, repr=False)
    _body_font: pygame.font.Font = field(init=False, repr=False)
    _small_font: pygame.font.Font = field(init=False, repr=False)
    is_closed: bool = field(init=False, default=False)
    start_requested: bool = field(init=False, default=False)
    leave_requested: bool = field(init=False, default=False)
    _copy_flash_until: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        pygame.init()
        self._screen = pygame.display.set_mode((self.width, self.height))
        pygame.display.set_caption("Distributed SMB - Lobby")
        self._title_font = pygame.font.Font(None, 48)
        self._body_font = pygame.font.Font(None, 30)
        self._small_font = pygame.font.Font(None, 24)
        self._init_clipboard()

    def prompt_join_details(
        self,
        *,
        initial_host_ip: str,
        initial_session_id: str = "",
    ) -> tuple[str, str] | None:
        """Collect host IP and session ID from the client before joining."""
        pygame.display.set_caption("Distributed SMB - Join Session")
        pygame.key.start_text_input()
        self._init_clipboard()
        clock = pygame.time.Clock()
        host_ip = initial_host_ip
        session_id = initial_session_id
        active_field = "session_id"
        error_message = ""

        while not self.is_closed:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.is_closed = True
                    pygame.key.stop_text_input()
                    return None
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        self.is_closed = True
                        pygame.key.stop_text_input()
                        return None
                    if event.key == pygame.K_TAB:
                        active_field = (
                            "host_ip" if active_field == "session_id" else "session_id"
                        )
                    elif event.key == pygame.K_RETURN:
                        if host_ip.strip() and session_id.strip():
                            pygame.key.stop_text_input()
                            return host_ip.strip(), session_id.strip()
                        error_message = "Host IP and Session ID are required"
                    elif event.key == pygame.K_BACKSPACE:
                        if active_field == "host_ip":
                            host_ip = host_ip[:-1]
                        else:
                            session_id = session_id[:-1]
                    elif event.key == pygame.K_v and event.mod & pygame.KMOD_CTRL:
                        pasted_text = self._get_clipboard_text()
                        if active_field == "host_ip":
                            host_ip = (host_ip + pasted_text)[:64]
                        else:
                            session_id = (session_id + pasted_text)[:96]
                elif event.type == pygame.TEXTINPUT and event.text.isprintable():
                    if active_field == "host_ip" and len(host_ip) < 64:
                        host_ip += event.text
                    elif active_field == "session_id" and len(session_id) < 96:
                        session_id += event.text

            self._render_join_form(
                host_ip=host_ip,
                session_id=session_id,
                active_field=active_field,
                error_message=error_message,
            )
            clock.tick(30)

        pygame.key.stop_text_input()
        return None

    def prompt_session_id(self, *, initial_session_id: str = "") -> str | None:
        """Collect only the session ID — LAN discovery resolves the host automatically."""
        pygame.display.set_caption("Distributed SMB - Join Session")
        pygame.key.start_text_input()
        self._init_clipboard()
        clock = pygame.time.Clock()
        session_id = initial_session_id
        error_message = ""

        while not self.is_closed:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.is_closed = True
                    pygame.key.stop_text_input()
                    return None
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        self.is_closed = True
                        pygame.key.stop_text_input()
                        return None
                    elif event.key == pygame.K_RETURN:
                        if session_id.strip():
                            pygame.key.stop_text_input()
                            return session_id.strip()
                        error_message = "Session ID is required"
                    elif event.key == pygame.K_BACKSPACE:
                        session_id = session_id[:-1]
                    elif event.key == pygame.K_v and event.mod & pygame.KMOD_CTRL:
                        session_id = (session_id + self._get_clipboard_text())[:96]
                elif event.type == pygame.TEXTINPUT and event.text.isprintable():
                    if len(session_id) < 96:
                        session_id += event.text

            self._render_session_id_form(
                session_id=session_id, error_message=error_message
            )
            clock.tick(30)

        pygame.key.stop_text_input()
        return None

    def render(
        self,
        *,
        role: PlayerRole,
        status: str,
        session_id: str,
        roster: GlobalRoster,
    ) -> bool:
        """Draw the current lobby view and return False if the user closed it."""
        if self.is_closed:
            return False

        btn_w, btn_h = 90, 36
        btn_x = self.width - 52 - 16 - btn_w
        btn_y = 238
        copy_btn = pygame.Rect(btn_x, btn_y, btn_w, btn_h)
        start_btn = pygame.Rect(btn_x - 166, btn_y, 150, btn_h)
        leave_btn = pygame.Rect(self.width - 52 - 130, 640, 130, 40)
        leave_hovered = leave_btn.collidepoint(pygame.mouse.get_pos())
        hovered = bool(session_id) and copy_btn.collidepoint(pygame.mouse.get_pos())
        start_hovered = (
            role is PlayerRole.HOST
            and bool(session_id)
            and start_btn.collidepoint(pygame.mouse.get_pos())
        )

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.is_closed = True
                return False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_RETURN:
                if role is PlayerRole.HOST and session_id:
                    self.start_requested = True
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self.leave_requested = True
                return False
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if leave_btn.collidepoint(event.pos):
                    self.leave_requested = True
                    return False
                if copy_btn.collidepoint(event.pos) and session_id:
                    self._copy_to_clipboard(session_id)
                    self._copy_flash_until = time.time() + 2.0
                elif (
                    role is PlayerRole.HOST
                    and start_btn.collidepoint(event.pos)
                    and session_id
                ):
                    self.start_requested = True

        try:
            cursor = (
                pygame.SYSTEM_CURSOR_HAND
                if hovered or start_hovered or leave_hovered
                else pygame.SYSTEM_CURSOR_ARROW
            )
            pygame.mouse.set_cursor(cursor)
        except Exception:
            pass

        self._screen.fill(self.background_color)
        self._draw_text(
            "Distributed SMB Lobby", self._title_font, self.text_color, 48, 44
        )
        self._draw_text(
            f"Role: {role.value.upper()}", self._body_font, self.accent_color, 52, 104
        )
        self._draw_text(status, self._body_font, self.text_color, 52, 144)

        session_label = session_id if session_id else "waiting for session id"
        self._draw_panel(52, 200, self.width - 104, 112)
        self._draw_text("Session ID", self._small_font, self.muted_text_color, 78, 222)
        self._draw_text(session_label, self._body_font, self.text_color, 78, 256)
        if session_id:
            btn_color = (85, 175, 125) if hovered else (60, 140, 100)
            pygame.draw.rect(self._screen, btn_color, copy_btn, border_radius=6)
            self._draw_text(
                "Copy", self._small_font, self.text_color, btn_x + 24, btn_y + 10
            )
        if time.time() < self._copy_flash_until:
            # Right-aligned: shares row 322 with "ENTER also starts" on the left.
            confirm = "Session ID copied!"
            confirm_x = self.width - 78 - self._small_font.size(confirm)[0]
            self._draw_text(
                confirm, self._small_font, self.accent_color, confirm_x, 322
            )

        if role is PlayerRole.HOST and session_id:
            start_color = (108, 200, 150) if start_hovered else self.accent_color
            pygame.draw.rect(self._screen, start_color, start_btn, border_radius=8)
            self._draw_text(
                "Start Game",
                self._small_font,
                self.background_color,
                start_btn.x + 26,
                start_btn.y + 10,
            )
            self._draw_text(
                "ENTER also starts",
                self._small_font,
                self.muted_text_color,
                78,
                322,
            )

        self._draw_panel(52, 344, self.width - 104, 270)
        self._draw_text("Roster", self._body_font, self.text_color, 78, 370)
        entries = roster.get_all_players()
        if not entries:
            self._draw_text(
                "No players connected yet",
                self._small_font,
                self.muted_text_color,
                78,
                418,
            )
        else:
            for index, entry in enumerate(entries):
                marker = "HOST" if entry.is_host else "CLIENT"
                line = (
                    f"{entry.join_index}. {entry.player_id}  "
                    f"{marker}  {entry.host}:{entry.udp_port}"
                )
                y = 418 + index * 34
                self._draw_text(line, self._small_font, self.text_color, 78, y)

        leave_color = (198, 88, 88) if leave_hovered else (150, 66, 66)
        pygame.draw.rect(self._screen, leave_color, leave_btn, border_radius=8)
        self._draw_text(
            "Leave  (ESC)",
            self._small_font,
            self.text_color,
            leave_btn.x + 22,
            leave_btn.y + 12,
        )

        pygame.display.flip()
        return True

    def show_error(self, *, title: str, message: str) -> None:
        """Keep an error view open until the user closes the window."""
        pygame.display.set_caption("Distributed SMB - Lobby Error")
        clock = pygame.time.Clock()
        while not self.is_closed:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.is_closed = True
                    break

            self._screen.fill(self.background_color)
            self._draw_text(title, self._title_font, self.error_color, 48, 72)
            self._draw_panel(52, 150, self.width - 104, 220)
            self._draw_wrapped_text(
                message,
                self._body_font,
                self.text_color,
                78,
                182,
                self.width - 156,
                34,
            )
            self._draw_text(
                "Close this window to exit",
                self._small_font,
                self.muted_text_color,
                78,
                410,
            )
            pygame.display.flip()
            clock.tick(30)

    def play_game_start_transition(
        self,
        *,
        role: PlayerRole,
        roster: GlobalRoster,
        duration_seconds: float = 1.8,
    ) -> bool:
        """Animate the handoff from lobby to gameplay."""
        if self.is_closed:
            return False

        pygame.display.set_caption("Distributed SMB - Starting Game")
        clock = pygame.time.Clock()
        started_at = time.monotonic()
        player_count = len(roster.get_all_players())

        while not self.is_closed:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.is_closed = True
                    return False

            elapsed = time.monotonic() - started_at
            progress = min(1.0, elapsed / max(duration_seconds, 0.01))
            self._render_game_start_transition(
                progress=progress,
                role=role,
                player_count=player_count,
            )
            pygame.display.flip()

            if progress >= 1.0:
                return True
            clock.tick(60)

    def close(self) -> None:
        """Close the lobby display surface before the gameplay app takes over."""
        pygame.display.quit()

    def _draw_panel(self, x: int, y: int, width: int, height: int) -> None:
        pygame.draw.rect(
            self._screen,
            self.panel_color,
            pygame.Rect(x, y, width, height),
            border_radius=8,
        )

    def _render_join_form(
        self,
        *,
        host_ip: str,
        session_id: str,
        active_field: str,
        error_message: str,
    ) -> None:
        self._screen.fill(self.background_color)
        self._draw_text("Join Session", self._title_font, self.text_color, 48, 54)
        self._draw_text(
            "Enter the host IP and Session ID",
            self._body_font,
            self.muted_text_color,
            52,
            112,
        )

        self._draw_join_field(
            label="Host IP",
            value=host_ip,
            x=52,
            y=180,
            is_active=active_field == "host_ip",
        )
        self._draw_join_field(
            label="Session ID",
            value=session_id,
            x=52,
            y=300,
            is_active=active_field == "session_id",
        )

        if error_message:
            self._draw_text(error_message, self._small_font, self.error_color, 58, 430)
        self._draw_text(
            "TAB changes field  |  ENTER joins  |  ESC cancels",
            self._small_font,
            self.muted_text_color,
            58,
            472,
        )
        pygame.display.flip()

    def _render_session_id_form(self, *, session_id: str, error_message: str) -> None:
        self._screen.fill(self.background_color)
        self._draw_text("Join Session", self._title_font, self.text_color, 48, 54)
        self._draw_text(
            "Enter the Session ID — the host is found automatically on the LAN",
            self._body_font,
            self.muted_text_color,
            52,
            112,
        )

        self._draw_join_field(
            label="Session ID",
            value=session_id,
            x=52,
            y=220,
            is_active=True,
        )

        if error_message:
            self._draw_text(error_message, self._small_font, self.error_color, 58, 320)
        self._draw_text(
            "ENTER joins  |  ESC cancels",
            self._small_font,
            self.muted_text_color,
            58,
            472,
        )
        pygame.display.flip()

    def _render_game_start_transition(
        self,
        *,
        progress: float,
        role: PlayerRole,
        player_count: int,
    ) -> None:
        self._screen.fill((12, 18, 26))
        center_x = self.width // 2
        center_y = self.height // 2

        pulse = int(32 * min(progress, 1.0))
        pygame.draw.circle(
            self._screen, (40, 82, 98), (center_x, center_y), 120 + pulse, width=3
        )
        pygame.draw.circle(
            self._screen,
            (82, 174, 132),
            (center_x, center_y),
            max(16, int(88 * progress)),
            width=5,
        )

        title = "GET READY" if progress < 0.72 else "GO!"
        title_surface = self._title_font.render(title, True, self.text_color)
        self._screen.blit(
            title_surface, title_surface.get_rect(center=(center_x, center_y - 68))
        )

        plural = "s" if player_count != 1 else ""
        detail = f"{role.value.upper()} | {player_count} player{plural}"
        detail_surface = self._body_font.render(detail, True, self.accent_color)
        self._screen.blit(
            detail_surface, detail_surface.get_rect(center=(center_x, center_y - 18))
        )

        bar_width = min(420, self.width - 160)
        bar = pygame.Rect(center_x - bar_width // 2, center_y + 42, bar_width, 14)
        pygame.draw.rect(self._screen, (38, 45, 58), bar, border_radius=7)
        fill = pygame.Rect(bar.x, bar.y, int(bar.width * progress), bar.height)
        pygame.draw.rect(self._screen, self.accent_color, fill, border_radius=7)

        fade_alpha = int(255 * max(0.0, (progress - 0.82) / 0.18))
        if fade_alpha > 0:
            overlay = pygame.Surface((self.width, self.height))
            overlay.fill((0, 0, 0))
            overlay.set_alpha(fade_alpha)
            self._screen.blit(overlay, (0, 0))

    def _draw_join_field(
        self,
        *,
        label: str,
        value: str,
        x: int,
        y: int,
        is_active: bool,
    ) -> None:
        self._draw_text(label, self._small_font, self.muted_text_color, x + 4, y)
        rect = pygame.Rect(x, y + 30, self.width - 104, 58)
        pygame.draw.rect(self._screen, self.panel_color, rect, border_radius=8)
        border_color = self.accent_color if is_active else self.muted_text_color
        pygame.draw.rect(self._screen, border_color, rect, width=2, border_radius=8)
        display_value = value if value else "type here"
        if is_active:
            display_value = f"{display_value}|"
        color = self.text_color if value else self.muted_text_color
        self._draw_text(display_value, self._body_font, color, x + 20, y + 46)

    def _draw_text(
        self,
        text: str,
        font: pygame.font.Font,
        color: tuple[int, int, int],
        x: int,
        y: int,
    ) -> None:
        surface = font.render(text, True, color)
        self._screen.blit(surface, (x, y))

    def _draw_wrapped_text(
        self,
        text: str,
        font: pygame.font.Font,
        color: tuple[int, int, int],
        x: int,
        y: int,
        max_width: int,
        line_height: int,
    ) -> None:
        words = text.split()
        line = ""
        current_y = y
        for word in words:
            candidate = word if not line else f"{line} {word}"
            if font.size(candidate)[0] <= max_width:
                line = candidate
                continue
            if line:
                self._draw_text(line, font, color, x, current_y)
                current_y += line_height
            line = word
        if line:
            self._draw_text(line, font, color, x, current_y)

    def _copy_to_clipboard(self, text: str) -> None:
        """Write text to the system clipboard (Linux/X11 and Windows)."""
        import sys

        encoded = text.encode("utf-8")
        if sys.platform == "win32":
            try:
                proc = subprocess.Popen(
                    ["clip"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                proc.stdin.write(encoded)
                proc.stdin.close()
                proc.wait()
            except FileNotFoundError:
                pass
        else:
            try:
                proc = subprocess.Popen(
                    ["xclip", "-selection", "clipboard"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                proc.stdin.write(encoded)
                proc.stdin.close()
                # xclip holds the clipboard in background — do not call proc.wait()
            except FileNotFoundError:
                pass

    def _init_clipboard(self) -> None:
        try:
            pygame.scrap.init()
        except pygame.error:
            pass

    def _get_clipboard_text(self) -> str:
        try:
            # X11 advertises "text/plain;charset=utf-8" — pygame.SCRAP_TEXT ("text/plain")
            # rarely matches it exactly, so look up any text/plain* type the clipboard offers.
            text_type = next(
                (t for t in pygame.scrap.get_types() if t.startswith("text/plain")),
                pygame.SCRAP_TEXT,
            )
            clipboard = pygame.scrap.get(text_type)
        except pygame.error:
            return ""
        if clipboard is None:
            return ""
        return clipboard.decode("utf-8", errors="ignore").replace("\x00", "").strip()
