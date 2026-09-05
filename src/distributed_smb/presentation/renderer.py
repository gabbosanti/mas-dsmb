"""Rendering abstractions for the game client."""

from dataclasses import dataclass, field

import pygame

from distributed_smb.application.dto import RenderCharacter, RenderFrame
from distributed_smb.presentation.renderer_camera import CameraController
from distributed_smb.presentation.renderer_state import RenderStateTracker
from distributed_smb.presentation.renderer_support import (
    BACKGROUND_DECORATION_KINDS,
    FOREGROUND_DECORATION_KINDS,
    EffectRenderer,
    PlayerDeathEffect,
    RendererSpriteSystem,
)
from distributed_smb.presentation.renderer_ui import UiRenderer
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
    _effect_renderer: EffectRenderer = field(init=False)
    _camera_controller: CameraController = field(init=False)
    _state_tracker: RenderStateTracker = field(init=False)
    _ui_renderer: UiRenderer = field(init=False)

    def __post_init__(self) -> None:
        if self.player_palette is None:
            self.player_palette = {
                "player1": (220, 50, 50),
                "player2": (50, 90, 220),
                "player3": (50, 180, 50),
                "player4": (200, 150, 50),
            }
        self._sprite_system = RendererSpriteSystem(self)
        self._effect_renderer = EffectRenderer(self)
        self._camera_controller = CameraController(self)
        self._state_tracker = RenderStateTracker(self)
        self._ui_renderer = UiRenderer(self)

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
        return self._camera_controller.world_bounds(frame, platforms)

    def _camera_offset(self, frame: RenderFrame, platforms: list[pygame.Rect]) -> tuple[int, int]:
        return self._camera_controller.camera_offset(frame, platforms)

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
        self._effect_renderer.render_powerup_collection_effects(
            screen, frame, now_ms, camera_offset
        )

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
        return self._effect_renderer.player_blink_alpha(character, now_ms)

    def _get_player_sprite(self, character: RenderCharacter) -> pygame.Surface:
        return self._sprite_system.get_player_sprite(character)

    def _sync_player_death_effects(self, frame: RenderFrame, now_ms: int) -> None:
        self._state_tracker.sync_player_death_effects(frame, now_ms)

    def _render_player_death_effects(
        self,
        screen: pygame.Surface,
        now_ms: int,
        camera_offset: tuple[int, int],
    ) -> None:
        self._effect_renderer.render_player_death_effects(screen, now_ms, camera_offset)

    def _remember_rendered_characters(self, frame: RenderFrame) -> None:
        self._state_tracker.remember_rendered_characters(frame)

    def _render_coin_counter(self, screen: pygame.Surface, frame: RenderFrame) -> None:
        self._ui_renderer.render_coin_counter(screen, frame)

    def _sync_checkpoint_toasts(self, frame: RenderFrame, now_ms: int) -> None:
        self._ui_renderer.sync_checkpoint_toasts(frame, now_ms)

    def _render_checkpoint_toasts(self, screen: pygame.Surface, now_ms: int) -> None:
        self._ui_renderer.render_checkpoint_toasts(screen, now_ms)

    def _render_victory_overlay(self, screen: pygame.Surface) -> None:
        self._ui_renderer.render_victory_overlay(screen)

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
        self._render_powerup_collection_effects(screen, frame, now_ms, camera_offset)
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
