"""Election and host failure detection module."""

from distributed_smb.application.election.coordinator import (
    ElectionCoordinator,
    ElectionEvent,
    ElectionState,
    FollowingHost,
    SelfElected,
)
from distributed_smb.application.election.env_state_buffer import (
    EnvironmentalStateBuffer,
)
from distributed_smb.application.election.timeout_watcher import HostTimeoutWatcher

__all__ = [
    "HostTimeoutWatcher",
    "ElectionCoordinator",
    "ElectionState",
    "ElectionEvent",
    "SelfElected",
    "FollowingHost",
    "EnvironmentalStateBuffer",
]
