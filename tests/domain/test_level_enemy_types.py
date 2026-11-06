from types import SimpleNamespace

import pytest

from distributed_smb.domain.entity import EnemyType
from distributed_smb.domain.level import TiledLevel


@pytest.mark.parametrize(
    ("properties", "enemy_type_attribute", "expected_type"),
    [
        ({"left_bound": 10, "right_bound": 100}, None, EnemyType.KOOPA),
        (
            {"left_bound": 10, "right_bound": 100, "enemy_type": "goomba"},
            None,
            EnemyType.GOOMBA,
        ),
        (
            {"left_bound": 10, "right_bound": 100},
            "Goomba",
            EnemyType.GOOMBA,
        ),
    ],
)
def test_tiled_level_reads_enemy_type(
    monkeypatch, properties, enemy_type_attribute, expected_type
):
    enemy_object = SimpleNamespace(
        type="Enemies",
        name="enemy-1",
        x=20,
        y=30,
        properties=properties,
        enemy_type=enemy_type_attribute,
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
