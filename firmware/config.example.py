# Copy this file to config.py and fill in your real values.
#
#   cp config.example.py config.py
#
# config.py is gitignored — never commit real WiFi credentials or API keys.

WIFI_SSID = "YourOfficeWiFi"
WIFI_PASSWORD = "supersecret"

# Base URL of your Convex deployment's HTTP actions, e.g.
# "https://happy-animal-123.convex.site" — note the .convex.site domain,
# NOT .convex.cloud (that's the client-SDK domain; HTTP actions are
# served separately). Find it on the Convex dashboard, or in tda-app's
# deployment settings. See docs/TDA_APP_INTEGRATION.md for what needs to
# exist at this URL.
CONVEX_BASE_URL = "https://your-deployment.convex.site"

# Shared secret sent as the `X-Api-Key` header on every request to
# Convex. Must match the PICO_API_KEY environment variable set in the
# Convex deployment (see docs/TDA_APP_INTEGRATION.md). Generate one with:
#   python3 -c "import secrets; print(secrets.token_hex(24))"
API_KEY = "change-me"

# Polling cadence. Every poll costs Convex function calls (an HTTP
# action + the internal mutation it runs to atomically claim a job —
# Convex requires that split, HTTP actions can't touch the database
# directly), whether or not anything's actually pending. Polling flat-out
# at POLL_ACTIVE_INTERVAL_S 24/7 adds up fast — see
# docs/TDA_APP_INTEGRATION.md's note on this — so this backs off to
# POLL_IDLE_INTERVAL_S once nothing's shown up for a while, and snaps
# back to the fast interval the moment a job appears (or right after
# handling one, in case another is queued right behind it).
POLL_ACTIVE_INTERVAL_S = 5  # cadence right after activity — a few
# seconds of latency doesn't matter for a label printer, but this is the
# rate a burst of jobs gets drained at, so keep it reasonably brisk.
POLL_IDLE_INTERVAL_S = 45  # cadence once idle — cuts function-call
# volume by ~9x during idle stretches, which for an office label printer
# is the overwhelming majority of the time.
POLL_IDLE_AFTER_MISSES = 6  # consecutive empty polls at the active
# interval (~30s by default) before backing off to the idle interval —
# a short grace period so a second job queued moments after the first
# still gets picked up fast.

# Printers this device can send jobs to, keyed by the `printerId` a job
# names. "ip" is the Brother QL-810W's LAN address — set a DHCP
# reservation for it on your router so it never changes and this file
# doesn't need updating. The QL-810W accepts raw Brother raster print
# jobs on TCP port 9100.
PRINTERS = {
    "ql810w": {
        "name": "Brother QL-810W",
        "ip": "192.168.1.50",
        "port": 9100,
    },
}
