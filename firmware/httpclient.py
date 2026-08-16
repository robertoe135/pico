# httpclient.py — a minimal, dependency-free HTTP/1.1 client for the
# *outbound* requests this device now makes (poll Convex, fetch a job's
# raster bytes, report completion). Hand-rolled instead of using
# MicroPython's `urequests`/`requests` because that library buffers an
# entire response body in RAM before returning it — fine for the small
# JSON responses here, but not for a label's raster bytes (tens of KB on
# a 264KB-RAM board). `get_stream()` instead feeds the body to a callback
# in small chunks as it arrives, so peak memory use stays flat regardless
# of job size.
#
# No redirect following. See docs/TDA_APP_INTEGRATION.md's note on why
# the Convex content endpoint is designed to avoid ever needing one — a
# redirect here is treated as a hard error rather than silently
# mishandled.

import socket

try:
    import ssl
except ImportError:
    import ussl as ssl  # older MicroPython builds

CHUNK_SIZE = 2048
CONNECT_TIMEOUT_S = 6  # kept under the ~8s hardware watchdog ceiling —
# nothing feeds the watchdog during connect/handshake, so this phase on
# its own must never be the thing that trips it.
SOCK_TIMEOUT_S = 10  # applies per blocking socket op (recv/send), not to
# the whole transfer — a slow-but-steady multi-chunk download is fine as
# long as each individual read returns within this window.

# request()'s small-response path is for control-message JSON only
# (job metadata, completion acks) — a few hundred bytes in practice. It
# buffers the whole body, unlike get_stream(), so it's capped well above
# anything real but well below "accidentally exhaust a 264KB-RAM board
# because a response was bigger than expected."
MAX_SMALL_BODY_BYTES = 16 * 1024

_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


class HTTPError(Exception):
    pass


def _parse_url(url):
    if url.startswith("https://"):
        scheme, rest = "https", url[len("https://"):]
    elif url.startswith("http://"):
        scheme, rest = "http", url[len("http://"):]
    else:
        raise HTTPError("unsupported URL scheme: %r" % (url,))
    if "/" in rest:
        hostport, path = rest.split("/", 1)
        path = "/" + path
    else:
        hostport, path = rest, "/"
    if ":" in hostport:
        host, port_s = hostport.split(":", 1)
        port = int(port_s)
    else:
        host = hostport
        port = 443 if scheme == "https" else 80
    return scheme, host, port, path


def _sendall(sock, data):
    mv = memoryview(data)
    sent = 0
    while sent < len(mv):
        sent += sock.send(mv[sent:])


class _BufferedSocket:
    """Small read buffer over a raw/TLS socket so header parsing doesn't
    need one-byte-at-a-time recv() calls, while still bounding memory to
    roughly CHUNK_SIZE ahead of what's been consumed."""

    def __init__(self, sock):
        self._sock = sock
        self._buf = b""

    def _fill(self):
        chunk = self._sock.recv(CHUNK_SIZE)
        if chunk:
            self._buf += chunk
        return chunk

    def readline(self):
        while b"\r\n" not in self._buf:
            if not self._fill():
                line, self._buf = self._buf, b""
                return line
        idx = self._buf.index(b"\r\n") + 2
        line, self._buf = self._buf[:idx], self._buf[idx:]
        return line

    def read(self, n):
        while len(self._buf) < n:
            if not self._fill():
                break
        data, self._buf = self._buf[:n], self._buf[n:]
        return data


def _connect(scheme, host, port, timeout_s):
    addr = socket.getaddrinfo(host, port)[0][-1]
    sock = socket.socket()
    sock.settimeout(timeout_s)
    sock.connect(addr)
    if scheme == "https":
        sock = ssl.wrap_socket(sock, server_hostname=host)
    return sock


def _send_request(sock, method, host, path, headers, body):
    lines = ["%s %s HTTP/1.1" % (method, path), "Host: %s" % host, "Connection: close"]
    headers = dict(headers or {})
    if body:
        headers["Content-Length"] = str(len(body))
    for k, v in headers.items():
        lines.append("%s: %s" % (k, v))
    lines.append("")
    lines.append("")
    _sendall(sock, "\r\n".join(lines).encode())
    if body:
        _sendall(sock, body)


def _read_status_and_headers(bsock):
    status_line = bsock.readline()
    if not status_line:
        raise HTTPError("connection closed before response")
    try:
        parts = status_line.decode().split(" ", 2)
        status = int(parts[1])
    except Exception:
        raise HTTPError("malformed status line: %r" % (status_line,))

    headers = {}
    while True:
        line = bsock.readline()
        if line in (b"\r\n", b""):
            break
        if b":" in line:
            k, v = line.decode().split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return status, headers


