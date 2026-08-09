# CAN log CSV formats

`canlog.load_can_log` auto-detects the common layouts. It needs three things:
a **time**, a **CAN id**, and the **payload** (either one hex column or per-byte
columns). Column names are matched case-insensitively and ignoring punctuation.

## Recognized columns

- **Time:** `t`, `time`, `timestamp`, `time_s`, `abs time`, `datetime`, … .
  Numeric values are taken as seconds and re-zeroed to the first frame; obvious
  ms/µs/ns scales are auto-corrected. Datetime strings are parsed. If there is
  no time column, a uniform 1 ms grid is synthesized (correlation still works,
  but real timestamps are strongly preferred).
- **CAN id:** `id`, `can_id`, `arbitration_id`, `identifier`, `msg_id`, … .
  Hex (`0x1AB` or `1AB` with letters) and decimal are both accepted; force with
  `--id-base 16` / `--id-base 10`.
- **Payload:** either
  - a single hex column (`data`, `payload`, `bytes`, …) like `0A1B2C…` or
    `0A 1B 2C …` (separators optional), or
  - per-byte columns `b0..b7` / `byte0..byte7` / `d0..d7` / `data0..`.

## Examples

**python-can CSV logger**
```
timestamp,arbitration_id,extended,remote,error,dlc,data
1690000000.000,1001,0,0,0,8,4c215a000000de00
```

**Generic / CSS Electronics / webCAN style**
```
Timestamp,ID,Data
0.0000,0x3E9,4C 21 5A 00 00 00 DE 00
```

**Per-byte columns**
```
Time,CAN ID,B0,B1,B2,B3,B4,B5,B6,B7
0.000,3E9,76,33,90,0,0,0,222,0
```

## Getting both signals into one file

The whole method needs the proprietary data **and** the reference on the same
time base. The clean way is one time-sorted trace containing both:

- If the car exposes proprietary data on the OBD2 connector, record everything
  there — one file, done.
- If not (common on modern cars that gate the OBD2 port), record OBD2 on one
  channel and tap the proprietary bus contactlessly on another, and export a
  single merged, time-sorted CSV (e.g. from webCAN). Recording them into two
  separate files defeats the purpose — the timestamps must share a clock, or at
  least overlap so the lag search can align them.

If you must use two files, keep them running simultaneously and pass the
reference separately with `--ref`; the correlation lag search tolerates a
constant offset but not a missing overlap.
