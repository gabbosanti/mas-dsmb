from dataclasses import dataclass

@dataclass(frozen=True)
class GoombaPerceptionDTO:
    obstacle_ahead: bool
    direction: str