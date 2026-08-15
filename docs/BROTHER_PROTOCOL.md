# Brother QL-810W raster protocol — where it's handled, and why not here

The QL-810W prints from a proprietary Brother "raster mode" command
stream: a handful of setup commands (initialize, media/quality info,
raster-mode switch) followed by one raster-line command per row of the
label, terminated by a print/cut command. It's well documented (Brother
publish a "Raster Command Reference" for the QL-800 series, which covers
the 810W), but it has enough fiddly, printer-model-specific bit-flag and
framing detail that getting it subtly wrong produces a garbled or blank
label rather than a clean error — not something worth hand-rolling from
memory on a 264KB-RAM microcontroller with no printer attached to test
against during development.

## The split

- **This repo (the Pico server)** treats print job content as opaque
  bytes: decode base64, open a TCP socket to the printer on port 9100,
  write the bytes, done. See `firmware/printjobs.py`. This part is simple
  enough to be confident is correct by construction — there's no
  protocol-specific logic to get wrong.
- **Producing those bytes** — turning a label image/PDF into a correct
  Brother raster stream for the QL-810W at 62mm continuous tape — is
  delegated to [`brother_ql`](https://github.com/pklaus/brother_ql), a
  mature, MIT-licensed, actively maintained Python library built
  specifically for this printer family. Don't reimplement it; use it.

## Two ways to use it

**1. Dev/test tool in this repo** (`tools/render_label.py`): converts an
image file to a `.bin` of raw raster bytes using `brother_ql`, for testing
the Pico relay end-to-end before tda-app is wired up:

```bash
pip install brother_ql pillow
python3 tools/render_label.py label.png out.bin --label 62
python3 tools/send_test_job.py --host 192.168.1.60 --api-key $API_KEY \
    --printer ql810w --file out.bin
```

**2. tda-app integration (not part of this repo)**: tda-app already
renders labels client-side via jsPDF + canvas
(`lib/pdf/generateSampleLabel.js`). The missing piece there is a step that
takes that rendered label and produces Brother raster bytes before calling
this server's `POST /printjobs`. Two reasonable ways to do it, roughly in
order of how much new infrastructure they need:

- **A small Node/Python side-service.** Since Convex actions can run in a
  Node environment (`"use node"`), the cleanest option is a tiny helper —
  either a Node CLI wrapping a Python `brother_ql` subprocess, or (if
  avoiding a Python runtime dependency in production matters) a from-image
  raster encoder written directly in JS. `brother_ql`'s raster encoding
  logic (`brother_ql/raster.py` in that project) is short and readable
  enough to port faithfully if a pure-JS/Node path is preferred over
  shelling out to Python — but port *it*, rather than re-deriving the
  protocol independently, so the well-tested reference behavior carries
  over.
- **Render to a rasterized image, not a vector PDF, for this path.**
  `generateSampleLabelPDF`'s jsPDF output is vector; `brother_ql` (and any
  raster encoder) wants a bitmap. The simplest bridge is rendering the
  label to a canvas (jsPDF can already export to canvas / you can render
  the same draw calls to an offscreen `<canvas>`) at the QL-810W's native
  300dpi for 62mm tape, then feeding that bitmap through the encoder.

This repo doesn't include that bridge — it's tda-app-side work depending
on which of the above tda-app's maintainers prefer, and is a natural
follow-up task once this print server is deployed and reachable.

## First physical test checklist

1. `python3 tools/mock_printer.py` on your laptop, pointed at by a test
   `config.py` — confirms the Pico → relay → "printer" path works without
   risking real label stock.
2. Point `config.py` at the real QL-810W's LAN IP, send
   `tools/send_test_job.py --ping` (harmless `ESC @` init-only frame) —
   confirms the socket connection succeeds and the printer doesn't reject
   the connection outright.
3. Send a real `render_label.py`-produced job with a small, simple test
   image (e.g. a black square) before trying an actual label design —
   easier to diagnose "printed something, but offset/cut wrong" against a
   known-simple image than against a full sample-tag layout.
