# Copy this file to config.py and fill in your real values.
#
#   cp config.example.py config.py
#
# config.py is gitignored — never commit real WiFi credentials or API keys.

WIFI_SSID = "YourOfficeWiFi"
WIFI_PASSWORD = "supersecret"

# Shared secret tda-app (or any other caller) must send as the `X-Api-Key`
# header on every request except GET /health. Generate something long and
# random, e.g.:
#   python3 -c "import secrets; print(secrets.token_hex(24))"
API_KEY = "change-me"

# Port this server listens on for incoming requests. This is what you'll
# forward through the office router (or tunnel via Tailscale/Cloudflare
# Tunnel — see docs/DEPLOYMENT.md) to reach the Pico from outside the LAN.
SERVER_PORT = 8090

# Printers this server knows about, keyed by an id you choose (used in the
# `printerId` field of a /printjobs request). "ip" is the Brother QL-810W's
# LAN address — set a DHCP reservation for it on your router so it never
# changes and this file doesn't need updating.
#
# The QL-810W accepts raw Brother raster print jobs on TCP port 9100
# (standard "JetDirect-style" raw socket printing), which is what this
# server relays to — see docs/BROTHER_PROTOCOL.md for how job content is
# expected to be encoded before it reaches this server.
PRINTERS = {
    "ql810w": {
        "name": "Brother QL-810W",
        "ip": "192.168.1.50",
        "port": 9100,
    },
}
