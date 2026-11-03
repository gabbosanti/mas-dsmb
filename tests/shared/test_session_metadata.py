import json
from pathlib import Path

from distributed_smb.shared.session_metadata import (
    CachedPeer,
    SessionMetadata,
    delete_session_metadata,
    read_session_metadata,
    write_session_metadata,
)


def test_metadata_survives_a_write_read_delete_cycle(tmp_path: Path) -> None:
    """This file is what lets a crashed node find its session again."""
    metadata = SessionMetadata(
        session_id="session-123",
        local_player_id="player3",
        peers=[
            CachedPeer(player_id="player-1", ip="192.168.0.1", join_index=0),
            CachedPeer(player_id="player-2", ip="192.168.0.2", join_index=1),
        ],
        join_index=2,
    )

    write_session_metadata(metadata, base_dir=tmp_path)
    assert read_session_metadata(base_dir=tmp_path) == metadata

    delete_session_metadata(base_dir=tmp_path)
    delete_session_metadata(base_dir=tmp_path)  # deleting twice must not raise
    assert not (tmp_path / "session_metadata.json").exists()


def test_unreadable_metadata_is_treated_as_absent(tmp_path: Path) -> None:
    """A missing or corrupt file must send the player to the menu, not crash."""
    assert read_session_metadata(base_dir=tmp_path) is None

    (tmp_path / "session_metadata.json").write_text("{ not json", encoding="utf-8")
    assert read_session_metadata(base_dir=tmp_path) is None

    (tmp_path / "session_metadata.json").write_text(
        json.dumps({"session_id": "only-a-field"}), encoding="utf-8"
    )
    assert read_session_metadata(base_dir=tmp_path) is None


def test_legacy_metadata_recovers_the_slot_from_the_player_id(tmp_path: Path) -> None:
    (tmp_path / "session_metadata.json").write_text(
        json.dumps(
            {
                "session_id": "session-123",
                "local_player_id": "player3",
                "peers": [],
            }
        ),
        encoding="utf-8",
    )

    metadata = read_session_metadata(base_dir=tmp_path)

    assert metadata is not None
    assert metadata.join_index == 2
