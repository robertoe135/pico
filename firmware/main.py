# main.py — wires up WiFi, the HTTP API, and the background housekeeping
# tasks (WiFi watchdog, status LED). This is the whole application; see
# docs/API.md for the routes it exposes.

import time
import machine
import uasyncio as asyncio

import config
import wifi
import printjobs
from status_led import StatusLed
from httpserver import HTTPServer, json_response

led = StatusLed()
app = HTTPServer()
START_TIME = time.time()


def _authorized(req):
    return req.headers.get("x-api-key") == config.API_KEY


@app.route("GET", "/health")
async def health(req):
    # No auth — this is meant to be pollable by uptime monitors without
    # needing the shared secret. Deliberately excludes LAN IP / RSSI: this
    # endpoint is the one thing meant to sit behind a public tunnel
    # unauthenticated, so it shouldn't hand out internal network details
    # to anyone who requests it. Authorized callers get that detail from
    # GET /printers or their own network tooling instead.
    body = {
        "status": "ok",
        "uptimeS": time.time() - START_TIME,
        "wifi": "connected" if wifi.is_connected() else "disconnected",
    }
    if _authorized(req):
        body["ip"] = wifi.ip()
        body["rssi"] = wifi.rssi()
    return json_response(body)


@app.route("GET", "/printers")
async def list_printers(req):
    if not _authorized(req):
        return json_response({"error": "unauthorized"}, 401)
    return json_response(
        [
            {"id": pid, "name": p["name"], "ip": p["ip"], "port": p["port"]}
            for pid, p in config.PRINTERS.items()
        ]
    )


@app.route("POST", "/printjobs")
async def create_job(req):
    if not _authorized(req):
        return json_response({"error": "unauthorized"}, 401)
    try:
        record = await printjobs.submit_job(config.PRINTERS, req.body)
        led.job_ok()
        return json_response(record, 201)
    except printjobs.JobError as e:
        led.job_failed()
        return json_response({"error": e.message}, e.status)
    except Exception as e:
        led.job_failed()
        return json_response({"error": str(e)}, 500)


@app.route("GET", "/printjobs/{id}")
async def get_job(req):
    if not _authorized(req):
        return json_response({"error": "unauthorized"}, 401)
    try:
        job_id = int(req.params["id"])
    except (KeyError, ValueError):
        return json_response({"error": "invalid job id"}, 400)
    record = printjobs.get_job(job_id)
    if record is None:
        return json_response({"error": "not found"}, 404)
    return json_response(record)


async def wifi_watchdog(wdt):
    """Runs for the life of the process: re-connects WiFi if it drops, and
    feeds the hardware watchdog so a wedged event loop reboots the board
    instead of leaving a dead device sitting next to the printer."""
    while True:
        if not wifi.is_connected():
            led.wifi_lost()
            try:
                wifi.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
                led.wifi_ready()
            except Exception:
                pass  # try again next tick
        if wdt is not None:
            wdt.feed()
        await asyncio.sleep(5)


async def main():
    led.wifi_connecting()
    wifi.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
    led.wifi_ready()

    wdt = None
    try:
        # RP2040's hardware watchdog caps out around ~8.3s; feed it well
        # under that from wifi_watchdog's 5s loop.
        wdt = machine.WDT(timeout=8000)
    except Exception:
        pass  # not available on every board/firmware build — non-fatal

    asyncio.create_task(wifi_watchdog(wdt))
    asyncio.create_task(led.run())
    await app.serve(config.SERVER_PORT)


asyncio.run(main())
