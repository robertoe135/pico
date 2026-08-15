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

# How often (seconds) to ask Convex whether a job is pending. A few
# seconds of latency here doesn't matter for a label printer — nobody's
# timing a print job to the second. Lower = more responsive, more
# requests; the device polls again immediately (no wait) right after
# handling a job, so a burst of jobs doesn't queue up behind this delay.
POLL_INTERVAL_S = 5

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
