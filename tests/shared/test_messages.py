import pytest

from distributed_smb.shared.messages.gameplay import MessageValidationError
from distributed_smb.shared.messages.recovery import (
    HostDiscoveryProbe,
    HostIdentityResponse,
)
from distributed_smb.shared.messages.session import SessionCreate, SessionJoin


def test_session_messages_reject_malformed_fields():
    """Validation happens in __post_init__, so a bad message can never be sent."""
    with pytest.raises(MessageValidationError, match="Invalid player_id"):
        SessionJoin(session_id="abc", player_id="", ip="127.0.0.1", port=5000)

    with pytest.raises(MessageValidationError, match="Invalid session_id"):
        SessionJoin(session_id="", player_id="p1", ip="127.0.0.1", port=5000)

    with pytest.raises(MessageValidationError, match="Port out of range"):
        SessionJoin(session_id="abc", player_id="p1", ip="127.0.0.1", port=100)

    with pytest.raises(MessageValidationError, match="Invalid player_id"):
        SessionCreate(player_id="", ip="127.0.0.1", udp_port=50010)


def test_recovery_messages_reject_malformed_fields():
    with pytest.raises(MessageValidationError, match="Invalid session_id"):
        HostDiscoveryProbe(session_id="", requester_ip="10.0.0.5")

    with pytest.raises(MessageValidationError, match="Invalid requester_ip"):
        HostDiscoveryProbe(session_id="abc", requester_ip="")

    with pytest.raises(MessageValidationError, match="Invalid session_id"):
        HostIdentityResponse(session_id="", host_ip="10.0.0.1")

    with pytest.raises(MessageValidationError, match="Invalid host_ip"):
        HostIdentityResponse(session_id="abc", host_ip="")
