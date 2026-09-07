"""ShadowCopy protocol and implementations.

ShadowCopy smooths the visual representation of remote entities between
authoritative snapshots. Each remote player gets its own ShadowCopy
instance, initialised after lobby and updated on every snapshot arrival.

"""

import time
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from distributed_smb.domain.shadow_copy import ShadowCopy as DomainShadowCopy
from distributed_smb.domain.world import CharacterState


@runtime_checkable
class ShadowCopyProtocol(Protocol):
    """Manages the display state for one remote entity."""

    def update(self, state: CharacterState, sequence_number: int) -> None:
        """Record a new authoritative state, ordered by the snapshot's sequence_number."""

    def get_display_state(self) -> CharacterState | None:
        """Return the state to render, potentially interpolated or extrapolated.

        Returns None if no snapshot has arrived yet for this entity.
        """


class NoopShadowCopy:
    """Pass-through — stores the last known state and returns it unchanged."""

    def __init__(self) -> None:
        self._state: CharacterState | None = None

    def update(self, state: CharacterState, sequence_number: int) -> None:
        self._state = state

    def get_display_state(self) -> CharacterState | None:
        return self._state


class InterpolatedShadowCopy:
    """Adapter that exposes the real interpolation/dead-reckoning ShadowCopy."""

    def __init__(
        self,
        *,
        time_provider: Callable[[], float] = time.monotonic,
        shadow_copy: DomainShadowCopy | None = None,
    ) -> None:
        self._time_provider = time_provider
        self._shadow_copy = shadow_copy or DomainShadowCopy()

    def update(self, state: CharacterState, sequence_number: int) -> None:
        self._shadow_copy.update(
            state,
            sequence_number=sequence_number,
            received_at=self._time_provider(),
        )

    def get_display_state(self) -> CharacterState | None:
        return self._shadow_copy.get_visual_state(self._time_provider())
