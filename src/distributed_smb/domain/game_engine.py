"""Authoritative game simulation placeholder."""

import time
from dataclasses import dataclass, field

from distributed_smb.domain.collisions import check_collision, resolve_collision
from distributed_smb.domain.entity import DestructibleBlock, Enemy
from distributed_smb.domain.events import LevelResetEvent, PlayerDeathEvent
from distributed_smb.domain.level import Level, TiledLevel
from distributed_smb.domain.physics import JUMP_FORCE, MOVE_SPEED, apply_physics
from distributed_smb.domain.world import CharacterState, WorldState
from distributed_smb.shared.config import RESPAWN_DELAY_S, VICTORY_RESET_DELAY_S
from distributed_smb.shared.input import InputState

BLOCK_SIZE = 36
POWERUP_SIZE = 34
COIN_SIZE = 26
GATE_WIDTH = 54
GATE_HEIGHT = 96
VOID_DEATH_CAUSE = "void"


@dataclass(slots=True)
class GameEngine:
    """Owns the local world simulation and the default playable level."""

    world_state: WorldState = field(default_factory=WorldState)
    platforms: list = field(default_factory=list)
    events: list = field(default_factory=list)
    is_authoritative: bool = True
    world_width: int = 0
    world_height: int = 0
    spawn_points: list = field(default_factory=list)
    decorations: list = field(default_factory=list)
    _respawn_join_index: dict = field(default_factory=dict, init=False)
    _level: Level | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        level = TiledLevel("assets/levels/level.tmx").build()
        self.platforms = level.platforms
        self.world_width = level.width
        self.world_height = level.height
        self.spawn_points = level.spawn_points
        self.decorations = level.decorations
        self.world_state.load_level(level)
        self._level = level

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
            if self.is_authoritative and self._victory_reset_due():
                self.reset_for_new_run()
                self.events.append(LevelResetEvent())
            return

        for player in self.world_state.characters.values():
            player.prev_x = player.x
            player.prev_y = player.y

        self._expire_powerup_effects(time.time())
        self.apply_inputs(inputs)
        for player in self.world_state.characters.values():
            apply_physics(player, dt)

        self.handle_collisions()
        self.handle_environment_collisions()
        self._update_enemies(dt)
        self._handle_enemy_collisions()
        self._handle_void_deaths()
        self._sync_objective_progress_from_environment()
        self.handle_gate_collisions()
        self._clamp_players_to_world()
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
        self._clamp_character_to_world(player)
        self.world_state.add_player(player)

    def handle_environment_collisions(self) -> None:
        self.handle_block_collisions()
        self.handle_powerup_collisions()

    def handle_block_collisions(self) -> None:
        for player in self.world_state.characters.values():
            for block in self.world_state.environment.destructible_blocks:
                if not block.destroyed and check_collision(player, block):
                    if self._is_head_bump(player, block):
                        event = block.destroy()
                        self.events.append(event)
                    resolve_collision(player, block)

    def _clamp_character_to_world(
        self, character: CharacterState, *, clamp_bottom: bool = True
    ) -> None:
        max_x = max(0, self.world_width - character.width)
        character.x = max(0, min(character.x, max_x))
        if clamp_bottom:
            max_y = max(0, self.world_height - character.height)
            character.y = max(0, min(character.y, max_y))
        else:
            character.y = max(0, character.y)

    def _clamp_players_to_world(self) -> None:
        for player in self.world_state.characters.values():
            self._clamp_character_to_world(player, clamp_bottom=False)

    def _sync_objective_progress_from_environment(self) -> None:
        self.world_state.coins_collected = sum(
            1
            for power_up in self.world_state.environment.power_ups.values()
            if power_up.collected and power_up.powerup_id.startswith("coin-")
        )
        self.world_state.blocks_destroyed = sum(
            1 for block in self.world_state.environment.destructible_blocks if block.destroyed
        )
        self.world_state.enemies_defeated = max(
            0,
            self.world_state.initial_enemy_count - len(self.world_state.environment.enemies),
        )

    @staticmethod
    def _is_star_powerup(power_up) -> bool:
        return power_up.powerup_id.startswith("star") or power_up.powerup_id == "star"

    def _expire_powerup_effects(self, now: float | None = None) -> None:
        if now is None:
            now = time.time()
        for player in self.world_state.characters.values():
            if (
                player.powerup_effect_expires_at is not None
                and player.powerup_effect_expires_at <= now
            ):
                player.powerup_effect_expires_at = None

    def _has_active_powerup_effect(self, player: CharacterState, now: float | None = None) -> bool:
        if now is None:
            now = time.time()
        if player.powerup_effect_expires_at is None:
            return False
        if player.powerup_effect_expires_at <= now:
            player.powerup_effect_expires_at = None
            return False
        return True

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

            if self._is_star_powerup(power_up):
                winner.powerup_effect_expires_at = time.time() + 10.0

    def handle_gate_collisions(self) -> None:
        for gate in self.world_state.environment.cooperative_gates.values():
            should_be_open = (
                self.world_state.coins_collected >= gate.coins_required
                and self.world_state.blocks_destroyed >= gate.blocks_required
                and self.world_state.enemies_defeated >= gate.enemies_required
            )
            colliding_players = [
                player
                for player in self.world_state.characters.values()
                if check_collision(player, gate)
            ]
            event = gate.update_state(should_be_open)
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

        for gate in self.world_state.environment.cooperative_gates.values():
            if gate.state != "open" or not gate.is_final:
                continue

            for player in self.world_state.characters.values():
                if check_collision(player, gate):
                    self.world_state.victory = True
                    self.world_state.victory_player_id = player.player_id
                    self.world_state.victory_at = time.time()
                    return

    def _victory_reset_due(self) -> bool:
        return (
            self.world_state.victory_at is not None
            and time.time() - self.world_state.victory_at >= VICTORY_RESET_DELAY_S
        )

    def reset_for_new_run(self) -> None:
        """Restart the current session in place: fresh level state (blocks,
        power-ups, enemies, gates, counters), every connected player respawned.
        Same session/roster, no lobby round-trip."""
        self.world_state.load_level(self._level)
        self._respawn_join_index.clear()
        self.world_state.respawn_timers.clear()
        for player in self.world_state.characters.values():
            player.x, player.y = self.spawn_position_for(player.join_index)
            player.vx = 0.0
            player.vy = 0.0
            player.on_ground = False
            player.is_crouching = False
            player.powerup_effect_expires_at = None

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

    def _is_stomp(self, player: CharacterState, enemy: Enemy) -> bool:
        """A stomp is a landing from above: player's feet were at/above the
        enemy's head last frame and the player is currently falling."""
        previous_bottom = player.prev_y + player.height
        horizontally_overlapping = (
            player.x < enemy.x + enemy.width and player.x + player.width > enemy.x
        )
        return horizontally_overlapping and player.vy > 0 and previous_bottom <= enemy.y + 4

    def _queue_player_death(self, player: CharacterState, cause: str) -> None:
        self.events.append(PlayerDeathEvent(player_id=player.player_id, enemy_id=cause))
        self._respawn_join_index[player.player_id] = player.join_index
        self.world_state.remove_player(player.player_id)
        self.world_state.respawn_timers[player.player_id] = time.time() + RESPAWN_DELAY_S

    def _handle_enemy_collisions(self) -> None:
        if not self.is_authoritative:
            return
        for enemy in list(self.world_state.environment.enemies.values()):
            for player in list(self.world_state.characters.values()):
                if not check_collision(player, enemy):
                    continue
                if self._has_active_powerup_effect(player):
                    del self.world_state.environment.enemies[enemy.enemy_id]
                    player.vy = JUMP_FORCE * 0.6
                    break
                if self._is_stomp(player, enemy):
                    del self.world_state.environment.enemies[enemy.enemy_id]
                    player.vy = JUMP_FORCE * 0.6
                    break
                self._queue_player_death(player, enemy.enemy_id)

    def _handle_void_deaths(self) -> None:
        """Kill players that fell past the level's floor through a pit,
        before _clamp_players_to_world() would otherwise silently arrest
        the fall at the world's bottom edge."""
        if not self.is_authoritative:
            return
        for player in list(self.world_state.characters.values()):
            if player.y + player.height <= self.world_height:
                continue
            self._queue_player_death(player, VOID_DEATH_CAUSE)

    def spawn_position_for(self, join_index: int) -> tuple[int, int]:
        """Spawn point for a given join_index, from the level's TMX SpawnPoints.

        Shared by respawn-after-death and by application's initial join spawn
        (node_controller._spawn_position_for) so both use the same
        level-authored positions instead of two independent formulas.
        """
        if not self.spawn_points:
            return 100, 100
        point = self.spawn_points[join_index % len(self.spawn_points)]
        return point.x, point.y

    def _process_respawns(self) -> None:
        if not self.is_authoritative:
            return
        now = time.time()
        for pid, due in list(self.world_state.respawn_timers.items()):
            if now >= due:
                join_index = self._respawn_join_index.pop(pid, 0)
                x, y = self.spawn_position_for(join_index)
                self.spawn_player(pid, x=x, y=y, join_index=join_index)
                del self.world_state.respawn_timers[pid]
