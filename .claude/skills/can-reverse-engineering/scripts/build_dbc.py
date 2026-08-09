#!/usr/bin/env python
# /// script
# requires-python = ">=3.10"
# dependencies = ["cantools>=39", "numpy", "pandas"]
# ///
"""Write a DBC file from solved signal parameters.

Two ways to call it:

1. Single signal from the command line (quick, appends to / creates a DBC).
2. A spec JSON describing one or more messages, each with one or more
   signals — used to assemble a multi-signal DBC (e.g. all eight gauges).

Big-endian note
---------------
This skill searches big-endian fields only at byte boundaries. Such a field
whose most-significant byte is ``b0`` maps to a DBC (Motorola/sawtooth) start
bit of ``b0 * 8 + 7``. ``make_synthetic.py`` round-trips this through cantools
so the emitted DBC decodes bit-for-bit the same value the search found.

Usage
-----
    uv run build_dbc.py --out out.dbc \
        --id 0x3E9 --name vehicle_speed --start-bit 0 --length 16 \
        --byte-order little_endian --scale 0.01 --offset 0 --unit km/h \
        [--msg-len 8] [--extended]

    uv run build_dbc.py --spec signals.json --out out.dbc
"""

from __future__ import annotations

import argparse
import json
import sys

import cantools
from cantools.database.can import Database, Message, Signal
from cantools.database.conversion import BaseConversion


def dbc_start_bit(start_bit: int, length: int, byte_order: str) -> int:
    """Convert our extraction start bit to cantools' DBC start bit."""
    if byte_order == "little_endian":
        return start_bit
    # big-endian, byte-aligned: msb byte index -> sawtooth MSB position
    if start_bit % 8 != 0 or length % 8 != 0:
        raise ValueError("big_endian signals must be byte-aligned in this skill")
    return start_bit + 7


def make_signal(s: dict) -> Signal:
    order = s.get("byte_order", "little_endian")
    return Signal(
        name=s["name"],
        start=dbc_start_bit(s["start_bit"], s["length"], order),
        length=s["length"],
        byte_order=order,
        is_signed=bool(s.get("signed", False)),
        conversion=BaseConversion.factory(
            scale=s.get("scale", 1), offset=s.get("offset", 0)
        ),
        minimum=s.get("min"),
        maximum=s.get("max"),
        unit=s.get("unit") or None,
    )


def build_database(spec: dict) -> Database:
    messages = []
    for m in spec["messages"]:
        signals = [make_signal(s) for s in m["signals"]]
        messages.append(Message(
            frame_id=m["id"],
            name=m.get("name", f"MSG_{m['id']:X}"),
            length=m.get("length", 8),
            signals=signals,
            is_extended_frame=bool(m.get("is_extended", False)),
        ))
    return Database(messages=messages)


def spec_from_cli(args) -> dict:
    return {
        "messages": [{
            "id": int(args.id, 0),
            "name": args.msg_name or f"MSG_{int(args.id, 0):X}",
            "length": args.msg_len,
            "is_extended": args.extended,
            "signals": [{
                "name": args.name,
                "start_bit": args.start_bit,
                "length": args.length,
                "byte_order": args.byte_order,
                "signed": args.signed,
                "scale": args.scale,
                "offset": args.offset,
                "unit": args.unit,
                "min": args.min,
                "max": args.max,
            }],
        }]
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--spec", help="JSON spec (overrides single-signal args)")
    # single-signal args
    ap.add_argument("--id")
    ap.add_argument("--name", default="signal")
    ap.add_argument("--msg-name", default=None)
    ap.add_argument("--start-bit", type=int)
    ap.add_argument("--length", type=int)
    ap.add_argument("--byte-order", default="little_endian",
                    choices=["little_endian", "big_endian"])
    ap.add_argument("--signed", action="store_true")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--offset", type=float, default=0.0)
    ap.add_argument("--unit", default="")
    ap.add_argument("--min", type=float, default=None)
    ap.add_argument("--max", type=float, default=None)
    ap.add_argument("--msg-len", type=int, default=8)
    ap.add_argument("--extended", action="store_true")
    args = ap.parse_args()

    if args.spec:
        with open(args.spec) as f:
            spec = json.load(f)
    else:
        if not (args.id and args.start_bit is not None and args.length):
            ap.error("provide --spec OR --id/--start-bit/--length")
        spec = spec_from_cli(args)

    db = build_database(spec)
    with open(args.out, "w") as f:
        f.write(db.as_dbc_string())

    n_sig = sum(len(m["signals"]) for m in spec["messages"])
    print(f"wrote {args.out}: {len(spec['messages'])} message(s), "
          f"{n_sig} signal(s)  [cantools {cantools.__version__}]", file=sys.stderr)


if __name__ == "__main__":
    main()
