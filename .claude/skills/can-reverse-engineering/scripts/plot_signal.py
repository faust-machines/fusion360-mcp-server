#!/usr/bin/env python
# /// script
# requires-python = ">=3.10"
# dependencies = ["cantools>=39", "matplotlib", "numpy", "pandas"]
# ///
"""Decode a signal from a DBC over a log and plot it against the reference.

This is the visual sanity check the workflow ends on: overlay the
DBC-decoded proprietary signal on the reference, plus a scatter of the two,
annotated with R^2 and the best time lag. A tight overlay / diagonal scatter
means the DBC is correct.

Usage
-----
    uv run plot_signal.py LOG.csv --dbc out.dbc --ref REF.csv --out plot.png \
        [--id 0x3E9] [--name vehicle_speed]
"""

from __future__ import annotations

import argparse
import sys

import cantools
import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from canlog import (  # noqa: E402
    best_lag_corr,
    byte_matrix,
    common_grid,
    load_can_log,
    zoh_resample,
)
from refsig import load_reference  # noqa: E402


def decode_series(db, message, log_frames):
    """Return (t, value) decoding `message`'s chosen signal over its frames."""
    fid = message.frame_id
    grp = log_frames[log_frames["id"] == fid]
    bm = byte_matrix(grp)
    t = grp["t"].to_numpy()
    sig_name = message.signals[0].name
    vals = np.full(len(grp), np.nan)
    for i in range(len(grp)):
        payload = bytes(int(x) for x in bm[i, :message.length])
        try:
            dec = message.decode(payload, decode_choices=False,
                                 allow_truncated=True)
            vals[i] = float(dec[sig_name])
        except Exception:
            pass
    return t, vals, sig_name


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log")
    ap.add_argument("--dbc", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--out", default="signal_plot.png")
    ap.add_argument("--id", help="message id to decode (default: first in DBC)")
    ap.add_argument("--name", help="signal name (default: first in message)")
    ap.add_argument("--ref-col", default=None)
    ap.add_argument("--ref-time-col", default=None)
    ap.add_argument("--id-base", type=int, choices=[10, 16], default=None)
    args = ap.parse_args()

    db = cantools.database.load_file(args.dbc)
    if args.id:
        message = db.get_message_by_frame_id(int(args.id, 0))
    else:
        message = db.messages[0]
    if args.name:
        # reorder so the requested signal is first (decode_series uses [0])
        message.signals.sort(key=lambda s: s.name != args.name)

    df, _ = load_can_log(args.log, id_base=args.id_base)
    t_sig, v_sig, sig_name = decode_series(db, message, df)
    t_ref, v_ref = load_reference(args.ref, time_col=args.ref_time_col,
                                  value_col=args.ref_col)

    corr, lag = best_lag_corr(t_sig, v_sig, t_ref, v_ref)
    grid = common_grid(t_sig, t_ref)
    sig_g = zoh_resample(t_sig, v_sig, grid)
    ref_g = zoh_resample(t_ref, v_ref, grid + lag)
    r2 = corr ** 2

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 4.5))
    ax1.plot(t_ref + lag, v_ref, label="reference", color="#888", lw=2)
    ax1.plot(t_sig, v_sig, label=f"decoded {sig_name}", color="#c0392b", lw=1.2)
    ax1.set_xlabel("time [s]")
    ax1.set_ylabel(sig_name)
    ax1.set_title(f"{message.name}.{sig_name}  (lag {lag:+.2f}s)")
    ax1.legend(loc="best")
    ax1.grid(alpha=0.3)

    ax2.scatter(ref_g, sig_g, s=6, alpha=0.4, color="#2c3e50")
    ax2.set_xlabel("reference")
    ax2.set_ylabel(f"decoded {sig_name}")
    ax2.set_title(f"R^2 = {r2:.4f}   r = {corr:+.4f}")
    ax2.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(args.out, dpi=110)
    print(f"wrote {args.out}  (r={corr:+.4f}, R^2={r2:.4f}, lag={lag:+.2f}s)",
          file=sys.stderr)


if __name__ == "__main__":
    main()
