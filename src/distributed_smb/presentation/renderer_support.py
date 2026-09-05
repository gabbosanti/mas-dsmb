"""Shared rendering helpers for sprite assembly and effect drawing."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pygame

from distributed_smb.application.dto import RenderCharacter, RenderFrame
from distributed_smb.presentation.renderer_assets import AssetSpriteFactory
from distributed_smb.presentation.renderer_world import WorldRenderer

POWERUP_COLLECTION_EFFECT_MS = 420
PLAYER_DEATH_EFFECT_MS = 900
PLAYER_DEATH_RISE_PX = 72
PLAYER_DEATH_FALL_PX = 120
CHECKPOINT_TOAST_MS = 2500
CHECKPOINT_TOAST_FADE_START = 0.7
BACKGROUND_DECORATION_KINDS = {"cloud", "bush", "hill"}
FOREGROUND_DECORATION_KINDS = {"pipe"}


@dataclass(slots=True)
class PlayerDeathEffect:
    character: RenderCharacter
    started_at_ms: int


class SpriteFactory(AssetSpriteFactory):
    """Compatibility wrapper for extracted asset and world render logic."""

    def __init__(self, owner: Any) -> None:
        super().__init__(owner)
        self.world_renderer = WorldRenderer(owner, self)

    def render_platforms(self, screen, platforms, camera_offset):
        self.world_renderer.render_platforms(screen, platforms, camera_offset)

    def render_decoration_layer(self, screen, frame, camera_offset, kinds):
        self.world_renderer.render_decoration_layer(screen, frame, camera_offset, kinds)

    def render_environment(self, screen, frame, camera_offset):
        self.world_renderer.render_environment(screen, frame, camera_offset)

    def _get_environment_sprite(self, sprite_kind, state, width, height):
        return self.world_renderer._get_environment_sprite(sprite_kind, state, width, height)

    def _powerup_sprite_state(self, powerup_id: str) -> str:
        return self.world_renderer._powerup_sprite_state(powerup_id)

    def _get_decoration_sprite(self, kind: str, width: int, height: int):
        return self.world_renderer._get_decoration_sprite(kind, width, height)

    def _powerup_source_rect(self, state: str):
        return self.world_renderer._powerup_source_rect(state)

    def get_player_sprite(self, character: RenderCharacter) -> pygame.Surface:
        return super().get_player_sprite(character)


class EffectRenderer:
    """Draw transient effects like power-up blooms and death animations."""

    def __init__(self, owner: Any) -> None:
        self.owner = owner

    def player_blink_alpha(self, character: RenderCharacter, now_ms: int) -> int:
        if not character.powerup_effect_active:
            return 255
        blink_period_ms = 180
        return 255 if (now_ms // blink_period_ms) % 2 == 0 else 90

    def render_powerup_collection_effects(
        self,
        screen: pygame.Surface,
        frame: RenderFrame,
        now_ms: int,
        camera_offset: tuple[int, int],
    ) -> None:
        for powerup_id, started_at in list(self.owner._powerup_collection_effects.items()):
            power_up = frame.power_ups.get(powerup_id)
            if power_up is None:
                del self.owner._powerup_collection_effects[powerup_id]
                continue

            progress = (now_ms - started_at) / POWERUP_COLLECTION_EFFECT_MS
            if progress >= 1:
                del self.owner._powerup_collection_effects[powerup_id]
                continue

            sprite_name = self.owner._sprite_system._powerup_sprite_state(power_up.powerup_id)
            scale = 1 + progress * 0.55
            alpha = max(0, min(255, round(255 * (1 - progress))))
            width = max(1, round(power_up.width * scale))
            height = max(1, round(power_up.height * scale))
            x = round(power_up.x + power_up.width / 2 - width / 2)
            y = round(power_up.y - progress * 34)
            sprite = self.owner._sprite_system._get_environment_sprite(
                "powerup", sprite_name, width, height
            ).copy()
            sprite.set_alpha(alpha)

            ring_radius = round(max(power_up.width, power_up.height) * (0.55 + progress * 0.8))
            ring = pygame.Surface((ring_radius * 2 + 4, ring_radius * 2 + 4), pygame.SRCALPHA)
            pygame.draw.circle(
                ring,
                (255, 246, 156, round(alpha * 0.65)),
                ring.get_rect().center,
                ring_radius,
                width=max(2, round(4 * (1 - progress))),
            )
            ring_x = round(power_up.x + power_up.width / 2 - ring.get_width() / 2)
            ring_y = round(power_up.y + power_up.height / 2 - ring.get_height() / 2)
            screen.blit(ring, self.owner._to_screen_position(ring_x, ring_y, camera_offset))
            screen.blit(sprite, self.owner._to_screen_position(x, y, camera_offset))

    def render_player_death_effects(
        self,
        screen: pygame.Surface,
        now_ms: int,
        camera_offset: tuple[int, int],
    ) -> None:
        for player_id, effect in list(self.owner._death_effects.items()):
            progress = (now_ms - effect.started_at_ms) / PLAYER_DEATH_EFFECT_MS
            if progress >= 1:
                del self.owner._death_effects[player_id]
                continue

            character = deepcopy(effect.character)
            character.vx = 0
            character.vy = 0
            character.on_ground = False
            sprite = self.owner._sprite_system.get_player_sprite(character).copy()
            sprite.set_alpha(max(0, min(255, round(255 * (1 - progress)))))

            vertical_offset = -PLAYER_DEATH_RISE_PX * (
                1 - (2 * progress - 1) ** 2
            ) + PLAYER_DEATH_FALL_PX * (progress**2)
            screen.blit(
                sprite,
                self.owner._to_screen_position(
                    character.x,
                    character.y + vertical_offset,
                    camera_offset,
                ),
            )


RendererSpriteSystem = SpriteFactory
