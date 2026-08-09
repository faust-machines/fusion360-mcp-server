#!/usr/bin/env python
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "pandas"]
# ///
"""Exhaustive bit-field search + scale/offset fit for one CAN id.

Given a CAN id (from correlate.py) and a reference signal, this tries every
plausible bit field — little-endian at any start/length, big-endian at byte
boundaries, signed and unsigned — and ranks them by best time-lagged
correlation. For the top matches it fits ``physical = raw * scale + offset``
by least squares and reports R^2. The winning row is everything needed to
write a DBC signal.

Usage
-----
    uv run bitsearch.py LOG.csv --ref REF.csv --id 0x3E9 \
        [--max-len 24] [--top 8] [--json result.json] \
        [--name vehicle_speed] [--unit km/h]
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
from canlog import (
    byte_matrix,
    common_grid,
    extract_field,
    load_can_log,
    zoh_resample,
)
from refsig import load_reference

HZ = 20.0


def _standardize(x: np.ndarray) -> np.ndarray:
    mu = x.mean()
    sd = x.std()
    return (x - mu) / sd if sd > 1e-12 else np.zeros_like(x)


def _bit_varies(bm: np.ndarray) -> np.ndarray:
    """Return a length-64 bool mask: does each payload bit ever change?"""
    varies = np.zeros(64, dtype=bool)
    for p in range(64):
        byte = bm[:, p // 8]
        bit = (byte >> (p % 8)) & 1
        varies[p] = bit.min() != bit.max()
    return varies


def _const_bits(varies: np.ndarray, start: int, length: int) -> int:
    """How many of a field's occupied bits are constant across the log.

    For both little-endian and byte-aligned big-endian fields the occupied
    payload bit positions are ``start .. start+length-1``.
    """
    end = min(start + length, 64)
    return int((~varies[start:end]).sum())


def _varying_byte_blocks(bm: np.ndarray, dlc: int) -> list[tuple[int, int]]:
    """Maximal contiguous runs of bytes that change across the log.

    Returns (start_byte, n_bytes) pairs — the natural byte-aligned field
    candidates a human would read off a trace.
    """
    varies_byte = [bm[:, j].min() != bm[:, j].max() for j in range(dlc)]
    blocks, j = [], 0
    while j < dlc:
        if varies_byte[j]:
            k = j
            while k < dlc and varies_byte[k]:
                k += 1
            blocks.append((j, k - j))
            j = k
        else:
            j += 1
    return blocks


def _candidates(dlc: int, max_len: int):
    nbits = dlc * 8
    seen = set()
    # little-endian, any alignment
    for length in range(1, max_len + 1):
        for start in range(0, nbits - length + 1):
            for signed in (False, True):
                if signed and length == 1:
                    continue
                key = (start, length, "little_endian", signed)
                if key not in seen:
                    seen.add(key)
                    yield key
    # big-endian, byte aligned
    for length in (8, 16, 24, 32):
        if length > max_len:
            continue
        for b in range(0, dlc - length // 8 + 1):
            for signed in (False, True):
                key = (8 * b, length, "big_endian", signed)
                if key not in seen:
                    seen.add(key)
                    yield key


def search(path, ref_path, cid, max_len, top, ref_col, ref_time_col, id_base):
    df, _ = load_can_log(path, id_base=id_base)
    grp = df[df["id"] == cid]
    if len(grp) < 5:
        raise SystemExit(f"id 0x{cid:X} has too few frames ({len(grp)})")
    t = grp["t"].to_numpy()
    bm = byte_matrix(grp)
    dlc = int(grp["dlc"].mode().iloc[0])

    t_ref, v_ref = load_reference(ref_path, time_col=ref_time_col, value_col=ref_col)

    grid = common_grid(t, t_ref, hz=HZ)
    if grid.size < 4:
        raise SystemExit("no time overlap between log and reference")
    duration = min(t.max() - t.min(), t_ref.max() - t_ref.min())
    max_lag = min(5.0, 0.25 * duration)
    dks = list(range(-int(max_lag * HZ), int(max_lag * HZ) + 1))
    ref_stack = np.array([_standardize(zoh_resample(t_ref, v_ref, grid + dk / HZ))
                          for dk in dks])  # (n_lags, n_grid)
    n_grid = grid.size

    varies = _bit_varies(bm)
    rows = []
    for start, length, order, signed in _candidates(dlc, max_len):
        try:
            raw = extract_field(bm, start, length, order, signed)
        except ValueError:
            continue
        if raw.std() < 1e-9:
            continue
        sig_g = _standardize(zoh_resample(t, raw, grid))
        corrs = (ref_stack @ sig_g) / n_grid  # (n_lags,)
        j = int(np.argmax(np.abs(corrs)))
        rows.append((abs(corrs[j]), corrs[j], dks[j] / HZ,
                     start, length, order, signed))

    rows.sort(key=lambda r: r[0], reverse=True)

    # Recommended byte-aligned guess: read each maximal run of varying bytes
    # as a whole field, in whichever endianness correlates best. This is how
    # a human reads a signal off a trace and resolves the length ambiguity
    # (unexercised high bits look "constant" but still belong to the field).
    recommended = None
    best_rec = -1.0
    for (b0, nb) in _varying_byte_blocks(bm, dlc):
        nb = min(nb, 4)  # cap at 32-bit
        length = nb * 8
        start = b0 * 8
        orders = ["little_endian"] if nb == 1 else ["little_endian", "big_endian"]
        for order in orders:
            for signed in (False, True):
                raw = extract_field(bm, start, length, order, signed)
                if raw.std() < 1e-9:
                    continue
                sig_g = _standardize(zoh_resample(t, raw, grid))
                corrs = (ref_stack @ sig_g) / n_grid
                j = int(np.argmax(np.abs(corrs)))
                if abs(corrs[j]) > best_rec:
                    best_rec = abs(corrs[j])
                    recommended = (abs(corrs[j]), corrs[j], dks[j] / HZ,
                                   start, length, order, signed)

    def fit_row(absc, corr, lag, start, length, order, signed):
        raw = extract_field(bm, start, length, order, signed)
        sig_g = zoh_resample(t, raw, grid)
        ref_g = zoh_resample(t_ref, v_ref, grid + lag)
        # least squares: ref = scale*sig + offset
        A = np.vstack([sig_g, np.ones_like(sig_g)]).T
        (scale, offset), *_ = np.linalg.lstsq(A, ref_g, rcond=None)
        pred = scale * sig_g + offset
        ss_res = float(np.sum((ref_g - pred) ** 2))
        ss_tot = float(np.sum((ref_g - ref_g.mean()) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
        return {
            "id": f"0x{cid:X}",
            "id_int": cid,
            "start_bit": start,
            "length": length,
            "byte_order": order,
            "signed": signed,
            "const_bits": _const_bits(varies, start, length),
            "corr": round(corr, 5),
            "lag_s": round(lag, 3),
            "scale": float(f"{scale:.6g}"),
            "offset": float(f"{offset:.6g}"),
            "r2": round(r2, 5),
        }

    out = [fit_row(*r) for r in rows[:top]]
    rec = fit_row(*recommended) if recommended else None
    return {"file": path, "ref": ref_path, "id": f"0x{cid:X}",
            "recommended": rec, "results": out}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log")
    ap.add_argument("--ref", required=True)
    ap.add_argument("--id", required=True, help="CAN id, e.g. 0x3E9")
    ap.add_argument("--max-len", type=int, default=24)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--json")
    ap.add_argument("--name", default="signal")
    ap.add_argument("--unit", default="")
    ap.add_argument("--ref-col", default=None)
    ap.add_argument("--ref-time-col", default=None)
    ap.add_argument("--id-base", type=int, choices=[10, 16], default=None)
    args = ap.parse_args()

    cid = int(args.id, 0)
    res = search(args.log, args.ref, cid, args.max_len, args.top,
                 args.ref_col, args.ref_time_col, args.id_base)
    res["name"] = args.name
    res["unit"] = args.unit

    print(f"# bitsearch id={res['id']}  name={args.name}", file=sys.stderr)
    print(f"{'start':>5} {'len':>3} {'order':>13} {'sgn':>3} {'cbits':>5} "
          f"{'r':>7} {'scale':>10} {'offset':>10} {'R2':>7}", file=sys.stderr)
    for r in res["results"]:
        print(f"{r['start_bit']:>5} {r['length']:>3} {r['byte_order']:>13} "
              f"{str(r['signed'])[0]:>3} {r['const_bits']:>5} {r['corr']:>7.3f} "
              f"{r['scale']:>10.4g} {r['offset']:>10.4g} {r['r2']:>7.4f}",
              file=sys.stderr)
    rec = res.get("recommended")
    if rec:
        print(f"\n-> recommended: start={rec['start_bit']} length={rec['length']} "
              f"{rec['byte_order']} signed={rec['signed']} "
              f"scale={rec['scale']} offset={rec['offset']} "
              f"r={rec['corr']:.4f} R2={rec['r2']:.4f}", file=sys.stderr)

    text = json.dumps(res, indent=2)
    if args.json:
        with open(args.json, "w") as f:
            f.write(text)
        print(f"\nwrote {args.json}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
