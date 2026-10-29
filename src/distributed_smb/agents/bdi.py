"""Belief-desire-intention agent primitives."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TypeAlias

#TypeAlias 
Beliefs: TypeAlias = Mapping[str, object]
Condition: TypeAlias = Callable[[Beliefs], bool] #Callable[[Arguments], ReturnType]


@dataclass(frozen=True, slots=True)
class Goal: #Desired state, ranked by priority and tested against current beliefs.
    
    name: str
    priority: int
    condition: Condition


@dataclass(frozen=True, slots=True)
class ActionRequest: #An action understood by the BDI agent's action boundary.

    name: str
    parameters: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Plan: # Actions for a goal, available only when its precondition is true.

    goal_name: str
    precondition: Condition
    actions: list[ActionRequest]


@dataclass(frozen=True, slots=True)
class AgentMessage:
    sender_id: str
    recipient_id: str
    message_id: str
    information: Mapping[str, object]


class InvalidActionError(ValueError):
    """Raised when an action is not accepted by an action boundary."""


class ActionBoundary:
    """Validates action names before they can be submitted to the game."""

    def __init__(
        self,
        engine: object,
        player_id: str,
        supported_actions: Iterable[str],
    ) -> None:
        self.engine = engine
        self.player_id = player_id
        self.supported_actions = frozenset(supported_actions)

    def submit(self, request: ActionRequest) -> ActionRequest:
        if request.name not in self.supported_actions:
            raise InvalidActionError(f"Unsupported action: {request.name}")
        return request


@dataclass(frozen=True, slots=True)
class _Intention:
    goal: Goal
    plan: Plan


class Agent:
    """Maintains beliefs and selects an executable plan for the best goal."""

    def __init__(
        self,
        agent_id: str,
        goals: Iterable[Goal],
        plans: Iterable[Plan],
        relevant_beliefs: Iterable[str] = (),
        initial_observation: Mapping[str, object] | None = None,
        action_sink: Callable[[ActionRequest], object] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.goals = tuple(goals)
        self.plans = tuple(plans)
        self.relevant_beliefs = frozenset(relevant_beliefs)
        self.beliefs: dict[str, object] = {}
        self.processed_message_ids: set[str] = set()
        self.intention: _Intention | None = None
        self._action_sink = action_sink
        self._autonomous_control = False
        if initial_observation is not None:
            self.observe(initial_observation)

    def observe(self, observation: Mapping[str, object]) -> None:
        for key, value in observation.items():
            if key in self.relevant_beliefs:
                self.beliefs[key] = value

    def set_belief(self, name: str, value: object) -> None:
        if name in self.relevant_beliefs:
            self.beliefs[name] = value

    def deliberate(self) -> _Intention | None:
        candidates = sorted(self.goals, key=lambda goal: goal.priority, reverse=True)
        for goal in candidates:
            if not goal.condition(self.beliefs):
                continue
            plan = next(
                (
                    candidate
                    for candidate in self.plans
                    if candidate.goal_name == goal.name
                    and candidate.precondition(self.beliefs)
                ),
                None,
            )
            if plan is not None:
                if (
                    self.intention is None
                    or self.intention.goal != goal
                    or self.intention.plan != plan
                ):
                    self.intention = _Intention(goal=goal, plan=plan)
                return self.intention
        self.intention = None
        return None

    def execute_intention(self) -> list[ActionRequest]:
        if self.intention is None:
            return []

        plan = next(
            (
                candidate
                for candidate in self.plans
                if candidate.goal_name == self.intention.goal.name
                and candidate.precondition(self.beliefs)
            ),
            None,
        )
        if plan is None:
            return []

        requests = list(plan.actions)
        if self._action_sink is not None:
            for request in requests:
                self._action_sink(request)
        return requests

    def receive(self, message: AgentMessage) -> None:
        if message.message_id in self.processed_message_ids:
            return
        self.processed_message_ids.add(message.message_id)
        self.observe(message.information)

    def set_autonomous_control(self, enabled: bool) -> None:
        self._autonomous_control = enabled

    def step(self) -> list[ActionRequest]:
        if not self._autonomous_control:
            return []
        self.deliberate()
        return self.execute_intention()