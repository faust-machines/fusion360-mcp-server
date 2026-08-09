#!/usr/bin/env python
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "pandas"]
# ///
"""Decode OBD2 (mode 01) reference signals directly from a mixed CAN log.

When you record proprietary CAN data together with polled OBD2 responses in
one time-sorted trace, this pulls a chosen PID (speed, rpm, coolant temp, ...)
out as a clean reference CSV (``t,value``) on the *same clock* as the log.
That reference is what correlate.py / bitsearch.py align the proprietary
signal against.

OBD2 mode-01 response frame layout::

    byte0 = number of additional data bytes
    byte1 = 0x41                (0x40 | requested mode 0x01)
    byte2 = PID
    byte3.. = PID data (A, B, C, D)

Usage
-----
    uv run extract_obd2.py LOG.csv --list
    uv run extract_obd2.py LOG.csv --pid 0x0D --out ref_speed.csv
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd
from canlog import byte_matrix, load_can_log

# PID -> (name, unit, n_data_bytes, formula(A,B,C,D))
PIDS = {
    0x04: ("engine_load", "%", 1, lambda a, b, c, d: a * 100 / 255),
    0x05: ("coolant_temp", "degC", 1, lambda a, b, c, d: a - 40),
    0x0B: ("intake_map", "kPa", 1, lambda a, b, c, d: a),
    0x0C: ("engine_rpm", "rpm", 2, lambda a, b, c, d: (256 * a + b) / 4),
    0x0D: ("vehicle_speed", "km/h", 1, lambda a, b, c, d: a),
    0x0F: ("intake_temp", "degC", 1, lambda a, b, c, d: a - 40),
    0x10: ("maf_rate", "g/s", 2, lambda a, b, c, d: (256 * a + b) / 100),
    0x11: ("throttle_pos", "%", 1, lambda a, b, c, d: a * 100 / 255),
    0x1F: ("run_time", "s", 2, lambda a, b, c, d: 256 * a + b),
    0x2F: ("fuel_level", "%", 1, lambda a, b, c, d: a * 100 / 255),
    0x5C: ("oil_temp", "degC", 1, lambda a, b, c, d: a - 40),
}

# Standard 11-bit (0x7E8..0x7EF) and 29-bit (0x18DAF100..0x18DAF1FF) reply ids.
DEFAULT_RESP_11 = set(range(0x7E8, 0x7F0))


def is_resp_id(cid: int, resp_ids: set[int] | None) -> bool:
    if resp_ids is not None:
        return cid in resp_ids
    if cid in DEFAULT_RESP_11:
        return True
    return 0x18DAF100 <= cid <= 0x18DAF1FF


def decode_pid(df, bm, pid: int, resp_ids):
    name, unit, nbytes, formula = PIDS[pid]
    ids = df["id"].to_numpy()
    resp_mask = np.array([is_resp_id(int(c), resp_ids) for c in ids])
    mask = resp_mask & (bm[:, 1] == 0x41) & (bm[:, 2] == pid)
    t = df["t"].to_numpy()[mask]
    data = bm[mask]
    if len(t) == 0:
        return name, unit, np.array([]), np.array([])
    a = data[:, 3].astype(float)
    b = data[:, 4].astype(float)
    c = data[:, 5].astype(float)
    d = data[:, 6].astype(float)
    v = formula(a, b, c, d)
    return name, unit, t, np.asarray(v, dtype=float)


def list_pids(df, bm, resp_ids):
    ids = df["id"].to_numpy()
    resp_mask = np.array([is_resp_id(int(c), resp_ids) for c in ids])
    resp = bm[resp_mask & (bm[:, 1] == 0x41)]
    present = sorted(set(int(x) for x in resp[:, 2]))
    print("# OBD2 PIDs present in log:", file=sys.stderr)
    for pid in present:
        if pid in PIDS:
            n, u, *_ = PIDS[pid]
            cnt = int(np.sum(resp[:, 2] == pid))
            print(f"  0x{pid:02X}  {n:<14} [{u}]  frames={cnt}", file=sys.stderr)
        else:
            print(f"  0x{pid:02X}  (unknown, not in decoder table)", file=sys.stderr)
    if not present:
        print("  (none — no 0x41 mode-01 responses found)", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log")
    ap.add_argument("--pid", help="PID to extract, e.g. 0x0D")
    ap.add_argument("--out", help="output reference CSV (t,value)")
    ap.add_argument("--list", action="store_true", help="list PIDs present")
    ap.add_argument("--resp-ids", help="comma list of reply ids (overrides default)")
    ap.add_argument("--id-base", type=int, choices=[10, 16], default=None)
    args = ap.parse_args()

    resp_ids = None
    if args.resp_ids:
        resp_ids = {int(x, 0) for x in args.resp_ids.split(",")}

    df, _ = load_can_log(args.log, id_base=args.id_base)
    bm = byte_matrix(df)

    if args.list or not args.pid:
        list_pids(df, bm, resp_ids)
        if not args.pid:
            return

    pid = int(args.pid, 0)
    if pid not in PIDS:
        raise SystemExit(f"PID 0x{pid:02X} not in decoder table; add it to PIDS")
    name, unit, t, v = decode_pid(df, bm, pid, resp_ids)
    if len(t) == 0:
        raise SystemExit(f"no responses for PID 0x{pid:02X} found in log")

    out = args.out or f"ref_{name}.csv"
    pd.DataFrame({"t": t, "value": v}).to_csv(out, index=False)
    print(f"wrote {out}: {name} [{unit}], {len(t)} samples, "
          f"range {v.min():.1f}..{v.max():.1f}", file=sys.stderr)


if __name__ == "__main__":
    main()
