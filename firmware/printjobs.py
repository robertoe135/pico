# printjobs.py — a TCP connection to the target printer, used to stream a
# job's raster bytes straight from the Convex fetch (see poller.py) into
# the printer's socket without ever buffering a whole job in RAM.

import socket

CONNECT_TIMEOUT_S = 10


class PrinterConnection:
    """
    with PrinterConnection(ip, port) as conn:
        conn.write(chunk)
        ...
    """

    def __init__(self, ip, port):
        self._ip = ip
        self._port = port
        self._sock = None

    def __enter__(self):
        addr = socket.getaddrinfo(self._ip, self._port)[0][-1]
        sock = socket.socket()
        sock.settimeout(CONNECT_TIMEOUT_S)
        sock.connect(addr)
        self._sock = sock
        return self

    def write(self, data):
        mv = memoryview(data)
        sent = 0
        while sent < len(mv):
            sent += self._sock.send(mv[sent:])

    def __exit__(self, exc_type, exc, tb):
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
        return False  # never suppress an exception from inside the `with`
