"""Pygame application loop for the local presentation layer."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import pygame

from distributed_smb.application.dto import RenderFrame
from distributed_smb.presentation.input_handler import InputHandler
from distributed_smb.presentation.renderer import Renderer
from distributed_smb.shared.config import VICTORY_OVERLAY_DURATION_S, WINDOW_HEIGHT, WINDOW_WIDTH
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
    time_provider: Callable[[], float] = time.monotonic

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

    def run(self) -> str:
        """Returns "quit" or "victory" — host and client each reach "victory"
        independently from their own local frame.victory."""
        running = True
        outcome = "quit"
        victory_since: float | None = None
        while running:
            dt = min(self.clock.tick(self.fps) / 1000, self.max_frame_dt)
            if self._should_quit():
                break
            local_input = self.input_handler.read_input()
            frame = self.frame_handler(dt, local_input)
            self._update_window_caption(frame)
            self.renderer.render(screen=self.screen, frame=frame)

            if frame.victory:
                if victory_since is None:
                    victory_since = self.time_provider()
                elif self.time_provider() - victory_since >= VICTORY_OVERLAY_DURATION_S:
                    outcome = "victory"
                    running = False
            else:
                victory_since = None

        if outcome != "victory":
            pygame.quit()
        return outcome
