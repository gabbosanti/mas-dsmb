from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.bdi.enemy_agent import EnemyAgent
from distributed_smb.agents.models import KoopaPerception
from distributed_smb.agents.perception import Vision

# A Lakitu flies, so it looks down as well as sideways: a player is seen when
# it is at most KOOPA_SIGHT_RANGE px away horizontally and KOOPA_VERTICAL_SIGHT
# px vertically, with no platform in between. Whether that player is inside
# the Lakitu's patrol area (and so worth attacking) is decided in koopa.asl.
KOOPA_SIGHT_RANGE = 300.0
KOOPA_VERTICAL_SIGHT = 300.0


class KoopaAgent(EnemyAgent):
    """
    Koopa (Lakitu) driven by koopa.asl, which groups its plans in two behaviours:
        - patrol: fly back and forth inside an area centred on its spawn point;
          no walls or voids to care about, it is in the air;
        - kill_mario: as soon as a player is inside that area, fly towards it
          and hover above it.
    """

    agent_type = AgentType.KOOPA

    player_sensor = Vision(sight_distance=KOOPA_SIGHT_RANGE)

    def _update_beliefs(self, perception: KoopaPerception, sequence: int) -> None:
        """Translate a game snapshot into facts consumed by the ASL plans."""
        center = self._sense_position(perception)

        # The patrol area is centred where the Lakitu starts: the game keeps
        # every enemy still until its agent has decided once, so the first
        # perception is taken at the spawn point.
        if not getattr(self, "_home_set", False):
            self.bdi.set_belief("home", round(center.x))
            self._home_set = True

        player = self.player_sensor.nearest_player_at_height(
            center,
            perception.players,
            perception.obstacles,
            vertical_tolerance=KOOPA_VERTICAL_SIGHT,
        )
        self._last_player = player
        self._set_single_belief(
            "player_seen",
            (round(player.center.x), round(player.center.y))
            if player is not None
            else None,
        )

        self._new_perception(perception, sequence)
