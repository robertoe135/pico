#!/usr/bin/env python3
"""
mock_printer.py — stand-in for the Brother QL-810W's raw-socket print
listener (TCP port 9100), for testing the Pico relay end-to-end without a
physical printer on hand. Every connection's bytes are saved to a
timestamped file so you can inspect what the Pico actually sent.

Usage:
    python3 tools/mock_printer.py --port 9100 --out-dir ./received

Then point firmware/config.py's PRINTERS[...]["ip"]/["port"] at this
machine's LAN address instead of the real printer.
"""
import argparse
import datetime
import os
import socketserver


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        chunks = []
        while True:
            data = self.request.recv(65536)
            if not data:
                break
            chunks.append(data)
        payload = b"".join(chunks)
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = os.path.join(self.server.out_dir, "received_%s.bin" % ts)
        with open(path, "wb") as f:
            f.write(payload)
        print("[%s] %d bytes from %s -> %s" % (ts, len(payload), self.client_address, path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--out-dir", default="./received")
    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True

    server = Server(("0.0.0.0", args.port), Handler)
    server.out_dir = args.out_dir
    print("mock_printer listening on :%d, writing to %s" % (args.port, args.out_dir))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
