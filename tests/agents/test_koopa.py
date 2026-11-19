"""Koopa (Lakitu) agent: runtime wiring, per-type routing and koopa.asl plans."""

import asyncio

import pytest

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.bdi.enemy_agent import EnemyAgent
from distributed_smb.agents.bdi.goomba import GoombaAgent
from distributed_smb.agents.bdi.koopa import KoopaAgent
from distributed_smb.agents.models import (
    ActionResult,
    EnemyAction,
    EnemyPerceptionMessage,
    GoombaPerception,
    KoopaPerception,
    ObstacleObservation,
    PlayerPerception,
    Position,
    SightDirection,
)
from distributed_smb.agents.spade_runtime import SpadeRuntime
from distributed_smb.application.agent_integration import AgentGameIntegration
from distributed_smb.application.spade_agent_bridge import (
    SpadeAgentBridge,
    make_agent_queues,
)
from distributed_smb.domain.game_engine import GameEngine

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


def mixed_runtime() -> SpadeRuntime:
    return SpadeRuntime(
        make_agent_queues(),
        make_agent_queues(),
        enemies={"enemy-1": AgentType.GOOMBA, "enemy-3": AgentType.KOOPA},
    )


def test_runtime_builds_the_agent_of_each_enemy_kind():
    runtime = mixed_runtime()

    assert isinstance(runtime.agents["enemy-1"], GoombaAgent)
    assert isinstance(runtime.agents["enemy-3"], KoopaAgent)
    assert all(isinstance(agent, EnemyAgent) for agent in runtime.agents.values())
    assert list(runtime.goomba_agents) == ["enemy-1"]
    assert list(runtime.koopa_agents) == ["enemy-3"]
    assert str(runtime.agents["enemy-3"].jid) == "koopa-enemy-3@localhost"


def test_every_agent_knows_the_agents_of_the_other_kinds(run_bdi_steps):
    runtime = mixed_runtime()
    goomba = runtime.agents["enemy-1"]
    run_bdi_steps(goomba, count=1)

    peers = next(b for b in goomba.bdi.get_beliefs() if b.startswith("peers"))
    assert "koopa-enemy-3@localhost" in peers


class Recorder:
    def __init__(self):
        self.perceived = []

    def perceive(self, message):
        self.perceived.append(message)

    def set_peers(self, jids):
        pass


def message(perception_cls, enemy_id, sequence):
    return EnemyPerceptionMessage(
        sequence,
        perception_cls(enemy_id, Position(0, 0), 51, 51, SightDirection.RIGHT),
    )


def test_dispatch_drains_each_kind_queue_independently():
    """Regression: an empty KOOPA queue used to swallow GOOMBA perceptions."""
    runtime = SpadeRuntime(
        make_agent_queues(),
        make_agent_queues(),
        enemies={"enemy-1": AgentType.GOOMBA, "enemy-3": AgentType.KOOPA},
        goomba_factory=lambda _id: Recorder(),
        koopa_factory=lambda _id: Recorder(),
    )
    runtime.perception_queue[AgentType.GOOMBA].put(message(GoombaPerception, "enemy-1", 5))

    assert runtime.dispatch_perceptions() == 1
    assert len(runtime.agents["enemy-1"].perceived) == 1

    runtime.perception_queue[AgentType.KOOPA].put(message(KoopaPerception, "enemy-3", 10))
    # A Koopa perception that ended up on the Goomba queue is not delivered.
    runtime.perception_queue[AgentType.GOOMBA].put(message(KoopaPerception, "enemy-3", 10))

    assert runtime.dispatch_perceptions() == 1
    assert len(runtime.agents["enemy-3"].perceived) == 1
    assert len(runtime.agents["enemy-1"].perceived) == 1


# ---------------------------------------------------------------------------
# Game side: each enemy kind uses its own queues
# ---------------------------------------------------------------------------


@pytest.fixture
def level_integration():
    engine = GameEngine()  # the real level: enemy-3 is a Lakitu
    integration = AgentGameIntegration(
        engine, enabled=True, perception_interval_ticks=1, bridge=SpadeAgentBridge()
    )
    integration.control_enemy_with_spade("enemy-1")
    integration.control_enemy_with_spade("enemy-3")
    return engine, integration


