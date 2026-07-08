from dataclasses import dataclass, field
from pathlib import Path

import pytmx

from distributed_smb.domain.entity import (
    CooperativeGate,
    DestructibleBlock,
    Enemy,
    ExclusivePowerUp,
    Platform,
)
from distributed_smb.shared.config import ENEMY_HEIGHT, ENEMY_WIDTH


@dataclass(slots=True)
class SpawnPoint:
    x: int
    y: int


@dataclass(slots=True)
class Level:
    platforms: list = field(default_factory=list)
    blocks: list = field(default_factory=list)
    powerups: list = field(default_factory=list)
    enemies: list = field(default_factory=list)
    gates: list = field(default_factory=list)
    spawn_points: list = field(default_factory=list)
    coins_to_win: int = 5
    width: int = 0
    height: int = 0


class TiledLevel:
    def __init__(self, filename: str):
        self.filename = filename

    def build(self) -> Level:
        package_root = Path(__file__).resolve().parent.parent
        tmx_path = package_root / self.filename

        tmx = pytmx.TiledMap(tmx_path)

        level = Level(
            width=int(tmx.width * tmx.tilewidth),
            height=int(tmx.height * tmx.tileheight),
        )

        for obj in tmx.objects:
            if obj.type == "Platforms":
                level.platforms.append(
                    Platform(
                        x=int(obj.x),
                        y=int(obj.y),
                        width=int(obj.width),
                        height=int(obj.height),
                    )
                )

            elif obj.type == "Blocks":
                level.blocks.append(
                    DestructibleBlock(
                        x=int(obj.x),
                        y=int(obj.y),
                        width=int(obj.width),
                        height=int(obj.height),
                    )
                )

            elif obj.type == "PowerUps":
                level.powerups.append(
                    ExclusivePowerUp(
                        x=int(obj.x),
                        y=int(obj.y),
                        width=int(obj.width),
                        height=int(obj.height),
                        powerup_id=obj.name,
                    )
                )

            elif obj.type == "Gates":
                level.gates.append(
                    CooperativeGate(
                        gate_id=obj.name,
                        x=int(obj.x),
                        y=int(obj.y),
                        width=int(obj.width),
                        height=int(obj.height),
                    )
                )

            elif obj.type == "Enemies":
                level.enemies.append(
                    Enemy(
                        enemy_id=obj.name,
                        x=int(obj.x),
                        y=int(obj.y),
                        width=ENEMY_WIDTH,
                        height=ENEMY_HEIGHT,
                        vx=float(obj.properties.get("vx", 50.0)),
                        left_bound=float(obj.properties["left_bound"]),
                        right_bound=float(obj.properties["right_bound"]),
                    )
                )

            elif obj.type == "SpawnPoints":
                level.spawn_points.append(SpawnPoint(x=int(obj.x), y=int(obj.y)))

        if "coins_to_win" in tmx.properties:
            level.coins_to_win = int(tmx.properties["coins_to_win"])

        return level
