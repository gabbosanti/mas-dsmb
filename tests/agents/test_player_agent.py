"""BDI agent that drives the character of a disconnected player.

Covers player.asl (with the real SPADE-BDI agent), the game-side adapter and
the host lifecycle: disconnection -> agent -> rejoin.
"""

import pytest

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.bdi.game_agent import GameAgent
from distributed_smb.agents.bdi.player import PlayerAgent
from distributed_smb.agents.models import (
    EnemyObservation,
    ObstacleObservation,
    PlayerAction,
    PlayerActionResult,
    PlayerAgentPerception,
    Position,
)
from distributed_smb.agents.spade_runtime import SpadeRuntime
from distributed_smb.application.agent_integration import AgentGameIntegration
from distributed_smb.application.node_controller import NodeController
from distributed_smb.application.protocols import NoopGameEventBroker, NoopLobbyService
from distributed_smb.application.spade_agent_bridge import make_agent_queues
from distributed_smb.domain.game_engine import GameEngine
from distributed_smb.shared.enums import PlayerRole
from distributed_smb.shared.input import InputState
from distributed_smb.shared.messages.session import RosterUpdate
from distributed_smb.shared.roster import GlobalRoster, RosterEntry

# ---------------------------------------------------------------------------
# player.asl
# ---------------------------------------------------------------------------

# A 50x75 character at (100, 25): feet on y=100, front edge at x=150.
FLOOR = ObstacleObservation(0, 100, 1000, 32)


@pytest.fixture
def agent():
    runtime = SpadeRuntime(make_agent_queues(), make_agent_queues())
    runtime.add_player_agent("player2")  # the loop is not running: not started
    return runtime.player_agents["player2"], runtime


def decide(agent, run_bdi_steps, *, obstacles=(FLOOR,), enemies=(), on_ground=True, goal_x=None):
    agent, runtime = agent
    agent._update_beliefs(
        PlayerAgentPerception(
            "player2",
            Position(100, 25),
            50,
            75,
            on_ground=on_ground,
            goal_x=goal_x,
            obstacles=tuple(obstacles),
            enemies=tuple(enemies),
        ),
        sequence=getattr(agent, "_test_sequence", 0) + 1,
    )
    agent._test_sequence = getattr(agent, "_test_sequence", 0) + 1
    run_bdi_steps(agent, count=1)
    queue = runtime.action_queue[AgentType.PLAYER]
    actions = []
    while not queue.empty():
        result = queue.get_nowait()
        assert result.player_id == "player2"
        actions.append(result.action)
    return actions


def test_player_agent_is_a_bdi_agent_of_the_game(agent):
    assert isinstance(agent[0], PlayerAgent)
    assert isinstance(agent[0], GameAgent)
    assert str(agent[0].jid) == "player-player2@localhost"


def test_runs_right_on_flat_ground(agent, run_bdi_steps):
    assert decide(agent, run_bdi_steps) == [PlayerAction.MOVE_RIGHT]


def test_jumps_over_a_pit(agent, run_bdi_steps):
    floor_ends_soon = ObstacleObservation(0, 100, 180, 32)
    assert decide(agent, run_bdi_steps, obstacles=[floor_ends_soon]) == [
        PlayerAction.JUMP_RIGHT
    ]


@pytest.mark.parametrize(
    "wall",
    [
        ObstacleObservation(200, 4, 64, 96),  # a pipe 50px ahead
        ObstacleObservation(190, 68, 64, 32),  # a low step 40px ahead
    ],
)
def test_jumps_over_a_wall(agent, run_bdi_steps, wall):
    assert decide(agent, run_bdi_steps, obstacles=[FLOOR, wall]) == [
        PlayerAction.JUMP_RIGHT
    ]


def test_ignores_a_wall_still_far_away(agent, run_bdi_steps):
    pipe = ObstacleObservation(300, 4, 64, 96)  # 150px ahead
    assert decide(agent, run_bdi_steps, obstacles=[FLOOR, pipe]) == [
        PlayerAction.MOVE_RIGHT
    ]


def test_jumps_over_a_walking_enemy_but_not_under_a_flying_one(agent, run_bdi_steps):
    goomba = EnemyObservation("goomba", Position(200, 49), 51, 51)
    lakitu = EnemyObservation("koopa", Position(200, -150), 51, 51)

    assert decide(agent, run_bdi_steps, enemies=[goomba]) == [PlayerAction.JUMP_RIGHT]
    assert decide(agent, run_bdi_steps, enemies=[lakitu]) == [PlayerAction.MOVE_RIGHT]


def test_never_jumps_in_the_air(agent, run_bdi_steps):
    pipe = ObstacleObservation(200, 4, 64, 96)
    assert decide(agent, run_bdi_steps, obstacles=[FLOOR, pipe], on_ground=False) == [
        PlayerAction.MOVE_RIGHT
    ]


def test_stops_next_to_the_castle(agent, run_bdi_steps):
    assert decide(agent, run_bdi_steps, goal_x=160) == [PlayerAction.STOP]


def test_logs_what_it_does(agent, run_bdi_steps, caplog):
    caplog.set_level("INFO", logger="distributed_smb.agents.bdi.player")
    decide(agent, run_bdi_steps)
    decide(agent, run_bdi_steps, obstacles=[ObstacleObservation(0, 100, 180, 32)])

    assert "[player player2] start -> reach_castle: running right" in caplog.text
    assert "[player player2] reach_castle -> jump: jumping over a pit at x=125" in caplog.text


