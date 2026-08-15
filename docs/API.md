# API reference

The Pico exposes a small JSON/HTTP API, shaped loosely like PrintNode's job
API, so tda-app's integration is a drop-in against a self-hosted relay
instead of a bespoke protocol.

Base URL: `http://<pico-ip-or-hostname>:<SERVER_PORT>` (default port
`8090`, set in `firmware/config.py`).

All endpoints except `GET /health` require an `X-Api-Key` header matching
`API_KEY` in `firmware/config.py`.

## `GET /health`

No auth required — meant to be pollable by uptime monitors, including
externally if you put this server behind a public tunnel. Deliberately
omits the Pico's LAN IP/RSSI when called unauthenticated, since this is
the one endpoint expected to sit on an open public hostname.

```
curl http://192.168.1.60:8090/health
```

```json
{
  "status": "ok",
  "uptimeS": 8123,
  "wifi": "connected"
}
```

With a valid `X-Api-Key`, the same endpoint also includes `ip`/`rssi`:

```json
{
  "status": "ok",
  "uptimeS": 8123,
  "wifi": "connected",
  "ip": "192.168.1.60",
  "rssi": -54
}
```

## `GET /printers`

Lists the printers configured in `firmware/config.py`.

```
curl -H "X-Api-Key: $API_KEY" http://192.168.1.60:8090/printers
```

```json
[
  { "id": "ql810w", "name": "Brother QL-810W", "ip": "192.168.1.50", "port": 9100 }
]
```

## `POST /printjobs`

Submits a print job. The Pico does **not** parse or rasterize the
content — `content` must already be complete, printer-ready bytes (Brother
QL raster-mode commands), base64-encoded. See
[BROTHER_PROTOCOL.md](./BROTHER_PROTOCOL.md) for how to produce that from
a label image or PDF.

Request body:

```json
{
  "printerId": "ql810w",
  "title": "Sample Label — TA / Project 1234",
  "contentType": "raw_base64",
  "content": "G0BbaQBB...<base64>...="
}
```

| field         | required | notes                                                        |
|---------------|----------|---------------------------------------------------------------|
| `printerId`   | yes      | must match a key in `firmware/config.py`'s `PRINTERS`        |
| `content`     | yes      | base64-encoded raw print bytes                               |
| `contentType` | no       | only `"raw_base64"` is supported; defaults to it if omitted   |
| `title`       | no       | free text, echoed back in job status, not sent to the printer |

Response — `201 Created`:

```json
{
  "id": 7,
  "printerId": "ql810w",
  "title": "Sample Label — TA / Project 1234",
  "bytes": 15234,
  "state": "sent",
  "createdAt": 1755292345,
  "error": null
}
```

Errors:

| status | when                                                        |
|--------|-------------------------------------------------------------|
| 400    | malformed JSON, missing/invalid `content`, unsupported `contentType` |
| 401    | missing/incorrect `X-Api-Key`                                |
| 404    | `printerId` not found                                        |
| 502    | couldn't reach the printer over TCP (wrong IP, printer off, out of tape/label errors reported by socket-level failure, etc.) |

A `502` means the bytes never made it to the printer — safe to retry once
the underlying problem (printer off, wrong IP, etc.) is fixed. A `201`
means the bytes were successfully written to the printer's socket; it does
**not** guarantee the label physically printed cleanly (out-of-tape /
paper-jam errors surface at the printer, not over the socket) — if you
need that confirmation, add printer status polling as a follow-up (the
QL-810W does support a status-request command; not implemented here).

## `GET /printjobs/{id}`

Look up a previously submitted job's status. History is kept in RAM only
(last 50 jobs) and is lost on reboot — this is a relay, not a database.

```
curl -H "X-Api-Key: $API_KEY" http://192.168.1.60:8090/printjobs/7
```

```json
{
  "id": 7,
  "printerId": "ql810w",
  "title": "Sample Label — TA / Project 1234",
  "bytes": 15234,
  "state": "sent",
  "createdAt": 1755292345,
  "error": null
}
```

`state` is one of `sending`, `sent`, `error`.

> `createdAt`/`uptimeS` come from the Pico's onboard clock, which starts
> counting from boot and is **not** wall-clock-synced unless you add NTP
> sync (not configured by default — the RP2040 has no battery-backed RTC).
> Fine for relative ordering/uptime; don't treat `createdAt` as a real
> timestamp without adding that.

## Example: calling from tda-app (Convex action)

Convex actions run server-side (Node), so this is a normal outbound
`fetch` — no CORS involved (the server also sends permissive CORS headers
regardless, in case you ever call it from a browser-side admin tool).

```ts
"use node";
import { action } from "./_generated/server";
import { v } from "convex/values";

export const printSampleLabel = action({
  args: { printerId: v.string(), title: v.string(), rasterBytesBase64: v.string() },
  handler: async (ctx, args) => {
    const res = await fetch(`${process.env.PICO_PRINT_SERVER_URL}/printjobs`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Api-Key": process.env.PICO_PRINT_SERVER_API_KEY!,
      },
      body: JSON.stringify({
        printerId: args.printerId,
        title: args.title,
        contentType: "raw_base64",
        content: args.rasterBytesBase64,
      }),
    });
    if (!res.ok) {
      throw new Error(`Print job failed: ${res.status} ${await res.text()}`);
    }
    return await res.json();
  },
});
```

The `rasterBytesBase64` argument is the missing piece on the tda-app side:
something needs to turn the existing `generateSampleLabelPDF`/jsPDF output
into Brother raster bytes before this call. See
[BROTHER_PROTOCOL.md](./BROTHER_PROTOCOL.md) for the recommended approach.
