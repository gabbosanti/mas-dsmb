"""Environment and world-object rendering logic."""

from __future__ import annotations

from typing import Any

import pygame

from distributed_smb.application.dto import RenderFrame
from distributed_smb.presentation.renderer_assets import (
    AssetSpriteFactory,
    DECORATION_SOURCE_RECTS,
    DISPLAY_TILE_SIZE,
    TILE_SIZE,
)


class WorldRenderer:
    def __init__(self, owner: Any, sprite_factory: AssetSpriteFactory) -> None:
        self.owner = owner
        self.sprite_factory = sprite_factory

    def _powerup_sprite_state(self, powerup_id: str) -> str:
        if powerup_id.startswith("coin-"):
            return "coin"
        if powerup_id.startswith("flower-"):
            return "flower"
        if powerup_id.startswith("mushroom-"):
            return "mushroom"
        return "star"

    def _powerup_source_rect(self, state: str) -> tuple[int, int, int, int]:
        rects = {
            "coin": (0, TILE_SIZE, TILE_SIZE, TILE_SIZE),
            "flower": (TILE_SIZE * 2, 0, TILE_SIZE, TILE_SIZE),
            "mushroom": (0, 0, TILE_SIZE, TILE_SIZE),
            "star": (TILE_SIZE * 3, 0, TILE_SIZE, TILE_SIZE),
        }
        return rects.get(state, rects["star"])

    def _get_environment_sprite(
        self,
        sprite_kind: str,
        state: str,
        width: int,
        height: int,
    ) -> pygame.Surface:
        cache_key = (sprite_kind, state, width, height)
        cached = self.owner._environment_sprite_cache.get(cache_key)
        if cached is not None:
            return cached

        sprite = self._build_environment_asset_sprite(sprite_kind, state, width, height)
        if sprite is None:
            sprite = pygame.Surface((width, height), pygame.SRCALPHA)
            if sprite_kind == "block":
                self.sprite_factory._draw_block_surface(sprite)
            elif sprite_kind == "powerup":
                self.sprite_factory._draw_powerup_surface(sprite)
            elif sprite_kind == "gate":
                self.sprite_factory._draw_gate_surface(sprite, state)
        self.owner._environment_sprite_cache[cache_key] = sprite
        return sprite

    def _build_environment_asset_sprite(
        self,
        sprite_kind: str,
        state: str,
        width: int,
        height: int,
    ) -> pygame.Surface | None:
        if sprite_kind == "block":
            return self.sprite_factory._get_asset_sprite(
                "OverWorld.png",
                (TILE_SIZE * 3, 0, TILE_SIZE, TILE_SIZE),
                width,
                height,
            )
        if sprite_kind == "powerup":
            return self.sprite_factory._get_asset_sprite(
                "Items.png",
                self._powerup_source_rect(state),
                width,
                height,
            )
        if sprite_kind == "gate":
            sprite = self.sprite_factory._get_asset_sprite("Castle.png", (0, 0, 80, 80), width, height)
            if sprite is None:
                return None
            sprite = sprite.copy()
            if state == "closed":
                door = pygame.Rect(width * 0.36, height * 0.58, width * 0.28, height * 0.36)
                pygame.draw.rect(sprite, (91, 55, 30), door)
                pygame.draw.rect(sprite, (36, 24, 18), door, width=max(1, width // 18))
            return sprite
        if sprite_kind == "enemy":
            return self.sprite_factory._get_asset_sprite("Enemies.png", (100, 6, 18, 25), width, height)
        return None

    def _get_decoration_sprite(self, kind: str, width: int, height: int) -> pygame.Surface | None:
        rect = DECORATION_SOURCE_RECTS.get(kind)
        if rect is None:
            return None
        return self.sprite_factory._get_asset_sprite("OverWorld.png", rect, width, height)

    def render_platforms(
        self,
        screen: pygame.Surface,
        platforms: list[pygame.Rect],
        camera_offset: tuple[int, int],
    ) -> None:
        tile = self.sprite_factory._get_asset_sprite(
            "OverWorld.png",
            (TILE_SIZE, 0, TILE_SIZE, TILE_SIZE),
            DISPLAY_TILE_SIZE,
            DISPLAY_TILE_SIZE,
        )
        camera_x, camera_y = camera_offset
        if tile is None:
            for platform in platforms:
                pygame.draw.rect(screen, self.owner.platform_color, platform.move(-camera_x, -camera_y))
            return

        for platform in platforms:
            for y in range(platform.top, platform.bottom, DISPLAY_TILE_SIZE):
                for x in range(platform.left, platform.right, DISPLAY_TILE_SIZE):
                    width = min(DISPLAY_TILE_SIZE, platform.right - x)
                    height = min(DISPLAY_TILE_SIZE, platform.bottom - y)
                    target = (x - camera_x, y - camera_y)
                    if width == DISPLAY_TILE_SIZE and height == DISPLAY_TILE_SIZE:
                        screen.blit(tile, target)
                    else:
                        screen.blit(pygame.transform.scale(tile, (width, height)), target)

    def render_decoration_layer(
        self,
        screen: pygame.Surface,
        frame: RenderFrame,
        camera_offset: tuple[int, int],
        kinds: set[str],
    ) -> None:
        for decoration in frame.decorations:
            if decoration.kind not in kinds:
                continue
            sprite = self._get_decoration_sprite(decoration.kind, decoration.width, decoration.height)
            if sprite is None:
                continue
            screen.blit(sprite, self.owner._to_screen_position(decoration.x, decoration.y, camera_offset))

    def render_environment(
        self,
        screen: pygame.Surface,
        frame: RenderFrame,
        camera_offset: tuple[int, int],
    ) -> None:
        for block in frame.blocks:
            if not block.destroyed:
                screen.blit(
                    self._get_environment_sprite("block", "intact", block.width, block.height),
                    self.owner._to_screen_position(block.x, block.y, camera_offset),
                )

        now_ms = pygame.time.get_ticks()
        seen = set()
        for power_up in frame.power_ups.values():
            seen.add(power_up.powerup_id)
            previous = self.owner._powerup_collected_state.get(power_up.powerup_id, power_up.collected)
            if power_up.collected and not previous:
                self.owner._powerup_collection_effects[power_up.powerup_id] = now_ms
            self.owner._powerup_collected_state[power_up.powerup_id] = power_up.collected
            if power_up.collected:
                continue
            screen.blit(
                self._get_environment_sprite(
                    "powerup",
                    self._powerup_sprite_state(power_up.powerup_id),
                    power_up.width,
                    power_up.height,
                ),
                self.owner._to_screen_position(power_up.x, power_up.y, camera_offset),
            )

        for powerup_id in set(self.owner._powerup_collected_state) - seen:
            del self.owner._powerup_collected_state[powerup_id]
            self.owner._powerup_collection_effects.pop(powerup_id, None)

        for gate in frame.gates.values():
            screen.blit(
                self._get_environment_sprite("gate", gate.state, gate.width, gate.height),
                self.owner._to_screen_position(gate.x, gate.y, camera_offset),
            )

        for enemy in frame.enemies.values():
            screen.blit(
                self._get_environment_sprite("enemy", "default", enemy.width, enemy.height),
                self.owner._to_screen_position(enemy.x, enemy.y, camera_offset),
            )