# ---------------------------------------------------------------------------
# Game side
# ---------------------------------------------------------------------------


@pytest.fixture
def driven():
    engine = GameEngine()
    engine.spawn_player("player2", x=200, y=853, join_index=1)
    integration = AgentGameIntegration(
        engine, enabled=True, perception_interval_ticks=1, player_perception_interval_ticks=1
    )
    integration.control_player_with_spade("player2")
    return engine, integration


def act(integration, action):
    integration.bridge.submit_action(
        AgentType.PLAYER, PlayerActionResult(player_id="player2", action=action)
    )


def test_the_agent_input_is_held_between_decisions(driven):
    engine, integration = driven
    player = engine.world_state.characters["player2"]
    act(integration, PlayerAction.MOVE_RIGHT)

    xs = []
    for _ in range(4):  # one decision, four ticks
        integration.tick(1 / 60, {})
        xs.append(player.x)
    assert xs == sorted(xs) and len(set(xs)) == 4


def test_a_running_jump_goes_up_and_right(driven):
    engine, integration = driven
    for _ in range(10):  # land on the floor first
        integration.tick(1 / 60, {})
    player = engine.world_state.characters["player2"]
    x, y = player.x, player.y

    act(integration, PlayerAction.JUMP_RIGHT)
    for _ in range(6):
        integration.tick(1 / 60, {})
    assert player.x > x and player.y < y


def test_released_player_is_driven_by_its_own_inputs_again(driven):
    engine, integration = driven
    player = engine.world_state.characters["player2"]
    act(integration, PlayerAction.MOVE_RIGHT)
    integration.tick(1 / 60, {})

    integration.release_player("player2")
    x = player.x
    act(integration, PlayerAction.MOVE_RIGHT)  # a late action: ignored
    integration.tick(1 / 60, {"player2": InputState()})  # the player stands still
    assert player.x == x


def test_perception_has_what_the_agent_needs(driven):
    _engine, integration = driven
    integration.tick(1 / 60, {})
    message = integration.bridge.poll_perception(AgentType.PLAYER)

    perception = message.perception
    assert perception.player_id == "player2"
    assert perception.goal_x == 6520  # the castle (final gate) of the level
    # The closed checkpoint gate is solid: it is reported as an obstacle.
    assert any(o.x == 3068 for o in perception.obstacles)
    assert {e.kind for e in perception.enemies} == {"goomba", "koopa"}


# ---------------------------------------------------------------------------
# Host lifecycle: disconnection -> agent -> rejoin
# ---------------------------------------------------------------------------


class _RosterFeed:
    """Stands in for the lobby WebSocket: delivers one RosterUpdate."""

    def __init__(self):
        self.messages = []
        self.sent = []

    def poll(self):
        return self.messages.pop(0) if self.messages else None

    def send(self, message):
        self.sent.append(message)


def _node(role: PlayerRole) -> NodeController:
    node = NodeController(
        game_event_broker=NoopGameEventBroker(), lobby_service=NoopLobbyService()
    ).bootstrap(role=role)
    node.roster.add_player(
        RosterEntry("player1", "127.0.0.1", 50000, join_index=0, is_host=True)
    )
    node.roster.add_player(RosterEntry("player2", "127.0.0.1", 50001, join_index=1))
    node._rebuild_world_from_roster()
    return node


def test_host_hands_a_disconnected_player_to_an_agent_and_back_on_rejoin():
    host = _node(PlayerRole.HOST)
    host._evict_player("player2")

    assert isinstance(host.spade_runtime.player_agents["player2"], PlayerAgent)
    assert "player2" in host.agent_integration.spade_controlled_player_ids

    # The agent moves the character on...
    character = host.engine.world_state.characters["player2"]
    character.x += 500
    moved_x = character.x

    # ...then the player rejoins (new UDP port).
    feed = _RosterFeed()
    roster = GlobalRoster()
    roster.add_player(RosterEntry("player1", "127.0.0.1", 50000, join_index=0, is_host=True))
    roster.add_player(RosterEntry("player2", "127.0.0.1", 50077, join_index=1))
    feed.messages.append(RosterUpdate(roster=roster))
    host.ws_handler = feed
    host._check_for_rejoining_players()

    assert "player2" not in host.spade_runtime.player_agents
    assert "player2" not in host.agent_integration.spade_controlled_player_ids
    assert "player2" not in host.dummy_player_ids
    # It takes the character back where the agent brought it.
    assert host.engine.world_state.characters["player2"].x == moved_x
    assert host.roster.get_player("player2").udp_port == 50077


def test_a_client_keeps_the_character_but_never_runs_an_agent():
    client = _node(PlayerRole.CLIENT)
    client._evict_player("player2")

    assert "player2" in client.engine.world_state.characters
    assert "player2" in client.dummy_player_ids
    assert client.spade_runtime.player_agents == {}


def test_a_client_promoted_to_host_drives_the_missing_players():
    node = _node(PlayerRole.CLIENT)
    node._evict_player("player2")  # marked while still a client
    assert node.spade_runtime.player_agents == {}

    node.engine.is_authoritative = True
    node._drive_replaced_players_with_agents()

    assert "player2" in node.spade_runtime.player_agents
