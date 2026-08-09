#!/usr/bin/env python
# /// script
# requires-python = ">=3.10"
# dependencies = ["opencv-python-headless", "numpy", "pandas"]
# ///
"""OCR a numeric readout from a dashboard video into a reference CSV.

Reference method #2 from the workflow: when there is no OBD2/CAN reference,
film the vehicle's digital speed (or any numeric gauge) with a webcam, then
turn that video into a ``t,value`` time series to correlate against.

How it works
------------
* Sample the video every ``--sample-fps`` frames.
* Crop the digits region ``--roi X,Y,W,H`` (use ``--preview`` first to find it).
* OCR the crop with EasyOCR (default) or Tesseract.
* Write ``t,value`` where ``t = frame_time + --t-offset`` seconds.

First find the ROI, then run the full pass::

    uv run video_to_timeseries.py drive.mp4 --preview            # dumps sample crops
    uv run video_to_timeseries.py drive.mp4 --roi 900,560,240,120 \
        --out ref_speed.csv --sample-fps 5 --max-value 260

The OCR backend is imported lazily; EasyOCR downloads a small model on first
use. Review the printed failure rate — a noisy readout still correlates well
once the obvious misreads are dropped (``--max-value`` clamps them out).
"""

from __future__ import annotations

import argparse
import re
import sys

import cv2
import pandas as pd


def parse_roi(s: str | None):
    if not s:
        return None
    x, y, w, h = (int(v) for v in s.split(","))
    return x, y, w, h


def make_reader(backend: str):
    """Return a function crop->(text) for the chosen OCR backend (lazy import)."""
    if backend == "easyocr":
        import easyocr  # noqa: PLC0415
        reader = easyocr.Reader(["en"], gpu=False, verbose=False)

        def ocr(crop):
            res = reader.readtext(crop, allowlist="0123456789.-", detail=0)
            return " ".join(res)
        return ocr
    if backend == "tesseract":
        import pytesseract  # noqa: PLC0415

        def ocr(crop):
            cfg = "--psm 7 -c tessedit_char_whitelist=0123456789.-"
            return pytesseract.image_to_string(crop, config=cfg)
        return ocr
    raise SystemExit(f"unknown backend {backend!r}")


def preprocess(crop, invert: bool, thresh: int):
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    if thresh >= 0:
        _, gray = cv2.threshold(gray, thresh, 255,
                                cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY)
    return gray


_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def to_number(text: str):
    m = _NUM.search(text.replace(" ", ""))
    return float(m.group()) if m else None


def preview(path, roi, invert, thresh, n=6):
    cap = cv2.VideoCapture(path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i * total / n))
        ok, frame = cap.read()
        if not ok:
            continue
        crop = frame if roi is None else frame[roi[1]:roi[1] + roi[3],
                                               roi[0]:roi[0] + roi[2]]
        out = f"preview_{i:02d}.png"
        cv2.imwrite(out, preprocess(crop, invert, thresh) if roi else crop)
        print(f"wrote {out}", file=sys.stderr)
    cap.release()
    if roi is None:
        print("No --roi given: previews are full frames. Open one, read off the "
              "digit box pixel coords, and pass --roi X,Y,W,H.", file=sys.stderr)


def run(args):
    roi = parse_roi(args.roi)
    if args.preview:
        preview(path=args.video, roi=roi, invert=args.invert, thresh=args.threshold)
        return

    if roi is None:
        raise SystemExit("--roi X,Y,W,H is required (run with --preview first)")

    ocr = make_reader(args.backend)
    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(fps / args.sample_fps)))

    rows, fails, n = [], 0, 0
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            n += 1
            crop = frame[roi[1]:roi[1] + roi[3], roi[0]:roi[0] + roi[2]]
            val = to_number(ocr(preprocess(crop, args.invert, args.threshold)))
            t = idx / fps + args.t_offset
            if val is None or (args.max_value is not None and
                               not (args.min_value <= val <= args.max_value)):
                fails += 1
            else:
                rows.append((t, val))
        idx += 1
    cap.release()

    if not rows:
        raise SystemExit("OCR produced no valid samples — check --roi / --threshold")
    df = pd.DataFrame(rows, columns=["t", "value"])
    df.to_csv(args.out, index=False)
    print(f"wrote {args.out}: {len(df)} samples "
          f"({fails}/{n} frames dropped, {100*fails/max(n,1):.0f}% fail rate), "
          f"range {df['value'].min():.1f}..{df['value'].max():.1f}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video")
    ap.add_argument("--out", default="ref_video.csv")
    ap.add_argument("--roi", help="digit box as X,Y,W,H in pixels")
    ap.add_argument("--backend", default="easyocr", choices=["easyocr", "tesseract"])
    ap.add_argument("--sample-fps", type=float, default=5.0,
                    help="how many frames per second to OCR")
    ap.add_argument("--t-offset", type=float, default=0.0,
                    help="seconds added to every timestamp (align to CAN clock)")
    ap.add_argument("--invert", action="store_true",
                    help="light digits on dark background")
    ap.add_argument("--threshold", type=int, default=-1,
                    help="binarization threshold 0..255, or -1 to disable")
    ap.add_argument("--min-value", type=float, default=-1e9)
    ap.add_argument("--max-value", type=float, default=None,
                    help="drop OCR reads above this (kills misreads)")
    ap.add_argument("--preview", action="store_true",
                    help="dump sample frames/crops to find the ROI, then exit")
    args = ap.parse_args()
    run(args)


if __name__ == "__main__":
    main()
