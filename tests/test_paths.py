from distributed_smb.shared.paths import ASSETS_DIR, PACKAGE_ROOT
from src.distributed_smb.shared.paths import LEVELS_DIR, TILESETS_DIR


def test_shared_asset_paths_resolve_package_resources():
    assert PACKAGE_ROOT.name == "distributed_smb"
    assert ASSETS_DIR == PACKAGE_ROOT / "assets"
    assert TILESETS_DIR == ASSETS_DIR / "tilesets"
    assert LEVELS_DIR == ASSETS_DIR / "levels"
