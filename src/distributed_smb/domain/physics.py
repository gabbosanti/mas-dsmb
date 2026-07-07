from distributed_smb.shared.config import WORLD_SCALE

GRAVITY = int(1000 * WORLD_SCALE)
MOVE_SPEED = int(200 * WORLD_SCALE)
JUMP_FORCE = int(-450 * WORLD_SCALE)


def apply_physics(player, dt):
    player.vy += GRAVITY * dt
    player.x += player.vx * dt
    player.y += player.vy * dt
