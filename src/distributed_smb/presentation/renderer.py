"""Rendering abstractions for the game client."""

from copy import deepcopy
from dataclasses import dataclass, field

import pygame

from distributed_smb.application.dto import RenderCharacter, RenderFrame
from distributed_smb.shared.config import WINDOW_HEIGHT, WINDOW_WIDTH
from distributed_smb.shared.paths import TILESETS_DIR

MARIO_FRAME_SIZE = 32
TILE_SIZE = 16
DISPLAY_TILE_SIZE = 30
POWERUP_COLLECTION_EFFECT_MS = 420
PLAYER_DEATH_EFFECT_MS = 900
PLAYER_DEATH_RISE_PX = 72
PLAYER_DEATH_FALL_PX = 120
CHECKPOINT_TOAST_MS = 2500
CHECKPOINT_TOAST_FADE_START = 0.7

# Source rects for background decorations within OverWorld.png, measured from the
# sheet's actual sprite bounding boxes (they are not aligned to the 16px tile grid).
DECORATION_SOURCE_RECTS = {
    "cloud": (89, 32, 37, 22),
    "bush": (8, 96, 32, 16),
    "hill": (48, 77, 80, 35),
    "pipe": (96, 0, 32, 32),
}
# Decoration kinds that are purely background dressing, drawn before platforms.
BACKGROUND_DECORATION_KINDS = {"cloud", "bush", "hill"}
# Decoration kinds that overlay a solid Platform (pipes), drawn after platforms
# so the pipe art replaces the generic brick look at that spot.
FOREGROUND_DECORATION_KINDS = {"pipe"}


