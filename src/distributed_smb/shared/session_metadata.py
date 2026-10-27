from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

SESSION_METADATA_FILENAME = "session_metadata.json"


@dataclass(frozen=True)
class CachedPeer:
    player_id: str
    ip: str
    join_index: int


@dataclass(frozen=True)
class SessionMetadata:
    session_id: str
    local_player_id: str
    peers: list[CachedPeer]


def _session_metadata_path(base_dir: Path | str | None = None) -> Path:
    return (
        Path(Path.cwd() if base_dir is None else base_dir) / SESSION_METADATA_FILENAME
    )


def write_session_metadata(
    metadata: SessionMetadata, base_dir: Path | str | None = None
) -> None:
    session_metadata_path = _session_metadata_path(base_dir)
    session_metadata_path.parent.mkdir(parents=True, exist_ok=True)
    with session_metadata_path.open("w", encoding="utf-8") as handle:
        json.dump(asdict(metadata), handle)


def read_session_metadata(
    base_dir: Path | str | None = None,
) -> Optional[SessionMetadata]:
    session_metadata_path = _session_metadata_path(base_dir)
    if not session_metadata_path.exists():
        return None

    try:
        with session_metadata_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)

        peers_raw = raw.get("peers")
        if not isinstance(peers_raw, list):
            return None

        peers = []
        for peer in peers_raw:
            if (
                not isinstance(peer, dict)
                or not isinstance(peer.get("player_id"), str)
                or not isinstance(peer.get("ip"), str)
                or not isinstance(peer.get("join_index"), int)
            ):
                return None
            peers.append(
                CachedPeer(
                    player_id=peer["player_id"],
                    ip=peer["ip"],
                    join_index=peer["join_index"],
                )
            )

        session_id = raw.get("session_id")
        local_player_id = raw.get("local_player_id")
        if not isinstance(session_id, str) or not isinstance(local_player_id, str):
            return None

        return SessionMetadata(
            session_id=session_id,
            local_player_id=local_player_id,
            peers=peers,
        )
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None


def delete_session_metadata(base_dir: Path | str | None = None) -> None:
    session_metadata_path = _session_metadata_path(base_dir)
    try:
        session_metadata_path.unlink()
    except FileNotFoundError:
        pass
