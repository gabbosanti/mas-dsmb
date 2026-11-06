"""Distributed roster support structures."""

from dataclasses import dataclass, field

from distributed_smb.shared.enums import ConnectionStatus


class RosterValidationError(ValueError):
    """Raised when roster validation fails."""


@dataclass(slots=True)
class RosterEntry:
    """Represents a known participant in the session."""

    player_id: str
    host: str
    udp_port: int
    join_index: int
    status: ConnectionStatus = ConnectionStatus.CONNECTED
    is_host: bool = False

    def __post_init__(self):
        """Validate entry fields."""
        if not self.player_id or not isinstance(self.player_id, str):
            raise RosterValidationError(f"Invalid player_id: {self.player_id}")
        if not self.host or not isinstance(self.host, str):
            raise RosterValidationError(f"Invalid host: {self.host}")
        if not (1024 <= self.udp_port <= 65535):
            raise RosterValidationError(f"udp_port out of range: {self.udp_port}")
        if self.join_index < 0:
            raise RosterValidationError(f"join_index must be >= 0: {self.join_index}")


@dataclass(slots=True)
class GlobalRoster:
    """Locally replicated view of session participants."""

    players: list[RosterEntry] = field(default_factory=list)

    def add_player(self, entry: RosterEntry):
        """Add a player to roster, ensuring join_index uniqueness."""
        # Check for duplicate join_index
        for existing in self.players:
            if existing.join_index == entry.join_index:
                raise RosterValidationError(
                    f"Duplicate join_index {entry.join_index} for player {entry.player_id}"
                )
        self.players.append(entry)

    def get_player(self, player_id: str) -> RosterEntry | None:
        for p in self.players:
            if p.player_id == player_id:
                return p
        return None

    def get_host(self) -> RosterEntry | None:
        """Retrieve the current host entry."""
        for p in self.players:
            if p.is_host:
                return p
        return None

    def remove_player(self, player_id: str) -> None:
        """Remove a player from the roster by player_id (no-op if not found)."""
        self.players = [p for p in self.players if p.player_id != player_id]

    def promote_host(self, player_id: str) -> None:
        """Mark player_id as the new authoritative host, clearing the flag on all others."""
        for p in self.players:
            p.is_host = p.player_id == player_id

    def get_all_players(self) -> list[RosterEntry]:
        """Get all players sorted by join order."""
        return sorted(self.players, key=lambda p: p.join_index)
