import pickle
import struct


def send_msg(sock, data):
    if not sock:
        return False
    try:
        raw = pickle.dumps(data)
        sock.sendall(struct.pack('>I', len(raw)) + raw)
        return True
    except:
        return False


def recv_msg(sock):
    if not sock:
        return None
    try:
        raw_len = b""
        while len(raw_len) < 4:
            chunk = sock.recv(4 - len(raw_len))
            if not chunk:
                return None
            raw_len += chunk
        msg_len = struct.unpack('>I', raw_len)[0]

        raw_data = b""
        while len(raw_data) < msg_len:
            chunk = sock.recv(msg_len - len(raw_data))
            if not chunk:
                return None
            raw_data += chunk
        return pickle.loads(raw_data)
    except:
        return None
