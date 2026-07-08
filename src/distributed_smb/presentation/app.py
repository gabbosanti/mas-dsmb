"""Pygame application loop for the local presentation layer."""

from collections.abc import Callable
from dataclasses import dataclass, field

import pygame

from distributed_smb.domain.game_engine import GameEngine
from distributed_smb.domain.world import WorldState
from distributed_smb.presentation.input_handler import InputHandler
from distributed_smb.presentation.renderer import Renderer
from distributed_smb.shared.config import WINDOW_HEIGHT, WINDOW_WIDTH
from distributed_smb.shared.input import InputState


@dataclass
class GameApp:
    """Owns the local Pygame window and presentation loop."""

    width: int = WINDOW_WIDTH
    height: int = WINDOW_HEIGHT
    fps: int = 60
    max_frame_dt: float = 0.05
    local_player_id: str = "player1"
    engine: GameEngine = field(default_factory=GameEngine)
    input_handler: InputHandler = field(default_factory=InputHandler)
    renderer: Renderer = field(default_factory=Renderer)
    frame_handler: Callable[[float, InputState], WorldState] | None = None

    def __post_init__(self) -> None:
        pygame.init()
        self.screen = pygame.display.set_mode((self.width, self.height))
        pygame.display.set_caption("Distributed SMB")
        self.clock = pygame.time.Clock()

    def _should_quit(self) -> bool:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return True
        return False

    def _clamp_player_to_world(self, world_state: WorldState, player_id: str) -> None:
        character = world_state.get_player(player_id)
        if character is None:
            return
        max_x = max(0, getattr(self.engine, "world_width", self.width) - character.width)
        max_y = max(0, getattr(self.engine, "world_height", self.height) - character.height)
        character.x = max(0, min(character.x, max_x))
        character.y = max(0, min(character.y, max_y))

    def _build_platform_rects(self) -> list[pygame.Rect]:
        return [
            pygame.Rect(int(p.x), int(p.y), int(p.width), int(p.height))
            for p in self.engine.platforms
        ]

    def _update_window_caption(self, world_state: WorldState) -> None:
        character = self.get_local_player(world_state)
        if character is None:
            return
        all_coords = " | ".join(
            f"p{c.join_index + 1}=({int(c.x)},{int(c.y)})"
            for c in sorted(world_state.characters.values(), key=lambda c: c.join_index)
        )
        pygame.display.set_caption(
            f"Distributed SMB [{self.local_player_id}] "
            f"| local=({int(character.x)},{int(character.y)}) "
            f"| {all_coords} seq={world_state.sequence_number}"
        )

    def run(self) -> None:
        running = True
        while running:
            dt = min(self.clock.tick(self.fps) / 1000, self.max_frame_dt)
            running = not self._should_quit()
            local_input = self.input_handler.read_input()
            render_world_state = self.frame_handler(dt, local_input)
            self._clamp_player_to_world(render_world_state, self.local_player_id)
            self._update_window_caption(render_world_state)
            self.renderer.render(
                screen=self.screen,
                world_state=render_world_state,
                platforms=self._build_platform_rects(),
                focus_player_id=self.local_player_id,
                world_size=(self.engine.world_width, self.engine.world_height),
            )
        pygame.quit()

    def get_local_player(self, world_state: WorldState | None = None):
        state = self.engine.world_state if world_state is None else world_state
        return state.get_player(self.local_player_id)
