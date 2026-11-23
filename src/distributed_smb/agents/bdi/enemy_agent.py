"""Common base of the SPADE-BDI enemy agents (Goomba, Koopa, ...).

On top of ``GameAgent`` it adds the actions every enemy shares and publishes
``ActionResult`` messages. A concrete enemy declares its ``agent_type``,
turns its perception into beliefs (``_update_beliefs``) and, if needed, adds
the ASL actions that only it knows (``_add_actions``).
"""

from distributed_smb.agents.bdi.game_agent import (  # noqa: F401 (re-exported)
    BDI_CYCLE_PERIOD,
    MAX_STEPS_PER_CYCLE,
    GameAgent,
    ThrottledBDIBehaviour,
    agent_name,
)
from distributed_smb.agents.models import (
    ActionResult,
    EnemyAction,
    EnemyPerception,
    Position,
    SightDirection,
)


class EnemyAgent(GameAgent):
    """Shared machinery of every enemy agent."""

    # Injected by SpadeRuntime right after construction.
    enemy_id: str | None = None

    @property
    def agent_id(self) -> str | None:
        return self.enemy_id

    def _action_result(self, action: EnemyAction) -> ActionResult:
        return ActionResult(enemy_id=self.enemy_id, action=action)

    # ------------------------------------------------------------------ #
    # Helpers for _update_beliefs
    # ------------------------------------------------------------------ #

    def _sense_position(self, perception: EnemyPerception) -> Position:
        """Set ``position(X, Y)`` (the agent's center) and return it."""
        center = perception.center
        self._last_center = center
        self.bdi.set_belief("position", round(center.x), round(center.y))
        return center

    def _new_perception(self, perception: EnemyPerception, sequence: int) -> None:
        """Last step of ``_update_beliefs``: heading + the decision event."""
        self.bdi.set_belief("direction", perception.direction.value)
        # This fresh event triggers exactly one ASL decision for this snapshot.
        self.bdi.set_belief("perception", sequence)

    # ------------------------------------------------------------------ #
    # ASL actions shared by every enemy
    # ------------------------------------------------------------------ #

    def add_custom_actions(self, actions):
        @actions.add(".move_right", 0)
        def move_right(agent, term, intention):
            self._enter_behaviour("patrol", "moving right")
            self._publish_action(EnemyAction.MOVE_RIGHT)
            yield

        @actions.add(".move_left", 0)
        def move_left(agent, term, intention):
            self._enter_behaviour("patrol", "moving left")
            self._publish_action(EnemyAction.MOVE_LEFT)
            yield

        @actions.add(".turn_around", 1)
        def turn_around(agent, term, intention):
            # Turn away from the heading of the snapshot that triggered the
            # plan (passed by the ASL), never from the current belief: a newer
            # perception may have changed it. The move is absolute, so repeated
            # turns for stale snapshots are no-ops instead of cancelling out.
            self._enter_behaviour("patrol", "turning around")
            heading = self._grounded(term, intention, 0)
            if heading == SightDirection.RIGHT.value:
                self._publish_action(EnemyAction.MOVE_LEFT)
            elif heading == SightDirection.LEFT.value:
                self._publish_action(EnemyAction.MOVE_RIGHT)
            else:
                self.logger.warning("%s: unknown heading %r", self.enemy_id, heading)
            yield

        @actions.add(".chase", 1)
        def chase(agent, term, intention):
            # kill_mario: move towards the player's side. A separate action
            # from .move_* only to tell the behaviours apart in the logs.
            side = self._grounded(term, intention, 0)
            player = getattr(self, "_last_player", None)
            where = f"x={player.center.x:.0f}" if player is not None else "?"
            self._enter_behaviour(
                "kill_mario", f"sees the player at {where} on its {side}"
            )
            self._walk_towards(side)
            yield

        @actions.add(".hold", 0)
        def hold(agent, term, intention):
            self._enter_behaviour("wait", "holding its position")
            self._publish_action(EnemyAction.STOP)
            yield

        super().add_custom_actions(actions)  # the enemy's own actions

    def _walk_towards(self, side: str) -> None:
        if side == SightDirection.RIGHT.value:
            self._publish_action(EnemyAction.MOVE_RIGHT)
        elif side == SightDirection.LEFT.value:
            self._publish_action(EnemyAction.MOVE_LEFT)
        else:
            self.logger.warning("%s: unknown side %r", self.enemy_id, side)
