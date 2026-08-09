#!/usr/bin/env python
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "pandas"]
# ///
"""Survey a CAN log: per-id timing, DLC, and per-byte behavior.

Purpose in the workflow: shrink a multi-million-frame trace down to a compact
JSON map so Claude can reason about it without ingesting raw frames. It also
flags bytes that are *constants*, *counters*, or *high-entropy* (checksum-like)
so the correlation step can skip them.

Usage
-----
    uv run survey.py LOG.csv [--json survey.json] [--top 40] [--id-base 16]
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
from canlog import byte_matrix, load_can_log


def classify_byte(vals: np.ndarray) -> tuple[str, dict]:
    distinct = int(np.unique(vals).size)
    stats = {
        "distinct": distinct,
        "min": int(vals.min()),
        "max": int(vals.max()),
    }
    if distinct == 1:
        return "constant", stats
    # counter: near-constant nonzero step (mod 256)
    if len(vals) > 5:
        diffs = np.diff(vals.astype(np.int64)) % 256
        vals_d, counts = np.unique(diffs, return_counts=True)
        step = int(vals_d[np.argmax(counts)])
        frac = counts.max() / counts.sum()
        if step != 0 and frac > 0.9:
            stats["step"] = step
            return "counter", stats
    # entropy (normalized to [0,1]) — high entropy hints at checksum/random
    p = np.bincount(vals, minlength=256) / len(vals)
    p = p[p > 0]
    entropy = float(-(p * np.log2(p)).sum() / 8.0)
    stats["entropy"] = round(entropy, 3)
    if entropy > 0.85 and distinct > 64:
        return "high_entropy", stats
    return "variable", stats


def survey(path: str, id_base: int | None, top: int) -> dict:
    df, info = load_can_log(path, id_base=id_base)
    result = {
        "file": path,
        "n_frames": info.n_frames,
        "n_ids": info.n_ids,
        "duration_s": round(info.duration_s, 3),
        "detected_columns": {
            "time": info.time_col,
            "id": info.id_col,
            "data": info.data_col,
            "bytes": info.byte_cols,
        },
        "ids": [],
    }

    for cid, grp in df.groupby("id"):
        t = grp["t"].to_numpy()
        bm = byte_matrix(grp)
        dlc = int(grp["dlc"].mode().iloc[0]) if len(grp) else 0
        period_ms = float(np.median(np.diff(t)) * 1000) if len(t) > 1 else 0.0
        bytes_info = []
        for i in range(max(dlc, 1)):
            cls, stats = classify_byte(bm[:, i])
            bytes_info.append({"i": i, "class": cls, **stats})
        result["ids"].append({
            "id": f"0x{int(cid):X}",
            "id_int": int(cid),
            "count": int(len(grp)),
            "period_ms": round(period_ms, 2),
            "dlc": dlc,
            "bytes": bytes_info,
        })

    result["ids"].sort(key=lambda d: d["count"], reverse=True)
    if top:
        result["ids"] = result["ids"][:top]
    return result


def print_summary(res: dict) -> None:
    print(f"# CAN survey — {res['file']}", file=sys.stderr)
    print(f"  frames={res['n_frames']}  ids={res['n_ids']}  "
          f"duration={res['duration_s']}s", file=sys.stderr)
    print(f"  columns: {res['detected_columns']}", file=sys.stderr)
    for d in res["ids"]:
        var = sum(1 for b in d["bytes"] if b["class"] == "variable")
        print(f"  {d['id']:>8}  n={d['count']:<7} ~{d['period_ms']}ms  "
              f"dlc={d['dlc']}  variable_bytes={var}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log")
    ap.add_argument("--json", help="write full survey JSON here")
    ap.add_argument("--top", type=int, default=40, help="keep the N busiest ids")
    ap.add_argument("--id-base", type=int, choices=[10, 16], default=None)
    args = ap.parse_args()

    res = survey(args.log, args.id_base, args.top)
    print_summary(res)
    text = json.dumps(res, indent=2)
    if args.json:
        with open(args.json, "w") as f:
            f.write(text)
        print(f"\nwrote {args.json}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
