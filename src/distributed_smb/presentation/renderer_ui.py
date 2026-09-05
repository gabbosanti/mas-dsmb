"""Heads-up display and overlay rendering."""

from __future__ import annotations

import pygame

from distributed_smb.application.dto import RenderCharacter, RenderFrame
from distributed_smb.presentation.renderer_support import (
    CHECKPOINT_TOAST_FADE_START,
    CHECKPOINT_TOAST_MS,
)


class UiRenderer:
    def __init__(self, owner: object) -> None:
        self.owner = owner

    @staticmethod
    def _character_touches_gate(character: RenderCharacter, gate) -> bool:
        return (
            character.x < gate.x + gate.width
            and character.x + character.width > gate.x
            and character.y < gate.y + gate.height
            and character.y + character.height > gate.y
        )

    def render_coin_counter(self, screen: pygame.Surface, frame: RenderFrame) -> None:
        font = pygame.font.SysFont(None, 22)
        lines = [
            f"Coins: {frame.coins_collected}/{frame.coins_to_win}",
            f"Blocks: {frame.blocks_destroyed}/{frame.blocks_to_win}",
            f"Enemies: {frame.enemies_defeated}/{frame.enemies_to_win}",
        ]
        surfaces = [font.render(line, True, (255, 255, 255)) for line in lines]
        panel_width = max(surface.get_width() for surface in surfaces) + 24
        line_height = surfaces[0].get_height() + 4
        panel_height = line_height * len(surfaces) + 8
        panel = pygame.Surface((panel_width, panel_height), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 160))
        x = self.owner.width - panel_width - 16
        y = 16
        screen.blit(panel, (x, y))
        for index, surface in enumerate(surfaces):
            screen.blit(surface, (x + 12, y + 6 + index * line_height))

    def sync_checkpoint_toasts(self, frame: RenderFrame, now_ms: int) -> None:
        for gate in frame.gates.values():
            already_shown = gate.gate_id in self.owner._checkpoint_toast_shown
            if gate.is_final or gate.state != "open" or already_shown:
                continue
            if any(
                self._character_touches_gate(character, gate)
                for character in frame.characters.values()
            ):
                self.owner._checkpoint_toasts[gate.gate_id] = now_ms
                self.owner._checkpoint_toast_shown.add(gate.gate_id)

    def render_checkpoint_toasts(self, screen: pygame.Surface, now_ms: int) -> None:
        if not self.owner._checkpoint_toasts:
            return

        font = pygame.font.SysFont(None, 40)
        text_surface = font.render("Checkpoint raggiunto!", True, (255, 230, 120))
        panel_width = text_surface.get_width() + 40
        panel_height = text_surface.get_height() + 20

        for gate_id, started_at in list(self.owner._checkpoint_toasts.items()):
            progress = (now_ms - started_at) / CHECKPOINT_TOAST_MS
            if progress >= 1:
                del self.owner._checkpoint_toasts[gate_id]
                continue

            alpha = 255
            if progress > CHECKPOINT_TOAST_FADE_START:
                fade_progress = (progress - CHECKPOINT_TOAST_FADE_START) / (
                    1 - CHECKPOINT_TOAST_FADE_START
                )
                alpha = round(255 * (1 - fade_progress))

            panel = pygame.Surface((panel_width, panel_height), pygame.SRCALPHA)
            panel.fill((0, 0, 0, 170))
            panel.blit(text_surface, (20, 10))
            panel.set_alpha(alpha)
            screen.blit(panel, panel.get_rect(center=(self.owner.width // 2, 60)))

    def render_victory_overlay(self, screen: pygame.Surface) -> None:
        overlay = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 180))
        screen.blit(overlay, (0, 0))

        title_font = pygame.font.SysFont(None, 56)
        body_font = pygame.font.SysFont(None, 32)
        title_surface = title_font.render("Victory!", True, (255, 230, 120))
        body_surface = body_font.render(
            "Hai raggiunto la porta del castello",
            True,
            (255, 255, 255),
        )
        screen.blit(
            title_surface,
            title_surface.get_rect(center=(self.owner.width // 2, self.owner.height // 2 - 20)),
        )
        screen.blit(
            body_surface,
            body_surface.get_rect(center=(self.owner.width // 2, self.owner.height // 2 + 20)),
        )
