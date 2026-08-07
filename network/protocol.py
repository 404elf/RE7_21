import pickle
import struct


def send_msg(sock, data):
    payload = pickle.dumps(data)
    sock.sendall(struct.pack('>I', len(payload)) + payload)


def recv_msg(sock):
    raw_len = recv_exact(sock, 4)
    if not raw_len:
        return None

    length = struct.unpack('>I', raw_len)[0]
    payload = recv_exact(sock, length)
    if not payload:
        return None

    return pickle.loads(payload)


def recv_exact(sock, length):
    data = b''
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            return None
        data += chunk
    return data
