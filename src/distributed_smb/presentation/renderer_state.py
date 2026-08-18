"""State tracking for death and respawn effects."""

from __future__ import annotations

from copy import deepcopy

from distributed_smb.application.dto import RenderFrame
from distributed_smb.presentation.renderer_support import PlayerDeathEffect


class RenderStateTracker:
    def __init__(self, owner: object) -> None:
        self.owner = owner

    def sync_player_death_effects(self, frame: RenderFrame, now_ms: int) -> None:
        respawning = frame.respawning_player_ids
        for player_id in respawning:
            if player_id in frame.characters or player_id in self.owner._death_effects:
                continue
            last_seen = self.owner._last_rendered_characters.get(player_id)
            if last_seen is None:
                continue
            self.owner._death_effects[player_id] = PlayerDeathEffect(
                character=deepcopy(last_seen),
                started_at_ms=now_ms,
            )

        for player_id in list(self.owner._death_effects):
            if player_id in frame.characters and player_id not in respawning:
                del self.owner._death_effects[player_id]

    def remember_rendered_characters(self, frame: RenderFrame) -> None:
        self.owner._last_rendered_characters = {
            player_id: deepcopy(character) for player_id, character in frame.characters.items()
        }
