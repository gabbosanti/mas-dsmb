"""Dummy SPADE agent that controls a crashed player's character."""

import random
from spade.agent import Agent

from distributed_smb.agents.agent_type import AgentType
from distributed_smb.agents.models import (
    PlayerAction,
    PlayerActionResult,
    PlayerPerceptionMessage,
)


class DummyPlayerAgent(Agent):
    """Generate deliberately random input commands through the SPADE runtime."""

    def __init__(
        self,
        jid: str,
        password: str,
        player_id: str,
    ) -> None:
        super().__init__(jid, password)
        self.player_id = player_id
        self.spade_runtime = None
        self._alive = False

    async def setup(self) -> None:
        self._alive = True

    async def start(self, auto_register: bool = True) -> None:
        del auto_register
        self._alive = True

    async def stop(self) -> None:
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive

    def perceive(self, message: PlayerPerceptionMessage) -> None:
        del message
        action = random.choice(tuple(PlayerAction))
        self.spade_runtime.put_action(
            AgentType.PLAYER,
            PlayerActionResult(player_id=self.player_id, action=action),
        )
