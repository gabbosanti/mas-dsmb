import asyncio
import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING

import agentspeak as asp
from spade_bdi.bdi import BDIAgent
from spade_bdi.bdi_behaviour import PERCEPT_TAG, BDIBehaviour

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.models import (
    ActionResult,
    EnemyAction,
    GoombaPerception,
    GoombaPerceptionMessage,
    SightDirection,
)
from distributed_smb.agents.perception import GroundSensor, Vision, side_of

if TYPE_CHECKING:
    from distributed_smb.agents.spade_runtime import SpadeRuntime

LOGGER = logging.getLogger(__name__)

# spade_bdi's reasoning cycle never yields to the event loop on its own: it
# would spin at 100% CPU and, through the GIL, slow down the game thread.
BDI_CYCLE_PERIOD = 0.02

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

# Upper bound of interpreter steps per cycle, so a runaway plan cannot
# monopolise the SPADE event loop.
MAX_STEPS_PER_CYCLE = 64


class GoombaPatrolBehaviour(BDIBehaviour):
    #spade_bdi reasoning cycle that runs at most once every BDI_CYCLE_PERIOD.

    async def run(self):
        await super().run()  # applies queued belief changes + one step
        if self.agent.bdi_enabled:
            for _ in range(MAX_STEPS_PER_CYCLE):
                if not self.agent.bdi_agent.step():
                    break
        await asyncio.sleep(BDI_CYCLE_PERIOD)


