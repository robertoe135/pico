# wifi.py — station-mode WiFi connect/reconnect helper for the Pico W.
#
# Kept deliberately synchronous and blocking: it's only ever called from
# main.py's startup and from the periodic watchdog task (main.py awaits
# around it via asyncio, so it doesn't stall the HTTP server for more than
# one watchdog tick if the AP is briefly unreachable).

import network
import time

_wlan = None


def connect(ssid, password, timeout_s=20):
    """Connect to `ssid`, blocking up to `timeout_s`. Safe to call again on
    an already-connected interface (no-op)."""
    global _wlan
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(ssid, password)
        deadline = time.ticks_add(time.ticks_ms(), timeout_s * 1000)
        while not wlan.isconnected():
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                raise RuntimeError("WiFi connect timed out after %ds" % timeout_s)
            time.sleep_ms(200)
    _wlan = wlan
    return wlan


def is_connected():
    return _wlan is not None and _wlan.isconnected()


def ip():
    if _wlan is not None and _wlan.isconnected():
        return _wlan.ifconfig()[0]
    return None


def rssi():
    """Signal strength in dBm, or None if unavailable/unsupported."""
    if _wlan is None:
        return None
    try:
        return _wlan.status("rssi")
    except Exception:
        return None
