# pico — Brother QL-810W print server on a Raspberry Pi Pico W

A small, self-hosted stand-in for [PrintNode](https://www.printnode.com),
running on a Raspberry Pi Pico W instead of a dedicated office PC, so
[tda-app](https://github.com/robertoe135/tda-app) can print sample labels
to the office's Brother QL-810W from outside the office LAN.

## Why

The QL-810W only accepts print jobs over raw TCP (port 9100) from inside
the LAN — it has no cloud-print service of its own. tda-app's label
generator (`lib/pdf/generateSampleLabel.js`) already renders labels
client-side; the missing piece was always-on relay inside the office LAN
that's reachable from outside it. That was originally planned as
PrintNode's free client running on a spare office machine, but there
wasn't one to dedicate to it. A Pico W is a $6, always-on, no-moving-parts
substitute for that spare machine.

## How it works

```
tda-app (anywhere)  →  HTTP + API key  →  Pico W (office LAN)  →  raw TCP:9100  →  Brother QL-810W
```

The Pico runs MicroPython and exposes a small PrintNode-shaped REST API
(`GET /printers`, `POST /printjobs`, `GET /printjobs/{id}`, `GET
/health`). It treats print job content as opaque, pre-encoded Brother
raster bytes and just relays them to the printer's socket — it does not
parse PDFs or rasterize labels itself. See
[docs/BROTHER_PROTOCOL.md](./docs/BROTHER_PROTOCOL.md) for why that
encoding step is deliberately kept off this device, and how to produce
those bytes with the `brother_ql` library.

## Repo layout

```
firmware/     MicroPython code that runs on the Pico W
  boot.py           minimal, runs once at power-on
  main.py           HTTP routes, startup, WiFi/watchdog wiring
  wifi.py           WiFi connect/reconnect
  httpserver.py     minimal async HTTP/1.1 server (no external deps)
  printjobs.py      job validation + relay to the printer's TCP socket
  status_led.py     onboard LED status patterns
  config.example.py copy to config.py and fill in — gitignored

tools/        CPython dev/test helpers, run from a laptop, not the Pico
  mock_printer.py   fake TCP printer that dumps received bytes to disk
  send_test_job.py  CLI to POST a test print job to the Pico
  render_label.py   image -> Brother raster bytes, via brother_ql

docs/
  API.md               full REST API reference + tda-app integration example
  DEPLOYMENT.md         flashing, config, deploying, exposing beyond the LAN
  BROTHER_PROTOCOL.md   why raster encoding lives off-device, and how to do it
```

## Quick start

1. [Flash MicroPython and deploy the firmware](./docs/DEPLOYMENT.md).
2. Test the relay without a printer attached:
   ```bash
   python3 tools/mock_printer.py
   python3 tools/send_test_job.py --host <pico-ip> --api-key <key> --printer ql810w --ping
   ```
3. Point `config.py` at the real QL-810W and work through the
   [first physical test checklist](./docs/BROTHER_PROTOCOL.md#first-physical-test-checklist).
4. Wire tda-app up to it — see [docs/API.md](./docs/API.md).

## Status / what's not done yet

- **Rasterization on the tda-app side is not implemented.** This repo
  ships the relay and the tooling to produce/send raster bytes for
  testing, but tda-app itself doesn't yet have a step that converts its
  rendered label into Brother raster bytes before calling `/printjobs` —
  see [docs/BROTHER_PROTOCOL.md](./docs/BROTHER_PROTOCOL.md#tda-app-integration-not-part-of-this-repo).
- **No public exposure is configured** — the Pico only listens on the LAN
  until you set up a tunnel or port-forward; see
  [docs/DEPLOYMENT.md](./docs/DEPLOYMENT.md#5-reaching-it-from-outside-the-office-lan).
- **Job history is in-memory only** (last 50 jobs, lost on reboot) — fine
  for a relay, but there's no retry queue for a printer that's temporarily
  offline.
