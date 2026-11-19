"""Behavioural tests for the Goombas' encircle behaviour (goomba.asl).

The agents are the real SPADE-BDI ones and they talk with the real
AgentSpeak ``.send``; only the XMPP transport is replaced by an in-memory
post office, so the suite needs no server.
"""

import asyncio

import pytest

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.models import (
    EnemyAction,
    GoombaPerception,
    ObstacleObservation,
    PlayerPerception,
    Position,
    SightDirection,
)
from distributed_smb.agents.spade_runtime import SpadeRuntime
from distributed_smb.application.spade_agent_bridge import make_agent_queues

FLOOR = ObstacleObservation(0, 100, 3000, 32)
GOOMBA_WIDTH, GOOMBA_HEIGHT = 40, 50


class Squad:
    """A few real Goomba agents wired to an in-memory post office."""

    def __init__(self, *enemy_ids: str) -> None:
        self.runtime = SpadeRuntime(
            make_agent_queues(),
            make_agent_queues(),
            enemies={enemy_id: AgentType.GOOMBA for enemy_id in enemy_ids},
        )
        self.agents = self.runtime.goomba_agents
        self.sent: list = []
        inboxes = {
            SpadeRuntime.jid_for(enemy_id): [] for enemy_id in enemy_ids
        }
        for enemy_id, agent in self.agents.items():
            jid = SpadeRuntime.jid_for(enemy_id)

            def post(message, sender=jid):
                message.sender = sender
                self.sent.append(message)
                inboxes[str(message.to)].append(message)

            async def receive(timeout=None, inbox=inboxes[jid]):
                return inbox.pop(0) if inbox else None

            agent.bdi.send = post
            agent.submit = lambda _result: None
            agent.bdi.receive = receive

    def perceive(self, enemy_id, center_x, *, sequence, players=(), direction=SightDirection.RIGHT, obstacles=(FLOOR,)):
        self.agents[enemy_id]._update_beliefs(
            GoombaPerception(
                enemy_id=enemy_id,
                position=Position(center_x - GOOMBA_WIDTH / 2, 100 - GOOMBA_HEIGHT),
                width=GOOMBA_WIDTH,
                height=GOOMBA_HEIGHT,
                direction=direction,
                players=tuple(players),
                obstacles=tuple(obstacles),
            ),
            sequence=sequence,
        )

    def think(self, cycles=3):
        """Run every agent's BDI cycle a few times (one message is read per cycle)."""

        async def advance():
            for _ in range(cycles):
                for agent in self.agents.values():
                    await agent.bdi.run()

        asyncio.run(advance())

    def actions(self) -> dict[str, list[EnemyAction]]:
        queue = self.runtime.action_queue[AgentType.GOOMBA]
        result: dict[str, list[EnemyAction]] = {}
        while not queue.empty():
            item = queue.get_nowait()
            result.setdefault(item.enemy_id, []).append(item.action)
        return result

    def told(self, illocution):
        return [m for m in self.sent if m.get_metadata("ilf_type") == illocution]


def player(center_x: float, center_y: float = 75) -> PlayerPerception:
    return PlayerPerception("player1", Position(center_x - 10, center_y - 20), 20, 40)


@pytest.fixture
def squad():
    return Squad("enemy-1", "enemy-2", "enemy-3")


def test_runtime_gives_every_agent_the_addresses_of_the_others(squad):
    squad.think(1)
    beliefs = squad.agents["enemy-1"].bdi.get_beliefs()
    peers = next(belief for belief in beliefs if belief.startswith("peers"))

    assert "goomba-enemy-2@localhost" in peers
    assert "goomba-enemy-3@localhost" in peers
    assert "goomba-enemy-1@localhost" not in peers


def test_a_goomba_that_sees_a_player_tells_the_others_where_it_is(squad):
    squad.perceive("enemy-1", 300, sequence=5, players=[player(400)])
    squad.think()

    told = squad.told("tell")
    assert sorted(str(m.to) for m in told) == [
        "goomba-enemy-2@localhost",
        "goomba-enemy-3@localhost",
    ]
    # player_spotted(PlayerX, PlayerY, SenderX, Tick)
    assert {m.body for m in told} == {"player_spotted(400, 75, 300, 5)"}


def test_goomba_on_the_other_side_of_the_player_closes_in(squad):
    # enemy-1 (x=300) sees the player (x=400); enemy-2 (x=800) is too far to
    # see it (range 200) but the player stands between the two.
    squad.perceive("enemy-1", 300, sequence=5, players=[player(400)])
    squad.think()
    squad.perceive("enemy-2", 800, sequence=10, players=[player(400)])
    squad.think()

    actions = squad.actions()
    assert actions["enemy-1"] == [EnemyAction.MOVE_RIGHT]  # chases
    assert actions["enemy-2"] == [EnemyAction.MOVE_LEFT]  # closes in, heading was right


