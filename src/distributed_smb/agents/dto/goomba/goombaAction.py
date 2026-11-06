from dataclasses import dataclass
from enum import Enum


class ActionType(Enum):
    # Supported autonomous-agent categories

    TURN_AROUND = "turn_around"
    MOVE_RIGHT = "move_right"
    MOVE_LEFT = "move_left"


@dataclass(frozen=True)
class GoombaActionDTO:
    action: str | None