def test_each_enemy_perception_goes_to_the_queue_of_its_kind(level_integration):
    _engine, integration = level_integration
    integration.tick(1 / 60, {})

    goomba = integration.bridge.poll_perception(AgentType.GOOMBA)
    koopa = integration.bridge.poll_perception(AgentType.KOOPA)
    assert isinstance(goomba.perception, GoombaPerception)
    assert goomba.perception.enemy_id == "enemy-1"
    assert isinstance(koopa.perception, KoopaPerception)
    assert koopa.perception.enemy_id == "enemy-3"


def test_koopa_actions_are_read_from_the_koopa_queue_only(level_integration):
    engine, integration = level_integration
    lakitu = engine.world_state.environment.enemies["enemy-3"]
    start_x = lakitu.x

    # Wrong queue: a Goomba agent cannot drive a Lakitu.
    integration.bridge.submit_action(
        AgentType.GOOMBA, ActionResult("enemy-3", EnemyAction.MOVE_LEFT)
    )
    integration.tick(0.1, {})
    assert lakitu.x == start_x  # still waiting for its own agent

    integration.bridge.submit_action(
        AgentType.KOOPA, ActionResult("enemy-3", EnemyAction.MOVE_LEFT)
    )
    integration.tick(0.1, {})
    assert lakitu.x < start_x


# ---------------------------------------------------------------------------
# koopa.asl
# ---------------------------------------------------------------------------

HOME_X = 1000
LAKITU_Y = 700


@pytest.fixture
def lakitu():
    runtime = SpadeRuntime(
        make_agent_queues(), make_agent_queues(), enemies={"enemy-3": AgentType.KOOPA}
    )
    return runtime.agents["enemy-3"], runtime


# The ground: its top at y=910, a player standing on it has its center at 890.
FLOOR = ObstacleObservation(0, 910, 3000, 32)


def perceive(
    agent,
    center_x,
    sequence,
    *,
    center_y=LAKITU_Y,
    direction=SightDirection.RIGHT,
    player_x=None,
):
    players = ()
    if player_x is not None:  # a player on the ground, below the Lakitu
        players = (PlayerPerception("player1", Position(player_x - 10, 870), 20, 40),)
    agent._update_beliefs(
        KoopaPerception(
            "enemy-3",
            Position(center_x - 25, center_y - 25),
            50,
            50,
            direction,
            players,
            (FLOOR,),
        ),
        sequence,
    )


def decide(agent, runtime, center_x, sequence, **kwargs):
    perceive(agent, center_x, sequence, **kwargs)

    async def cycle():
        await agent.bdi.run()

    asyncio.run(cycle())
    queue = runtime.action_queue[AgentType.KOOPA]
    actions = []
    while not queue.empty():
        actions.append(queue.get_nowait().action)
    return actions


def test_lakitu_patrols_inside_its_area(lakitu):
    agent, runtime = lakitu
    assert decide(agent, runtime, HOME_X, 1) == [EnemyAction.MOVE_RIGHT]
    # At the right border of the area (home + 150): turn back.
    assert decide(agent, runtime, HOME_X + 150, 2) == [EnemyAction.MOVE_LEFT]
    # At the left border: turn back again.
    assert decide(
        agent, runtime, HOME_X - 150, 3, direction=SightDirection.LEFT
    ) == [EnemyAction.MOVE_RIGHT]


def test_lakitu_attacks_a_player_inside_its_area(lakitu):
    agent, runtime = lakitu
    decide(agent, runtime, HOME_X, 1)  # spawn: sets home

    # Player behind it (left) but inside the area: fly towards it.
    assert decide(agent, runtime, HOME_X + 20, 2, player_x=HOME_X - 80) == [
        EnemyAction.MOVE_LEFT
    ]
    # Right above the player: dive on it.
    assert decide(agent, runtime, HOME_X - 78, 3, player_x=HOME_X - 80) == [
        EnemyAction.DIVE
    ]