@dataclass(slots=True)
class PlayerDeathEffect:
    character: RenderCharacter
    started_at_ms: int


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

    def __post_init__(self) -> None:
        if self.player_palette is None:
            self.player_palette = {
                "player1": (220, 50, 50),
                "player2": (50, 90, 220),
                "player3": (50, 180, 50),
                "player4": (200, 150, 50),
            }

    def _resolve_facing(self, character: RenderCharacter) -> int:
        """Keep the latest horizontal facing direction for each player."""
        if character.vx > 0:
            facing = 1
        elif character.vx < 0:
            facing = -1
        else:
            facing = self._facing_by_player.get(character.player_id, 1)
        self._facing_by_player[character.player_id] = facing
        return facing

    def _animation_state(self, character: RenderCharacter) -> str:
        """Classify the current sprite state from physics data."""
        if not character.on_ground:
            return "jump"
        if character.is_crouching:
            return "duck"
        if abs(character.vx) > 1:
            return "walk"
        return "idle"

    def _animation_frame(self, state: str) -> int:
        """Return the frame index for the current animation state."""
        if state == "walk":
            return (pygame.time.get_ticks() // 140) % 2
        return 0

    def _shade(self, color: tuple[int, int, int], delta: int) -> tuple[int, int, int]:
        return tuple(max(0, min(255, channel + delta)) for channel in color)

    def _draw_sprite_frame(
        self,
        surface: pygame.Surface,
        state: str,
        frame: int,
        body_color: tuple[int, int, int],
    ) -> None:
        """Draw a simple pixel-art plumber-like sprite into the target surface."""
        width, height = surface.get_size()
        skin = (247, 214, 177)
        hat = self._shade(body_color, -25)
        shirt = self._shade(body_color, 20)
        overalls = self._shade(body_color, -50)
        boots = (102, 68, 32)
        eye = (20, 20, 20)

        def rect(rx: float, ry: float, rw: float, rh: float, color: tuple[int, int, int]) -> None:
            pygame.draw.rect(
                surface,
                color,
                pygame.Rect(
                    round(rx * width),
                    round(ry * height),
                    max(1, round(rw * width)),
                    max(1, round(rh * height)),
                ),
            )

        leg_offset = 0.04 if state == "walk" and frame == 1 else -0.04 if state == "walk" else 0.0
        arm_offset = -0.04 if state == "walk" and frame == 1 else 0.04 if state == "walk" else 0.0
        jump_raise = -0.03 if state == "jump" else 0.0
        duck_drop = 0.18 if state == "duck" else 0.0

        y = jump_raise + duck_drop
        rect(0.28, 0.07 + y, 0.44, 0.12, hat)
        rect(0.23, 0.17 + y, 0.54, 0.06, hat)
        rect(0.32, 0.23 + y, 0.36, 0.18, skin)
        rect(0.6, 0.29 + y, 0.05, 0.05, eye)
        rect(0.35, 0.42 + y, 0.3, 0.12, shirt)
        rect(0.26, 0.42 + arm_offset + y, 0.08, 0.22, shirt)
        rect(0.66, 0.42 - arm_offset + y, 0.08, 0.22, shirt)
        rect(0.32, 0.54 + y, 0.36, 0.16, overalls)
        rect(0.39, 0.56 + y, 0.06, 0.14, shirt)
        rect(0.55, 0.56 + y, 0.06, 0.14, shirt)
        rect(0.34, 0.7 + leg_offset + y, 0.12, 0.16, boots)
        rect(0.54, 0.7 - leg_offset + y, 0.12, 0.16, boots)

    def _build_sprite(
        self,
        body_color: tuple[int, int, int],
        state: str,
        frame: int,
        width: int,
        height: int,
        facing: int,
    ) -> pygame.Surface:
        sprite = pygame.Surface((width, height), pygame.SRCALPHA)
        shadow = pygame.Rect(
            width // 5, height - max(4, height // 10), width * 3 // 5, max(4, height // 12)
        )
        pygame.draw.ellipse(sprite, (0, 0, 0, 60), shadow)
        self._draw_sprite_frame(sprite, state, frame, body_color)
        if facing < 0:
            sprite = pygame.transform.flip(sprite, True, False)
        return sprite

    def _load_asset_sheet(self, filename: str) -> pygame.Surface | None:
        if filename in self._asset_sheets:
            return self._asset_sheets[filename]

        path = TILESETS_DIR / filename
        if not path.exists():
            self._asset_sheets[filename] = None
            return None

        try:
            sheet = pygame.image.load(str(path))
            try:
                sheet = sheet.convert_alpha()
            except pygame.error:
                sheet = sheet.copy()
        except pygame.error:
            sheet = None
        self._asset_sheets[filename] = sheet
        return sheet

    def _get_asset_sprite(
        self,
        filename: str,
        rect: tuple[int, int, int, int],
        width: int,
        height: int,
    ) -> pygame.Surface | None:
        cache_key = (filename, rect, width, height)
        cached = self._asset_sprite_cache.get(cache_key)
        if cached is not None:
            return cached

        sheet = self._load_asset_sheet(filename)
        if sheet is None:
            return None

        sprite = pygame.Surface((rect[2], rect[3]), pygame.SRCALPHA)
        sprite.blit(sheet, (0, 0), pygame.Rect(rect))
        scaled = pygame.transform.scale(sprite, (width, height))
        self._asset_sprite_cache[cache_key] = scaled
        return scaled

    def _world_bounds(
        self,
        frame: RenderFrame,
        platforms: list[pygame.Rect],
    ) -> tuple[int, int]:
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

    def _camera_offset(
        self,
        frame: RenderFrame,
        platforms: list[pygame.Rect],
    ) -> tuple[int, int]:
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
        tile = self._get_asset_sprite(
            "OverWorld.png",
            (TILE_SIZE, 0, TILE_SIZE, TILE_SIZE),
            DISPLAY_TILE_SIZE,
            DISPLAY_TILE_SIZE,
        )
        camera_x, camera_y = camera_offset
        if tile is None:
            for platform in platforms:
                pygame.draw.rect(screen, self.platform_color, platform.move(-camera_x, -camera_y))
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

    def _draw_block_surface(self, surface: pygame.Surface) -> None:
        width, height = surface.get_size()
        body = (177, 100, 46)
        mortar = (118, 62, 28)
        highlight = (224, 160, 98)
        pygame.draw.rect(surface, body, surface.get_rect(), border_radius=max(2, width // 10))
        pygame.draw.rect(surface, mortar, surface.get_rect(), width=max(2, width // 9))
        pygame.draw.line(
            surface, mortar, (width // 2, 3), (width // 2, height - 3), max(2, width // 12)
        )
        pygame.draw.line(
            surface, mortar, (3, height // 2), (width - 3, height // 2), max(2, height // 12)
        )
        pygame.draw.line(surface, highlight, (5, 5), (width - 5, 5), max(1, height // 14))

    def _draw_powerup_surface(self, surface: pygame.Surface) -> None:
        width, height = surface.get_size()
        glow_color = (255, 227, 107, 90)
        star_color = (255, 217, 52)
        shine = (255, 247, 205)
        center = (width // 2, height // 2)
        radius = max(6, min(width, height) // 2 - 3)
        pygame.draw.circle(surface, glow_color, center, radius)
        points = [
            (width * 0.50, height * 0.12),
            (width * 0.60, height * 0.38),
            (width * 0.88, height * 0.38),
            (width * 0.66, height * 0.56),
            (width * 0.76, height * 0.86),
            (width * 0.50, height * 0.68),
            (width * 0.24, height * 0.86),
            (width * 0.34, height * 0.56),
            (width * 0.12, height * 0.38),
            (width * 0.40, height * 0.38),
        ]
        pygame.draw.polygon(surface, star_color, [(round(x), round(y)) for x, y in points])
        pygame.draw.circle(
            surface, shine, (round(width * 0.43), round(height * 0.32)), max(2, width // 10)
        )

    def _draw_gate_surface(self, surface: pygame.Surface, state: str) -> None:
        width, height = surface.get_size()
        frame = (92, 65, 40)
        closed_fill = (79, 127, 173)
        open_fill = (116, 195, 122)
        accent = (212, 233, 248) if state == "closed" else (215, 255, 220)
        fill = open_fill if state == "open" else closed_fill
        panel_width = max(4, width // 5)
        pygame.draw.rect(surface, frame, surface.get_rect(), border_radius=max(2, width // 10))
        inner = surface.get_rect().inflate(-max(4, width // 5), -max(4, height // 8))
        pygame.draw.rect(surface, fill, inner, border_radius=max(2, width // 12))
        pygame.draw.rect(
            surface, accent, inner, width=max(2, width // 12), border_radius=max(2, width // 12)
        )
        if state == "open":
            opening = pygame.Rect(
                inner.centerx - panel_width // 2, inner.y, panel_width, inner.height
            )
            pygame.draw.rect(surface, (30, 30, 30, 0), opening)
            pygame.draw.rect(surface, (35, 45, 58), opening.inflate(-2, 0))
        else:
            pygame.draw.circle(
                surface,
                (255, 236, 135),
                (inner.centerx, inner.centery),
                max(3, min(width, height) // 9),
            )

    def _get_environment_sprite(
        self,
        sprite_kind: str,
        state: str,
        width: int,
        height: int,
    ) -> pygame.Surface:
        cache_key = (sprite_kind, state, width, height)
        sprite = self._environment_sprite_cache.get(cache_key)
        if sprite is not None:
            return sprite

        sprite = self._build_environment_asset_sprite(sprite_kind, state, width, height)
        if sprite is None:
            sprite = pygame.Surface((width, height), pygame.SRCALPHA)
            if sprite_kind == "block":
                self._draw_block_surface(sprite)
            elif sprite_kind == "powerup":
                self._draw_powerup_surface(sprite)
            elif sprite_kind == "gate":
                self._draw_gate_surface(sprite, state)
        self._environment_sprite_cache[cache_key] = sprite
        return sprite

    def _build_environment_asset_sprite(
        self,
        sprite_kind: str,
        state: str,
        width: int,
        height: int,
    ) -> pygame.Surface | None:
        if sprite_kind == "block":
            return self._get_asset_sprite(
                "OverWorld.png",
                (TILE_SIZE * 3, 0, TILE_SIZE, TILE_SIZE),
                width,
                height,
            )
        if sprite_kind == "powerup":
            return self._get_asset_sprite(
                "Items.png", self._powerup_source_rect(state), width, height
            )
        if sprite_kind == "gate":
            sprite = self._get_asset_sprite("Castle.png", (0, 0, 80, 80), width, height)
            if sprite is None:
                return None
            sprite = sprite.copy()
            if state == "closed":
                door = pygame.Rect(width * 0.36, height * 0.58, width * 0.28, height * 0.36)
                pygame.draw.rect(sprite, (91, 55, 30), door)
                pygame.draw.rect(sprite, (36, 24, 18), door, width=max(1, width // 18))
            return sprite
        if sprite_kind == "enemy":
            return self._get_asset_sprite(
                "Enemies.png",
                (100, 6, 18, 25),
                width,
                height,
            )
        return None

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

    def _render_powerup_collection_effects(
        self,
        screen: pygame.Surface,
        frame: RenderFrame,
        now_ms: int,
        camera_offset: tuple[int, int],
    ) -> None:
        for powerup_id, started_at in list(self._powerup_collection_effects.items()):
            power_up = frame.power_ups.get(powerup_id)
            if power_up is None:
                del self._powerup_collection_effects[powerup_id]
                continue

            progress = (now_ms - started_at) / POWERUP_COLLECTION_EFFECT_MS
            if progress >= 1:
                del self._powerup_collection_effects[powerup_id]
                continue

            state = self._powerup_sprite_state(power_up.powerup_id)
            scale = 1 + progress * 0.55
            alpha = max(0, min(255, round(255 * (1 - progress))))
            width = max(1, round(power_up.width * scale))
            height = max(1, round(power_up.height * scale))
            x = round(power_up.x + power_up.width / 2 - width / 2)
            y = round(power_up.y - progress * 34)
            sprite = self._get_environment_sprite("powerup", state, width, height).copy()
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
            screen.blit(ring, self._to_screen_position(ring_x, ring_y, camera_offset))
            screen.blit(sprite, self._to_screen_position(x, y, camera_offset))

    def _get_decoration_sprite(self, kind: str, width: int, height: int) -> pygame.Surface | None:
        rect = DECORATION_SOURCE_RECTS.get(kind)
        if rect is None:
            return None
        return self._get_asset_sprite("OverWorld.png", rect, width, height)

    def _render_decorations(
        self,
        screen: pygame.Surface,
        frame: RenderFrame,
        camera_offset: tuple[int, int],
        kinds: set[str],
    ) -> None:
        for decoration in frame.decorations:
            if decoration.kind not in kinds:
                continue
            sprite = self._get_decoration_sprite(
                decoration.kind, decoration.width, decoration.height
            )
            if sprite is None:
                continue
            screen.blit(
                sprite,
                self._to_screen_position(decoration.x, decoration.y, camera_offset),
            )

    def _render_environment(
        self,
        screen: pygame.Surface,
        frame: RenderFrame,
        camera_offset: tuple[int, int],
    ) -> None:
        for block in frame.blocks:
            if block.destroyed:
                continue
            screen.blit(
                self._get_environment_sprite("block", "intact", block.width, block.height),
                self._to_screen_position(block.x, block.y, camera_offset),
            )

        now_ms = pygame.time.get_ticks()
        seen_powerups = set()
        for power_up in frame.power_ups.values():
            seen_powerups.add(power_up.powerup_id)
            was_collected = self._powerup_collected_state.get(
                power_up.powerup_id, power_up.collected
            )
            if power_up.collected and not was_collected:
                self._powerup_collection_effects[power_up.powerup_id] = now_ms
            self._powerup_collected_state[power_up.powerup_id] = power_up.collected

            if power_up.collected:
                continue
            screen.blit(
                self._get_environment_sprite(
                    "powerup",
                    self._powerup_sprite_state(power_up.powerup_id),
                    power_up.width,
                    power_up.height,
                ),
                self._to_screen_position(power_up.x, power_up.y, camera_offset),
            )
        for powerup_id in set(self._powerup_collected_state) - seen_powerups:
            del self._powerup_collected_state[powerup_id]
            self._powerup_collection_effects.pop(powerup_id, None)
        self._render_powerup_collection_effects(screen, frame, now_ms, camera_offset)

        for gate in frame.gates.values():
            screen.blit(
                self._get_environment_sprite("gate", gate.state, gate.width, gate.height),
                self._to_screen_position(gate.x, gate.y, camera_offset),
            )

        for enemy in frame.enemies.values():
            screen.blit(
                self._get_environment_sprite("enemy", "default", enemy.width, enemy.height),
                self._to_screen_position(enemy.x, enemy.y, camera_offset),
            )

    def _player_blink_alpha(self, character: RenderCharacter, now_ms: int) -> int:
        if not character.powerup_effect_active:
            return 255
        blink_period_ms = 180
        return 255 if (now_ms // blink_period_ms) % 2 == 0 else 90

    def _get_player_sprite(self, character: RenderCharacter) -> pygame.Surface:
        """Return a cached sprite frame for the given character state."""
        color = self.player_palette.get(character.player_id, (80, 80, 80))
        state = self._animation_state(character)
        frame = self._animation_frame(state)
        facing = self._resolve_facing(character)
        cache_key = (color, state, frame, character.width, character.height, facing)
        sprite = self._sprite_cache.get(cache_key)
        if sprite is None:
            sprite = self._build_player_asset_sprite(
                character=character,
                state=state,
                frame=frame,
                facing=facing,
            )
            if sprite is None:
                sprite = self._build_sprite(
                    body_color=color,
                    state=state,
                    frame=frame,
                    width=int(character.width),
                    height=int(character.height),
                    facing=facing,
                )
            self._sprite_cache[cache_key] = sprite
        return sprite

    def _build_player_asset_sprite(
        self,
        *,
        character: RenderCharacter,
        state: str,
        frame: int,
        facing: int,
    ) -> pygame.Surface | None:
        base_frame = 8 if character.join_index % 2 == 0 else 17
        if state == "jump":
            frame_index = base_frame + 5
        elif state == "duck":
            frame_index = base_frame + 6
        elif state == "walk":
            frame_index = base_frame + 1 + frame
        else:
            frame_index = base_frame

        sprite = self._get_asset_sprite(
            "Mario.png",
            (frame_index * MARIO_FRAME_SIZE, 0, MARIO_FRAME_SIZE, MARIO_FRAME_SIZE),
            int(character.width),
            int(character.height),
        )
        if sprite is None:
            return None
        if facing < 0:
            sprite = pygame.transform.flip(sprite, True, False)
        return sprite

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
        for player_id, effect in list(self._death_effects.items()):
            progress = (now_ms - effect.started_at_ms) / PLAYER_DEATH_EFFECT_MS
            if progress >= 1:
                del self._death_effects[player_id]
                continue

            character = deepcopy(effect.character)
            character.vx = 0
            character.vy = 0
            character.on_ground = False
            sprite = self._get_player_sprite(character).copy()
            sprite.set_alpha(max(0, min(255, round(255 * (1 - progress)))))

            vertical_offset = -PLAYER_DEATH_RISE_PX * (
                1 - (2 * progress - 1) ** 2
            ) + PLAYER_DEATH_FALL_PX * (progress**2)
            screen.blit(
                sprite,
                self._to_screen_position(
                    character.x,
                    character.y + vertical_offset,
                    camera_offset,
                ),
            )

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
        """Render one frame of the game world."""
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
