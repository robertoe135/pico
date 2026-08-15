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

- **This repo (the Pico)** treats print job content as opaque bytes:
  stream them straight from the Convex fetch into a TCP socket to the
  printer on port 9100, chunk by chunk. See `firmware/poller.py` and
  `firmware/printjobs.py`. This part is simple enough to be confident is
  correct by construction — there's no protocol-specific logic to get
  wrong.
- **Producing those bytes** — turning a label image/PDF into a correct
  Brother raster stream for the QL-810W at 62mm continuous tape — is
  delegated to [`brother_ql`](https://github.com/pklaus/brother_ql), a
  mature, MIT-licensed, actively maintained Python library built
  specifically for this printer family. Don't reimplement it; use it (or
  port its encoder — see below).

## Two ways to use it

**1. Dev/test tool in this repo** (`tools/render_label.py`): converts an
image file to a `.bin` of raw raster bytes using `brother_ql`, for
testing the poll/print pipeline end-to-end before tda-app is wired up:

```bash
pip install brother_ql pillow
python3 tools/render_label.py label.png out.bin --label 62
python3 tools/mock_convex_server.py enqueue --file out.bin --printer ql810w
```

**2. tda-app integration**: see
[TDA_APP_INTEGRATION.md](./TDA_APP_INTEGRATION.md) for the full plan —
short version, tda-app's existing jsPDF-based label rendering needs a
rasterized (not vector) path, and the resulting bitmap needs a Brother
raster encoder, recommended as a TypeScript port of `brother_ql`'s
encoder rather than shelling out to Python from a Convex action.

## First physical test checklist

1. `python3 tools/mock_printer.py` — stands in for the QL-810W on TCP
   9100, dumping whatever it receives to a file.
2. `python3 tools/mock_convex_server.py serve --api-key testkey` — stands
   in for Convex.
3. Point the Pico's `config.py` at both mocks
   (`CONVEX_BASE_URL = "http://<laptop-ip>:8091"`,
   `PRINTERS["ql810w"]["ip"] = "<laptop-ip>"`, matching `port = 9100`) and
   confirm a harmless init-only frame makes it all the way through:
   ```bash
   python3 -c "open('ping.bin','wb').write(b'\x1b\x40')"  # ESC @ — init only, prints nothing
   python3 tools/mock_convex_server.py enqueue --file ping.bin --printer ql810w
   ```
   Check `tools/mock_printer.py`'s output directory for a 2-byte file —
   confirms Pico → Convex-poll → printer-socket works end to end without
   risking real label stock.
4. Point `config.py`'s printer IP back at the real QL-810W and repeat
   step 3's ping — confirms the TCP connection succeeds and the printer
   doesn't reject it outright.
5. Send a real `render_label.py`-produced job with a small, simple test
   image (e.g. a black square) before trying an actual label design —
   easier to diagnose "printed something, but offset/cut wrong" against a
   known-simple image than against a full sample-tag layout.
