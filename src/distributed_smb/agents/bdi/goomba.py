from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.bdi.enemy_agent import EnemyAgent, agent_name
from distributed_smb.agents.models import GoombaPerception, SightDirection
from distributed_smb.agents.perception import GroundSensor, Vision, side_of

# Distance from the Goomba's center to a wall's nearest face that makes it
# turn: half its width (~25px) plus a ~22px gap that absorbs the BDI latency
# (one perception every 5 ticks + dispatch + reasoning ≈ 12px at 80px/s).
# It must stay below half the narrowest corridor, otherwise a Goomba between
# two pipes always sees a wall ahead and turns on every perception: in the
# level the gap between pipe-2 and pipe-3 is only 136px.
OBSTACLE_REACTION_DISTANCE = 48.0

# kill_mario behaviour: a player is noticed when it is within this horizontal
# range and at the same height, i.e. its center is at most
# PLAYER_VERTICAL_TOLERANCE px above/below the Goomba's center.
PLAYER_DETECTION_RANGE = 200.0

PLAYER_VERTICAL_TOLERANCE = 32.0

# Below this horizontal distance the side of the player is ambiguous (the
# bodies overlap): ignore it rather than flip direction at every perception.
PLAYER_SIDE_DEADBAND = 4.0


class GoombaAgent(EnemyAgent):
    """
    Goomba driven by goomba.asl, which groups its plans in three behaviours:
        - patrol: walk and turn around at walls and at the edge of platforms;
        - kill_mario: when a player is near and at the same height, walk towards
          it, unless that side is blocked by a wall or a void;
        - encircle: a Goomba that sees a player tells the other agents where it
          is (AgentSpeak ``.send``); a Goomba that learns the player stands
          between itself and the sender walks towards it to close it in.
    """

    agent_type = AgentType.GOOMBA

    ground_sensor = GroundSensor()
    bump_sensor = Vision(sight_distance=OBSTACLE_REACTION_DISTANCE)
    player_sensor = Vision(sight_distance=PLAYER_DETECTION_RANGE)

    def _update_beliefs(self, perception: GoombaPerception, sequence: int) -> None:
        """Translate a game snapshot into facts consumed by the ASL plans."""
        obstacle_ahead = self.bump_sensor.obstacle_ahead(
            position=perception.center,
            direction=perception.direction,
            obstacles=perception.obstacles,
        )
        void_ahead = self._void_towards(perception, perception.direction)

        center = self._sense_position(perception)

        # kill_mario: is a player near, and on which side?
        player = self.player_sensor.nearest_player_at_height(
            center,
            perception.players,
            perception.obstacles,
            vertical_tolerance=PLAYER_VERTICAL_TOLERANCE,
            deadband=PLAYER_SIDE_DEADBAND,
        )
        self._last_player = player
        # encircle: the sighting the ASL shares with the other agents.
        self._set_single_belief(
            "player_sighted",
            (round(player.center.x), round(player.center.y), sequence)
            if player is not None
            else None,
        )
        self._set_single_belief(
            "player_near",
            (side_of(player.center, center).value,) if player is not None else None,
        )
        # Which sides cannot be walked: a wall or a void is in the way.
        for side in SightDirection:
            self._set_flag_belief(
                "path_blocked", side.value, self._blocked_towards(perception, side)
            )

        for belief, present in (
            ("obstacle_ahead", obstacle_ahead),
            ("void_ahead", void_ahead),
            ("clear_ahead", not obstacle_ahead and not void_ahead),
        ):
            if present:
                self.bdi.set_belief(belief)
            else:
                self.bdi.remove_belief(belief)

        self._new_perception(perception, sequence)

    def _void_towards(
        self, perception: GoombaPerception, direction: SightDirection
    ) -> bool:
        return self.ground_sensor.is_void_ahead(
            position=perception.position,
            width=perception.width,
            height=perception.height,
            direction=direction,
            obstacles=perception.obstacles,
        )

    def _blocked_towards(
        self, perception: GoombaPerception, direction: SightDirection
    ) -> bool:
        """True if walking towards ``direction`` hits a wall or a void."""
        return self._void_towards(perception, direction) or self.bump_sensor.obstacle_ahead(
            position=perception.center,
            direction=direction,
            obstacles=perception.obstacles,
        )

    def _add_actions(self, actions) -> None:
        @actions.add(".encircle", 4)
        def encircle(agent, term, intention):
            side = self._grounded(term, intention, 0)
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
                        (sender_x, f"{sender} x={sender_x:.0f}"),
                        (player_x, f"player x={player_x:.0f}"),
                        (my_x, f"{self.enemy_id} x={my_x:.0f}"),
                    ]
                )
            )
            self._enter_behaviour(
                "encircle",
                f"cannot see the player, {sender} told where it is: [{line}] "
                f"-> {self.enemy_id} walks {side} to close it in",
                key=("encircle", sender),
            )
            self._walk_towards(side)
            yield
