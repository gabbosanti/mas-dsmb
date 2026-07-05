"""Rendering abstractions for the game client."""

from dataclasses import dataclass, field

import pygame

from distributed_smb.domain.world import CharacterState, WorldState
from distributed_smb.shared.config import WINDOW_HEIGHT, WINDOW_WIDTH
from distributed_smb.shared.paths import MARIO1_ASSETS_DIR

MARIO_FRAME_SIZE = 32
TILE_SIZE = 16
DISPLAY_TILE_SIZE = 30
POWERUP_COLLECTION_EFFECT_MS = 420


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

    def __post_init__(self) -> None:
        if self.player_palette is None:
            self.player_palette = {
                "player1": (220, 50, 50),
                "player2": (50, 90, 220),
                "player3": (50, 180, 50),
                "player4": (200, 150, 50),
            }

    def _resolve_facing(self, character: CharacterState) -> int:
        """Keep the latest horizontal facing direction for each player."""
        if character.vx > 0:
            facing = 1
        elif character.vx < 0:
            facing = -1
        else:
            facing = self._facing_by_player.get(character.player_id, 1)
        self._facing_by_player[character.player_id] = facing
        return facing

    def _animation_state(self, character: CharacterState) -> str:
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

        path = MARIO1_ASSETS_DIR / filename
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

    def _render_platforms(self, screen: pygame.Surface, platforms: list[pygame.Rect]) -> None:
        tile = self._get_asset_sprite(
            "OverWorld.png",
            (TILE_SIZE, 0, TILE_SIZE, TILE_SIZE),
            DISPLAY_TILE_SIZE,
            DISPLAY_TILE_SIZE,
        )
        if tile is None:
            for platform in platforms:
                pygame.draw.rect(screen, self.platform_color, platform)
            return

        for platform in platforms:
            for y in range(platform.top, platform.bottom, DISPLAY_TILE_SIZE):
                for x in range(platform.left, platform.right, DISPLAY_TILE_SIZE):
                    width = min(DISPLAY_TILE_SIZE, platform.right - x)
                    height = min(DISPLAY_TILE_SIZE, platform.bottom - y)
                    if width == DISPLAY_TILE_SIZE and height == DISPLAY_TILE_SIZE:
                        screen.blit(tile, (x, y))
                    else:
                        screen.blit(pygame.transform.scale(tile, (width, height)), (x, y))

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
        world_state: WorldState,
        now_ms: int,
    ) -> None:
        for powerup_id, started_at in list(self._powerup_collection_effects.items()):
            power_up = world_state.environment.power_ups.get(powerup_id)
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
            screen.blit(ring, (ring_x, ring_y))
            screen.blit(sprite, (x, y))

    def _render_environment(self, screen: pygame.Surface, world_state: WorldState) -> None:
        for block in world_state.environment.destructible_blocks:
            if block.destroyed:
                continue
            screen.blit(
                self._get_environment_sprite("block", "intact", block.width, block.height),
                (int(block.x), int(block.y)),
            )

        now_ms = pygame.time.get_ticks()
        seen_powerups = set()
        for power_up in world_state.environment.power_ups.values():
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
                (int(power_up.x), int(power_up.y)),
            )
        for powerup_id in set(self._powerup_collected_state) - seen_powerups:
            del self._powerup_collected_state[powerup_id]
            self._powerup_collection_effects.pop(powerup_id, None)
        self._render_powerup_collection_effects(screen, world_state, now_ms)

        for gate in world_state.environment.cooperative_gates.values():
            screen.blit(
                self._get_environment_sprite("gate", gate.state, gate.width, gate.height),
                (int(gate.x), int(gate.y)),
            )

    def _get_player_sprite(self, character: CharacterState) -> pygame.Surface:
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
        character: CharacterState,
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

    def _render_coin_counter(self, screen: pygame.Surface, world_state: WorldState) -> None:
        font = pygame.font.SysFont(None, 22)
        text = f"Coins: {world_state.coins_collected}/{world_state.coins_to_win}"
        label_surface = font.render(text, True, (255, 255, 255))
        panel_width = label_surface.get_width() + 24
        panel_height = label_surface.get_height() + 12
        panel = pygame.Surface((panel_width, panel_height), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 160))
        x = self.width - panel_width - 16
        y = 16
        screen.blit(panel, (x, y))
        screen.blit(label_surface, (x + 12, y + 6))

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

    def render(
        self,
        screen: pygame.Surface,
        world_state: WorldState,
        platforms: list[pygame.Rect],
    ) -> None:
        """Render one frame of the game world."""
        screen.fill(self.background_color)

        self._render_platforms(screen, platforms)
        self._render_coin_counter(screen, world_state)

        self._render_environment(screen, world_state)

        for character in sorted(world_state.characters.values(), key=lambda c: (c.y, c.player_id)):
            player_rect = pygame.Rect(
                int(character.x),
                int(character.y),
                int(character.width),
                int(character.height),
            )
            screen.blit(self._get_player_sprite(character), player_rect.topleft)

        if world_state.victory:
            self._render_victory_overlay(screen)

        pygame.display.flip()
