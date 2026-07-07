"""Shared filesystem paths for package resources."""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PACKAGE_ROOT / "assets"
TILESETS_DIR = ASSETS_DIR / "tilesets"
LEVELS_DIR = ASSETS_DIR / "levels"