class GoombaAgent(BDIAgent):
    """
    Goomba driven by goomba.asl, which groups its plans in three behaviours:
        - patrol: walk and turn around at walls and at the edge of platforms;
        - kill_mario: when a player is near and at the same height, walk towards
          it, unless that side is blocked by a wall or a void;
        - encircle: a Goomba that sees a player tells the other agents where it
          is (AgentSpeak ``.send``); a Goomba that learns the player stands
          between itself and the sender walks towards it to close it in.

    This class only senses (snapshot -> beliefs) and acts (ASL action -> game
    command): every decision, including who talks to whom, is in the ASL.
    """

    # Both injected by SpadeRuntime right after construction.
    spade_runtime: "SpadeRuntime"
    goomba_id: str | None = None

    ground_sensor = GroundSensor()
    bump_sensor = Vision(sight_distance=OBSTACLE_REACTION_DISTANCE)
    player_sensor = Vision(sight_distance=PLAYER_DETECTION_RANGE)

    def add_behaviour(self, behaviour, template=None):
        # BDIAgent.__init__ installs a plain BDIBehaviour: swap in the
        # throttled one, keeping the same message template.
        if isinstance(behaviour, BDIBehaviour) and not isinstance(
            behaviour, GoombaPatrolBehaviour
        ):
            behaviour = GoombaPatrolBehaviour()
        super().add_behaviour(behaviour, template)

    async def setup(self):
        LOGGER.info("Goomba agent %s started", self.goomba_id)

    def set_peers(self, jids: Iterable[str]) -> None:
        """Tell the agent which other agents it can talk to (``peers(List)``)."""
        peers = tuple(jids)
        self._peer_count = len(peers)
        self.bdi.set_belief("peers", peers)

    def perceive(self, message: GoombaPerceptionMessage) -> None:
        """Entry point used by SpadeRuntime to deliver this Goomba's perception."""
        if not getattr(self, "_logged_first_perception", False):
            LOGGER.info(
                "Goomba %s received perception %s",
                self.goomba_id,
                message.simulation_sequence,
            )
            self._logged_first_perception = True
        else:
            LOGGER.debug(
                "Goomba %s received perception %s",
                self.goomba_id,
                message.simulation_sequence,
            )
        self._update_beliefs(message.perception, message.simulation_sequence)

    def _update_beliefs(self, perception: GoombaPerception, sequence: int) -> None:
        """Translate a game snapshot into facts consumed by the ASL plans."""
        obstacle_ahead = self.bump_sensor.obstacle_ahead(
            position=perception.center,
            direction=perception.direction,
            obstacles=perception.obstacles,
        )
        
        void_ahead = self._void_towards(perception, perception.direction)

        center = perception.center
        self._last_center = center
        self.bdi.set_belief("position", round(center.x), round(center.y))

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

        self.bdi.set_belief("direction", perception.direction.value)
        # This fresh event triggers exactly one ASL decision for this snapshot.
        self.bdi.set_belief("perception", sequence)

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

    def _set_single_belief(self, name: str, args: tuple | None) -> None:
        """Keep at most one ``name(*args)`` belief, or none when ``args`` is None.

        ``remove_belief(name)`` alone would not match ``name(left)`` (different
        arity), so the arguments last set are remembered to remove them.
        """
        single_beliefs: dict[str, tuple | None] = self.__dict__.setdefault(
            "_single_beliefs", {}
        )
        previous = single_beliefs.get(name)
        if previous == args:
            return
        if args is not None:
            self.bdi.set_belief(name, *args)  # also replaces the previous one
        elif previous is not None:
            self.bdi.remove_belief(name, *previous)
        single_beliefs[name] = args

    def _set_flag_belief(self, name: str, arg: str, present: bool) -> None:
        """Add or remove ``name(arg)`` without touching ``name(other_arg)``.

        ``bdi.set_belief`` replaces every other belief with the same name, so
        it cannot hold ``path_blocked(left)`` and ``path_blocked(right)`` at
        the same time: the addition is queued directly, as spade_bdi does.
        """
        flags: set[tuple[str, str]] = self.__dict__.setdefault("_flag_beliefs", set())
        key = (name, arg)
        if present == (key in flags):
            return
        if present:
            flags.add(key)
            term = asp.Literal(name, (asp.Literal(arg),), PERCEPT_TAG)
            self.bdi_intention_buffer.append(
                (
                    asp.Trigger.addition,
                    asp.GoalType.belief,
                    term,
                    asp.runtime.Intention(),
                )
            )
        else:
            flags.discard(key)
            self.bdi.remove_belief(name, arg)

    def add_custom_actions(self, actions):
        @actions.add(".move_right", 0)
        def move_right(agent, term, intention):
            self._enter_behaviour("patrol", "walking right")
            self._publish_action(EnemyAction.MOVE_RIGHT)
            yield

        @actions.add(".move_left", 0)
        def move_left(agent, term, intention):
            self._enter_behaviour("patrol", "walking left")
            self._publish_action(EnemyAction.MOVE_LEFT)
            yield

        @actions.add(".turn_around", 1)
        def turn_around(agent, term, intention):
            self._enter_behaviour("patrol", "turning at a wall or a void")
            heading = str(asp.grounded(term.args[0], intention.scope))
            if heading == SightDirection.RIGHT.value:
                self._publish_action(EnemyAction.MOVE_LEFT)
            elif heading == SightDirection.LEFT.value:
                self._publish_action(EnemyAction.MOVE_RIGHT)
            else:
                LOGGER.warning("Goomba %s: unknown heading %r", self.goomba_id, heading)
            yield

        # .chase (kill_mario) and .encircle walk towards a side exactly like
        # .move_*: they are separate actions to tell the behaviours apart.
        @actions.add(".chase", 1)
        def chase(agent, term, intention):
            side = self._grounded(term, intention, 0)
            player = getattr(self, "_last_player", None)
            where = f"x={player.center.x:.0f}" if player is not None else "?"
            self._enter_behaviour(
                "kill_mario",
                f"sees the player at {where} on its {side}, "
                f"sighting shared with {getattr(self, '_peer_count', 0)} agents",
            )
            self._walk_towards(side)
            yield

        @actions.add(".encircle", 4)
        def encircle(agent, term, intention):
            side = self._grounded(term, intention, 0)
            sender = _agent_name(self._grounded(term, intention, 1))
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
                        (my_x, f"{self.goomba_id} x={my_x:.0f}"),
                    ]
                )
            )
            self._enter_behaviour(
                "encircle",
                f"cannot see the player, {sender} told where it is: [{line}] "
                f"-> {self.goomba_id} walks {side} to close it in",
                key=("encircle", sender),
            )
            self._walk_towards(side)
            yield

        @actions.add(".hold", 0)
        def hold(agent, term, intention):
            self._enter_behaviour(
                "wait", "the way to the player is blocked by a wall or a void"
            )
            self._publish_action(EnemyAction.STOP)
            yield

    @staticmethod
    def _grounded(term, intention, index: int) -> str:
        return str(asp.grounded(term.args[index], intention.scope)).strip('"')

    def _enter_behaviour(
        self, behaviour: str, detail: str, *, key: object = None
    ) -> None:
        """Log once per change of behaviour: what the Goomba does now, and why.

        ``key`` tells two situations of the same behaviour apart (e.g. the
        encircle partner): a new key is logged even if the behaviour is the same.
        """
        key = key if key is not None else behaviour
        if getattr(self, "_behaviour_key", None) == key:
            return
        LOGGER.info(
            "[%s] %s -> %s: %s",
            self.goomba_id,
            getattr(self, "_behaviour", None) or "start",
            behaviour,
            detail,
        )
        self._behaviour = behaviour
        self._behaviour_key = key

    def _walk_towards(self, side: str) -> None:
        if side == SightDirection.RIGHT.value:
            self._publish_action(EnemyAction.MOVE_RIGHT)
        elif side == SightDirection.LEFT.value:
            self._publish_action(EnemyAction.MOVE_LEFT)
        else:
            LOGGER.warning("Goomba %s: unknown side %r", self.goomba_id, side)

    def _publish_action(self, action: EnemyAction) -> None:
        """Publish the action selected by the ASL plan to the game thread."""
        if self.goomba_id is None:
            LOGGER.warning("Goomba agent without an enemy id, %s dropped", action)
            return
        self.spade_runtime.put_action(
            AgentType.GOOMBA,
            ActionResult(enemy_id=self.goomba_id, action=action),
        )


def _agent_name(jid: str) -> str:
    """``goomba-enemy-9@localhost`` -> ``enemy-9``."""
    local = jid.split("@", 1)[0]
    return local.removeprefix("goomba-")
