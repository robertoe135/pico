# main.py — connects WiFi, then polls Convex for print jobs forever.
#
# There is no inbound networking anywhere in this app: every network call
# this device makes is outbound (poll for a job, fetch its content, report
# completion), so nothing needs a port-forward, a tunnel, or a public IP —
# the office WiFi just needs normal outbound internet access, same as any
# other device on it. See docs/TDA_APP_INTEGRATION.md for the Convex side
# of this contract.

import time
import machine

import config
import wifi
import poller
from status_led import StatusLed

led = StatusLed()

# Hardware watchdog ceiling on RP2040 is ~8.3s. Nothing feeds it during a
# blocking connect/TLS-handshake phase, so httpclient.py's own connect
# timeout is kept under this — see CONNECT_TIMEOUT_S there. Once a
# transfer is underway, poller.py feeds the watchdog per chunk, so an
# individual job can safely take much longer than 8s end-to-end.
WDT_TIMEOUT_MS = 8000


def _poll_once(feed):
    job = poller.fetch_pending_job(config.CONVEX_BASE_URL, config.API_KEY, feed=feed)
    if job is None:
        return False  # nothing to do this tick

    led.job_started()
    try:
        poller.run_job(config.CONVEX_BASE_URL, config.API_KEY, job, config.PRINTERS, feed=feed)
    except Exception as e:
        led.job_failed()
        try:
            poller.report_complete(
                config.CONVEX_BASE_URL, config.API_KEY, job["id"], "error", error=e, feed=feed
            )
        except Exception:
            pass  # best-effort — the job stays "claimed" Convex-side and
            # can be reaped/retried there; see docs/TDA_APP_INTEGRATION.md
        return True

    led.job_ok()
    try:
        poller.report_complete(config.CONVEX_BASE_URL, config.API_KEY, job["id"], "sent", feed=feed)
    except Exception:
        pass  # the label printed either way — a lost status report just
        # means Convex won't know it succeeded until reconciled
    return True


def main():
    led.wifi_connecting()
    wifi.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
    led.wifi_ready()

    wdt = None
    try:
        wdt = machine.WDT(timeout=WDT_TIMEOUT_MS)
    except Exception:
        pass  # not available on every board/firmware build — non-fatal

    feed = wdt.feed if wdt is not None else (lambda: None)

    while True:
        feed()

        if not wifi.is_connected():
            led.wifi_lost()
            try:
                wifi.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
                led.wifi_ready()
            except Exception:
                time.sleep(config.POLL_INTERVAL_S)
                continue

        try:
            found_job = _poll_once(feed)
        except Exception:
            led.job_failed()
            found_job = False

        if not found_job:
            led.heartbeat()
            time.sleep(config.POLL_INTERVAL_S)
        # else: loop straight back around to check for another job
        # immediately, rather than waiting out the poll interval while
        # jobs are actively queued up


main()
