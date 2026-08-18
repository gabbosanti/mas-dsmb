"""Camera and world-bound calculations for rendering."""

from __future__ import annotations

import pygame

from distributed_smb.application.dto import RenderFrame


class CameraController:
    def __init__(self, owner: object) -> None:
        self.owner = owner

    def world_bounds(self, frame: RenderFrame, platforms: list[pygame.Rect]) -> tuple[int, int]:
        if frame.world_width and frame.world_height:
            return max(self.owner.width, frame.world_width), max(self.owner.height, frame.world_height)

        max_right = self.owner.width
        max_bottom = self.owner.height

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

    def camera_offset(self, frame: RenderFrame, platforms: list[pygame.Rect]) -> tuple[int, int]:
        if frame.focus_player_id is None:
            return 0, 0

        focus = frame.characters.get(frame.focus_player_id)
        if focus is None:
            return self.owner._last_camera_offset

        world_width, world_height = self.world_bounds(frame, platforms)
        target_x = focus.x + focus.width / 2 - self.owner.width / 2
        target_y = focus.y + focus.height / 2 - self.owner.height / 2
        max_x = max(0, world_width - self.owner.width)
        max_y = max(0, world_height - self.owner.height)
        return round(max(0, min(target_x, max_x))), round(max(0, min(target_y, max_y)))
