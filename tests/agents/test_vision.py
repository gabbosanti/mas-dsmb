"""Agent-side sight, tested without any game engine."""

import pytest

from distributed_smb.agents.models import (
    ObstacleObservation,
    PlayerPerception,
    Position,
    SightDirection,
)
from distributed_smb.agents.perception import Vision, nearest_player

POSITION = Position(100, 100)


def player(player_id: str, center_x: float, center_y: float = 100) -> PlayerPerception:
    return PlayerPerception(player_id, Position(center_x - 5, center_y - 5), 10, 10)


def wall(x: float) -> ObstacleObservation:
    return ObstacleObservation(x=x, y=80, width=20, height=100)


def test_sees_only_forward_entities_with_a_clear_line_of_sight():
    players = [player("visible", 145), player("behind-wall", 300), player("behind", 20)]
    obstacles = [wall(200), wall(260)]

    view = Vision().look(POSITION, SightDirection.RIGHT, players, obstacles)

    assert [p.player_id for p in view.players] == ["visible"]
    assert [o.x for o in view.obstacles] == [200]


def test_looking_left_mirrors_the_field_of_view():
    players = [player("in-front", 50), player("behind", 200)]

    view = Vision().look(POSITION, SightDirection.LEFT, players, [wall(20)])

    assert [p.player_id for p in view.players] == ["in-front"]
    assert [o.x for o in view.obstacles] == [20]


def test_no_direction_means_looking_everywhere():
    players = [player("left", 50), player("right", 150)]

    view = Vision().look(POSITION, None, players, [])

    assert [p.player_id for p in view.players] == ["left", "right"]


def test_sight_is_limited_by_distance():
    players = [player("near", 150), player("far", 400)]

    view = Vision(sight_distance=100).look(POSITION, SightDirection.RIGHT, players, [])

    assert [p.player_id for p in view.players] == ["near"]


def test_nearest_player_breaks_ties_by_id():
    assert nearest_player(POSITION, []) is None
    assert nearest_player(POSITION, [player("far", 300), player("near", 140)]).player_id == "near"
    assert nearest_player(POSITION, [player("b", 150), player("a", 50)]).player_id == "a"


def test_rejects_a_negative_sight_distance():
    with pytest.raises(ValueError):
        Vision(sight_distance=-1)
