# tda-app / Convex integration plan

This is a plan, not a diff — it describes what needs to be built in the
`tda-app` repo to complete the other half of this system. This session
only has read access to `tda-app`, so none of it has been implemented
there; this doc is meant to be handed to whoever (human or another Claude
session with write access) picks that up next.

## Why this shape

The Pico now *polls out* for work instead of being called into (see
[PROTOCOL.md](./PROTOCOL.md) for why, and the exact wire contract). That
means tda-app's job is to be a small queue the Pico can poll, not a
server that reaches into the office network. Since tda-app already runs
on Convex, that queue is just a table plus a few HTTP actions — no new
infrastructure, no separate service to host or pay for.

## 1. Schema

Add to `convex/schema.ts`:

```ts
printJobs: defineTable({
  printerId: v.string(),           // e.g. "ql810w" — matches a key in
                                    // the Pico's firmware/config.py PRINTERS
  title: v.string(),
  contentStorageId: v.id("_storage"), // the raster bytes, via Convex file storage
  bytes: v.number(),
  state: v.union(
    v.literal("pending"),
    v.literal("claimed"),
    v.literal("sent"),
    v.literal("error"),
  ),
  createdAt: v.number(),
  claimedAt: v.optional(v.number()),
  completedAt: v.optional(v.number()),
  error: v.optional(v.string()),
})
  .index("by_state_createdAt", ["state", "createdAt"]),
```

Store the raster bytes via Convex file storage (`ctx.storage.store(blob)`)
rather than inline in the document — keeps job documents small and lets
the content-serving httpAction stream straight from storage. See §3 for
why this also matters for keeping the Pico's content fetch same-origin
(no redirect).

## 2. Mutations (`convex/printJobs.ts`, new file)

- `enqueuePrintJob({ printerId, title, contentStorageId, bytes })` —
  called after the client has rendered + uploaded the raster bytes (see
  §4). Inserts a `state: "pending"` row.
- `claimNextPendingJob()` (internal mutation, called only from the
  httpAction in §3) — finds the oldest `state: "pending"` row via the
  `by_state_createdAt` index, flips it to `"claimed"` with
  `claimedAt: Date.now()`, returns it (or `null`). Must be a mutation
  (not just a query-then-patch in the httpAction) so the claim is
  atomic — Convex mutations are transactional, which is what actually
  prevents two poll responses from handing out the same job.
- `completeJob({ jobId, state, error })` (internal mutation) — patches
  `state`, `completedAt: Date.now()`, and `error` if given.