def test_goomba_on_the_same_side_as_the_sender_keeps_patrolling(squad):
    # enemy-3 (x=100) is behind enemy-1: the player is not between them.
    squad.perceive("enemy-1", 300, sequence=5, players=[player(400)])
    squad.think()
    squad.perceive("enemy-3", 100, sequence=10, direction=SightDirection.LEFT)
    squad.think()

    assert squad.actions()["enemy-3"] == [EnemyAction.MOVE_LEFT]


def test_goomba_too_far_or_at_another_height_does_not_join(squad):
    squad.perceive("enemy-1", 300, sequence=5, players=[player(400)])
    squad.think()
    # 1200px from the player: beyond encircle_range (600).
    squad.perceive("enemy-2", 1600, sequence=10)
    squad.think()
    assert squad.actions()["enemy-2"] == [EnemyAction.MOVE_RIGHT]

    # Close enough, but on a platform 300px higher.
    high_floor = ObstacleObservation(0, -200, 3000, 32)
    squad.agents["enemy-3"]._update_beliefs(
        GoombaPerception(
            "enemy-3", Position(780, -250), GOOMBA_WIDTH, GOOMBA_HEIGHT,
            SightDirection.RIGHT, (), (high_floor,),
        ),
        sequence=10,
    )
    squad.think()
    assert squad.actions()["enemy-3"] == [EnemyAction.MOVE_RIGHT]


def test_goomba_waits_when_the_way_to_the_player_is_blocked(squad):
    squad.perceive("enemy-1", 300, sequence=5, players=[player(400)])
    squad.think()
    # enemy-2's platform starts right at its feet: a void separates it from the player.
    island = ObstacleObservation(780, 100, 500, 32)
    squad.perceive("enemy-2", 800, sequence=10, obstacles=(island,))
    squad.think()

    assert squad.actions()["enemy-2"] == [EnemyAction.STOP]


def test_sighting_is_withdrawn_when_the_player_is_lost(squad):
    squad.perceive("enemy-1", 300, sequence=5, players=[player(400)])
    squad.think()
    squad.perceive("enemy-1", 305, sequence=10)  # player gone
    squad.think()

    assert {m.body for m in squad.told("untell")} == {"player_spotted(400, 75, 300, 5)"}
    squad.actions()
    squad.perceive("enemy-2", 800, sequence=15)
    squad.think()
    assert squad.actions()["enemy-2"] == [EnemyAction.MOVE_RIGHT]  # back to patrol
    assert not any(
        belief.startswith("player_spotted")
        for belief in squad.agents["enemy-2"].bdi.get_beliefs()
    )


def test_sighting_is_refreshed_only_every_sighting_refresh_ticks(squad):
    for sequence in range(5, 65, 5):  # 12 perceptions within 60 ticks
        squad.perceive("enemy-1", 300, sequence=sequence, players=[player(400)])
        squad.think(1)
    assert len(squad.told("tell")) == 2  # one announcement, to two peers

    squad.perceive("enemy-1", 320, sequence=65, players=[player(410)])
    squad.think()
    assert {m.body for m in squad.told("tell")} == {
        "player_spotted(400, 75, 300, 5)",
        "player_spotted(410, 75, 320, 65)",
    }
    # The old sighting was withdrawn: receivers hold a single one per sender.
    spotted = [
        belief
        for belief in squad.agents["enemy-2"].bdi.get_beliefs()
        if belief.startswith("player_spotted")
    ]
    assert spotted == ["player_spotted(410, 75, 320, 65)"]


def test_stale_sighting_from_a_silent_agent_expires(squad):
    """If the sender dies before withdrawing, the sighting must not last forever."""
    squad.perceive("enemy-1", 300, sequence=5, players=[player(400)])
    squad.think()
    squad.perceive("enemy-2", 800, sequence=5 + 180)  # sighting_ttl ticks later
    squad.think()

    assert squad.actions()["enemy-2"] == [EnemyAction.MOVE_RIGHT]


def test_both_goombas_log_their_decision_to_close_in(squad, caplog):
    caplog.set_level("INFO", logger="distributed_smb.agents.bdi.goomba")
    squad.perceive("enemy-1", 300, sequence=5, players=[player(400)])
    squad.think()
    squad.perceive("enemy-2", 800, sequence=10, players=[player(400)])
    squad.think()

    assert "[goomba enemy-1] start -> kill_mario: sees the player at x=400" in caplog.text
    assert (
        "[goomba enemy-2] start -> encircle: cannot see the player, enemy-1 told where it "
        "is: [enemy-1 x=300 | player x=400 | enemy-2 x=800] -> enemy-2 walks left"
    ) in caplog.text
