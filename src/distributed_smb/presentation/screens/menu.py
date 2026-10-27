"""Pygame main menu — lets the player choose to host or join a session."""

from dataclasses import dataclass, field

import pygame

from distributed_smb.shared.config import WINDOW_HEIGHT, WINDOW_WIDTH
from distributed_smb.shared.enums import PlayerRole


@dataclass(slots=True)
class MenuScreen:
    """Render the role-selection menu shown before the lobby phase."""

    width: int = WINDOW_WIDTH
    height: int = WINDOW_HEIGHT
    background_color: tuple[int, int, int] = (24, 28, 36)
    text_color: tuple[int, int, int] = (235, 239, 245)
    muted_text_color: tuple[int, int, int] = (160, 170, 185)
    accent_color: tuple[int, int, int] = (96, 180, 140)
    _screen: pygame.Surface = field(init=False, repr=False)
    _title_font: pygame.font.Font = field(init=False, repr=False)
    _body_font: pygame.font.Font = field(init=False, repr=False)
    is_closed: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        pygame.init()
        self._screen = pygame.display.set_mode((self.width, self.height))
        pygame.display.set_caption("Distributed SMB - Menu")
        self._title_font = pygame.font.Font(None, 48)
        self._body_font = pygame.font.Font(None, 30)

    def prompt_role_selection(self) -> PlayerRole | None:
        """Show the menu and return the chosen role, or None if the window was closed."""
        clock = pygame.time.Clock()
        btn_w, btn_h = 280, 64
        center_x = self.width // 2
        host_btn = pygame.Rect(center_x - btn_w // 2, 230, btn_w, btn_h)
        join_btn = pygame.Rect(center_x - btn_w // 2, 320, btn_w, btn_h)

        while not self.is_closed:
            mouse_pos = pygame.mouse.get_pos()
            host_hovered = host_btn.collidepoint(mouse_pos)
            join_hovered = join_btn.collidepoint(mouse_pos)

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.is_closed = True
                    return None
                if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    self.is_closed = True
                    return None
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    if host_btn.collidepoint(event.pos):
                        return PlayerRole.HOST
                    if join_btn.collidepoint(event.pos):
                        return PlayerRole.CLIENT

            try:
                cursor = (
                    pygame.SYSTEM_CURSOR_HAND
                    if host_hovered or join_hovered
                    else pygame.SYSTEM_CURSOR_ARROW
                )
                pygame.mouse.set_cursor(cursor)
            except Exception:
                pass

            self._render(host_btn, join_btn, host_hovered, join_hovered)
            clock.tick(30)

        return None

    def close(self) -> None:
        """Close the menu display surface before the next screen takes over."""
        pygame.display.quit()

    def _render(
        self,
        host_btn: pygame.Rect,
        join_btn: pygame.Rect,
        host_hovered: bool,
        join_hovered: bool,
    ) -> None:
        self._screen.fill(self.background_color)
        self._draw_centered_text(
            "Distributed SMB", self._title_font, self.text_color, 96
        )
        self._draw_centered_text(
            "What do you want to do?", self._body_font, self.muted_text_color, 160
        )
        self._draw_button(host_btn, "Create Room", host_hovered)
        self._draw_button(join_btn, "Join Room", join_hovered)
        pygame.display.flip()

    def _draw_button(self, rect: pygame.Rect, label: str, hovered: bool) -> None:
        color = (108, 200, 150) if hovered else self.accent_color
        pygame.draw.rect(self._screen, color, rect, border_radius=8)
        text_surface = self._body_font.render(label, True, self.background_color)
        self._screen.blit(text_surface, text_surface.get_rect(center=rect.center))

    def _draw_centered_text(
        self, text: str, font: pygame.font.Font, color: tuple[int, int, int], y: int
    ) -> None:
        surface = font.render(text, True, color)
        self._screen.blit(surface, surface.get_rect(center=(self.width // 2, y)))
