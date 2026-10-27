from math import isclose

from distributed_smb.application.reconciliation.shadow_copy import (
    InterpolatedShadowCopy,
)
from distributed_smb.domain.entity import Player
from distributed_smb.domain.shadow_copy import ShadowCopy


def _character(
    *,
    x: float,
    y: float = 0.0,
    vx: float = 0.0,
    vy: float = 0.0,
) -> Player:
    return Player(
        player_id="player2",
        x=x,
        y=y,
        vx=vx,
        vy=vy,
        prev_x=x,
        prev_y=y,
    )


def test_the_remote_is_interpolated_then_extrapolated_and_finally_frozen():
    """Between two snapshots the position is interpolated; if the next one is
    late it is extrapolated, and past max_extrapolation_time the player
    freezes instead of drifting away on a stale velocity."""
    shadow_copy = ShadowCopy(snapshot_timeout=0.1, max_extrapolation_time=0.3)
    shadow_copy.update(_character(x=0.0, vx=20.0), sequence_number=1, received_at=0.0)
    shadow_copy.update(_character(x=10.0, vx=20.0), sequence_number=2, received_at=0.1)

    interpolated = shadow_copy.get_visual_state(0.15)
    assert isclose(interpolated.x, 5.0)
    assert isclose(interpolated.vx, 20.0)

    extrapolated = shadow_copy.get_visual_state(0.25)
    assert isclose(extrapolated.x, 11.0)

    frozen = shadow_copy.get_visual_state(0.6)
    assert isclose(frozen.x, 16.0)
    assert isclose(frozen.vx, 0.0)
    assert isclose(frozen.vy, 0.0)


def test_out_of_order_snapshots_are_ignored_using_the_real_sequence_number():
    """The adapter must forward the snapshot's own number: with a locally
    generated one this check could never fire."""
    domain_copy = ShadowCopy(snapshot_timeout=0.1, max_extrapolation_time=0.3)
    domain_copy.update(_character(x=5.0), sequence_number=2, received_at=0.1)

    accepted = domain_copy.update(_character(x=1.0), sequence_number=1, received_at=0.2)

    assert accepted is False
    assert isclose(domain_copy.target.x, 5.0)
    assert domain_copy.last_sequence_number == 2

    through_adapter = ShadowCopy(snapshot_timeout=0.1, max_extrapolation_time=0.3)
    adapter = InterpolatedShadowCopy(
        time_provider=lambda: 0.1, shadow_copy=through_adapter
    )
    adapter.update(_character(x=5.0), sequence_number=5)
    adapter.update(_character(x=1.0), sequence_number=2)

    assert through_adapter.last_sequence_number == 5
    assert adapter.get_display_state().x == 5.0
