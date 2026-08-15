# status_led.py — onboard LED patterns for a headless device sitting next
# to the printer. Fully synchronous/blocking (a few hundred ms at most)
# rather than backgrounded on an event loop: main.py's poll loop is
# synchronous too now that there's no inbound server to keep responsive,
# so there's no event loop for a background blink task to share.
#
# On the Pico W the onboard LED is wired to the wifi chip (CYW43), reached
# via machine.Pin("LED", ...) rather than a numbered GPIO — that only
# works on the *W* board. Wrapped in try/except so this module doesn't
# crash the whole app on a plain Pico or under a desktop simulator.

import machine
import time


class StatusLed:
    def __init__(self):
        try:
            self._pin = machine.Pin("LED", machine.Pin.OUT)
        except Exception:
            self._pin = None

    def _set(self, value):
        if self._pin is not None:
            self._pin.value(value)

    def _blink(self, n, on_ms=80, off_ms=80):
        for _ in range(n):
            self._set(1)
            time.sleep_ms(on_ms)
            self._set(0)
            time.sleep_ms(off_ms)

    def wifi_connecting(self):
        self._blink(1, on_ms=100, off_ms=100)

    def wifi_ready(self):
        self._blink(1, on_ms=30, off_ms=30)

    def wifi_lost(self):
        self._blink(3, on_ms=400, off_ms=200)

    def heartbeat(self):
        """Call once per idle poll tick — a brief blip every
        POLL_INTERVAL_S seconds reads as a slow "I'm alive" pulse over
        time without needing a background task."""
        self._set(1)
        time.sleep_ms(30)
        self._set(0)

    def job_started(self):
        self._set(1)  # stays lit while a job is in flight

    def job_ok(self):
        self._set(0)
        self._blink(2)

    def job_failed(self):
        self._set(0)
        self._blink(6)
