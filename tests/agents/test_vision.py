"""Agent-side sight tests for obstacle detection and occlusion."""

import pytest

from distributed_smb.agents.models import ObstacleObservation, Position, SightDirection
from distributed_smb.agents.perception import Vision, nearest_player


def wall(x: float, *, y=80, width=20, height=100) -> ObstacleObservation:
    return ObstacleObservation(x=x, y=y, width=width, height=height)


def test_detects_an_obstacle_ahead_but_not_one_behind():
    vision = Vision()
    obstacles = (wall(150), wall(20))

    assert vision.obstacle_ahead(Position(100, 100), SightDirection.RIGHT, obstacles)
    assert not vision.obstacle_ahead(Position(100, 100), SightDirection.LEFT, (wall(180),))


def test_obstacle_hidden_behind_a_nearer_obstacle_is_not_seen():
    vision = Vision()
    obstacles = (wall(150), wall(220))

    visible = vision.visible_obstacles(Position(100, 100), SightDirection.RIGHT, obstacles)

    assert [obstacle.x for obstacle in visible] == [150]


def test_obstacle_detection_respects_sight_distance():
    vision = Vision(sight_distance=100)

    assert not vision.obstacle_ahead(Position(100, 100), SightDirection.RIGHT, (wall(250),))


def test_nearest_player_returns_none_when_there_are_no_players():
    assert nearest_player(Position(100, 100), []) is None


def test_rejects_a_negative_sight_distance():
    with pytest.raises(ValueError):
        Vision(sight_distance=-1)