def _stream_body(bsock, headers, on_chunk, feed=None, max_bytes=None):
    """Feeds body bytes to on_chunk(bytes) in CHUNK_SIZE-ish pieces,
    without holding the full body in memory. Supports Content-Length and
    chunked transfer-encoding — the two things a Convex httpAction
    response is realistically going to use. Calls feed() (if given) once
    per chunk read — this is what keeps the watchdog fed through a
    transfer that runs longer than its ~8s ceiling. If max_bytes is
    given, raises rather than reading past it (Content-Length is checked
    upfront; chunked is checked as it accumulates, since its total isn't
    known upfront)."""
    transfer_encoding = headers.get("transfer-encoding", "")
    if "chunked" in transfer_encoding:
        total = 0
        while True:
            size_line = bsock.readline().strip()
            if b";" in size_line:
                size_line = size_line.split(b";", 1)[0]
            if not size_line:
                raise HTTPError("connection closed mid-chunked-response")
            size = int(size_line, 16)
            if size == 0:
                while True:  # optional trailing headers, then blank line
                    line = bsock.readline()
                    if line in (b"\r\n", b""):
                        break
                return
            total += size
            if max_bytes is not None and total > max_bytes:
                raise HTTPError("chunked response exceeded %d byte cap" % max_bytes)
            remaining = size
            while remaining > 0:
                chunk = bsock.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise HTTPError("connection closed mid-chunk")
                on_chunk(chunk)
                if feed:
                    feed()
                remaining -= len(chunk)
            bsock.readline()  # trailing CRLF after each chunk's data
        return

    length = headers.get("content-length")
    if length is None:
        raise HTTPError("response has neither Content-Length nor chunked encoding")
    remaining = int(length)
    if max_bytes is not None and remaining > max_bytes:
        raise HTTPError("response Content-Length %d exceeds %d byte cap" % (remaining, max_bytes))
    while remaining > 0:
        chunk = bsock.read(min(CHUNK_SIZE, remaining))
        if not chunk:
            raise HTTPError("connection closed early (wanted %d more bytes)" % remaining)
        on_chunk(chunk)
        if feed:
            feed()
        remaining -= len(chunk)


def request(method, url, headers=None, body=None, timeout_s=SOCK_TIMEOUT_S, feed=None):
    """Small-response helper: returns (status, headers, body_bytes).
    Only use where the response is known to be small (JSON control
    messages, capped at MAX_SMALL_BODY_BYTES) — use get_stream() for a
    job's print content. Calls feed() (if given) after connect, after
    headers, and once per body chunk — see main.py's watchdog comment for
    why that matters."""
    scheme, host, port, path = _parse_url(url)
    sock = _connect(scheme, host, port, min(timeout_s, CONNECT_TIMEOUT_S))
    try:
        if feed:
            feed()
        sock.settimeout(timeout_s)
        _send_request(sock, method, host, path, headers, body)
        bsock = _BufferedSocket(sock)
        status, resp_headers = _read_status_and_headers(bsock)
        if feed:
            feed()
        if status in _REDIRECT_STATUSES:
            raise HTTPError("redirect (%d) not supported by this client" % status)
        chunks = []
        if status != 204:
            _stream_body(bsock, resp_headers, chunks.append, feed=feed, max_bytes=MAX_SMALL_BODY_BYTES)
        return status, resp_headers, b"".join(chunks)
    finally:
        sock.close()


def get_stream(url, headers, on_chunk, timeout_s=SOCK_TIMEOUT_S, feed=None):
    """Streams a response body to on_chunk(bytes) as it arrives, without
    ever buffering the whole thing. Returns the status code; raises
    HTTPError on a redirect or a transport-level problem. Calls feed()
    (if given) after connect, after headers, and once per body chunk."""
    scheme, host, port, path = _parse_url(url)
    sock = _connect(scheme, host, port, min(timeout_s, CONNECT_TIMEOUT_S))
    try:
        if feed:
            feed()
        sock.settimeout(timeout_s)
        _send_request(sock, "GET", host, path, headers, None)
        bsock = _BufferedSocket(sock)
        status, resp_headers = _read_status_and_headers(bsock)
        if feed:
            feed()
        if status in _REDIRECT_STATUSES:
            raise HTTPError(
                "redirect (%d) not supported by this client — see "
                "docs/TDA_APP_INTEGRATION.md's note on same-origin content "
                "serving" % status
            )
        if status == 200:
            _stream_body(bsock, resp_headers, on_chunk, feed=feed)
        return status
    finally:
        sock.close()
