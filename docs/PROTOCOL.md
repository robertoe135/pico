# Protocol: Pico ↔ Convex

The Pico never listens for inbound requests — it only ever calls *out* to
Convex. This is the contract both sides implement: the Pico consumes it
(`firmware/poller.py`, `firmware/httpclient.py`), and tda-app's Convex
deployment must serve it (see
[TDA_APP_INTEGRATION.md](./TDA_APP_INTEGRATION.md) for the implementation
plan on that side — this doc is the wire format itself).

Base URL: your Convex deployment's HTTP-actions domain, e.g.
`https://happy-animal-123.convex.site` (note `.convex.site`, not
`.convex.cloud`).

All three endpoints require an `X-Api-Key` header matching the shared
secret configured on both sides (`API_KEY` in the Pico's `config.py`,
`PICO_API_KEY` in Convex's environment).

## `GET /pico/pending-jobs`

Polled by the Pico every `POLL_INTERVAL_S` (default 5s). Must **atomically**
claim the job it returns (flip it from `pending` to `claimed`
server-side, in the same transaction as reading it) — if two poll
responses can hand out the same job, it gets printed twice.

- Nothing pending → `204 No Content`, empty body.
- A job is available → `200`:
  ```json
  { "id": "j17", "printerId": "ql810w", "title": "Sample Label — TA / Project 1234", "bytes": 15234 }
  ```
  `id` can be any string/number the server can look up later (a Convex
  document ID is fine). `bytes` is informational only (not currently
  used by the Pico to validate the download, but useful for logging/UI).

## `GET /pico/jobs/{id}/content`

Returns the job's raw print-ready bytes (Brother QL raster-mode commands
for the QL-810W — see [BROTHER_PROTOCOL.md](./BROTHER_PROTOCOL.md) for
what "print-ready" means here) as the literal response body — not JSON,
not base64.

- `200`, `Content-Type: application/octet-stream`, with either
  `Content-Length` set or `Transfer-Encoding: chunked` — the Pico's
  client (`firmware/httpclient.py`) supports both.
- **Must not redirect.** The Pico's HTTP client deliberately doesn't
  follow redirects (kept out to keep a memory-constrained device's hand-
  rolled HTTP implementation small and easy to get right) — serve the
  bytes directly from this same-origin route rather than, e.g.,
  redirecting to a signed storage URL on a different host. See
  TDA_APP_INTEGRATION.md §3 for how this shapes the Convex implementation.
- `404` if the id is unknown.

The Pico streams this response straight into a TCP write to the
printer's socket as bytes arrive — it never buffers the whole job in RAM,
so there's no practical size ceiling from the Pico's side (the QL-810W's
own tape length is the real limit).

## `POST /pico/jobs/{id}/complete`

Sent once after `GET .../content` finishes (success or failure) — not
guaranteed delivery from the Pico's perspective (see below), so don't
treat a missing completion report as proof a job failed.

```json
{ "state": "sent" }
```
or
```json
{ "state": "error", "error": "failed to reach printer 192.168.1.50:9100: [Errno 116] ETIMEDOUT" }
```

- `200 {"ok": true}` expected; the Pico doesn't retry this call if it
  fails — a job the Pico successfully printed but couldn't report on
  stays `claimed` in Convex forever unless something reclaims it. That's
  what the recommended stale-claim reclaim cron in
  TDA_APP_INTEGRATION.md §2 is for: it'll eventually flip the job back to
  `pending` and it'll print a second time. For a label printer, an
  occasional duplicate print on a lost report is a much better failure
  mode than a job silently vanishing, so this repo doesn't try to do
  better than at-least-once delivery here.

## Auth model

A shared secret (`X-Api-Key`), same trust model as before, just flipped
in direction — the Pico now presents it to Convex rather than checking it
on inbound requests.

This is a meaningfully smaller attack surface than the earlier inbound-
server design: there's no port, tunnel, or hostname for anything to probe
on the Pico's side at all — nothing on the Pico is ever reachable from
outside. The trade-off is that this key is now the **only** gate on
Convex's three HTTP action routes, which are themselves public URLs the
moment they're deployed. Two things that follow from that:

- Generate the key with real entropy
  (`python3 -c "import secrets; print(secrets.token_hex(24))"`), and
  treat it as a real credential — set it directly in each place it's
  needed (the Pico's local `config.py`, Convex's `PICO_API_KEY` env var)
  rather than pasting it through a channel that keeps a permanent record.
- Check it with a constant-time comparison on the Convex side, not `===`
  — see TDA_APP_INTEGRATION.md §3 for the specific approach.

The remaining residual risk is TLS server-certificate verification on the
Pico's `ssl.wrap_socket()` call, which depends on the MicroPython build's
mbedtls configuration — see the note in `docs/DEPLOYMENT.md` if you want
to harden that further.
