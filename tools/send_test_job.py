#!/usr/bin/env python3
"""
send_test_job.py — POST a print job to the Pico print server, for
bring-up and testing without needing tda-app wired up yet.

Usage:
    # send a real raster file (e.g. produced by render_label.py):
    python3 tools/send_test_job.py --host 192.168.1.60 --api-key <key> \\
        --printer ql810w --file label.bin

    # or just confirm connectivity with a harmless initialize-only frame
    # (ESC @ — resets the printer's command state, prints nothing):
    python3 tools/send_test_job.py --host 192.168.1.60 --api-key <key> \\
        --printer ql810w --ping

    # then check on it:
    python3 tools/send_test_job.py --host 192.168.1.60 --api-key <key> \\
        --job-id 1
"""
import argparse
import base64
import json
import sys
import urllib.error
import urllib.request

PING_PAYLOAD = b"\x1b\x40"  # ESC @ — initialize only


def request(url, api_key, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "X-Api-Key": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", required=True, help="Pico's IP or hostname")
    p.add_argument("--port", type=int, default=8090)
    p.add_argument("--api-key", required=True)
    p.add_argument("--printer", default="ql810w")
    p.add_argument("--title", default="test job")
    p.add_argument("--job-id", type=int, help="poll GET /printjobs/<id> instead of submitting a job")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--file", help="raw Brother raster bytes to send as-is")
    g.add_argument("--ping", action="store_true", help="send a minimal ESC @ no-op payload")
    args = p.parse_args()

    base = "http://%s:%d" % (args.host, args.port)

    if args.job_id is not None:
        status, body = request("%s/printjobs/%d" % (base, args.job_id), args.api_key)
        print(status, json.dumps(body, indent=2))
        sys.exit(0 if status < 300 else 1)

    if args.file:
        with open(args.file, "rb") as f:
            raw = f.read()
    elif args.ping:
        raw = PING_PAYLOAD
    else:
        p.error("one of --file, --ping, or --job-id is required")
        return

    payload = {
        "printerId": args.printer,
        "title": args.title,
        "contentType": "raw_base64",
        "content": base64.b64encode(raw).decode(),
    }
    status, body = request("%s/printjobs" % base, args.api_key, method="POST", payload=payload)
    print(status, json.dumps(body, indent=2))
    sys.exit(0 if status < 300 else 1)


if __name__ == "__main__":
    main()
