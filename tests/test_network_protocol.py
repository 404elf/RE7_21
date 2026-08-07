import socket

from network import protocol


def test_protocol_roundtrip():
    left, right = socket.socketpair()

    try:
        payload = {"type": "TEST", "value": 123}
        assert protocol.send_msg(left, payload) is True
        received = protocol.recv_msg(right)

        assert received == payload
    finally:
        left.close()
        right.close()


def test_protocol_empty_socket_returns_false_or_none():
    left, right = socket.socketpair()
    left.close()

    try:
        assert protocol.recv_msg(right) is None
    finally:
        right.close()
