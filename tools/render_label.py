#!/usr/bin/env python3
"""
render_label.py — turn an image into Brother QL-810W raster print bytes.

This intentionally does NOT reimplement Brother's raster protocol here or
on the Pico. It shells out to `brother_ql` (https://github.com/pklaus/brother_ql),
a mature, actively-used open-source library that already implements that
protocol correctly for this printer family — getting print-head timing,
media-size framing, and raster line encoding right from scratch, from
memory, on a microcontroller is exactly the kind of hardware-protocol
footgun worth avoiding when a proven library already exists.

Output is raw bytes, ready to hand to the Pico's poll/pull pipeline — see
tools/mock_convex_server.py's `enqueue` command to queue this file as a
test job without needing tda-app/Convex running yet, and
docs/BROTHER_PROTOCOL.md for how this fits into the tda-app integration.

Usage:
    pip install brother_ql pillow
    python3 tools/render_label.py label.png out.bin --label 62
    python3 tools/mock_convex_server.py enqueue --file out.bin --printer ql810w

`--label 62` selects 62mm continuous tape, matching the office's label
stock (tda-app's generateSampleLabel.js renders a 2.4in / ~62mm square
label — see lib/pdf/generateSampleLabel.js in the tda-app repo).
"""
import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", help="input image file (PNG recommended)")
    parser.add_argument("out", help="output file for raw raster bytes")
    parser.add_argument("--label", default="62", help="brother_ql label size, e.g. 62 for 62mm continuous tape (default)")
    parser.add_argument("--model", default="QL-810W", help="brother_ql printer model name")
    parser.add_argument("--threshold", type=int, default=128, help="black/white threshold, 0-255")
    parser.add_argument("--no-cut", action="store_true", help="don't append an auto-cut instruction at the end")
    args = parser.parse_args()

    try:
        from brother_ql.conversion import convert
        from brother_ql.raster import BrotherQLRaster
        from PIL import Image
    except ImportError:
        print(
            "Missing dependencies. Run: pip install brother_ql pillow",
            file=sys.stderr,
        )
        sys.exit(1)

    im = Image.open(args.image)
    qlr = BrotherQLRaster(args.model)
    qlr.exception_on_warning = True

    instructions = convert(
        qlr=qlr,
        images=[im],
        label=args.label,
        rotate="0",
        threshold=args.threshold,
        dither=False,
        compress=False,
        red=False,
        dpi_600=False,
        hq=True,
        cut=not args.no_cut,
    )

    with open(args.out, "wb") as f:
        f.write(instructions)
    print("Wrote %d bytes to %s" % (len(instructions), args.out))
    print("Note: brother_ql's convert() kwargs occasionally shift between")
    print("versions — if this errors, check `python3 -c \"import brother_ql,brother_ql.conversion as c; help(c.convert)\"`")
    print("for the signature your installed version expects.")


if __name__ == "__main__":
    main()
