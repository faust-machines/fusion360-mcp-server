#!/usr/bin/env python
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "pandas"]
# ///
"""Generate a synthetic mixed CAN log for demos and the skill self-test.

It embeds two proprietary signals with *known* encodings plus interleaved
OBD2 responses, so the whole pipeline can be validated without hardware:

* id 0x3E9  vehicle_speed : bytes 0-1, 16-bit little-endian, scale 0.01
* id 0x123  engine_rpm    : bytes 2-3, 16-bit big-endian,    scale 0.25
* id 0x7E8  OBD2 mode-01 responses for PID 0x0D (speed) and 0x0C (rpm)

Writes ``<out>.csv`` (timestamp,id,data) and ``<out>.groundtruth.json``.

Usage
-----
    uv run make_synthetic.py --out synth [--seconds 60]
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

RNG = np.random.default_rng(1939)


def drive_cycle(t: np.ndarray) -> np.ndarray:
    """A smooth 0..~150 km/h pseudo drive cycle."""
    base = (
        60
        + 55 * np.sin(2 * np.pi * t / 47)
        + 25 * np.sin(2 * np.pi * t / 13 + 1.1)
        + 10 * np.sin(2 * np.pi * t / 5 + 0.3)
    )
    return np.clip(base, 0, 200)


def rows_for(seconds: float):
    rows = []

    # proprietary + reference sampled on their own cadences
    t_prop = np.arange(0, seconds, 0.05)          # 20 Hz
    speed = drive_cycle(t_prop)
    rpm = np.clip(800 + speed * 32 + 200 * np.sin(2 * np.pi * t_prop / 3), 0, 7000)

    counterA = 0
    counterB = 0
    for k, tt in enumerate(t_prop):
        # --- id 0x3E9: speed 16-bit little-endian, scale 0.01 ---
        raw_s = int(round(speed[k] / 0.01)) & 0xFFFF
        b = [raw_s & 0xFF, (raw_s >> 8) & 0xFF, 0x5A, 0x00,
             0x00, 0x00, int(RNG.integers(0, 256)), counterA & 0xFF]
        rows.append((tt, 0x3E9, b))
        counterA += 1

        # --- id 0x123: rpm 16-bit big-endian, scale 0.25 ---
        raw_r = int(round(rpm[k] / 0.25)) & 0xFFFF
        b = [counterB & 0xFF, 0x11, (raw_r >> 8) & 0xFF, raw_r & 0xFF,
             0x00, 0x00, 0x00, 0x00]
        rows.append((tt, 0x123, b))
        counterB += 1

    # --- OBD2 responses (id 0x7E8) at ~3 Hz, alternating speed / rpm ---
    t_obd = np.arange(0.02, seconds, 0.33)
    for j, tt in enumerate(t_obd):
        s = drive_cycle(np.array([tt]))[0]
        r = np.clip(800 + s * 32 + 200 * np.sin(2 * np.pi * tt / 3), 0, 7000)
        if j % 2 == 0:
            rows.append((tt, 0x7E8, [0x03, 0x41, 0x0D, int(round(s)) & 0xFF,
                                     0, 0, 0, 0]))
        else:
            raw = int(round(r * 4)) & 0xFFFF
            rows.append((tt, 0x7E8, [0x04, 0x41, 0x0C, (raw >> 8) & 0xFF,
                                     raw & 0xFF, 0, 0, 0]))

    rows.sort(key=lambda x: x[0])
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="synth")
    ap.add_argument("--seconds", type=float, default=60.0)
    args = ap.parse_args()

    rows = rows_for(args.seconds)
    df = pd.DataFrame({
        "timestamp": [f"{t:.4f}" for t, _, _ in rows],
        "id": [f"0x{i:X}" for _, i, _ in rows],
        "data": ["".join(f"{x:02X}" for x in b) for _, _, b in rows],
    })
    csv_path = f"{args.out}.csv"
    df.to_csv(csv_path, index=False)

    truth = {
        "messages": [
            {"id": "0x3E9", "signal": "vehicle_speed", "start_bit": 0,
             "length": 16, "byte_order": "little_endian", "scale": 0.01,
             "offset": 0.0, "unit": "km/h", "ref_pid": "0x0D"},
            {"id": "0x123", "signal": "engine_rpm", "start_bit": 16,
             "length": 16, "byte_order": "big_endian", "scale": 0.25,
             "offset": 0.0, "unit": "rpm", "ref_pid": "0x0C"},
        ]
    }
    gt_path = f"{args.out}.groundtruth.json"
    with open(gt_path, "w") as f:
        json.dump(truth, f, indent=2)

    print(f"wrote {csv_path} ({len(df)} frames) and {gt_path}")


if __name__ == "__main__":
    main()
