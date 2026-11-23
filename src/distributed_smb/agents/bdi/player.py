"""BDI agent that drives the character of a disconnected player.

It keeps the character playing until its owner reconnects: it runs towards
the castle and jumps over walls, pits and enemies. As for the enemies, this
class only senses and acts; every decision is in player.asl.
"""

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.bdi.game_agent import GameAgent
from distributed_smb.agents.models import (
    PlayerAction,
    PlayerActionResult,
    PlayerAgentPerception,
    SightDirection,
)
from distributed_smb.agents.perception import GroundSensor

# The character runs at 300px/s and a decision reaches the game ~120ms after
# the snapshot it was taken on (one perception every 5 ticks + dispatch +
# reasoning): ~36px. Every distance below leaves room for that.

# Jump when the surface under the feet ends within this many px ahead: it
# also makes the character jump off a step instead of walking off it.
GAP_LOOKAHEAD = 64.0
# Jump when a wall (a pipe, a step, a closed gate) is this close ahead.
WALL_DISTANCE = 60.0
# Jump over (or onto) a walking enemy this close ahead, at body height.
ENEMY_DISTANCE = 90.0
# The castle is reached when the character is this close to it.
GOAL_MARGIN = 16.0


class PlayerAgent(GameAgent):
    """
    Player agent driven by player.asl, which groups its plans in two behaviours:
        - reach_castle: run right towards the castle and stop next to it;
        - jump: jump over a pit, a wall or a walking enemy in the way.
    """

    agent_type = AgentType.PLAYER

    # Injected by SpadeRuntime right after construction.
    player_id: str | None = None

    ground_sensor = GroundSensor(lookahead=GAP_LOOKAHEAD)

    @property
    def agent_id(self) -> str | None:
        return self.player_id

    def _action_result(self, action: PlayerAction) -> PlayerActionResult:
        return PlayerActionResult(player_id=self.player_id, action=action)

    def _update_beliefs(self, perception: PlayerAgentPerception, sequence: int) -> None:
        """Translate a game snapshot into facts consumed by the ASL plans."""
        position, width, height = perception.position, perception.width, perception.height
        center = perception.center
        self._last_center = center
        self.bdi.set_belief("position", round(center.x), round(center.y))

        front = position.x + width
        facts = {
            "on_ground": perception.on_ground,
            "gap_ahead": self.ground_sensor.is_void_ahead(
                position, width, height, SightDirection.RIGHT, perception.obstacles
            ),
            "wall_ahead": self.ground_sensor.wall_ahead(
                position,
                width,
                height,
                SightDirection.RIGHT,
                perception.obstacles,
                WALL_DISTANCE,
            ),
            # A walking enemy ahead, overlapping the body vertically (a Lakitu
            # cruising above the character is not in the way).
            "enemy_ahead": any(
                enemy.position.y < position.y + height
                and enemy.position.y + enemy.height > position.y
                and -enemy.width <= enemy.position.x - front <= ENEMY_DISTANCE
                for enemy in perception.enemies
            ),
            "at_goal": perception.goal_x is not None
            and front >= perception.goal_x - GOAL_MARGIN,
        }
        for belief, present in facts.items():
            if present:
                self.bdi.set_belief(belief)
            else:
                self.bdi.remove_belief(belief)

        # This fresh event triggers exactly one ASL decision for this snapshot.
        self.bdi.set_belief("perception", sequence)

    def _add_actions(self, actions) -> None:
        @actions.add(".run", 0)
        def run(agent, term, intention):
            self._enter_behaviour("reach_castle", "running right")
            self._publish_action(PlayerAction.MOVE_RIGHT)
            yield

        @actions.add(".jump", 1)
        def jump(agent, term, intention):
            reason = self._grounded(term, intention, 0)
            center = getattr(self, "_last_center", None)
            where = f" at x={center.x:.0f}" if center is not None else ""
            self._enter_behaviour(
                "jump", f"jumping over a {reason}{where}", key=("jump", reason, where)
            )
            self._publish_action(PlayerAction.JUMP_RIGHT)
            yield

        @actions.add(".wait", 0)
        def wait(agent, term, intention):
            self._enter_behaviour("wait", "reached the castle")
            self._publish_action(PlayerAction.STOP)
            yield
