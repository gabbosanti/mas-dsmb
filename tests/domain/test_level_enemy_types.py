from types import SimpleNamespace

import pytest

from distributed_smb.domain.entity import EnemyType
from distributed_smb.domain.level import TiledLevel


@pytest.mark.parametrize(
    ("properties", "expected_type"),
    [
        ({"left_bound": 10, "right_bound": 100}, EnemyType.KOOPA),
        (
            {"left_bound": 10, "right_bound": 100, "enemy_type": "goomba"},
            EnemyType.GOOMBA,
        ),
    ],
)
def test_tiled_level_reads_enemy_type(monkeypatch, properties, expected_type):
    enemy_object = SimpleNamespace(
        type="Enemies",
        name="enemy-1",
        x=20,
        y=30,
        properties=properties,
    )
    tiled_map = SimpleNamespace(
        width=10,
        height=10,
        tilewidth=32,
        tileheight=32,
        objects=[enemy_object],
        properties={},
    )
    monkeypatch.setattr(
        "distributed_smb.domain.level.pytmx.TiledMap", lambda _: tiled_map
    )

    level = TiledLevel("unused.tmx").build()

    assert level.enemies[0].enemy_type is expected_type
