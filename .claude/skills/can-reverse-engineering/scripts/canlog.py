"""Shared helpers for loading CAN logs and extracting bit-fields.

This module is imported by the other scripts in the skill. It is deliberately
dependency-light (numpy + pandas) so it can be reused everywhere.

Data model
----------
A loaded CAN log is a :class:`pandas.DataFrame` with these columns:

* ``t``    float, seconds relative to the first frame (monotonic)
* ``id``   int, CAN arbitration id
* ``dlc``  int, data length (number of valid bytes)
* ``b0``..``b7``  uint8, the data bytes (missing bytes are 0)

Bit numbering
-------------
* Little-endian (Intel): ``start_bit`` is the position of the LSB in the
  standard little-endian integer view of the payload (byte 0 = least
  significant). This matches cantools' Intel convention exactly.
* Big-endian (Motorola): only **byte-aligned** fields are supported
  (``start_bit`` a multiple of 8, ``length`` a multiple of 8). The DBC
  start bit emitted for such a field is ``byte_index * 8 + 7`` — see
  ``build_dbc.py``. This covers the vast majority of real speed / rpm /
  temperature signals; non-aligned Motorola fields are intentionally out
  of scope.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

MAX_BYTES = 8  # classic CAN; CAN-FD payloads are truncated to the first 8 bytes


# --------------------------------------------------------------------------- #
# CSV loading with column auto-detection
# --------------------------------------------------------------------------- #

_TIME_KEYS = ("t", "time", "timestamp", "time_s", "abs time", "abstime", "datetime")
_ID_KEYS = ("id", "can_id", "canid", "arbitration_id", "arbitrationid",
            "identifier", "pgn_id", "msg_id", "msgid")
_DATA_KEYS = ("data", "databytes", "data_bytes", "payload", "bytes")
_EXT_KEYS = ("extended", "ide", "is_extended")


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).strip().lower())


def _find_col(cols_norm: dict[str, str], keys) -> str | None:
    for k in keys:
        if k in cols_norm:
            return cols_norm[k]
    # substring fallback
    for k in keys:
        for norm, orig in cols_norm.items():
            if k in norm:
                return orig
    return None


def _parse_hex_series(s: pd.Series) -> list[np.ndarray]:
    """Parse a column of hex data strings into arrays of byte values."""
    out = []
    for v in s.fillna(""):
        txt = str(v).strip()
        if not txt:
            out.append(np.zeros(0, dtype=np.uint8))
            continue
        txt = txt.replace("0x", "").replace("0X", "")
        if any(c in txt for c in (" ", ",", ";", "-", ":")):
            parts = re.split(r"[\s,;:\-]+", txt.strip())
            vals = [int(p, 16) for p in parts if p]
        else:
            vals = [int(txt[i:i + 2], 16) for i in range(0, len(txt) - len(txt) % 2, 2)]
        out.append(np.array(vals[:MAX_BYTES], dtype=np.uint8))
    return out


def _parse_id_series(s: pd.Series, base: int | None) -> np.ndarray:
    def one(v):
        txt = str(v).strip()
        if txt == "" or txt.lower() == "nan":
            return -1
        if base == 16 or txt.lower().startswith("0x"):
            return int(txt.replace("0x", "").replace("0X", ""), 16)
        if base == 10:
            return int(float(txt))
        # auto: hex if it contains a-f, else decimal
        if re.search(r"[a-fA-F]", txt):
            return int(txt, 16)
        return int(float(txt))
    return np.array([one(v) for v in s], dtype=np.int64)


def _parse_time_series(s: pd.Series) -> np.ndarray:
    """Return seconds relative to the first sample."""
    # try numeric first
    num = pd.to_numeric(s, errors="coerce")
    if num.notna().mean() > 0.9:
        t = num.to_numpy(dtype=float)
        t = t - np.nanmin(t)
        # heuristic: rescale obvious ms / us / ns to seconds
        span = np.nanmax(t)
        if span > 1e11:
            t = t / 1e9  # ns
        elif span > 1e8:
            t = t / 1e6  # us
        elif span > 1e5:
            t = t / 1e3  # ms
        return t
    # fall back to datetime parsing
    dt = pd.to_datetime(s, errors="coerce", utc=True)
    t = (dt - dt.min()).dt.total_seconds().to_numpy(dtype=float)
    return t


@dataclass
class LoadInfo:
    time_col: str | None
    id_col: str | None
    data_col: str | None
    byte_cols: list[str]
    n_frames: int
    n_ids: int
    duration_s: float


def load_can_log(
    path: str,
    *,
    id_base: int | None = None,
    sep: str | None = None,
) -> tuple[pd.DataFrame, LoadInfo]:
    """Load a CAN log CSV into the normalized frame table.

    Auto-detects common column layouts (python-can CSV logger, CSS
    Electronics / webCAN exports, generic ``ID`` + ``B0..B7`` tables).
    """
    df = pd.read_csv(path, sep=sep, engine="python")
    cols_norm = {_norm(c): c for c in df.columns}

    time_col = _find_col(cols_norm, _TIME_KEYS)
    id_col = _find_col(cols_norm, _ID_KEYS)
    data_col = _find_col(cols_norm, _DATA_KEYS)

    # per-byte columns b0..b7 / byte0.. / d0..
    byte_cols: list[str] = []
    for i in range(MAX_BYTES):
        for pat in (f"b{i}", f"byte{i}", f"d{i}", f"data{i}", f"databyte{i}"):
            if pat in cols_norm:
                byte_cols.append(cols_norm[pat])
                break

    if id_col is None:
        raise ValueError(
            f"Could not find a CAN id column. Columns present: {list(df.columns)}. "
            "Pass an explicit layout or rename the column to 'id'."
        )

    ids = _parse_id_series(df[id_col], id_base)

    if byte_cols:
        byte_mat = np.zeros((len(df), MAX_BYTES), dtype=np.uint8)
        for i, c in enumerate(byte_cols):
            col = pd.to_numeric(df[c], errors="coerce").fillna(0).to_numpy()
            byte_mat[:, i] = col.astype(np.uint8)
        dlc = np.full(len(df), len(byte_cols), dtype=np.int64)
    elif data_col is not None:
        parsed = _parse_hex_series(df[data_col])
        byte_mat = np.zeros((len(df), MAX_BYTES), dtype=np.uint8)
        dlc = np.zeros(len(df), dtype=np.int64)
        for i, arr in enumerate(parsed):
            n = min(len(arr), MAX_BYTES)
            byte_mat[i, :n] = arr[:n]
            dlc[i] = n
    else:
        raise ValueError(
            "Could not find payload data. Expected a 'data' hex column or "
            f"per-byte columns (b0..b7). Columns present: {list(df.columns)}."
        )

    if time_col is not None:
        t = _parse_time_series(df[time_col])
    else:
        # no timestamps: synthesize a uniform 1ms grid so correlation still works
        t = np.arange(len(df), dtype=float) * 1e-3

    out = pd.DataFrame({"t": t, "id": ids, "dlc": dlc})
    for i in range(MAX_BYTES):
        out[f"b{i}"] = byte_mat[:, i]

    out = out[out["id"] >= 0].reset_index(drop=True)
    order = np.argsort(out["t"].to_numpy(), kind="stable")
    out = out.iloc[order].reset_index(drop=True)

    info = LoadInfo(
        time_col=time_col,
        id_col=id_col,
        data_col=data_col,
        byte_cols=byte_cols,
        n_frames=len(out),
        n_ids=int(out["id"].nunique()),
        duration_s=float(out["t"].max() - out["t"].min()) if len(out) else 0.0,
    )
    return out, info


def byte_matrix(frames: pd.DataFrame) -> np.ndarray:
    """Return the (n, 8) uint8 payload matrix of a (sub)frame table."""
    return frames[[f"b{i}" for i in range(MAX_BYTES)]].to_numpy(dtype=np.uint8)


# --------------------------------------------------------------------------- #
# Bit-field extraction (vectorized)
# --------------------------------------------------------------------------- #

def extract_field(
    byte_mat: np.ndarray,
    start_bit: int,
    length: int,
    byte_order: str = "little_endian",
    signed: bool = False,
) -> np.ndarray:
    """Extract a signal's raw integer value from every frame.

    Parameters
    ----------
    byte_mat : (n, 8) uint8 array
    start_bit, length : int
    byte_order : ``"little_endian"`` (Intel) or ``"big_endian"`` (Motorola,
        byte-aligned only)
    signed : two's-complement interpretation if True
    """
    n = byte_mat.shape[0]
    if length <= 0 or length > 32:
        raise ValueError("length must be in 1..32")

    if byte_order == "little_endian":
        weights = (1 << (8 * np.arange(MAX_BYTES, dtype=np.uint64)))
        packed = (byte_mat.astype(np.uint64) * weights).sum(axis=1, dtype=np.uint64)
        raw = (packed >> np.uint64(start_bit)) & np.uint64((1 << length) - 1)
        raw = raw.astype(np.int64)
    elif byte_order == "big_endian":
        if start_bit % 8 != 0 or length % 8 != 0:
            raise ValueError("big_endian extraction is byte-aligned only")
        b0 = start_bit // 8
        nb = length // 8
        if b0 + nb > MAX_BYTES:
            raise ValueError("field runs past end of payload")
        raw = np.zeros(n, dtype=np.uint64)
        for j in range(nb):
            raw = (raw << np.uint64(8)) | byte_mat[:, b0 + j].astype(np.uint64)
        raw = raw.astype(np.int64)
    else:
        raise ValueError(f"unknown byte_order {byte_order!r}")

    if signed and length < 64:
        sign_bit = 1 << (length - 1)
        raw = np.where(raw & sign_bit, raw - (1 << length), raw)
    return raw.astype(np.float64)


# --------------------------------------------------------------------------- #
# Resampling helpers
# --------------------------------------------------------------------------- #

def zoh_resample(t: np.ndarray, v: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Zero-order-hold resample (previous value) onto ``grid``.

    CAN signals are piecewise constant between frames, so ZOH is the
    faithful reconstruction. Points before the first sample take the first
    value.
    """
    if len(t) == 0:
        return np.full_like(grid, np.nan, dtype=float)
    idx = np.searchsorted(t, grid, side="right") - 1
    idx = np.clip(idx, 0, len(v) - 1)
    return v[idx]


