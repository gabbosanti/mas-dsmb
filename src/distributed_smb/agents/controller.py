"""Small controller interface and an optional pursuit controller for testing."""

from typing import Protocol

from distributed_smb.agents.models import (
    ActionRequest,
    EnemyAction,
    EnemyObservation,
)


class EnemyController(Protocol):
    def decide(self, observation: EnemyObservation) -> ActionRequest: ...


class PursuitController:
    """Move toward the nearest player using only the public observation."""

    def __init__(self, horizontal_deadband: float = 4.0) -> None:
        if horizontal_deadband < 0:
            raise ValueError("horizontal_deadband must be non-negative")
        self.horizontal_deadband = horizontal_deadband

    def decide(self, observation: EnemyObservation) -> ActionRequest:
        target = observation.mario_position
        if target is None:
            action = EnemyAction.STOP
        else:
            delta_x = target.x - observation.enemy_position.x
            if delta_x > self.horizontal_deadband:
                action = EnemyAction.MOVE_RIGHT
            elif delta_x < -self.horizontal_deadband:
                action = EnemyAction.MOVE_LEFT
            else:
                action = EnemyAction.STOP
        return ActionRequest(enemy_id=observation.enemy_id, action=action)
