#!/usr/bin/env python3
"""
mock_convex_server.py — stands in for the Convex HTTP endpoints described
in docs/TDA_APP_INTEGRATION.md, so the Pico's poll/pull firmware can be
tested end-to-end before that side exists in tda-app.

Implements the real contract:
    GET  /pico/pending-jobs        -> claims + returns the oldest queued
                                       job, or 204 if none
    GET  /pico/jobs/<id>/content   -> raw bytes for that job
    POST /pico/jobs/<id>/complete  -> records the reported outcome

Plus one mock-only convenience endpoint, NOT part of the real contract,
for queuing test jobs while the server is running:
    POST /_enqueue   {"printerId": ..., "title": ..., "contentBase64": ...}

Usage:
    # terminal 1
    python3 tools/mock_convex_server.py serve --port 8091 --api-key testkey

    # terminal 2 — queue a job (e.g. raw Brother raster bytes from
    # render_label.py)
    python3 tools/mock_convex_server.py enqueue --host localhost --port 8091 \\
        --printer ql810w --file label.bin

Then point the Pico's firmware/config.py at:
    CONVEX_BASE_URL = "http://<this-machine's-LAN-IP>:8091"
    API_KEY = "testkey"
Plain http:// is fine here — this mock has no TLS. Use https:// only for
the real Convex deployment; firmware/httpclient.py supports both.
"""
import argparse
import base64
import http.server
import itertools
import json
import sys
import threading
import urllib.error
import urllib.request

_jobs = {}  # id -> {id, printerId, title, content: bytes, state}
_ids = itertools.count(1)
_lock = threading.Lock()


def make_handler(api_key):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            print("[mock-convex] " + (fmt % a))

        def _send_json(self, status, obj):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self):
            return self.headers.get("X-Api-Key") == api_key

        def do_GET(self):
            if self.path == "/pico/pending-jobs":
                if not self._authorized():
                    return self._send_json(401, {"error": "unauthorized"})
                with _lock:
                    pending = [j for j in _jobs.values() if j["state"] == "pending"]
                    if not pending:
                        self.send_response(204)
                        self.end_headers()
                        return
                    job = min(pending, key=lambda j: j["id"])
                    job["state"] = "claimed"
                return self._send_json(
                    200,
                    {
                        "id": job["id"],
                        "printerId": job["printerId"],
                        "title": job["title"],
                        "bytes": len(job["content"]),
                    },
                )

            if self.path.startswith("/pico/jobs/") and self.path.endswith("/content"):
                if not self._authorized():
                    return self._send_json(401, {"error": "unauthorized"})
                job_id_s = self.path.split("/")[3]
                with _lock:
                    job = _jobs.get(int(job_id_s)) if job_id_s.isdigit() else None
                if job is None:
                    return self._send_json(404, {"error": "not found"})
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(job["content"])))
                self.end_headers()
                self.wfile.write(job["content"])
                return

            self._send_json(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b""

            if self.path.startswith("/pico/jobs/") and self.path.endswith("/complete"):
                if not self._authorized():
                    return self._send_json(401, {"error": "unauthorized"})
                job_id_s = self.path.split("/")[3]
                try:
                    payload = json.loads(raw or b"{}")
                except Exception:
                    payload = {}
                with _lock:
                    job = _jobs.get(int(job_id_s)) if job_id_s.isdigit() else None
                    if job is not None:
                        job["state"] = payload.get("state", "unknown")
                print("[mock-convex] job %s -> %s" % (job_id_s, payload))
                return self._send_json(200, {"ok": True})

            if self.path == "/_enqueue":
                try:
                    payload = json.loads(raw)
                    content = base64.b64decode(payload["contentBase64"])
                except Exception as e:
                    return self._send_json(400, {"error": "bad payload: %s" % e})
                job_id = next(_ids)
                with _lock:
                    _jobs[job_id] = {
                        "id": job_id,
                        "printerId": payload["printerId"],
                        "title": payload.get("title", ""),
                        "content": content,
                        "state": "pending",
                    }
                print(
                    "[mock-convex] queued job %d (%d bytes, printer=%s)"
                    % (job_id, len(content), payload["printerId"])
                )
                return self._send_json(201, {"id": job_id})

            self._send_json(404, {"error": "not found"})

    return Handler


def cmd_serve(args):
    handler = make_handler(args.api_key)
    server = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), handler)
    print("mock-convex listening on :%d (api key: %s)" % (args.port, args.api_key))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def cmd_enqueue(args):
    with open(args.file, "rb") as f:
        content = f.read()
    payload = {
        "printerId": args.printer,
        "title": args.title,
        "contentBase64": base64.b64encode(content).decode(),
    }
    url = "http://%s:%d/_enqueue" % (args.host, args.port)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(resp.status, resp.read().decode())
    except urllib.error.HTTPError as e:
        print(e.code, e.read().decode())
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", help="run the mock server")
    p_serve.add_argument("--port", type=int, default=8091)
    p_serve.add_argument("--api-key", default="testkey")
    p_serve.set_defaults(func=cmd_serve)

    p_enqueue = sub.add_parser("enqueue", help="queue a test job on a running mock server")
    p_enqueue.add_argument("--host", default="localhost")
    p_enqueue.add_argument("--port", type=int, default=8091)
    p_enqueue.add_argument("--printer", default="ql810w")
    p_enqueue.add_argument("--title", default="test job")
    p_enqueue.add_argument("--file", required=True, help="raw Brother raster bytes (e.g. from render_label.py)")
    p_enqueue.set_defaults(func=cmd_enqueue)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