- Optional but recommended: `reclaimStalePendingJobs()`, run from a
  Convex cron every minute or so, that flips any `"claimed"` job whose
  `claimedAt` is older than ~2 minutes back to `"pending"`. Covers the
  case where the Pico claimed a job and then lost power/WiFi mid-print —
  without this, that job is stuck forever since nothing else will retry
  it. (Two minutes is a guess — tune against how long a real job actually
  takes to fetch + print, which `docs/BROTHER_PROTOCOL.md`'s "first
  physical test checklist" will tell you.)

## 3. HTTP actions (`convex/http.ts`)

Three routes, each checking `X-Api-Key` against `process.env.PICO_API_KEY`
before doing anything and returning `401` otherwise. This is now the
**only** gate on these endpoints — unlike the earlier design, nothing on
the Pico is ever reachable from outside, so these Convex routes are the
entire public attack surface. Check the key with a constant-time
comparison, not `===` — a plain string comparison on a secret is a
narrow but real timing side-channel:

```ts
import { timingSafeEqual } from "node:crypto";

function isAuthorized(req: Request): boolean {
  const provided = req.headers.get("x-api-key");
  const expected = process.env.PICO_API_KEY;
  if (!provided || !expected) return false;
  const a = Buffer.from(provided);
  const b = Buffer.from(expected);
  // lengths must match before timingSafeEqual will even compare
  return a.length === b.length && timingSafeEqual(a, b);
}
```

(`node:crypto` needs the `"use node"` runtime on the action. If keeping
these routes on Convex's default runtime is preferred instead, a hand-
rolled constant-time compare — XOR each byte, accumulate, compare the
accumulator to 0 at the end — works too; don't short-circuit on the
first mismatching byte.)

### Cost of polling, and why it backs off

Every poll to `GET /pico/pending-jobs` costs **2** Convex function calls,
not 1 — the HTTP action itself, plus the internal `claimNextPendingJob`
mutation it must call to touch the database (HTTP actions can't call
`ctx.db` directly; that split is a Convex platform constraint, not a
design choice here). At a flat 5s interval running 24/7, that's roughly:

```
(30 days × 86400s / 5s) × 2 calls ≈ 1,036,800 function calls/month
```

from idle polling alone, sharing the same project-wide quota as
everything else tda-app does. That's why `firmware/config.py` polls
adaptively rather than at a fixed rate: `POLL_ACTIVE_INTERVAL_S` (5s)
right after activity, backing off to `POLL_IDLE_INTERVAL_S` (45s, ~9x
fewer calls) after `POLL_IDLE_AFTER_MISSES` consecutive empty polls,
snapping back to the fast interval the moment a job appears. An actual
printed job only costs ~6 calls total (claim + content fetch + completion
report) — trivial next to the idle-polling volume, so tuning the backoff
is the lever that matters, not the job lifecycle itself. Nothing needed
on the Convex side for this; it's entirely a firmware-side behavior.

Exact request/response shapes are the source-of-truth contract in
[PROTOCOL.md](./PROTOCOL.md) — implement to match that, not this
summary:

- `GET /pico/pending-jobs` → calls `claimNextPendingJob`; `204` if null,
  else `200` with the small job-metadata JSON PROTOCOL.md describes.

  **Free win worth taking here**: the Pico already calls this route on
  every poll regardless of whether there's a job — that's a heartbeat you
  already have for free. Have this route also touch a
  `lastSeenAt: Date.now()` on a small `devices` (or even a single-row)
  table as a side effect of every poll, no matter whether it found a job.
  tda-app's UI can then show "print bridge: online / last seen Ns ago"
  without any new endpoint, firmware change, or polling from the client —
  it's a live Convex query away.

- If you ever add a second printer or a second Pico, note that
  `claimNextPendingJob` as described doesn't filter by which printer(s)
  the polling device actually knows about (`firmware/config.py`'s
  `PRINTERS`) — it just claims the oldest pending job, period. A single
  Pico handling a printer it doesn't have configured would claim a job
  it can't serve, fail immediately (`poller.py` raises on an unknown
  `printerId`), and that job would report `error` rather than being left
  for a Pico that could actually reach that printer. Not a problem at the
  current one-Pico/one-printer scale, but if that changes, have the poll
  request pass the device's known printer IDs (or a `deviceId` Convex can
  map to a printer allowlist) so claiming can filter accordingly.
- `GET /pico/jobs/:id/content` → looks up the job, streams
  `ctx.storage.get(job.contentStorageId)`'s bytes directly as the
  response body with `Content-Type: application/octet-stream`.
  **Deliberately not** `ctx.storage.getUrl()` + a redirect to Convex's
  underlying blob storage — the Pico's hand-rolled HTTP client
  (`firmware/httpclient.py`) doesn't follow redirects, by design, to keep
  a memory-constrained device's HTTP implementation small and easy to
  get right. Streaming the blob through this same-origin action avoids
  needing that entirely.
- `POST /pico/jobs/:id/complete` → parses `{state, error}`, calls
  `completeJob`.

Convex HTTP actions are served from your deployment's `.convex.site`
domain (not `.convex.cloud`, which is the client-SDK domain) — that's
the value that goes into the Pico's `config.py` as `CONVEX_BASE_URL`.

## 4. Where rasterization happens (the still-open piece)

This was already flagged as a gap in `docs/BROTHER_PROTOCOL.md` before
this rework, and it's still the one piece with no code anywhere yet:
something has to turn tda-app's existing label output
(`lib/pdf/generateSampleLabel.js`, currently vector PDF via jsPDF) into
Brother QL-810W raster bytes before `enqueuePrintJob` can be called.

Recommended approach: **render to a bitmap client-side, encode
server-side, in pure TypeScript.**

1. Client-side: render the label to an offscreen `<canvas>` at the
   QL-810W's native 300dpi for 62mm tape (jsPDF's draw calls can target a
   canvas context instead of a PDF document — `generateSampleLabel.js`'s
   `buildDoc()` logic would need a canvas-rendering variant, or the
   canvas could be produced by rasterizing the existing PDF output via a
   library like `pdf.js` if reusing the PDF path is preferred over a
   parallel canvas-drawing path).
