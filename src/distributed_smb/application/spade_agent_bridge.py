from dataclasses import dataclass, field
from math import hypot
from queue import Empty, Full, Queue
from typing import TypeVar

from distributed_smb.agents.controller import EnemyController
from distributed_smb.agents.models import (
    ActionRequest,
    ActionResult,
    ActionStatus,
    EnemyAction,
    GoombaPerception,
    GoombaPerceptionMessage,
    MovementCommand,
    ObstacleKind,
    ObstacleObservation,
    PlayerPerception,
    Position,
    SightDirection,
)
from distributed_smb.domain.entity import Player
from distributed_smb.domain.game_engine import EnemyMovementCommand, GameEngine
from distributed_smb.shared.input import InputState

AGENT_QUEUE_CAPACITY = 64
GOOMBA_SIGHT_DISTANCE = 200.0
OBSTACLE_SIGHT_DISTANCE = GOOMBA_SIGHT_DISTANCE
AGENT_QUEUE_CAPACITY = 64
QueueItem = TypeVar("QueueItem")

@dataclass(slots=True)
class SpadeAgentBridge:
    """Thread-safe, non-blocking queues at the SPADE/application boundary.

    SPADE may poll ``perceptions`` and submit ``ActionRequest`` instances via
    ``submit_action``. When a queue fills, its oldest item is discarded so
    consumers always work with recent game state.
    """

    perceptions: Queue[GoombaPerceptionMessage] = field(
        default_factory=lambda: Queue(maxsize=AGENT_QUEUE_CAPACITY)
    )
    action_requests: Queue[ActionRequest] = field(
        default_factory=lambda: Queue(maxsize=AGENT_QUEUE_CAPACITY)
    )
    dropped_perceptions: int = field(default=0, init=False)
    dropped_actions: int = field(default=0, init=False)

    def publish(self, message: GoombaPerceptionMessage) -> None:
        self.dropped_perceptions += self._put_latest(self.perceptions, message)

    def submit_action(self, request: ActionRequest) -> None:
        if not isinstance(request, ActionRequest):
            raise TypeError("SPADE must submit ActionRequest instances")
        self.dropped_actions += self._put_latest(self.action_requests, request)

    @staticmethod
    def _put_latest(target: Queue[QueueItem], value: QueueItem) -> int:
        dropped = 0
        while True:
            try:
                target.put_nowait(value)
                return dropped
            except Full:
                try:
                    target.get_nowait()
                except Empty:
                    continue
                dropped += 1

    def discard_perceptions(self) -> None:
        while True:
            try:
                self.perceptions.get_nowait()
            except Empty:
                return

    def poll_perception(self) -> GoombaPerceptionMessage | None:
        try:
            return self.perceptions.get_nowait()
        except Empty:
            return None