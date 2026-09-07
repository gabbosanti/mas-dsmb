from math import isclose

from distributed_smb.application.reconciliation.shadow_copy import InterpolatedShadowCopy
from distributed_smb.domain.shadow_copy import ShadowCopy
from distributed_smb.domain.world import CharacterState


def _character(
    *,
    x: float,
    y: float = 0.0,
    vx: float = 0.0,
    vy: float = 0.0,
) -> CharacterState:
    return CharacterState(
        player_id="player2",
        x=x,
        y=y,
        vx=vx,
        vy=vy,
        prev_x=x,
        prev_y=y,
    )


def test_shadow_copy_interpolates_between_consecutive_snapshots():
    shadow_copy = ShadowCopy(snapshot_timeout=0.15, max_extrapolation_time=0.35)
    shadow_copy.update(_character(x=0.0, vx=10.0), sequence_number=1, received_at=0.0)
    shadow_copy.update(_character(x=10.0, vx=10.0), sequence_number=2, received_at=0.1)

    visual_state = shadow_copy.get_visual_state(0.15)

    assert visual_state is not None
    assert isclose(visual_state.x, 5.0)
    assert isclose(visual_state.vx, 10.0)


def test_shadow_copy_extrapolates_when_snapshot_is_missing():
    shadow_copy = ShadowCopy(snapshot_timeout=0.1, max_extrapolation_time=0.3)
    shadow_copy.update(_character(x=0.0, vx=20.0), sequence_number=1, received_at=0.0)
    shadow_copy.update(_character(x=10.0, vx=20.0), sequence_number=2, received_at=0.1)

    visual_state = shadow_copy.get_visual_state(0.25)

    assert visual_state is not None
    assert isclose(visual_state.x, 11.0)
    assert isclose(visual_state.vx, 20.0)


def test_shadow_copy_stops_extrapolating_after_max_time():
    shadow_copy = ShadowCopy(snapshot_timeout=0.1, max_extrapolation_time=0.3)
    shadow_copy.update(_character(x=0.0, vx=20.0), sequence_number=1, received_at=0.0)
    shadow_copy.update(_character(x=10.0, vx=20.0), sequence_number=2, received_at=0.1)

    visual_state = shadow_copy.get_visual_state(0.6)

    assert visual_state is not None
    assert isclose(visual_state.x, 16.0)
    assert isclose(visual_state.vx, 0.0)
    assert isclose(visual_state.vy, 0.0)


def test_shadow_copy_ignores_out_of_order_snapshots():
    shadow_copy = ShadowCopy(snapshot_timeout=0.1, max_extrapolation_time=0.3)
    shadow_copy.update(_character(x=5.0), sequence_number=2, received_at=0.1)

    accepted = shadow_copy.update(_character(x=1.0), sequence_number=1, received_at=0.2)

    assert accepted is False
    assert shadow_copy.target is not None
    assert isclose(shadow_copy.target.x, 5.0)
    assert shadow_copy.last_sequence_number == 2


def test_interpolated_shadow_copy_forwards_the_real_sequence_number():
    """InterpolatedShadowCopy must pass through the snapshot's own
    sequence_number instead of a locally-generated one, otherwise the
    domain ShadowCopy's out-of-order rejection can never trigger."""
    domain_copy = ShadowCopy(snapshot_timeout=0.1, max_extrapolation_time=0.3)
    adapter = InterpolatedShadowCopy(time_provider=lambda: 0.1, shadow_copy=domain_copy)

    adapter.update(_character(x=5.0), sequence_number=5)
    adapter.update(_character(x=1.0), sequence_number=2)  # stale: arrived out of order

    assert domain_copy.last_sequence_number == 5
    assert adapter.get_display_state().x == 5.0
