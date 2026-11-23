"""Agent-side ground sensing, tested without any game engine."""

import pytest

from distributed_smb.agents.models import ObstacleObservation, Position, SightDirection
from distributed_smb.agents.perception import GroundSensor

# A 50x50 walker standing on the floor at y=100 (feet at 50 + 50).
WIDTH = HEIGHT = 50
FLOOR_Y = 100


def floor(x: float, width: float) -> ObstacleObservation:
    return ObstacleObservation(x=x, y=FLOOR_Y, width=width, height=32)


def walker_at(x: float) -> Position:
    return Position(x, FLOOR_Y - HEIGHT)


def test_ground_ahead_is_not_a_void():
    sensor = GroundSensor(lookahead=10)

    assert not sensor.is_void_ahead(
        walker_at(100), WIDTH, HEIGHT, SightDirection.RIGHT, [floor(0, 400)]
    )


def test_detects_the_edge_of_a_platform_in_the_walking_direction():
    sensor = GroundSensor(lookahead=10)
    ground = [floor(0, 160)]  # right edge at x=160

    # Leading edge at 150 → probe at 160: still on the edge.
    assert not sensor.is_void_ahead(
        walker_at(100), WIDTH, HEIGHT, SightDirection.RIGHT, ground
    )
    # Leading edge at 155 → probe at 165: over the gap.
    assert sensor.is_void_ahead(
        walker_at(105), WIDTH, HEIGHT, SightDirection.RIGHT, ground
    )
    # Walking back to the left there is floor again.
    assert not sensor.is_void_ahead(
        walker_at(105), WIDTH, HEIGHT, SightDirection.LEFT, ground
    )


def test_looking_left_probes_beyond_the_left_edge():
    sensor = GroundSensor(lookahead=10)
    ground = [floor(100, 300)]  # left edge at x=100

    assert sensor.is_void_ahead(
        walker_at(105), WIDTH, HEIGHT, SightDirection.LEFT, ground
    )
    assert not sensor.is_void_ahead(
        walker_at(110), WIDTH, HEIGHT, SightDirection.LEFT, ground
    )


def test_a_surface_at_another_height_does_not_count_as_ground():
    sensor = GroundSensor(lookahead=10, tolerance=4)
    ledge_below = ObstacleObservation(x=0, y=FLOOR_Y + 40, width=400, height=32)
    wall_ahead = ObstacleObservation(x=150, y=FLOOR_Y - 96, width=64, height=96)

    assert sensor.is_void_ahead(
        walker_at(100), WIDTH, HEIGHT, SightDirection.RIGHT, [ledge_below, wall_ahead]
    )


def test_small_misalignments_are_tolerated():
    sensor = GroundSensor(lookahead=10, tolerance=4)
    slightly_lower = ObstacleObservation(x=0, y=FLOOR_Y + 3, width=400, height=32)

    assert not sensor.is_void_ahead(
        walker_at(100), WIDTH, HEIGHT, SightDirection.RIGHT, [slightly_lower]
    )


def test_rejects_negative_parameters():
    with pytest.raises(ValueError):
        GroundSensor(lookahead=-1)
    with pytest.raises(ValueError):
        GroundSensor(tolerance=-1)


def test_clearance_below_measures_the_space_under_the_body():
    sensor = GroundSensor()
    floor_ = ObstacleObservation(x=0, y=200, width=400, height=32)
    ledge = ObstacleObservation(x=500, y=150, width=50, height=32)

    # 50x50 body at (100, 100): bottom at 150, floor top at 200.
    assert sensor.clearance_below(Position(100, 100), 50, 50, [floor_, ledge]) == 50
    # Above a pit: nothing under the body.
    assert sensor.clearance_below(Position(420, 100), 50, 50, [floor_]) is None


def test_a_pit_hidden_between_the_feet_and_the_far_probe_is_detected():
    """Regression: only the far end of the lookahead used to be probed.

    A walker that lands with its leading edge already past the edge of a
    platform saw the floor on the other side of the pit, and walked into it.
    """
    sensor = GroundSensor(lookahead=64)
    near_side = ObstacleObservation(x=0, y=FLOOR_Y, width=160, height=32)
    far_side = ObstacleObservation(x=200, y=FLOOR_Y, width=400, height=32)

    # Leading edge at 170 (past the edge at 160), far probe at 234 (on the far side).
    assert sensor.is_void_ahead(
        walker_at(120), WIDTH, HEIGHT, SightDirection.RIGHT, [near_side, far_side]
    )
