from __future__ import annotations

from distributed_smb.shared.config import HOST_UDP_PORT
from distributed_smb.shared.session_metadata import (
    CachedPeer,
    SessionMetadata,
    delete_session_metadata,
    read_session_metadata,
    write_session_metadata,
)


class RecoveryMixin:
    def _write_session_metadata(self) -> None:
        peers = [
            CachedPeer(
                player_id=entry.player_id, ip=entry.host, join_index=entry.join_index
            )
            for entry in self.roster.get_all_players()
            if entry.player_id != self.local_player_id
        ]

        write_session_metadata(
            SessionMetadata(
                session_id=self.session_id,
                local_player_id=self.local_player_id,
                peers=peers,
                join_index=self.join_index,
            )
        )

    def attempt_recovery(self) -> str | None:
        self.lifecycle.move_to_recovering()
        metadata = read_session_metadata()
        if metadata is None:
            return None

        if not metadata.peers:
            delete_session_metadata()
            return None

        host_ip = self.recovery_prober.find_current_host(
            metadata.session_id,
            self.local_ip,
            metadata.peers,
            timeout_per_peer=0.5,
        )

        if host_ip is None:
            delete_session_metadata()
            return None

        self.remote_host = host_ip
        self.remote_port = HOST_UDP_PORT
        self.session_id = metadata.session_id
        return host_ip
