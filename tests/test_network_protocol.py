import socket

import re7_21


def test_send_and_receive_message_roundtrip():
    left, right = socket.socketpair()

    try:
        payload = {"type": "TEST", "value": 123}
        re7_21.send_msg(left, payload)
        received = re7_21.recv_msg(right)

        assert received == payload
    finally:
        left.close()
        right.close()
