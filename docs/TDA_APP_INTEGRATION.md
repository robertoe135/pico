# tda-app / Convex integration

**Implemented** on branch `claude/pico-print-server-integration` in the
`tda-app` repo (not merged, no PR opened — this session had push access
but wasn't asked to open one). This doc now describes what's actually
there, not a forward-looking plan; see §7 for exactly what was and wasn't
verified before handing it off, and what a human still needs to do
(deploy, set env vars, physically test).

## Why this shape

The Pico *polls out* for work instead of being called into (see
[PROTOCOL.md](./PROTOCOL.md) for why, and the exact wire contract). That
makes tda-app's job a small queue the Pico can poll, not a server that
reaches into the office network. Since tda-app already runs on Convex,
that queue is just a table plus a few HTTP actions — no new
infrastructure, no separate service to host or pay for.

## 1. Schema (`convex/schema.ts`)

```ts
printJobs: defineTable({
  printerId: v.string(),
  title: v.string(),
  contentStorageId: v.id("_storage"),
  bytes: v.number(),
  state: v.union(v.literal("pending"), v.literal("claimed"), v.literal("sent"), v.literal("error")),
  createdBy: v.id("users"),
  createdAt: v.number(),
  claimedAt: v.optional(v.number()),
  completedAt: v.optional(v.number()),
  error: v.optional(v.string()),
})
  .index("by_state_createdAt", ["state", "createdAt"])
  .index("by_state_claimedAt", ["state", "claimedAt"]),

// Free heartbeat (see §3) — singleton row, same pattern as appSettings.
printBridgeStatus: defineTable({
  key: v.literal("pico"),
  lastSeenAt: v.number(),
})
  .index("by_key", ["key"]),
```

The second index (`by_state_claimedAt`) exists for the stale-claim reclaim
cron (§2) to efficiently find long-`claimed` jobs.

## 2. Mutations (`convex/printJobs.ts`)

Public (user-authenticated, via `requireUser` — same as the rest of this
codebase's mutations):
- `generateUploadUrl()` — step 1 of the two-step Convex upload pattern
  already used elsewhere in this codebase (e.g. `convex/app.ts`'s logo
  upload).
- `enqueuePrintJob({ printerId, title, contentStorageId, bytes })` —
  inserts a `state: "pending"` row.
- `getJobStatus({ jobId })` — query, drives the UI's live "queued → sent/
  error" state.
- `getBridgeStatus()` — query, exposes `printBridgeStatus`'s `lastSeenAt`.

Internal (only reachable from `convex/http.ts`'s Pico-facing routes — not
callable from the tda-app client at all, since the Pico has no Convex user
identity):
- `touchBridgeHeartbeat()` — upserts `printBridgeStatus`, called on every
  poll regardless of outcome.
- `claimNextPendingJob()` — atomically finds the oldest `pending` row and
  flips it to `claimed`. This has to be a mutation, not a query the http
  action separately patches — Convex mutations are transactional, which is
  what actually prevents two poll responses from handing out the same job.
- `getJobContent({ jobId })` — looks up which `contentStorageId` a job
  points at (the content-serving http route needs this before it can
  stream the blob — http actions can't call `ctx.db` directly, only
  `ctx.storage`, so this lookup has to go through a query).
- `completeJob({ jobId, state, error })`.
- `reclaimStalePendingJobs()` — flips any `claimed` job whose `claimedAt`
  is older than 2 minutes (`STALE_CLAIM_MS`) back to `pending`. Wired to
  `convex/crons.ts` via `crons.interval("reclaim stale print jobs", { minutes: 2 }, internal.printJobs.reclaimStalePendingJobs)`.
  Two minutes is a guess — tune it against how long a real job actually
  takes once the first physical test (`docs/BROTHER_PROTOCOL.md`) has run.

## 3. HTTP actions (`convex/http.ts`)

Three routes, matching [PROTOCOL.md](./PROTOCOL.md) exactly:

- `GET /pico/pending-jobs` — touches the heartbeat, then calls
  `claimNextPendingJob`; `204` if nothing pending, else `200` with the job
  metadata.
- `GET /pico/jobs/{id}/content` — streams `ctx.storage.get(...)`'s bytes
  directly as the response body, same-origin (deliberately not
  `ctx.storage.getUrl()` + a redirect — `firmware/httpclient.py` on the
  Pico doesn't follow redirects, by design).
- `POST /pico/jobs/{id}/complete` — records the outcome.

**One thing worth flagging explicitly, since it's a real gotcha**: the
original version of this doc showed these as `/pico/jobs/:id/content`,
implying Express-style path-parameter routing. Convex's `httpRouter`
doesn't support that — it only matches an exact `path` or a `pathPrefix`
(which must end in `/`). The actual implementation registers
`pathPrefix: "/pico/jobs/"` and parses the id out of `url.pathname` with a
regex (`/^\/pico\/jobs\/([^/]+)\/content$/`) inside the handler. If you're
extending this later, that's the pattern to follow — the wire contract
(the URL shape the Pico builds) didn't change, only how Convex has to be
told to match it.

**Auth**: `X-Api-Key` checked with a constant-time comparison —
implemented as a **manual XOR-accumulate compare using only
`TextEncoder`**, not `node:crypto`'s `timingSafeEqual`/`Buffer`. Those
need Convex's `"use node"` runtime, which is a file-level directive that
would have applied to every other route already in `http.ts` too — a
bigger blast radius on an already-working file than one auth check
justifies. The manual version needs no runtime change:

```ts
function timingSafeEqualStrings(a: string, b: string): boolean {
  const aBytes = new TextEncoder().encode(a);
  const bBytes = new TextEncoder().encode(b);
  if (aBytes.length !== bBytes.length) return false;
  let diff = 0;
  for (let i = 0; i < aBytes.length; i++) diff |= aBytes[i] ^ bBytes[i];
  return diff === 0;
}
```

### Cost of polling, and why it backs off

Unchanged from the original plan — still accurate. Every poll costs 2
Convex function calls (the HTTP action + the internal mutation, since HTTP
actions can't touch `ctx.db` directly — a Convex platform constraint, not
a design choice). At a flat 5s interval 24/7 that's ~1,036,800 calls/month
from idle polling alone. `firmware/config.py`'s adaptive backoff
(`POLL_ACTIVE_INTERVAL_S` 5s / `POLL_IDLE_INTERVAL_S` 45s after
`POLL_IDLE_AFTER_MISSES` consecutive empty polls) cuts that by roughly
85-90% for realistic usage. Nothing needed on the Convex side for this —
it's entirely firmware-side behavior, already shipped.

### Free heartbeat — implemented

`printBridgeStatus`/`touchBridgeHeartbeat` (§1, §2) are live: every poll
updates `lastSeenAt` regardless of outcome. `getBridgeStatus()` exposes it
for the UI — not yet wired into any screen (no "print bridge: online"
indicator built), but the data's there for whoever adds one.

### Multi-printer/multi-device note — still just a note

`claimNextPendingJob` still doesn't filter by which printer(s) a polling
device knows about. Not a problem at today's one-Pico/one-printer scale;
see the original reasoning if this ever changes — unchanged from before.

## 4. Rasterization — implemented, entirely client-side

The original plan suggested "render client-side, encode server-side (a
Convex action)." **The actual implementation does both client-side**,
which turned out to be strictly simpler once the encoder existed: since
neither step needs anything server-only, round-tripping the (larger,
uncompressed) bitmap through a Convex action first would have been extra
network overhead for no benefit — only the final, already-encoded bytes
get uploaded.

- **`lib/print/brotherRaster.ts`** — the Brother QL-810W raster-mode
  encoder, fixed to this office's one configuration (62mm continuous tape,
  no compression, no red, no 600dpi, one label per job). This is a
  faithful, byte-for-byte port of the relevant parts of
  [`pklaus/brother_ql`](https://github.com/pklaus/brother_ql) (MIT) —
  `raster.py`'s command builders and `conversion.py`'s assembly order —
  fetched directly from GitHub and ported line-by-line rather than
  re-derived from protocol docs or memory. Verified with unit tests
  (26 assertions, run via `node --experimental-strip-types`) checking the
  exact byte sequence against brother_ql's own source, including a
  byte-exact match to a real captured payload documented in that
  project's own code comment (`8E 0A 3E 00 D2...`). Also caught and fixed
  the fiddliest detail by testing rather than assuming: the encoder pads
  a 696px-wide bitmap into the printer's full 720px raster line at an
  x-offset (12px) that isn't byte-aligned, requiring real bit-level
  shifting, and the whole padded row gets mirrored left-right before
  transmission (a real brother_ql behavior, not something to "fix").
- **`lib/print/rasterizeLabel.ts`** — turns the *existing* PDF output
  (`generateSampleLabelPDF`'s `returnBlob: true` path — already shipped,
  untouched) into the packed 1bpp bitmap the encoder expects, via
  `pdf.js`. Reuses the exact dynamic-import + CDN-worker pattern already
  established in `lib/parseContract.js` / `lib/pdf/generateCutSheetAppendix.js`,
  rather than inventing a second way to load pdf.js in this codebase.
  Deliberately does **not** touch `generateSampleLabel.js`'s draw calls at
  all — that file is a carefully-tuned, comment-dense layout function, and
  routing through its existing PDF output instead of adding a parallel
  canvas-drawing path means zero risk to what already works.

**A real, documented assumption that needs physical verification**: the
label's PDF is a 172.8pt (2.4in) square, but the QL-810W's printable width
on 62mm tape is fixed at 696px @ true 300dpi (2.32in) — that number comes
from the printer/tape (`brother_ql`'s own label constants), not from us.
Rather than distort the square design's aspect ratio to hit exactly 2.4in
on one axis, `rasterizeLabel.ts` uniformly scales both dimensions to fit
696px — the printed label comes out slightly smaller (~3.3%, ~2.32in
square) than the PDF's nominal size, but stays square and proportionally
correct. Confirming whether that's actually the right call — versus e.g.
adjusting the label's tape stock or the PDF's own nominal size — needs a
real test print; it's not resolvable from first principles. See
`docs/BROTHER_PROTOCOL.md`'s first physical test checklist, and treat the
first real print as a check on this specific assumption, not just on the
protocol bytes.

## 5. UI wiring — implemented in all 3 places the existing print actions appear

`printSampleLabelToPico({ sample, fixture, project, manufacturer, branding, printerId, generateUploadUrl, enqueuePrintJob })`
in `components/samples/SampleWidgets.jsx`, alongside the existing
`printSampleLabel`/`downloadSampleLabel`. Pipeline: generate the PDF
(`returnBlob: true`) → rasterize → encode → upload via the two-step
Convex storage pattern → `enqueuePrintJob`. Takes the mutate functions as
parameters rather than calling `useMutation` itself, since it's a plain
async helper, not a hook.

Wired into all three places the existing "🏷 Print Tag" / "Download PDF"
buttons already appear (found by grepping for those two function names,
not guessed at):
- `components/modules/SamplesModule.jsx` — project Samples tab.
- `components/modules/FixtureEditor.jsx` — per-fixture Samples tab.
- `app/samples/[id]/SampleDetailClient.tsx` — the standalone scan-a-QR-tag
  mobile page (also a legitimate use case: re-printing a lost tag from
  the field).

Each adds a "📡 Print to Office" button plus a live status line driven by
`getJobStatus` (Convex reactivity — no client-side polling). Expect a
few-second delay between clicking and the label coming out
(`POLL_ACTIVE_INTERVAL_S`, 5s default); if the Pico had backed off to
`POLL_IDLE_INTERVAL_S` (45s) before the job was queued, the first job
after a quiet stretch can take up to that long — the "Queued…" state
covers this but it's worth knowing the delay isn't always just a few
seconds.

## 6. Environment / config — code is ready, values are not set

- Convex: needs `PICO_API_KEY` set in the deployment's environment
  (`npx convex env set PICO_API_KEY <value>`, or via the dashboard) —
  **not done**, no live Convex deployment was available to this session.
- Pico: `firmware/config.py`'s `CONVEX_BASE_URL` needs to point at the
  real deployment's `.convex.site` URL, and `API_KEY` needs to match
  whatever gets set above.
- Generate the shared value with `python3 -c "import secrets; print(secrets.token_hex(24))"` —
  see the earlier conversation's note on generating it yourself rather
  than through an AI chat transcript, since it's a live credential.

## 7. What was actually verified, and what a human still needs to do

**Verified in this session** (no live Convex deployment or physical
printer was available, so this is as far as static verification goes):
- `npx tsc --noEmit` across the whole project: **0 errors**, both before
  and after every change described above.
- `npx next build`: compiled successfully, webpack resolved every new
  file and dynamic import cleanly; the build's only failure was missing
  `WORKOS_CLIENT_ID` during static page collection for an unrelated auth
  route — expected in a sandbox with no real secrets, unrelated to these
  changes.
- `lib/print/brotherRaster.ts`: 26 unit-test assertions (byte-exact
  structural checks against the real ported source, including matching a
  real captured payload).
- `convex/_generated/api.d.ts` was hand-updated to register the new
  `printJobs` module (Convex normally generates this via `npx convex
  dev`, which needs a live deployment this session didn't have) — it
  matches the pattern Convex's codegen produces, but **will be silently
  regenerated** the first time someone runs `npx convex dev`/`deploy`
  against the real deployment. That's expected and fine; the hand-edit
  only exists so `tsc` had something correct to check against in the
  meantime.
- ESLint could not be run — `eslint.config.mjs` uses ESLint 9's
  flat-config API (`eslint/config`) but `package.json` pins ESLint 8.57.1;
  confirmed via `git diff` on `package-lock.json` that this mismatch
  pre-dates these changes, not something introduced here. React
  hooks-rule compliance (all `useState`/`useMutation`/`useQuery` calls
  unconditional, before any early return) was checked by hand instead.

**Still needs a human** (or a follow-up session with deployment access):
1. Deploy (`npx convex dev` or `deploy`) — this also regenerates
   `_generated/` for real and will surface anything the hand-edit missed.
2. Set `PICO_API_KEY` in Convex's environment (§6).
3. Flash/configure a real Pico W (`docs/DEPLOYMENT.md` in this repo),
   pointed at the real `.convex.site` URL.
4. Run the first physical test print (`docs/BROTHER_PROTOCOL.md`) —
   this is the one thing that actually confirms the raster encoding and
   the label-size assumption (§4) are correct; nothing above tests
   against real hardware.
5. Test the full click-to-print flow from each of the 3 UI locations.

## Summary of tda-app files changed

| File | Change |
|---|---|
| `convex/schema.ts` | `printJobs` + `printBridgeStatus` tables |
| `convex/printJobs.ts` | new — all mutations/queries in §2 |
| `convex/http.ts` | 3 new Pico-facing routes + constant-time auth check |
| `convex/crons.ts` | stale-claim reclaim, every 2 minutes |
| `convex/_generated/api.d.ts` | hand-updated pending a real `npx convex dev` |
| `lib/print/brotherRaster.ts` | new — the ported, unit-tested Brother raster encoder |
| `lib/print/rasterizeLabel.ts` | new — PDF → 1bpp bitmap via pdf.js |
| `components/samples/SampleWidgets.jsx` | new `printSampleLabelToPico` |
| `components/modules/SamplesModule.jsx` | "📡 Print to Office" button + status |
| `components/modules/FixtureEditor.jsx` | same, in the fixture editor's Samples tab |
| `app/samples/[id]/SampleDetailClient.tsx` | same, on the scan-to-view mobile page |
