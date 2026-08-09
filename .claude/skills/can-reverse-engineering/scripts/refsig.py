"""Load a reference signal CSV (time, value) with light auto-detection.

A reference signal is any external measurement time-aligned with the CAN
trace: OBD2 speed decoded from the log, an OCR'd dashboard readout, or a
human-slider capture. Shared by correlate.py / bitsearch.py / plot_signal.py.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from canlog import _norm  # reuse column normalizer

_T_KEYS = ("t", "time", "timestamp", "time_s", "secs", "seconds")
_V_KEYS = ("value", "val", "v", "ref", "reference", "y", "signal", "speed",
           "rpm", "measurement")


def load_reference(
    path: str,
    *,
    time_col: str | None = None,
    value_col: str | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(t_seconds_from_start, value)`` arrays."""
    df = pd.read_csv(path)
    cols_norm = {_norm(c): c for c in df.columns}

    if time_col is None:
        for k in _T_KEYS:
            if k in cols_norm:
                time_col = cols_norm[k]
                break
    if value_col is None:
        for k in _V_KEYS:
            if k in cols_norm:
                value_col = cols_norm[k]
                break

    numeric = [c for c in df.columns
               if pd.to_numeric(df[c], errors="coerce").notna().mean() > 0.9]
    if time_col is None:
        time_col = numeric[0] if numeric else df.columns[0]
    if value_col is None:
        rest = [c for c in numeric if c != time_col]
        if not rest:
            raise ValueError(
                f"No value column found in {path}; columns={list(df.columns)}")
        value_col = rest[0]

    t = pd.to_numeric(df[time_col], errors="coerce").to_numpy(dtype=float)
    v = pd.to_numeric(df[value_col], errors="coerce").to_numpy(dtype=float)
    m = np.isfinite(t) & np.isfinite(v)
    t, v = t[m], v[m]
    order = np.argsort(t)
    t, v = t[order], v[order]
    t = t - t.min() if len(t) else t
    return t, v
