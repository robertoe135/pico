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
- `API_KEY` — generate one: `python3 -c "import secrets; print(secrets.token_hex(24))"`.
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
mpremote connect auto fs cp httpserver.py :httpserver.py
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

You should see the onboard LED fast-blink (connecting), then settle into
a slow heartbeat blip once WiFi connects. Find its IP either from that
serial output (add a `print()` in `main.py` if you want one — none is
wired in by default since after first bring-up you'll normally run
headless) or from your router's DHCP client list / the reservation you
set in step 2.

```bash
curl http://<pico-ip>:8090/health
```

Then work through the [first physical test checklist](./BROTHER_PROTOCOL.md#first-physical-test-checklist).

## 5. Reaching it from outside the office LAN

This is what actually solves the original problem — tda-app running
outside the office needs to reach this server. The Pico only listens on
the LAN; getting a request to it from the internet is a router/network
decision, not something this firmware handles. Two options, in order of
recommendation:

- **A tunnel (recommended): Tailscale or Cloudflare Tunnel.** Run either
  on some other always-on box on the same LAN (or even the router, if it
  supports it) and expose the Pico's `:8090` through it. Avoids opening any
  inbound port on the office router, and avoids relying solely on the
  `X-Api-Key` header as the only thing standing between this device and
  the open internet. The Pico itself doesn't need to run anything extra —
  it just needs to be reachable on the LAN from whatever box is running
  the tunnel.
- **Router port-forward + dynamic DNS**, if a tunnel isn't an option.
  Forward some external port to the Pico's `:8090` on the LAN, and use a
  DDNS service if the office doesn't have a static IP. Do **not** do this
  without also putting a TLS-terminating reverse proxy in front of it (the
  Pico serves plain HTTP; the API key would otherwise cross the public
  internet unencrypted) — a small reverse-proxy box (nginx, Caddy) with a
  Let's Encrypt cert is enough.

Either way, store the resulting URL + API key as `PICO_PRINT_SERVER_URL` /
`PICO_PRINT_SERVER_API_KEY` in tda-app's Convex environment (see
[API.md](./API.md)'s example action).

## Reliability notes

- The board reconnects WiFi automatically if it drops (`wifi_watchdog` in
  `main.py`, checked every 5s).
- A hardware watchdog (`machine.WDT`, ~8s timeout) reboots the board if
  the event loop ever wedges — no one is going to be standing next to it
  to power-cycle it.
- Job history lives in RAM only (`firmware/printjobs.py`, last 50 jobs)
  and is lost on reboot/power loss. This device is a relay, not a queue —
  if you need jobs to survive a reboot or a printer that's temporarily
  offline, that's a gap to close as a follow-up (e.g. retry logic in the
  Convex action, or a persistent queue on the Pico's flash).
