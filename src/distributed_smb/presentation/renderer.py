"""Rendering abstractions for the game client."""

from copy import deepcopy
from dataclasses import dataclass, field

import pygame

from distributed_smb.application.dto import RenderCharacter, RenderFrame
from distributed_smb.presentation.renderer_support import (
    BACKGROUND_DECORATION_KINDS,
    CHECKPOINT_TOAST_FADE_START,
    CHECKPOINT_TOAST_MS,
    FOREGROUND_DECORATION_KINDS,
    PLAYER_DEATH_EFFECT_MS,
    PLAYER_DEATH_FALL_PX,
    PLAYER_DEATH_RISE_PX,
    POWERUP_COLLECTION_EFFECT_MS,
    PlayerDeathEffect,
    RendererSpriteSystem,
)
from distributed_smb.shared.config import WINDOW_HEIGHT, WINDOW_WIDTH


@dataclass(slots=True)
class Renderer:
    """Render the scene and player sprites for the local client."""

    width: int = WINDOW_WIDTH
    height: int = WINDOW_HEIGHT
    background_color: tuple[int, int, int] = (135, 206, 235)
    platform_color: tuple[int, int, int] = (90, 60, 40)
    player_palette: dict[str, tuple[int, int, int]] = None
    _sprite_cache: dict[tuple[tuple[int, int, int], str, int, int, int, int], pygame.Surface] = (
        field(init=False, default_factory=dict)
    )
    _environment_sprite_cache: dict[tuple[str, str, int, int], pygame.Surface] = field(
        init=False, default_factory=dict
    )
    _asset_sheets: dict[str, pygame.Surface | None] = field(init=False, default_factory=dict)
    _asset_sprite_cache: dict[tuple[str, tuple[int, int, int, int], int, int], pygame.Surface] = (
        field(init=False, default_factory=dict)
    )
    _facing_by_player: dict[str, int] = field(init=False, default_factory=dict)
    _powerup_collected_state: dict[str, bool] = field(init=False, default_factory=dict)
    _powerup_collection_effects: dict[str, int] = field(init=False, default_factory=dict)
    _last_rendered_characters: dict[str, RenderCharacter] = field(init=False, default_factory=dict)
    _death_effects: dict[str, PlayerDeathEffect] = field(init=False, default_factory=dict)
    _last_camera_offset: tuple[int, int] = field(init=False, default=(0, 0))
    _checkpoint_toasts: dict[str, int] = field(init=False, default_factory=dict)
    _checkpoint_toast_shown: set[str] = field(init=False, default_factory=set)
    _sprite_system: RendererSpriteSystem = field(init=False)

    def __post_init__(self) -> None:
        if self.player_palette is None:
            self.player_palette = {
                "player1": (220, 50, 50),
                "player2": (50, 90, 220),
                "player3": (50, 180, 50),
                "player4": (200, 150, 50),
            }
        self._sprite_system = RendererSpriteSystem(self)

    def _resolve_facing(self, character: RenderCharacter) -> int:
        if character.vx > 0:
            facing = 1
        elif character.vx < 0:
            facing = -1
        else:
            facing = self._facing_by_player.get(character.player_id, 1)
        self._facing_by_player[character.player_id] = facing
        return facing

    def _animation_state(self, character: RenderCharacter) -> str:
        if not character.on_ground:
            return "jump"
        if character.is_crouching:
            return "duck"
        if abs(character.vx) > 1:
            return "walk"
        return "idle"

    def _animation_frame(self, state: str) -> int:
        if state == "walk":
            return (pygame.time.get_ticks() // 140) % 2
        return 0

    def _world_bounds(self, frame: RenderFrame, platforms: list[pygame.Rect]) -> tuple[int, int]:
        if frame.world_width and frame.world_height:
            return max(self.width, frame.world_width), max(self.height, frame.world_height)

        max_right = self.width
        max_bottom = self.height

        for platform in platforms:
            max_right = max(max_right, platform.right)
            max_bottom = max(max_bottom, platform.bottom)

        for character in frame.characters.values():
            max_right = max(max_right, int(character.x + character.width))
            max_bottom = max(max_bottom, int(character.y + character.height))

        for block in frame.blocks:
            max_right = max(max_right, int(block.x + block.width))
            max_bottom = max(max_bottom, int(block.y + block.height))

        for power_up in frame.power_ups.values():
            max_right = max(max_right, int(power_up.x + power_up.width))
            max_bottom = max(max_bottom, int(power_up.y + power_up.height))

        for gate in frame.gates.values():
            max_right = max(max_right, int(gate.x + gate.width))
            max_bottom = max(max_bottom, int(gate.y + gate.height))

        for enemy in frame.enemies.values():
            max_right = max(max_right, int(enemy.x + enemy.width))
            max_bottom = max(max_bottom, int(enemy.y + enemy.height))

        return max_right, max_bottom

    def _camera_offset(self, frame: RenderFrame, platforms: list[pygame.Rect]) -> tuple[int, int]:
        if frame.focus_player_id is None:
            return 0, 0

        focus = frame.characters.get(frame.focus_player_id)
        if focus is None:
            return self._last_camera_offset

        world_width, world_height = self._world_bounds(frame, platforms)
        target_x = focus.x + focus.width / 2 - self.width / 2
        target_y = focus.y + focus.height / 2 - self.height / 2
        max_x = max(0, world_width - self.width)
        max_y = max(0, world_height - self.height)
        return round(max(0, min(target_x, max_x))), round(max(0, min(target_y, max_y)))

    @staticmethod
    def _to_screen_position(x: float, y: float, camera_offset: tuple[int, int]) -> tuple[int, int]:
        camera_x, camera_y = camera_offset
        return round(x) - camera_x, round(y) - camera_y

    def _render_platforms(
        self,
        screen: pygame.Surface,
        platforms: list[pygame.Rect],
        camera_offset: tuple[int, int],
    ) -> None:
        self._sprite_system.render_platforms(screen, platforms, camera_offset)

    def _draw_block_surface(self, surface: pygame.Surface) -> None:
        self._sprite_system._draw_block_surface(surface)

    def _draw_powerup_surface(self, surface: pygame.Surface) -> None:
        self._sprite_system._draw_powerup_surface(surface)

    def _draw_gate_surface(self, surface: pygame.Surface, state: str) -> None:
        self._sprite_system._draw_gate_surface(surface, state)

    def _get_asset_sprite(
        self,
        filename: str,
        rect: tuple[int, int, int, int],
        width: int,
        height: int,
    ) -> pygame.Surface | None:
        return self._sprite_system._get_asset_sprite(filename, rect, width, height)

    def _get_environment_sprite(
        self,
        sprite_kind: str,
        state: str,
        width: int,
        height: int,
    ) -> pygame.Surface:
        return self._sprite_system._get_environment_sprite(sprite_kind, state, width, height)

    def _powerup_sprite_state(self, powerup_id: str) -> str:
        return self._sprite_system._powerup_sprite_state(powerup_id)

    def _powerup_source_rect(self, state: str) -> tuple[int, int, int, int]:
        return self._sprite_system._powerup_source_rect(state)

    def _render_powerup_collection_effects(
        self,
        screen: pygame.Surface,
        frame: RenderFrame,
        now_ms: int,
        camera_offset: tuple[int, int],
    ) -> None:
        self._sprite_system.render_powerup_collection_effects(screen, frame, now_ms, camera_offset)

    def _get_decoration_sprite(self, kind: str, width: int, height: int) -> pygame.Surface | None:
        return self._sprite_system._get_decoration_sprite(kind, width, height)

    def _render_decorations(
        self,
        screen: pygame.Surface,
        frame: RenderFrame,
        camera_offset: tuple[int, int],
        kinds: set[str],
    ) -> None:
        self._sprite_system.render_decoration_layer(screen, frame, camera_offset, kinds)

    def _render_environment(
        self,
        screen: pygame.Surface,
        frame: RenderFrame,
        camera_offset: tuple[int, int],
    ) -> None:
        self._sprite_system.render_environment(screen, frame, camera_offset)

    def _player_blink_alpha(self, character: RenderCharacter, now_ms: int) -> int:
        return self._sprite_system.player_blink_alpha(character, now_ms)

    def _get_player_sprite(self, character: RenderCharacter) -> pygame.Surface:
        return self._sprite_system.get_player_sprite(character)

    def _sync_player_death_effects(self, frame: RenderFrame, now_ms: int) -> None:
        respawning_players = frame.respawning_player_ids

        for player_id in respawning_players:
            if player_id in frame.characters or player_id in self._death_effects:
                continue
            last_seen = self._last_rendered_characters.get(player_id)
            if last_seen is None:
                continue
            self._death_effects[player_id] = PlayerDeathEffect(
                character=deepcopy(last_seen),
                started_at_ms=now_ms,
            )

        for player_id in list(self._death_effects):
            if player_id in frame.characters and player_id not in respawning_players:
                del self._death_effects[player_id]

    def _render_player_death_effects(
        self,
        screen: pygame.Surface,
        now_ms: int,
        camera_offset: tuple[int, int],
    ) -> None:
        self._sprite_system.render_player_death_effects(screen, now_ms, camera_offset)

    def _remember_rendered_characters(self, frame: RenderFrame) -> None:
        self._last_rendered_characters = {
            player_id: deepcopy(character) for player_id, character in frame.characters.items()
        }

    def _render_coin_counter(self, screen: pygame.Surface, frame: RenderFrame) -> None:
        font = pygame.font.SysFont(None, 22)
        lines = [
            f"Coins: {frame.coins_collected}/{frame.coins_to_win}",
            f"Blocks: {frame.blocks_destroyed}/{frame.blocks_to_win}",
            f"Enemies: {frame.enemies_defeated}/{frame.enemies_to_win}",
        ]
        label_surfaces = [font.render(line, True, (255, 255, 255)) for line in lines]
        panel_width = max(surface.get_width() for surface in label_surfaces) + 24
        line_height = label_surfaces[0].get_height() + 4
        panel_height = line_height * len(label_surfaces) + 8
        panel = pygame.Surface((panel_width, panel_height), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 160))
        x = self.width - panel_width - 16
        y = 16
        screen.blit(panel, (x, y))
        for index, label_surface in enumerate(label_surfaces):
            screen.blit(label_surface, (x + 12, y + 6 + index * line_height))

    @staticmethod
    def _character_touches_gate(character: RenderCharacter, gate) -> bool:
        return (
            character.x < gate.x + gate.width
            and character.x + character.width > gate.x
            and character.y < gate.y + gate.height
            and character.y + character.height > gate.y
        )

    def _sync_checkpoint_toasts(self, frame: RenderFrame, now_ms: int) -> None:
        for gate in frame.gates.values():
            already_shown = gate.gate_id in self._checkpoint_toast_shown
            if gate.is_final or gate.state != "open" or already_shown:
                continue
            if any(self._character_touches_gate(c, gate) for c in frame.characters.values()):
                self._checkpoint_toasts[gate.gate_id] = now_ms
                self._checkpoint_toast_shown.add(gate.gate_id)

    def _render_checkpoint_toasts(self, screen: pygame.Surface, now_ms: int) -> None:
        if not self._checkpoint_toasts:
            return

        font = pygame.font.SysFont(None, 40)
        text_surface = font.render("Checkpoint raggiunto!", True, (255, 230, 120))
        panel_width = text_surface.get_width() + 40
        panel_height = text_surface.get_height() + 20

        for gate_id, started_at in list(self._checkpoint_toasts.items()):
            progress = (now_ms - started_at) / CHECKPOINT_TOAST_MS
            if progress >= 1:
                del self._checkpoint_toasts[gate_id]
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
            screen.blit(panel, panel.get_rect(center=(self.width // 2, 60)))

    def _render_victory_overlay(self, screen: pygame.Surface) -> None:
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
            title_surface, title_surface.get_rect(center=(self.width // 2, self.height // 2 - 20))
        )
        screen.blit(
            body_surface, body_surface.get_rect(center=(self.width // 2, self.height // 2 + 20))
        )

    def render(self, screen: pygame.Surface, frame: RenderFrame) -> None:
        screen.fill(self.background_color)
        now_ms = pygame.time.get_ticks()
        self._sync_player_death_effects(frame, now_ms)
        platform_rects = [pygame.Rect(p.x, p.y, p.width, p.height) for p in frame.platforms]
        camera_offset = self._camera_offset(frame, platform_rects)
        self._last_camera_offset = camera_offset

        self._render_decorations(screen, frame, camera_offset, BACKGROUND_DECORATION_KINDS)
        self._render_platforms(screen, platform_rects, camera_offset)
        self._render_decorations(screen, frame, camera_offset, FOREGROUND_DECORATION_KINDS)
        self._render_environment(screen, frame, camera_offset)
        self._render_player_death_effects(screen, now_ms, camera_offset)

        for character in sorted(frame.characters.values(), key=lambda c: (c.y, c.player_id)):
            sprite = self._get_player_sprite(character)
            if character.powerup_effect_active:
                sprite = sprite.copy()
                sprite.set_alpha(self._player_blink_alpha(character, now_ms))
            screen.blit(
                sprite,
                self._to_screen_position(character.x, character.y, camera_offset),
            )

        self._remember_rendered_characters(frame)
        self._render_coin_counter(screen, frame)

        self._sync_checkpoint_toasts(frame, now_ms)
        self._render_checkpoint_toasts(screen, now_ms)

        if frame.victory:
            self._render_victory_overlay(screen)

        pygame.display.flip()
