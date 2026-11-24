"""Goomba -> Lakitu communication: the Lakitu dives to block a chased player.

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
    KoopaPerception,
    ObstacleObservation,
    PlayerPerception,
    Position,
    SightDirection,
)
from distributed_smb.agents.spade_runtime import SpadeRuntime
from distributed_smb.application.spade_agent_bridge import make_agent_queues

# The ground: its top at y=100. A Goomba (40x50) stands on it; a Lakitu
# (50x50) cruises with its center at y=-100; a player on the ground has its
# center at y=75.
FLOOR = ObstacleObservation(0, 100, 3000, 32)
CRUISE_Y = -100


class Squad:
    """Real Goomba and Lakitu agents wired to an in-memory post office."""

    def __init__(self, **enemies: AgentType) -> None:
        self.runtime = SpadeRuntime(
            make_agent_queues(), make_agent_queues(), enemies=enemies
        )
        self.agents = self.runtime.agents
        self.sent: list = []
        jids = {
            enemy_id: SpadeRuntime.jid_for(enemy_id, agent_type)
            for enemy_id, agent_type in enemies.items()
        }
        inboxes = {jid: [] for jid in jids.values()}
        for enemy_id, agent in self.agents.items():
            jid = jids[enemy_id]

            def post(message, sender=jid):
                message.sender = sender
                self.sent.append(message)
                inboxes[str(message.to)].append(message)

            async def receive(timeout=None, inbox=inboxes[jid]):
                return inbox.pop(0) if inbox else None

            agent.bdi.send = post
            agent.submit = lambda _result: None
            agent.bdi.receive = receive

    def goomba_sees(self, enemy_id, center_x, *, sequence, player_x=None):
        players = () if player_x is None else (player(player_x),)
        self.agents[enemy_id]._update_beliefs(
            GoombaPerception(
                enemy_id,
                Position(center_x - 20, 50),
                40,
                50,
                SightDirection.RIGHT,
                players,
                (FLOOR,),
            ),
            sequence=sequence,
        )

    def lakitu_sees(
        self, enemy_id, center_x, *, sequence, center_y=CRUISE_Y,
        direction=SightDirection.RIGHT, obstacles=(FLOOR,),
    ):
        self.agents[enemy_id]._update_beliefs(
            KoopaPerception(
                enemy_id,
                Position(center_x - 25, center_y - 25),
                50,
                50,
                direction,
                (),
                tuple(obstacles),
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
        result: dict[str, list[EnemyAction]] = {}
        for agent_type in (AgentType.GOOMBA, AgentType.KOOPA):
            queue = self.runtime.action_queue[agent_type]
            while not queue.empty():
                item = queue.get_nowait()
                result.setdefault(item.enemy_id, []).append(item.action)
        return result

    def told(self, illocution):
        return [m for m in self.sent if m.get_metadata("ilf_type") == illocution]


def player(center_x: float) -> PlayerPerception:
    return PlayerPerception("player1", Position(center_x - 10, 55), 20, 40)


@pytest.fixture
def squad():
    squad = Squad(
        **{
            "enemy-1": AgentType.GOOMBA,
            "enemy-2": AgentType.GOOMBA,
            "enemy-3": AgentType.KOOPA,
        }
    )
    # The Lakitu's first perception sets its home and cruise altitude.
    squad.lakitu_sees("enemy-3", 700, sequence=1)
    squad.think(1)
    squad.actions()
    return squad


def test_goomba_tells_where_the_player_is_to_the_lakitus_only(squad):
    squad.goomba_sees("enemy-1", 300, sequence=5, player_x=400)
    squad.think()

    told = squad.told("tell")
    assert [str(m.to) for m in told] == ["koopa-enemy-3@localhost"]
    # player_spotted(PlayerX, PlayerY, SenderX, Tick)
    assert told[0].body == "player_spotted(400, 75, 300, 5)"


def test_lakitu_dives_to_block_a_player_chased_towards_it(squad):
    # Goomba (x=300) -> player (x=400) -> Lakitu (x=700): the Lakitu cannot
    # attack the player itself (outside its area), but blocks its way.
    squad.goomba_sees("enemy-1", 300, sequence=5, player_x=400)
    squad.think()
    squad.lakitu_sees("enemy-3", 700, sequence=10)
    squad.think()

    actions = squad.actions()
    assert actions["enemy-1"] == [EnemyAction.MOVE_RIGHT]  # the Goomba chases
    assert actions["enemy-3"] == [EnemyAction.DIVE]  # the Lakitu dives down


def test_lakitu_stays_down_at_the_ground_while_blocking(squad):
    squad.goomba_sees("enemy-1", 300, sequence=5, player_x=400)
    squad.think()
    # Bottom 15px above the floor.
    squad.lakitu_sees("enemy-3", 700, sequence=10, center_y=100 - 15 - 25)
    squad.think()

    assert squad.actions()["enemy-3"] == [EnemyAction.STOP]


def test_lakitu_on_the_goomba_side_does_not_block(squad):
    # Lakitu (x=700) -> Goomba (x=900) -> player (x=1000): the player runs away
    # from the Lakitu, there is nothing to block.
    squad.goomba_sees("enemy-1", 900, sequence=5, player_x=1000)
    squad.think()
    squad.lakitu_sees("enemy-3", 700, sequence=10)
    squad.think()

    assert squad.actions()["enemy-3"] == [EnemyAction.MOVE_RIGHT]  # patrol


def test_lakitu_too_far_from_the_player_does_not_block(squad):
    squad.goomba_sees("enemy-1", 100, sequence=5, player_x=200)  # 500px away
    squad.think()
    squad.lakitu_sees("enemy-3", 700, sequence=10)
    squad.think()

    assert squad.actions()["enemy-3"] == [EnemyAction.MOVE_RIGHT]


def test_lakitu_climbs_back_when_the_goomba_loses_the_player(squad):
    squad.goomba_sees("enemy-1", 300, sequence=5, player_x=400)
    squad.think()
    squad.lakitu_sees("enemy-3", 700, sequence=10, center_y=0)  # half way down
    squad.think()
    assert squad.actions()["enemy-3"] == [EnemyAction.DIVE]

    squad.goomba_sees("enemy-1", 305, sequence=15)  # player gone
    squad.think()
    assert {m.body for m in squad.told("untell")} == {"player_spotted(400, 75, 300, 5)"}
    squad.lakitu_sees("enemy-3", 700, sequence=20, center_y=20)
    squad.think()
    assert squad.actions()["enemy-3"] == [EnemyAction.RISE]


def test_stale_sighting_from_a_silent_goomba_expires(squad):
    """If the Goomba dies before withdrawing, the block must not last forever."""
    squad.goomba_sees("enemy-1", 300, sequence=5, player_x=400)
    squad.think()
    squad.lakitu_sees("enemy-3", 700, sequence=5 + 180)  # sighting_ttl later
    squad.think()

    assert squad.actions()["enemy-3"] == [EnemyAction.MOVE_RIGHT]


def test_goombas_no_longer_encircle_each_other(squad):
    # Goomba enemy-2 (x=800) is beyond the player, too far to see it (200px):
    # it gets no message and keeps patrolling.
    squad.goomba_sees("enemy-1", 300, sequence=5, player_x=400)
    squad.think()
    squad.goomba_sees("enemy-2", 800, sequence=10)
    squad.think()

    assert squad.actions()["enemy-2"] == [EnemyAction.MOVE_RIGHT]
    assert not any(
        b.startswith("player_spotted") for b in squad.agents["enemy-2"].bdi.get_beliefs()
    )


def test_sighting_is_refreshed_only_every_sighting_refresh_ticks(squad):
    for sequence in range(5, 65, 5):  # 12 perceptions within 60 ticks
        squad.goomba_sees("enemy-1", 300, sequence=sequence, player_x=400)
        squad.think(1)
    assert len(squad.told("tell")) == 1  # one announcement, to the one Lakitu

    squad.goomba_sees("enemy-1", 320, sequence=65, player_x=410)
    squad.think()
    assert [m.body for m in squad.told("tell")] == [
        "player_spotted(400, 75, 300, 5)",
        "player_spotted(410, 75, 320, 65)",
    ]


def test_both_agents_log_their_part(squad, caplog):
    caplog.set_level("INFO", logger="distributed_smb.agents.bdi.goomba")
    caplog.set_level("INFO", logger="distributed_smb.agents.bdi.koopa")
    squad.goomba_sees("enemy-1", 300, sequence=5, player_x=400)
    squad.think()
    squad.lakitu_sees("enemy-3", 700, sequence=10)
    squad.think()

    assert "[goomba enemy-1] start -> kill_mario: sees the player at x=400" in caplog.text
    assert (
        "[koopa enemy-3] patrol -> block: enemy-1 is chasing the player towards it: "
        "[goomba enemy-1 x=300 | player x=400 | lakitu enemy-3 x=700] "
        "-> diving to block the way"
    ) in caplog.text


# Over a pit: no ground under the Lakitu. The player (center y=75) stands on
# the edge of the pit, block_margin is 24px.
PIT_EDGE = ObstacleObservation(0, 100, 450, 32)  # the floor ends at x=450


@pytest.mark.parametrize(
    ("center_y", "expected"),
    [
        (75 - 30, EnemyAction.DIVE),  # still above the player's height
        (75 - 10, EnemyAction.STOP),  # at the player's height: stop there
        (75 + 3, EnemyAction.STOP),  # slightly past it (latency): no bounce up
    ],
)
def test_lakitu_over_a_pit_stops_at_the_player_height(squad, center_y, expected):
    squad.goomba_sees("enemy-1", 300, sequence=5, player_x=400)
    squad.think()
    squad.lakitu_sees(
        "enemy-3", 700, sequence=10, center_y=center_y, obstacles=(PIT_EDGE,)
    )
    squad.think()

    assert squad.actions()["enemy-3"] == [expected]


def test_lakitu_over_a_pit_holds_steady_while_blocking(squad):
    squad.goomba_sees("enemy-1", 300, sequence=5, player_x=400)
    squad.think()
    actions = []
    for sequence, center_y in enumerate([65, 70, 78, 66], start=10):
        squad.lakitu_sees(
            "enemy-3", 700, sequence=sequence, center_y=center_y, obstacles=(PIT_EDGE,)
        )
        squad.think()
        actions += squad.actions()["enemy-3"]

    assert actions == [EnemyAction.STOP] * 4  # holds every time, never a RISE
