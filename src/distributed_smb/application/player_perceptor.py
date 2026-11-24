"""Game-side sensor for the agents that drive disconnected players.

Like ``EnemyPerceptor``, it only copies raw facts from the world: what to do
with them (jump, run, stop) is decided by the agent.
"""

from dataclasses import dataclass

from distributed_smb.agents.models import (
    EnemyObservation,
    ObstacleObservation,
    PlayerAgentPerception,
    Position,
)
from distributed_smb.application.enemy_perceptor import EnemyPerceptor
from distributed_smb.domain.game_engine import GameEngine


@dataclass(slots=True)
class PlayerPerceptor:
    engine: GameEngine

    def observe(self, player_id: str) -> PlayerAgentPerception | None:
        """Snapshot for ``player_id``, or None while it is dead (respawning)."""
        world = self.engine.world_state
        player = world.get_player(player_id)
        if player is None:
            return None
        gates = world.environment.cooperative_gates.values()
        final_gates = [gate.x for gate in gates if gate.is_final]
        return PlayerAgentPerception(
            player_id=player.player_id,
            position=Position(player.x, player.y),
            width=player.width,
            height=player.height,
            on_ground=player.on_ground,
            goal_x=min(final_gates) if final_gates else None,
            # Closed gates are solid: for the player they are walls.
            obstacles=EnemyPerceptor(self.engine).perceive_obstacles()
            + tuple(
                ObstacleObservation(gate.x, gate.y, gate.width, gate.height)
                for gate in gates
                if gate.state == "closed"
            ),
            enemies=tuple(
                EnemyObservation(
                    kind=enemy.enemy_type.value,
                    position=Position(enemy.x, enemy.y),
                    width=enemy.width,
                    height=enemy.height,
                )
                for enemy in world.environment.enemies.values()
            ),
        )
