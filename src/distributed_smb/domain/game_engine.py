"""Authoritative game simulation placeholder."""

from dataclasses import dataclass, field

from distributed_smb.domain.collisions import check_collision, resolve_collision
from distributed_smb.domain.entity import CooperativeGate, DestructibleBlock, ExclusivePowerUp
from distributed_smb.domain.physics import JUMP_FORCE, MOVE_SPEED, apply_physics
from distributed_smb.domain.world import CharacterState, WorldState
from distributed_smb.shared.config import WINDOW_HEIGHT, WINDOW_WIDTH
from distributed_smb.shared.input import InputState

BLOCK_SIZE = 36
POWERUP_SIZE = 34
COIN_SIZE = 26
GATE_WIDTH = 54
GATE_HEIGHT = 96


@dataclass(slots=True)
class GameEngine:
    """Owns the local world simulation and the default playable level."""

    world_state: WorldState = field(default_factory=WorldState)
    platforms: list = field(default_factory=list)
    events: list = field(default_factory=list)

    def __post_init__(self) -> None:
        self._build_default_level()

    def _build_default_level(self) -> None:
        """Create a compact level where all objects are visible and reachable."""
        floor_y = WINDOW_HEIGHT - 90
        self.platforms = [
            Platform(0, floor_y, WINDOW_WIDTH, 60),
            Platform(210, floor_y - 85, 190, 30),
            Platform(420, floor_y - 135, 180, 30),
            Platform(620, floor_y - 185, 155, 30),
            Platform(735, floor_y - 85, 165, 30),
            Platform(650, floor_y - 250, 230, 30),
            Platform(365, floor_y - 285, 150, 30),
            Platform(795, floor_y - 330, 105, 30),
        ]
        self._seed_default_environment()

    def _seed_default_environment(self) -> None:
        floor_y = WINDOW_HEIGHT - 90
        env = self.world_state.environment
        env.destructible_blocks.clear()
        env.power_ups.clear()
        env.cooperative_gates.clear()

        self.world_state.add_block(
            DestructibleBlock(x=225, y=floor_y - 236, width=BLOCK_SIZE, height=BLOCK_SIZE)
        )
        self.world_state.add_block(
            DestructibleBlock(x=265, y=floor_y - 236, width=BLOCK_SIZE, height=BLOCK_SIZE)
        )
        self.world_state.add_block(
            DestructibleBlock(x=430, y=floor_y - 286, width=BLOCK_SIZE, height=BLOCK_SIZE)
        )
        self.world_state.add_block(
            DestructibleBlock(x=570, y=floor_y - 336, width=BLOCK_SIZE, height=BLOCK_SIZE)
        )
        self.world_state.add_block(
            DestructibleBlock(x=745, y=floor_y - 401, width=BLOCK_SIZE, height=BLOCK_SIZE)
        )

        self._add_power_up("coin-1", 230, floor_y - 85, COIN_SIZE)
        self._add_power_up("coin-2", 265, floor_y - 85, COIN_SIZE)
        self._add_power_up("coin-3", 300, floor_y - 85, COIN_SIZE)
        self._add_power_up("mushroom-1", 350, floor_y - 85, POWERUP_SIZE)
        self._add_power_up("coin-4", 450, floor_y - 135, COIN_SIZE)
        self._add_power_up("coin-5", 490, floor_y - 135, COIN_SIZE)
        self._add_power_up("flower-1", 545, floor_y - 135, POWERUP_SIZE)
        self._add_power_up("coin-6", 645, floor_y - 185, COIN_SIZE)
        self._add_power_up("coin-7", 688, floor_y - 185, COIN_SIZE)
        self._add_power_up("star-1", 670, floor_y - 250, POWERUP_SIZE)
        self._add_power_up("coin-8", 725, floor_y - 250, COIN_SIZE)
        self._add_power_up("coin-9", 770, floor_y - 250, COIN_SIZE)
        self._add_power_up("mushroom-2", 835, floor_y - 330, POWERUP_SIZE)
        self._add_power_up("flower-2", 420, floor_y - 285, POWERUP_SIZE)

        self.world_state.add_gate(
            CooperativeGate(
                x=820,
                y=floor_y - 85 - GATE_HEIGHT,
                width=GATE_WIDTH,
                height=GATE_HEIGHT,
                gate_id="gate-test",
            )
        )

    def _add_power_up(self, powerup_id: str, x: int, platform_y: int, size: int) -> None:
        self.world_state.add_power_up(
            ExclusivePowerUp(
                x=x,
                y=platform_y - size,
                width=size,
                height=size,
                powerup_id=powerup_id,
            )
        )

    def apply_inputs(self, inputs: dict[str, InputState]) -> None:
        for player_id, input_state in inputs.items():
            player = self.world_state.get_player(player_id)
            if not player:
                continue

            if input_state.left:
                player.vx = -MOVE_SPEED
            elif input_state.right:
                player.vx = MOVE_SPEED
            else:
                player.vx = 0

            if input_state.jump and player.on_ground:
                player.vy = JUMP_FORCE
                player.on_ground = False

            player.is_crouching = bool(input_state.down and player.on_ground)

    def tick(self, dt, inputs: dict[str, InputState]):
        if self.world_state.victory:
            self.world_state.sequence_number += 1
            return

        for player in self.world_state.characters.values():
            player.prev_x = player.x
            player.prev_y = player.y

        self.apply_inputs(inputs)
        for player in self.world_state.characters.values():
            apply_physics(player, dt)

        self.handle_collisions()
        self.handle_environment_collisions()
        self.handle_victory_condition()
        self.world_state.sequence_number += 1

    def handle_collisions(self) -> None:
        for player in self.world_state.characters.values():
            player.on_ground = False

            for platform in self.platforms:
                if check_collision(player, platform):
                    resolve_collision(player, platform)

    def spawn_player(self, player_id: str, x=100, y=100, join_index: int = 0):
        player = CharacterState(player_id=player_id, x=x, y=y, join_index=join_index)
        self.world_state.add_player(player)

    def handle_environment_collisions(self) -> None:
        self.handle_block_collisions()
        self.handle_powerup_collisions()
        self.handle_gate_collisions()

    def handle_block_collisions(self) -> None:
        for player in self.world_state.characters.values():
            for block in self.world_state.environment.destructible_blocks:
                if not block.destroyed and check_collision(player, block):
                    if self._is_head_bump(player, block):
                        event = block.destroy()
                        self.events.append(event)
                    resolve_collision(player, block)

    def handle_powerup_collisions(self) -> None:
        for power_up in self.world_state.environment.power_ups.values():
            if power_up.collected:
                continue

            colliding_players = [
                player
                for player in self.world_state.characters.values()
                if check_collision(player, power_up)
            ]

            if not colliding_players:
                continue

            winner = min(colliding_players, key=lambda p: p.join_index)
            event = power_up.collect(winner.player_id)
            self.events.append(event)

    def handle_gate_collisions(self) -> None:
        active_players = self.world_state.get_all_players_dict().keys()
        for gate in self.world_state.environment.cooperative_gates.values():
            colliding_players = [
                player
                for player in self.world_state.characters.values()
                if check_collision(player, gate)
            ]
            for player in colliding_players:
                gate.contribute(player.player_id)
            event = gate.update_state(active_players)
            if event is not None:
                self.events.append(event)
            if gate.state == "closed":
                for player in colliding_players:
                    resolve_collision(player, gate)

    def handle_victory_condition(self) -> None:
        if self.world_state.victory:
            return

        for gate in self.world_state.environment.cooperative_gates.values():
            if gate.state != "open":
                continue

            for player in self.world_state.characters.values():
                if check_collision(player, gate):
                    self.world_state.victory = True
                    self.world_state.victory_player_id = player.player_id
                    return

    def _is_head_bump(self, player: CharacterState, block: DestructibleBlock) -> bool:
        previous_top = player.prev_y
        current_top = player.y
        block_bottom = block.y + block.height
        horizontally_overlapping = (
            player.x < block.x + block.width and player.x + player.width > block.x
        )
        return (
            horizontally_overlapping
            and previous_top >= block_bottom - 4
            and current_top <= block_bottom
            and player.y + player.height > block.y
        )


@dataclass
class Platform:
    x: int
    y: int
    width: int
    height: int
