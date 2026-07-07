"""Authoritative game simulation placeholder."""

import time
from dataclasses import dataclass, field

from distributed_smb.domain.collisions import check_collision, resolve_collision
from distributed_smb.domain.entity import (
    DestructibleBlock,
)
from distributed_smb.domain.events import PlayerDeathEvent
from distributed_smb.domain.level import TiledLevel
from distributed_smb.domain.physics import JUMP_FORCE, MOVE_SPEED, apply_physics
from distributed_smb.domain.world import CharacterState, WorldState
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
    is_authoritative: bool = True

    def __post_init__(self) -> None:
        level = TiledLevel("assets/levels/level.tmx").build()
        self.platforms = level.platforms
        self.world_state.load_level(level)

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
        self._update_enemies(dt)
        self._handle_enemy_collisions()
        self._sync_coin_counter_from_environment()
        self.handle_victory_condition()
        self._process_respawns()
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

    def _sync_coin_counter_from_environment(self) -> None:
        if not self.is_authoritative:
            return

        collected_coins = sum(
            1
            for power_up in self.world_state.environment.power_ups.values()
            if power_up.collected and power_up.powerup_id.startswith("coin-")
        )
        self.world_state.coins_collected = max(self.world_state.coins_collected, collected_coins)

    def handle_powerup_collisions(self) -> None:
        if not self.is_authoritative:
            return

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
            if power_up.powerup_id.startswith("coin-"):
                self.world_state.coins_collected += 1

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
        if not self.is_authoritative:
            return
        if self.world_state.victory:
            return
        if self.world_state.coins_collected < self.world_state.coins_to_win:
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

    def _update_enemies(self, dt: float) -> None:
        for enemy in self.world_state.environment.enemies.values():
            enemy.x += enemy.vx * dt
            if enemy.x < enemy.left_bound:
                enemy.x = enemy.left_bound
                enemy.vx = -enemy.vx
            elif enemy.x + enemy.width > enemy.right_bound:
                enemy.x = enemy.right_bound - enemy.width
                enemy.vx = -enemy.vx

    def _handle_enemy_collisions(self) -> None:
        if not self.is_authoritative:
            return
        now = time.time()
        for enemy in list(self.world_state.environment.enemies.values()):
            for player in list(self.world_state.characters.values()):
                if check_collision(player, enemy):
                    event = PlayerDeathEvent(player_id=player.player_id, enemy_id=enemy.enemy_id)
                    self.events.append(event)
                    # remove player and set respawn timer
                    if player.player_id in self.world_state.characters:
                        del self.world_state.characters[player.player_id]
                    self.world_state.respawn_timers[player.player_id] = now + 10.0

    def _process_respawns(self) -> None:
        if not self.is_authoritative:
            return
        now = time.time()
        for pid, due in list(self.world_state.respawn_timers.items()):
            if now >= due:
                self.spawn_player(pid, x=100, y=100)
                del self.world_state.respawn_timers[pid]
