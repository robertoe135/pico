# status_led.py — onboard LED heartbeat so a headless Pico W (no screen,
# probably tucked next to the printer) still tells you what it's doing at
# a glance:
#
#   fast blink (150ms)  — connecting to WiFi
#   slow blip  (~3s)     — idle / ready, WiFi connected
#   slow blink (500ms)  — WiFi lost, attempting to reconnect
#   short burst          — a print job just finished (2 blinks = sent ok,
#                           6 blinks = failed) — layers briefly on top of
#                           whichever heartbeat is running, then resumes it
#
# On the Pico W the onboard LED is wired to the wifi chip (CYW43), reached
# via machine.Pin("LED", ...) rather than a numbered GPIO — that only
# works on the *W* board. Wrapped in try/except so this module doesn't
# crash the whole server on a plain Pico or under a desktop simulator.

import machine
import uasyncio as asyncio

STATE_CONNECTING = "connecting"
STATE_IDLE = "idle"
STATE_ERROR = "error"


class StatusLed:
    def __init__(self):
        try:
            self._pin = machine.Pin("LED", machine.Pin.OUT)
        except Exception:
            self._pin = None
        self._state = STATE_CONNECTING

    def _set(self, value):
        if self._pin is not None:
            self._pin.value(value)

    def wifi_connecting(self):
        self._state = STATE_CONNECTING

    def wifi_ready(self):
        self._state = STATE_IDLE

    def wifi_lost(self):
        self._state = STATE_ERROR

    def job_ok(self):
        asyncio.create_task(self._burst(2))

    def job_failed(self):
        asyncio.create_task(self._burst(6))

    async def _burst(self, n):
        for _ in range(n):
            self._set(1)
            await asyncio.sleep_ms(80)
            self._set(0)
            await asyncio.sleep_ms(80)

    async def run(self):
        """Background heartbeat task — create_task this once from main()."""
        while True:
            if self._state == STATE_CONNECTING:
                self._set(1)
                await asyncio.sleep_ms(150)
                self._set(0)
                await asyncio.sleep_ms(150)
            elif self._state == STATE_ERROR:
                self._set(1)
                await asyncio.sleep_ms(500)
                self._set(0)
                await asyncio.sleep_ms(500)
            else:
                self._set(1)
                await asyncio.sleep_ms(50)
                self._set(0)
                await asyncio.sleep_ms(2950)
