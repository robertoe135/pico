# httpserver.py — a minimal async HTTP/1.1 server, hand-rolled instead of
# pulling in a framework (e.g. microdot). This server only ever needs to
# handle a handful of small JSON routes for a single client (tda-app /
# Convex), so a full framework is more dependency-management overhead
# (mip installs, version pinning) than it's worth — copying plain .py
# files to the board is the whole deployment story.
#
# Every route handler is `async def handler(req) -> Response`. Path
# segments wrapped in `{}` (e.g. "/printjobs/{id}") are captured into
# `req.params`.

import ujson as json
import uasyncio as asyncio

_STATUS_TEXT = {
    200: "OK",
    201: "Created",
    204: "No Content",
    400: "Bad Request",
    401: "Unauthorized",
    404: "Not Found",
    405: "Method Not Allowed",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
}

# Generous but bounded: a request line + headers past this is almost
# certainly not a well-formed client of ours, and unbounded reads on a
# 264KB-RAM board are how you get a device that needs a power cycle.
_MAX_BODY_BYTES = 512 * 1024
_MAX_HEADER_LINES = 40


class Request:
    def __init__(self, method, path, headers, body, params):
        self.method = method
        self.path = path
        self.headers = headers  # lower-cased header names -> value
        self.body = body  # bytes
        self.params = params  # dict, from {captured} path segments


class Response:
    def __init__(self, status=200, body="", headers=None, content_type="application/json"):
        self.status = status
        self.body = body
        self.headers = headers or {}
        self.content_type = content_type


def json_response(obj, status=200):
    return Response(status=status, body=json.dumps(obj), content_type="application/json")


class HTTPServer:
    def __init__(self):
        self._routes = {}  # method -> list of (segments, handler)

    def route(self, method, path):
        segments = path.strip("/").split("/") if path.strip("/") else []

        def deco(fn):
            self._routes.setdefault(method.upper(), []).append((segments, fn))
            return fn

        return deco

    def _match(self, method, path):
        segs = path.strip("/").split("/") if path.strip("/") else []
        for segments, fn in self._routes.get(method, []):
            if len(segments) != len(segs):
                continue
            params = {}
            ok = True
            for want, got in zip(segments, segs):
                if want.startswith("{") and want.endswith("}"):
                    params[want[1:-1]] = got
                elif want != got:
                    ok = False
                    break
            if ok:
                return fn, params
        return None, None

    async def _read_headers(self, reader):
        headers = {}
        for _ in range(_MAX_HEADER_LINES):
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                return headers
            if b":" in line:
                k, v = line.decode().split(":", 1)
                headers[k.strip().lower()] = v.strip()
        return headers

    async def _handle(self, reader, writer):
        try:
            req_line = await reader.readline()
            if not req_line:
                return
            try:
                method, path, _version = req_line.decode().strip().split(" ", 2)
            except Exception:
                await self._write(writer, Response(400, json.dumps({"error": "bad request line"})))
                return

            headers = await self._read_headers(reader)

            body = b""
            try:
                length = int(headers.get("content-length", "0") or "0")
            except ValueError:
                length = 0
            if length > _MAX_BODY_BYTES:
                await self._write(writer, Response(400, json.dumps({"error": "body too large"})))
                return
            if length > 0:
                body = await reader.readexactly(length)

            if method.upper() == "OPTIONS":
                await self._write(writer, Response(204, ""))
                return

            route_path = path.split("?", 1)[0]
            handler, params = self._match(method.upper(), route_path)
            if handler is None:
                resp = json_response({"error": "not found"}, 404)
            else:
                req = Request(method, path, headers, body, params)
                try:
                    resp = await handler(req)
                except Exception as e:
                    resp = json_response({"error": str(e)}, 500)

            await self._write(writer, resp)
        except Exception:
            # Never let a malformed request or a mid-write disconnect take
            # down the server loop — this is a headless device with no one
            # watching a console.
            pass
        finally:
            # writer.close() is synchronous and universally available across
            # MicroPython versions; avoid depending on wait_closed(), which
            # isn't consistently present in uasyncio's Stream API.
            try:
                writer.close()
            except Exception:
                pass

    async def _write(self, writer, resp):
        body = resp.body.encode() if isinstance(resp.body, str) else resp.body
        text = _STATUS_TEXT.get(resp.status, "")
        writer.write("HTTP/1.1 %d %s\r\n" % (resp.status, text))
        writer.write("Content-Type: %s\r\n" % resp.content_type)
        writer.write("Content-Length: %d\r\n" % len(body))
        writer.write("Connection: close\r\n")
        writer.write("Access-Control-Allow-Origin: *\r\n")
        writer.write("Access-Control-Allow-Headers: Content-Type, X-Api-Key\r\n")
        writer.write("Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n")
        for k, v in resp.headers.items():
            writer.write("%s: %s\r\n" % (k, v))
        writer.write("\r\n")
        writer.write(body)
        await writer.drain()

    async def serve(self, port):
        await asyncio.start_server(self._handle, "0.0.0.0", port)
        while True:
            await asyncio.sleep(3600)