def test_lakitu_ignores_a_player_outside_its_area(lakitu):
    agent, runtime = lakitu
    decide(agent, runtime, HOME_X, 1)

    # Visible (within 300px) but beyond the area border (home - 150).
    assert decide(
        agent, runtime, HOME_X + 40, 2, direction=SightDirection.LEFT, player_x=HOME_X - 200
    ) == [EnemyAction.MOVE_LEFT]  # plain patrol, keeps its heading
    assert agent._behaviour == "patrol"


def test_lakitu_logs_when_it_starts_attacking(lakitu, caplog):
    caplog.set_level("INFO", logger="distributed_smb.agents.bdi.koopa")
    agent, runtime = lakitu
    decide(agent, runtime, HOME_X, 1)
    decide(agent, runtime, HOME_X, 2, player_x=HOME_X + 100)

    assert "[koopa enemy-3] patrol -> kill_mario: sees the player at x=1100" in caplog.text


def test_lakitu_dives_only_while_the_player_is_right_below(lakitu):
    agent, runtime = lakitu
    decide(agent, runtime, HOME_X, 1)

    # Above the player, already lower than cruise: keep diving.
    assert decide(
        agent, runtime, HOME_X, 2, center_y=LAKITU_Y + 80, player_x=HOME_X + 10
    ) == [EnemyAction.DIVE]
    # The player stepped aside (out of the dive cone): climb back up.
    assert decide(
        agent, runtime, HOME_X, 3, center_y=LAKITU_Y + 80, player_x=HOME_X + 60
    ) == [EnemyAction.RISE]


def test_lakitu_never_dives_into_the_ground(lakitu):
    agent, runtime = lakitu
    decide(agent, runtime, HOME_X, 1)

    # Bottom 15px above the floor (<= GROUND_MARGIN), player still below.
    near_floor = 910 - 15 - 25
    assert decide(
        agent, runtime, HOME_X, 2, center_y=near_floor, player_x=HOME_X + 10
    ) == [EnemyAction.RISE]
    assert "near_ground" in agent.bdi.get_beliefs()


def test_lakitu_climbs_back_to_its_cruise_altitude_then_patrols(lakitu):
    agent, runtime = lakitu
    decide(agent, runtime, HOME_X, 1)

    assert decide(agent, runtime, HOME_X, 2, center_y=LAKITU_Y + 50) == [
        EnemyAction.RISE
    ]
    # Back within altitude_margin of the cruise altitude: patrol again.
    assert decide(agent, runtime, HOME_X, 3, center_y=LAKITU_Y + 5) == [
        EnemyAction.MOVE_RIGHT
    ]


def test_lakitu_does_not_dive_on_a_player_outside_its_area(lakitu):
    agent, runtime = lakitu
    decide(agent, runtime, HOME_X + 140, 1)  # home is HOME_X + 140

    # The area spans HOME_X - 10 .. HOME_X + 290. The Lakitu (x=HOME_X) is
    # inside it; the player is right below it but 20px beyond the border.
    assert decide(
        agent,
        runtime,
        HOME_X,
        2,
        direction=SightDirection.LEFT,
        player_x=HOME_X - 20,
    ) == [EnemyAction.MOVE_LEFT]


def test_lakitu_dive_kills_the_player_in_the_game():
    """The full chain: agent DIVE -> integration -> engine -> collision."""
    engine = GameEngine()
    integration = AgentGameIntegration(engine, enabled=True)
    integration.control_enemy_with_spade("enemy-3")
    lakitu = engine.world_state.environment.enemies["enemy-3"]
    lakitu.x = 2100
    engine.spawn_player("player1", x=lakitu.x - 12, y=853)

    integration.bridge.submit_action(
        AgentType.KOOPA, ActionResult("enemy-3", EnemyAction.DIVE)
    )
    integration.tick(1 / 60, {})
    start_x, start_y = lakitu.x, lakitu.y
    for _ in range(60):  # no further action: the dive is held every tick
        integration.tick(1 / 60, {})
        if "player1" not in engine.world_state.characters:
            break

    assert lakitu.x == start_x  # straight down
    assert lakitu.y > start_y
    assert "player1" not in engine.world_state.characters  # hit
