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
    assert not vision.obstacle_ahead(
        Position(100, 100), SightDirection.LEFT, (wall(180),)
    )


def test_obstacle_hidden_behind_a_nearer_obstacle_is_not_seen():
    vision = Vision()
    obstacles = (wall(150), wall(220))

    visible = vision.visible_obstacles(
        Position(100, 100), SightDirection.RIGHT, obstacles
    )

    assert [obstacle.x for obstacle in visible] == [150]


def test_obstacle_detection_respects_sight_distance():
    vision = Vision(sight_distance=100)

    assert not vision.obstacle_ahead(
        Position(100, 100), SightDirection.RIGHT, (wall(250),)
    )


def test_goomba_sees_pipe_early_enough_to_turn():
    pipe = ObstacleObservation(x=820, y=832, width=64, height=96)
    # Enemy center at x=720, y=925; its right edge is still 68px from the pipe.
    position = Position(720, 925)

    assert Vision(sight_distance=140).obstacle_ahead(
        position, SightDirection.RIGHT, (pipe,)
    )
    # The former 80px centre-to-centre threshold only detected this pipe
    # when the enemy was almost touching it.
    assert not Vision(sight_distance=80).obstacle_ahead(
        position, SightDirection.RIGHT, (pipe,)
    )


def test_obstacle_distance_is_measured_to_the_nearest_face_not_the_center():
    wide_pipe = ObstacleObservation(x=150, y=50, width=200, height=100)
    # Face 40px away, center 140px away.
    assert Vision(sight_distance=50).obstacle_ahead(
        Position(110, 100), SightDirection.RIGHT, (wide_pipe,)
    )


def test_goomba_between_two_close_pipes_does_not_see_both_walls():
    # pipe-2 and pipe-3 in the level: a 136px corridor.
    pipes = (
        ObstacleObservation(x=1020, y=800, width=64, height=128),
        ObstacleObservation(x=1220, y=768, width=64, height=160),
    )
    middle = Position(1152, 902)

    for direction in (SightDirection.LEFT, SightDirection.RIGHT):
        assert not Vision(sight_distance=48).obstacle_ahead(middle, direction, pipes)
    # Close to a pipe, only that side is blocked.
    near_right = Position(1180, 902)
    assert Vision(sight_distance=48).obstacle_ahead(
        near_right, SightDirection.RIGHT, pipes
    )
    assert not Vision(sight_distance=48).obstacle_ahead(
        near_right, SightDirection.LEFT, pipes
    )


def test_nearest_player_returns_none_when_there_are_no_players():
    assert nearest_player(Position(100, 100), []) is None


def test_rejects_a_negative_sight_distance():
    with pytest.raises(ValueError):
        Vision(sight_distance=-1)


def body(player_id: str, center_x: float, center_y: float = 100):
    from distributed_smb.agents.models import PlayerPerception

    return PlayerPerception(player_id, Position(center_x - 10, center_y - 20), 20, 40)


def test_player_side_notices_players_ahead_and_behind():
    vision = Vision(sight_distance=200)
    eye = Position(100, 100)

    assert vision.player_side(eye, [body("p", 180)], (), vertical_tolerance=30) is (
        SightDirection.RIGHT
    )
    assert vision.player_side(eye, [body("p", 20)], (), vertical_tolerance=30) is (
        SightDirection.LEFT
    )


def test_player_side_requires_the_same_height_and_the_horizontal_range():
    vision = Vision(sight_distance=200)
    eye = Position(100, 100)

    above = body("above", 150, center_y=40)
    too_far = body("far", 350)
    assert vision.player_side(eye, [above, too_far], (), vertical_tolerance=30) is None


def test_player_side_ignores_players_hidden_behind_an_obstacle():
    vision = Vision(sight_distance=200)
    pipe = wall(140)

    assert (
        vision.player_side(Position(100, 100), [body("p", 200)], (pipe,), vertical_tolerance=30)
        is None
    )


def test_player_side_picks_the_nearest_player_and_ignores_overlapping_ones():
    vision = Vision(sight_distance=200)
    eye = Position(100, 100)
    players = [body("far-right", 250), body("near-left", 60), body("on-top", 102)]

    assert (
        vision.player_side(eye, players, (), vertical_tolerance=30, deadband=4)
        is SightDirection.LEFT
    )
