class MessageValidationError(ValueError):
    """Raised when a message fails validation."""



def validate_player_id(player_id: str) -> None:
    """Validate that player_id is non-empty string."""
    if not player_id or not isinstance(player_id, str):
        raise MessageValidationError(f"Invalid player_id: {player_id}")


def validate_port(port: int) -> None:
    """Validate that port is in valid range."""
    if not (1024 <= port <= 65535):
        raise MessageValidationError(f"Port out of range: {port}")


def validate_join_index(join_index: int) -> None:
    """Validate that join_index is non-negative."""
    if join_index < 0:
        raise MessageValidationError(f"join_index must be >= 0, got {join_index}")
