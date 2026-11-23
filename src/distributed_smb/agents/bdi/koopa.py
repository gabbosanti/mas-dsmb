from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.bdi.enemy_agent import EnemyAgent, agent_name
from distributed_smb.agents.models import EnemyAction, KoopaPerception
from distributed_smb.agents.perception import GroundSensor, Vision

# A Lakitu flies, so it looks down as well as sideways: a player is seen when
# it is at most KOOPA_SIGHT_RANGE px away horizontally and KOOPA_VERTICAL_SIGHT
# px vertically, with no platform in between. Whether that player is inside
# the Lakitu's patrol area (and so worth attacking) is decided in koopa.asl.
KOOPA_SIGHT_RANGE = 300.0
KOOPA_VERTICAL_SIGHT = 300.0

# The engine applies no collision to enemies, so the Lakitu itself stops a
# dive before the ground: near_ground holds when the free space under it is
# at most this much. It covers the distance flown between two decisions
# (one perception every 5 ticks ≈ 83ms, i.e. ~20px at 240px/s).
GROUND_MARGIN = 24.0


class KoopaAgent(EnemyAgent):
    """
    Koopa (Lakitu) driven by koopa.asl, which groups its plans in three behaviours:
        - patrol: fly back and forth inside an area centred on its spawn point,
          at its spawn altitude; no walls or voids to care about, it is in the air;
        - kill_mario: as soon as a player is inside that area, fly over it and
          dive on it; then climb back to its cruise altitude;
        - block: a Goomba tells it that it is chasing a player, and the player
          stands between that Goomba and this Lakitu: dive down where it is and
          stay there, to block the player's escape.
    """

    agent_type = AgentType.KOOPA

    player_sensor = Vision(sight_distance=KOOPA_SIGHT_RANGE)
    ground_sensor = GroundSensor()

    def _update_beliefs(self, perception: KoopaPerception, sequence: int) -> None:
        """Translate a game snapshot into facts consumed by the ASL plans."""
        center = self._sense_position(perception)

        # The patrol area and the cruise altitude are where the Lakitu starts:
        # the game keeps every enemy still until its agent has decided once,
        # so the first perception is taken at the spawn point.
        if not getattr(self, "_home_set", False):
            self.bdi.set_belief("home", round(center.x))
            self.bdi.set_belief("cruise", round(center.y))
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

        clearance = self.ground_sensor.clearance_below(
            perception.position,
            perception.width,
            perception.height,
            perception.obstacles,
        )
        if clearance is not None and clearance <= GROUND_MARGIN:
            self.bdi.set_belief("near_ground")
        else:
            self.bdi.remove_belief("near_ground")

        self._new_perception(perception, sequence)

    def _add_actions(self, actions) -> None:
        @actions.add(".dive", 0)
        def dive(agent, term, intention):
            player = getattr(self, "_last_player", None)
            where = (
                f"x={player.center.x:.0f} y={player.center.y:.0f}"
                if player is not None
                else "?"
            )
            self._enter_behaviour("dive", f"the player is right below it ({where})")
            self._publish_action(EnemyAction.DIVE)
            yield

        @actions.add(".block", 4)
        def block(agent, term, intention):
            # stay: already down at the ground, hold there; otherwise descend.
            phase = self._grounded(term, intention, 0)
            sender = agent_name(self._grounded(term, intention, 1))
            sender_x = float(self._grounded(term, intention, 2))
            player_x = float(self._grounded(term, intention, 3))
            center = getattr(self, "_last_center", None)
            my_x = center.x if center is not None else float("nan")
            # Lay the three of them out left to right for the log.
            line = " | ".join(
                label
                for _, label in sorted(
                    [
                        (sender_x, f"goomba {sender} x={sender_x:.0f}"),
                        (player_x, f"player x={player_x:.0f}"),
                        (my_x, f"lakitu {self.enemy_id} x={my_x:.0f}"),
                    ]
                )
            )
            self._enter_behaviour(
                "block",
                f"{sender} is chasing the player towards it: [{line}] "
                f"-> diving to block the way",
                key=("block", sender),
            )
            self._publish_action(
                EnemyAction.STOP if phase == "stay" else EnemyAction.DIVE
            )
            yield

        @actions.add(".rise", 0)
        def rise(agent, term, intention):
            self._enter_behaviour("rise", "climbing back to its cruise altitude")
            self._publish_action(EnemyAction.RISE)
            yield
