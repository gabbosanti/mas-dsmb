"""Game-side sensor: copy raw world facts into immutable agent perceptions.

No reasoning happens here. Deciding what an enemy actually sees (field of
view, occlusion, nearest player...) is the agent's job, in the SPADE thread:
see ``distributed_smb.agents.perception``.
"""

from dataclasses import dataclass

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.models import (
    EnemyPerception,
    GoombaPerception,
    KoopaPerception,
    ObstacleObservation,
    PlayerPerception,
    Position,
    SightDirection,
)
from distributed_smb.domain.entity import Enemy, EnemyType, Player
from distributed_smb.domain.game_engine import GameEngine


class EnemyNotFoundError(LookupError):
    """Raised when an observation is requested for an unknown enemy."""


# Which SPADE agent drives each kind of enemy, and the perception it gets.
AGENT_TYPE_OF: dict[EnemyType, AgentType] = {
    EnemyType.GOOMBA: AgentType.GOOMBA,
    EnemyType.KOOPA: AgentType.KOOPA,
}
PERCEPTION_OF: dict[EnemyType, type[EnemyPerception]] = {
    EnemyType.GOOMBA: GoombaPerception,
    EnemyType.KOOPA: KoopaPerception,
}


def agent_type_of(enemy: Enemy) -> AgentType:
    """SPADE agent type that drives ``enemy``."""
    return AGENT_TYPE_OF[enemy.enemy_type]


@dataclass(slots=True)
class EnemyPerceptor:
    """Build immutable snapshots without exposing mutable simulation objects."""

    engine: GameEngine

    def observe(self, enemy_id: str) -> EnemyPerception:
        """Snapshot for ``enemy_id``, of the perception type of its kind."""
        enemy = self.engine.world_state.environment.enemies.get(enemy_id)
        if enemy is None:
            raise EnemyNotFoundError(f"Enemy {enemy_id!r} does not exist")

        return PERCEPTION_OF[enemy.enemy_type](
            enemy_id=enemy.enemy_id,
            position=Position(enemy.x, enemy.y),
            width=enemy.width,
            height=enemy.height,
            direction=SightDirection.LEFT if enemy.vx < 0 else SightDirection.RIGHT,
            players=tuple(
                self._perceive_player(player)
                for player in sorted(
                    self.engine.world_state.characters.values(),
                    key=lambda player: player.player_id,
                )
            ),
            obstacles=self.perceive_obstacles(),
        )

    @staticmethod
    def _perceive_player(player: Player) -> PlayerPerception:
        return PlayerPerception(
            player_id=player.player_id,
            position=Position(player.x, player.y),
            width=player.width,
            height=player.height,
        )

    def perceive_obstacles(self) -> tuple[ObstacleObservation, ...]:
        """Every solid rectangle in the world: platforms and intact blocks.

        To an agent they are the same thing: something that blocks sight and
        can be walked on.
        """
        solids = [
            *self.engine.platforms,
            *(
                block
                for block in self.engine.world_state.environment.destructible_blocks
                if not block.destroyed
            ),
        ]
        return tuple(
            ObstacleObservation(
                x=solid.x, y=solid.y, width=solid.width, height=solid.height
            )
            for solid in solids
        )
