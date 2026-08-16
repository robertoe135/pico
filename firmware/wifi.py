# wifi.py — station-mode WiFi connect/reconnect helper for the Pico W.
#
# Kept deliberately synchronous and blocking: it's called from main.py's
# startup and from its poll loop. The loop call happens with the hardware
# watchdog (~8s ceiling) already armed, and this function's own wait can
# take up to `timeout_s` (default 20s) — well past that ceiling — so
# callers running under the watchdog MUST pass `feed`, or a slow-to-
# reconnect AP reboots the device mid-reconnect instead of cleanly
# retrying next tick.

import network
import time

_wlan = None


def connect(ssid, password, timeout_s=20, feed=None):
    """Connect to `ssid`, blocking up to `timeout_s`. Safe to call again on
    an already-connected interface (no-op). Calls `feed()` (if given) on
    every poll of the wait loop — pass the watchdog's feed function when
    calling this with a watchdog armed."""
    global _wlan
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(ssid, password)
        deadline = time.ticks_add(time.ticks_ms(), timeout_s * 1000)
        while not wlan.isconnected():
            if feed:
                feed()
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
