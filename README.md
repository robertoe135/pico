# pico — Brother QL-810W print server on a Raspberry Pi Pico W

A small, self-hosted stand-in for [PrintNode](https://www.printnode.com),
running on a Raspberry Pi Pico W instead of a dedicated office PC, so
[tda-app](https://github.com/robertoe135/tda-app) can print sample labels
to the office's Brother QL-810W from outside the office LAN — without
opening any inbound port on the office network.

## Why

The QL-810W only accepts print jobs over raw TCP (port 9100) from inside
the LAN — it has no cloud-print service of its own. tda-app's label
generator (`lib/pdf/generateSampleLabel.js`) already renders labels
client-side; the missing piece was an always-on relay inside the office
LAN that's reachable from outside it. That was originally planned as
PrintNode's free client running on a spare office machine, but there
wasn't one to dedicate to it — and the alternative of exposing something
publicly (tunnel, port-forward) wasn't workable either without a second
always-on machine to host it. A Pico W fills the role differently: it
never listens for inbound traffic at all.

## How it works

```
Pico W (office LAN)  →  outbound HTTPS poll every few seconds  →  Convex (tda-app)
Pico W                →  raw TCP:9100                          →  Brother QL-810W
```

The Pico runs MicroPython and **polls out** for work — the same shape
PrintNode's own client actually uses (poll the cloud, don't be reached by
it), rather than exposing an HTTP server. Every few seconds it asks
Convex "anything to print?", and if so, streams the job's raster bytes
straight through to the printer's TCP socket as they arrive, then reports
back. Nothing on the Pico ever listens for inbound requests, so there's
no port-forward, no tunnel, and no dedicated always-on box to host
one — the office WiFi just needs the outbound internet access any device
on it already has.

The Pico treats print job content as opaque, pre-encoded Brother raster
bytes — it does not parse PDFs or rasterize labels itself. See
[docs/BROTHER_PROTOCOL.md](./docs/BROTHER_PROTOCOL.md) for why that
encoding step is deliberately kept off this device, and how to produce
those bytes with the `brother_ql` library.

## Repo layout

```
firmware/     MicroPython code that runs on the Pico W
  boot.py           minimal, runs once at power-on
  main.py           startup, poll loop, WiFi/watchdog/LED wiring
  wifi.py           WiFi connect/reconnect
  httpclient.py     minimal outbound HTTP(S) client, streams large responses
  poller.py         poll Convex, stream a job's bytes to the printer, report back
  printjobs.py      a TCP connection to the target printer
  status_led.py     onboard LED status patterns
  config.example.py copy to config.py and fill in — gitignored

tools/        CPython dev/test helpers, run from a laptop, not the Pico
  mock_convex_server.py   stands in for Convex's 3 endpoints, plus a
                           test-job enqueue command
  mock_printer.py         fake TCP printer that dumps received bytes to disk
  render_label.py         image -> Brother raster bytes, via brother_ql

docs/
  PROTOCOL.md              the Pico<->Convex wire contract
  TDA_APP_INTEGRATION.md   full implementation plan for the tda-app/Convex side
  DEPLOYMENT.md            flashing, config, deploying
  BROTHER_PROTOCOL.md      why raster encoding lives off-device, and how to do it
```

## Quick start

1. [Flash MicroPython and deploy the firmware](./docs/DEPLOYMENT.md).
2. Test the poll/print pipeline without a printer or Convex:
   ```bash
   python3 tools/mock_printer.py
   python3 tools/mock_convex_server.py serve --api-key testkey
   # point firmware/config.py at both, then:
   python3 tools/mock_convex_server.py enqueue --file <raster.bin> --printer ql810w
   ```
3. Point `config.py` at the real QL-810W and work through the
   [first physical test checklist](./docs/BROTHER_PROTOCOL.md#first-physical-test-checklist).
4. Build the Convex side of the contract — see
   [docs/TDA_APP_INTEGRATION.md](./docs/TDA_APP_INTEGRATION.md) for the
   full plan (this repo doesn't include tda-app changes; only read access
   to that repo was available here).

## Status / what's not done yet

- **The Convex side doesn't exist yet.** This repo implements and tests
  the Pico's half of the contract (see
  [docs/PROTOCOL.md](./docs/PROTOCOL.md)) and ships a mock server to
  develop against, but the real `printJobs` table, HTTP actions, and
  rasterization step described in
  [docs/TDA_APP_INTEGRATION.md](./docs/TDA_APP_INTEGRATION.md) still need
  to be built in `tda-app`.
- **Rasterization is not implemented anywhere yet** — tda-app's label
  output is currently vector PDF; something needs to render it to a
  bitmap and encode it as Brother raster bytes before a job can be
  queued. See TDA_APP_INTEGRATION.md §4.
- **Delivery is at-least-once, not exactly-once** — a lost completion
  report means a job can print twice rather than not at all. See
  [docs/PROTOCOL.md](./docs/PROTOCOL.md)'s note on the `complete`
  endpoint, and the recommended stale-claim reclaim cron in
  TDA_APP_INTEGRATION.md §2.
- **TLS server-certificate verification** on the Pico's outbound HTTPS
  client isn't confirmed hardened — see the note in
  [docs/DEPLOYMENT.md](./docs/DEPLOYMENT.md#5-reaching-the-printer-from-outside-the-office-lan).