def common_grid(t_a: np.ndarray, t_b: np.ndarray, hz: float = 20.0) -> np.ndarray:
    """A uniform time grid over the overlap of two signals."""
    lo = max(t_a.min(), t_b.min())
    hi = min(t_a.max(), t_b.max())
    if hi <= lo:
        return np.array([])
    n = max(2, int((hi - lo) * hz))
    return np.linspace(lo, hi, n)


def robust_corr(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation, NaN-safe, returns 0 for degenerate inputs."""
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 3:
        return 0.0
    a, b = a[m], b[m]
    if a.std() < 1e-12 or b.std() < 1e-12:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def best_lag_corr(
    t_sig: np.ndarray,
    v_sig: np.ndarray,
    t_ref: np.ndarray,
    v_ref: np.ndarray,
    *,
    hz: float = 20.0,
    max_lag: float | None = None,
    center_lag: float = 0.0,
) -> tuple[float, float]:
    """Best Pearson correlation between a signal and reference over a time-lag
    search. Returns ``(signed_corr_at_best_abs, best_lag_seconds)``.

    A positive lag means the reference lags the signal (reference shifted
    later in time). Handles the clock offset between an independently
    recorded reference (OCR / slider) and the CAN trace.
    """
    if len(t_sig) < 3 or len(t_ref) < 3:
        return 0.0, 0.0
    duration = min(t_sig.max() - t_sig.min(), t_ref.max() - t_ref.min())
    if max_lag is None:
        max_lag = min(5.0, 0.25 * duration)
    grid = common_grid(t_sig, t_ref, hz=hz)
    if grid.size < 4:
        return 0.0, 0.0
    sig_g = zoh_resample(t_sig, v_sig, grid)
    k = max(0, int(round(max_lag * hz)))
    best_c, best_lag = 0.0, center_lag
    center_k = int(round(center_lag * hz))
    for dk in range(center_k - k, center_k + k + 1):
        ref_g = zoh_resample(t_ref, v_ref, grid + dk / hz)
        c = robust_corr(sig_g, ref_g)
        if abs(c) > abs(best_c):
            best_c, best_lag = c, dk / hz
    return best_c, best_lag