2. Get 1-bit monochrome pixel data from the canvas (threshold the
   grayscale/alpha value per pixel).
3. Send that bitmap to a Convex action (`"use node"` if needed, though a
   pure-TS encoder likely doesn't need the Node runtime at all) that
   builds the actual Brother raster command stream: init, media/quality
   info, one raster-line command per row, print/cut command.
4. **Port `brother_ql`'s raster encoding logic
   (`brother_ql/raster.py` in that project) to TypeScript rather than
   re-deriving Brother's protocol independently.** It's short, it's
   MIT-licensed, and it's already correct for this exact printer model —
   the same reasoning that kept this repo from hand-rolling that protocol
   on the Pico applies here too. Don't invent the byte-level framing from
   scratch; port the reference implementation.
5. Upload the resulting bytes via `ctx.storage.store()`, get a
   `contentStorageId`, call `enqueuePrintJob`.

Alternative considered and not recommended: shelling out to the real
`brother_ql` Python CLI from a Node Convex action. Works locally (that's
literally what `tools/render_label.py` in this repo does for testing),
but depends on a Python runtime + the package being available in
whatever environment Convex actions run in, which is a fragile
deployment assumption for a serverless platform. Porting the encoder is
more work upfront but has no such runtime dependency.

## 5. UI wiring

Wherever `printSampleLabelViaBrowser` is currently called for the
in-office browser-print path (`lib/pdf/generateSampleLabel.js`), add a
parallel "Print to QL-810W" action that:
1. Renders + rasterizes the label (§4).
2. Calls a Convex action that uploads the bytes and calls
   `enqueuePrintJob`.
3. Shows live status via a Convex query on the job's `state` —
   Convex's reactivity means the UI can show "queued" → "sent" (or
   "error") without polling, as soon as the Pico's completion report
   lands. Given the Pico polls at `POLL_ACTIVE_INTERVAL_S` (5s default)
   and then reports back after printing, expect a few-second delay
   between clicking print and the label actually coming out — worth a
   small "printing…" state in the UI rather than expecting it instant.
   Worth noting: if the Pico had backed off to `POLL_IDLE_INTERVAL_S`
   (45s default) before this job was queued, the *first* job after a
   quiet stretch can take up to that long to be noticed, before it snaps
   back to the fast interval for anything queued right behind it — the
   UI's "queued" state covers this, but it's worth knowing the delay
   isn't always just a few seconds.

## 6. Environment / config

- Convex: `PICO_API_KEY` env var, matching the Pico's `config.py`
  `API_KEY`. Generate with `python3 -c "import secrets; print(secrets.token_hex(24))"`
  and set it in **both** places — nothing auto-syncs them.
- Pico: `CONVEX_BASE_URL` set to the deployment's `.convex.site` URL (see
  §3), plus the same `API_KEY`.

## 7. Testing plan

Build and test each side independently before wiring them together:

1. **Pico firmware, no tda-app needed yet**: run
   `tools/mock_convex_server.py serve`, queue a test job with
   `tools/mock_convex_server.py enqueue --file <raster-bytes>`, confirm
   the Pico picks it up and prints (or, with `tools/mock_printer.py`
   standing in for the QL-810W, confirm the bytes arrive intact). This
   validates the Pico side is solid against the *real* contract shape
   before Convex exists.
2. **Convex side against the real Pico**: once §1–§3 are implemented,
   point the Pico's `config.py` at the real `.convex.site` URL and use
   Convex's dashboard (or a manual `enqueuePrintJob` call from the
   dashboard's function runner) to queue a job without needing the UI
   wired up yet.
3. **End-to-end from the UI**: only after both sides are independently
   verified, wire up §5 and test the full click-to-print flow.

## Summary of new tda-app files/changes

| File | Change |
|---|---|
| `convex/schema.ts` | add `printJobs` table |
| `convex/printJobs.ts` | new — mutations for enqueue/claim/complete/reclaim |
| `convex/http.ts` | new (or extend) — the 3 httpAction routes |
| `convex/crons.ts` | add the stale-claim reclaim cron (optional but recommended) |
| a new raster encoder (TS port of `brother_ql`'s raster module) | new file, location TBD |
| `lib/pdf/generateSampleLabel.js` or a sibling module | add a canvas-rasterizing variant alongside the existing PDF path |
| wherever the sample-label print UI lives | new "Print to QL-810W" action + live status |
| Convex env | `PICO_API_KEY` |
