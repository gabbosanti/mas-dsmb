"""Game-side view of the per-agent-type queues shared with the SPADE thread."""

from dataclasses import dataclass, field
from queue import Empty, Full, Queue
from typing import Any

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.spade_runtime import ActionQueue, PerceptionQueue

AGENT_QUEUE_CAPACITY = 64


def make_agent_queues(capacity: int = AGENT_QUEUE_CAPACITY) -> dict[AgentType, Queue]:
    """Build one bounded queue per supported agent type."""
    return {agent_type: Queue(maxsize=capacity) for agent_type in AgentType}


@dataclass(slots=True)
class SpadeAgentBridge:
    """Non-blocking game-thread access to the queues consumed by ``SpadeRuntime``.

    ``perceptions`` and ``actions`` are the very same dictionaries injected
    into ``SpadeRuntime``: the game thread publishes perceptions and drains
    actions here, while SPADE uses ``get_perception``/``put_action``.
    When a queue is full its oldest item is discarded, so consumers always
    work with recent game state and the game loop never blocks.
    """

    perceptions: PerceptionQueue = field(default_factory=make_agent_queues)
    actions: ActionQueue = field(default_factory=make_agent_queues)
    dropped_perceptions: int = field(default=0, init=False)
    dropped_actions: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        for name, queues in (
            ("perceptions", self.perceptions),
            ("actions", self.actions),
        ):
            missing = set(AgentType) - queues.keys()
            if missing:
                raise ValueError(
                    f"{name} has no queue for: "
                    + ", ".join(sorted(agent_type.value for agent_type in missing))
                )

    def publish(self, agent_type: AgentType, message: Any) -> None:
        """Game thread -> SPADE: enqueue a perception for an agent type."""
        self.dropped_perceptions += self._put_latest(
            self.perceptions[AgentType(agent_type)], message
        )

    def submit_action(self, agent_type: AgentType, action: Any) -> None:
        """SPADE -> game thread: enqueue an action (mainly useful in tests)."""
        self.dropped_actions += self._put_latest(
            self.actions[AgentType(agent_type)], action
        )

    def poll_perception(self, agent_type: AgentType) -> Any | None:
        return self._get_nowait(self.perceptions[AgentType(agent_type)])

    def poll_action(self, agent_type: AgentType) -> Any | None:
        return self._get_nowait(self.actions[AgentType(agent_type)])

    def discard_perceptions(self) -> None:
        for queue in self.perceptions.values():
            self._drain(queue)

    def discard_actions(self) -> None:
        for queue in self.actions.values():
            self._drain(queue)

    @staticmethod
    def _put_latest(target: Queue, value: Any) -> int:
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

    @staticmethod
    def _get_nowait(source: Queue) -> Any | None:
        try:
            return source.get_nowait()
        except Empty:
            return None

    @staticmethod
    def _drain(source: Queue) -> None:
        while True:
            try:
                source.get_nowait()
            except Empty:
                return
