"""The artificial latency simulator used to demo the system under delay."""

import time

from distributed_smb.network.transport.udp import UdpHandler

RECEIVER_PORT = 59601
LATENCY_MS = 100
TOLERANCE = 0.030  # scheduling jitter


def test_packets_wait_for_the_delay_and_then_arrive_in_send_order():
    """Order matters: snapshots carry the sequence numbers the client compares."""
    sender = UdpHandler(host="127.0.0.1", port=0, artificial_latency_ms=LATENCY_MS)
    receiver = UdpHandler(host="127.0.0.1", port=RECEIVER_PORT)
    sender.open_socket()
    receiver.open_socket()
    try:
        for i in range(5):
            sender.send_packet_nowait(f"pkt-{i}".encode(), "127.0.0.1", RECEIVER_PORT)

        assert receiver.receive_packet_nowait() is None, (
            "delivered before the delay elapsed"
        )

        time.sleep((LATENCY_MS + TOLERANCE * 1000) / 1000.0)
        sender._flush_outgoing()

        received = []
        deadline = time.time() + 0.5
        while time.time() < deadline and len(received) < 5:
            packet = receiver.receive_packet_nowait()
            if packet:
                received.append(packet[0])
            else:
                time.sleep(0.01)

        assert received == [f"pkt-{i}".encode() for i in range(5)]

        direct = UdpHandler(host="127.0.0.1", port=0, artificial_latency_ms=0)
        direct.open_socket()
        direct.send_packet_nowait(b"immediate", "127.0.0.1", RECEIVER_PORT)
        time.sleep(0.02)
        immediate = receiver.receive_packet_nowait()
        direct.close_socket()

        assert immediate is not None, "zero latency must not queue"
        assert immediate[0] == b"immediate"
    finally:
        sender.close_socket()
        receiver.close_socket()
