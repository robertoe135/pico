# Deployment

## Hardware

- Raspberry Pi Pico **W** (the plain Pico has no WiFi radio — this won't
  work on it).
- USB-A to micro-USB cable for flashing and (typically) ongoing power —
  the Pico W has no other reasonable power input for an always-on office
  deployment; a USB wall adapter is fine, no need to keep it tethered to a
  computer.
- Same LAN as the Brother QL-810W (WiFi).

## 1. Flash MicroPython

Download the latest **Pico W** UF2 build (not the plain Pico build — they
are different images) from
https://micropython.org/download/RPI_PICO_W/. Hold the BOOTSEL button
while plugging the Pico W into USB, it mounts as a mass-storage device;
drag the `.uf2` file onto it. It reboots running MicroPython.

## 2. Configure

```bash
cd firmware
cp config.example.py config.py
```

Edit `config.py`:
- `WIFI_SSID` / `WIFI_PASSWORD` — the office WiFi.
- `CONVEX_BASE_URL` — your Convex deployment's `.convex.site` URL (only
  meaningful once the tda-app/Convex side from
  [TDA_APP_INTEGRATION.md](./TDA_APP_INTEGRATION.md) exists; point it at
  [`tools/mock_convex_server.py`](../tools/mock_convex_server.py) in the
  meantime for bring-up).
- `API_KEY` — generate one: `python3 -c "import secrets; print(secrets.token_hex(24))"`,
  and set the same value as `PICO_API_KEY` in Convex's environment.
- `PRINTERS["ql810w"]["ip"]` — the QL-810W's LAN address. Set a DHCP
  reservation for it on the router so this never drifts out of sync.

`config.py` is gitignored — it holds real credentials, never commit it.

## 3. Copy files to the board

Using [`mpremote`](https://docs.micropython.org/en/latest/reference/mpremote.html)
(`pip install mpremote`), with the Pico W connected over USB:

```bash
cd firmware
mpremote connect auto fs cp boot.py :boot.py
mpremote connect auto fs cp main.py :main.py
mpremote connect auto fs cp config.py :config.py
mpremote connect auto fs cp wifi.py :wifi.py
mpremote connect auto fs cp httpclient.py :httpclient.py
mpremote connect auto fs cp poller.py :poller.py
mpremote connect auto fs cp printjobs.py :printjobs.py
mpremote connect auto fs cp status_led.py :status_led.py
mpremote connect auto reset
```

(Thonny's file manager works just as well if you'd rather drag-and-drop —
`Tools > Options > Interpreter`, pick the Pico W, then use the Files
panel.)

## 4. Verify

Watch the boot over serial:

```bash
mpremote connect auto
```

You should see the onboard LED fast-blink (connecting), settle briefly
once WiFi connects, then blip once every `POLL_INTERVAL_S` seconds — that
slow blip is the normal idle heartbeat, one per poll against
`CONVEX_BASE_URL`.

There's no inbound port to `curl` anymore (that's the point — see below),
so to confirm it's actually reaching the network, point it at the mock
server first:

```bash
python3 tools/mock_convex_server.py serve --port 8091 --api-key testkey
# set config.py: CONVEX_BASE_URL = "http://<this-machine's-LAN-IP>:8091", API_KEY = "testkey"
python3 tools/mock_convex_server.py enqueue --file <some .bin file> --printer ql810w
```

You should see the mock server log the poll hitting `/pico/pending-jobs`,
then handing out the job, then (once
[`tools/mock_printer.py`](../tools/mock_printer.py) or the real QL-810W
receives it) the completion report on `/pico/jobs/<id>/complete`.

Then work through the [first physical test checklist](./BROTHER_PROTOCOL.md#first-physical-test-checklist).

## 5. Reaching the printer from outside the office LAN

This no longer needs anything on this device's side. The earlier design
had the Pico listening for inbound requests, which meant solving "how
does the internet reach something on my LAN" — port-forwarding, dynamic
DNS, or a tunnel. The current design flips that: the Pico only ever
makes *outbound* HTTPS requests (polling Convex, fetching a job, reporting
back), and outbound traffic needs no router configuration on basically
any network — the office WiFi just needs to allow normal outbound
internet access, same as any laptop or phone on it already has.

So there's nothing to set up here beyond what §2 already covers
(`CONVEX_BASE_URL` pointing at the real deployment). No tunnel, no
dedicated always-on box, no port-forward, no dynamic DNS.

One thing worth being honest about: `firmware/httpclient.py`'s TLS
handshake (`ssl.wrap_socket(sock, server_hostname=host)`) doesn't
currently pin or verify the server certificate — whether that's checked
at all depends on the MicroPython build's mbedtls configuration, which
varies. That's a materially weaker guarantee than a fully verified HTTPS
connection would give you (an on-path attacker could in principle
intercept the connection), mitigated in practice by the `X-Api-Key`
check still gating what Convex will actually hand back or accept. If you
want to close that gap later: check whether your MicroPython build
supports `cert_reqs=ssl.CERT_REQUIRED` with a supplied CA (`cadata=...`)
— Pico W's 2MB flash has room to embed the relevant root CA cert if so.
Not blocking for v1, worth revisiting once the rest of the pipeline is
proven out.

## Reliability notes

- The board reconnects WiFi automatically if it drops (checked every
  loop iteration in `main.py`); `wifi.connect()`'s retry wait feeds the
  watchdog itself, so a slow-to-reconnect AP doesn't get rebooted out
  from under a reconnect attempt that's still in progress.
- A hardware watchdog (`machine.WDT`, ~8s timeout) reboots the board if
  the event loop ever wedges — no one is going to be standing next to it
  to power-cycle it. Every connect-phase timeout (WiFi, Convex, the
  printer) is kept under that 8s ceiling on its own, since nothing can
  feed the watchdog mid-connect; past that, `httpclient.py` feeds it
  after headers and once per body chunk, so an individual job can safely
  take much longer than 8s end-to-end. See the comment in `main.py`.
- The printer connect timeout in particular is deliberately short
  (`printjobs.py`, 6s) — a QL-810W that's off or unreachable is a
  realistic failure mode, not just a theoretical one, and should report
  a failed job quickly rather than risk outliving the watchdog.
- `firmware/main.py` `print()`s on poll/job failures — invisible when
  running headless, but useful if you plug in `mpremote` to see what a
  device that "seems stuck" is actually doing.
- Delivery is **at-least-once**, not exactly-once: if the Pico prints a
  job but its `POST .../complete` report doesn't land, Convex's
  recommended stale-claim reclaim cron (see
  [TDA_APP_INTEGRATION.md](./TDA_APP_INTEGRATION.md) §2) will eventually
  re-queue it, and it prints again. For a label printer, an occasional
  duplicate is a far better failure mode than a silently lost job — see
  [PROTOCOL.md](./PROTOCOL.md)'s note on the `complete` endpoint.
