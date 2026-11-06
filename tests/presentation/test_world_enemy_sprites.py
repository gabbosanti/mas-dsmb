import pytest

from distributed_smb.domain.entity import EnemyType
from distributed_smb.presentation.renderer.world import WorldRenderer


class _SpriteFactory:
    def __init__(self):
        self.requested_asset = None

    def _get_asset_sprite(self, filename, source_rect, width, height):
        self.requested_asset = (filename, source_rect, width, height)
        return object()


@pytest.mark.parametrize(
    ("enemy_type", "expected_rect"),
    [
        (EnemyType.KOOPA, (100, 6, 18, 25)),
        (EnemyType.GOOMBA, (4, 6, 18, 25)),
    ],
)
def test_enemy_renderer_selects_sprite_by_type(enemy_type, expected_rect):
    sprite_factory = _SpriteFactory()
    renderer = WorldRenderer(owner=None, sprite_factory=sprite_factory)

    renderer._build_environment_asset_sprite(
        "enemy", enemy_type.value, width=34, height=34
    )

    assert sprite_factory.requested_asset == (
        "Enemies.png",
        expected_rect,
        34,
        34,
    )
