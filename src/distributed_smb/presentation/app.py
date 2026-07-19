"""Pygame application loop for the local presentation layer."""

from collections.abc import Callable
from dataclasses import dataclass, field

import pygame

from distributed_smb.application.dto import RenderFrame
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
    input_handler: InputHandler = field(default_factory=InputHandler)
    renderer: Renderer = field(default_factory=Renderer)
    frame_handler: Callable[[float, InputState], RenderFrame] | None = None

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

    def _update_window_caption(self, frame: RenderFrame) -> None:
        character = frame.characters.get(self.local_player_id)
        if character is None:
            return
        all_coords = " | ".join(
            f"p{c.join_index + 1}=({int(c.x)},{int(c.y)})"
            for c in sorted(frame.characters.values(), key=lambda c: c.join_index)
        )
        pygame.display.set_caption(
            f"Distributed SMB [{self.local_player_id}] "
            f"| local=({int(character.x)},{int(character.y)}) "
            f"| {all_coords} seq={frame.sequence_number}"
        )

    def run(self) -> None:
        running = True
        while running:
            dt = min(self.clock.tick(self.fps) / 1000, self.max_frame_dt)
            running = not self._should_quit()
            local_input = self.input_handler.read_input()
            frame = self.frame_handler(dt, local_input)
            self._update_window_caption(frame)
            self.renderer.render(screen=self.screen, frame=frame)
        pygame.quit()
