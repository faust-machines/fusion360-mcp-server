#!/usr/bin/env python
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "pandas"]
# ///
"""Rank candidate CAN fields against a reference signal (coarse scan).

For every CAN id that carries variable data, this tries a small set of
byte-aligned candidate fields (8/16/24-bit, little- and big-endian) and
scores each by its best time-lagged Pearson correlation with the reference.
The output narrows the search to a handful of (id, region) pairs that
bitsearch.py then refines bit-by-bit.

Usage
-----
    uv run correlate.py LOG.csv --ref REF.csv [--json cands.json] [--top 15]
                        [--id 0x3E9] [--ref-col value] [--ref-time-col t]
"""

from __future__ import annotations

import argparse
import json
import sys

from canlog import best_lag_corr, byte_matrix, extract_field, load_can_log
from refsig import load_reference


def candidate_fields(dlc: int):
    """Yield (start_bit, length, byte_order) byte-aligned candidates."""
    for b in range(dlc):
        yield (8 * b, 8, "little_endian")
    for b in range(dlc - 1):
        yield (8 * b, 16, "little_endian")
        yield (8 * b, 16, "big_endian")
    for b in range(dlc - 2):
        yield (8 * b, 24, "little_endian")
        yield (8 * b, 24, "big_endian")


def scan(path, ref_path, id_filter, ref_col, ref_time_col, id_base):
    df, info = load_can_log(path, id_base=id_base)
    t_ref, v_ref = load_reference(ref_path, time_col=ref_time_col, value_col=ref_col)

    results = []
    ids = df["id"].unique() if id_filter is None else [id_filter]
    for cid in ids:
        grp = df[df["id"] == cid]
        if len(grp) < 5:
            continue
        t = grp["t"].to_numpy()
        bm = byte_matrix(grp)
        dlc = int(grp["dlc"].mode().iloc[0])
        for start, length, order in candidate_fields(dlc):
            try:
                raw = extract_field(bm, start, length, order)
            except ValueError:
                continue
            if raw.std() < 1e-9:
                continue
            corr, lag = best_lag_corr(t, raw, t_ref, v_ref)
            results.append({
                "id": f"0x{int(cid):X}",
                "id_int": int(cid),
                "start_bit": start,
                "length": length,
                "byte_order": order,
                "corr": round(corr, 4),
                "abs_corr": round(abs(corr), 4),
                "lag_s": round(lag, 3),
            })

    results.sort(key=lambda d: d["abs_corr"], reverse=True)
    return {"file": path, "ref": ref_path, "candidates": results}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log")
    ap.add_argument("--ref", required=True, help="reference signal CSV")
    ap.add_argument("--json", help="write candidate JSON here")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--id", help="restrict to a single CAN id (e.g. 0x3E9)")
    ap.add_argument("--ref-col", default=None)
    ap.add_argument("--ref-time-col", default=None)
    ap.add_argument("--id-base", type=int, choices=[10, 16], default=None)
    args = ap.parse_args()

    id_filter = int(args.id, 0) if args.id else None
    res = scan(args.log, args.ref, id_filter, args.ref_col,
               args.ref_time_col, args.id_base)
    top = res["candidates"][:args.top]

    print("# correlation ranking (best time-lagged |r|)", file=sys.stderr)
    print(f"{'id':>8} {'start':>5} {'len':>3} {'order':>13} {'r':>7} {'lag_s':>6}",
          file=sys.stderr)
    for c in top:
        print(f"{c['id']:>8} {c['start_bit']:>5} {c['length']:>3} "
              f"{c['byte_order']:>13} {c['corr']:>7.3f} {c['lag_s']:>6.2f}",
              file=sys.stderr)

    out = {"file": res["file"], "ref": res["ref"], "candidates": top}
    text = json.dumps(out, indent=2)
    if args.json:
        with open(args.json, "w") as f:
            f.write(text)
        print(f"\nwrote {args.json}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
