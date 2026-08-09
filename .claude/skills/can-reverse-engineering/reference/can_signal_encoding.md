# CAN signal encoding — a short primer

A CAN **frame** carries a CAN **ID** (11- or 29-bit) and up to 8 data bytes
(64 for CAN FD). A **signal** is a field of bits inside those bytes that encodes
one physical quantity. Decoding a signal means knowing five things:

1. **start bit** — where the field begins
2. **length** — how many bits
3. **byte order** — little-endian (Intel) or big-endian (Motorola)
4. **signedness** — unsigned or two's-complement
5. **scale & offset** — `physical = raw * scale + offset`

## Byte / bit order

- **Little-endian (Intel):** byte 0 is the least significant. The raw value is
  `int.from_bytes(data, "little") >> start_bit & mask`. `start_bit` is the LSB
  position. This is what `canlog.extract_field` uses directly.
- **Big-endian (Motorola):** the first byte is the most significant. In DBC
  files the start bit is numbered in a "sawtooth" scheme. This skill only
  searches/emits **byte-aligned** big-endian fields: a field whose most
  significant byte is byte `b0` has DBC start bit `b0*8 + 7` (see
  `build_dbc.dbc_start_bit`). `make_synthetic.py` round-trips this through
  cantools so the emitted DBC decodes identically to the search.

Reading the wrong endianness byte-swaps the value and destroys the correlation,
so correlation itself tells you which is right for a multi-byte field.

## Scale & offset

Raw integers are counts; physical values need a linear map. Common automotive
examples:

| Signal | scale | offset | note |
|--------|-------|--------|------|
| Vehicle speed | 1, 0.01, 0.0625 | 0 | resolution varies by OEM |
| Engine RPM | 0.25, 0.125 | 0 | OBD2 uses 0.25 |
| Coolant/oil temp | 1 | −40 | the classic `A − 40` |
| Throttle / load / fuel | 100/255 | 0 | percentage of a byte |

`bitsearch.py` fits scale and offset by least squares against the reference.
Prefer results whose scale is a **round number** and whose offset is **near 0**
(or a sensible constant like −40): the fit will land close, and the round value
is almost always the true one.

## Bytes that are NOT signals

Real payloads mix in fields that will mislead a naive correlation:

- **Counters / rolling counters** — a byte that increments by a constant step
  each frame (wraps at 256). `survey.py` flags these as `counter`.
- **Checksums / CRCs** — high-entropy bytes that look random. Flagged as
  `high_entropy`. (Caution: the low byte of a fast real signal can also look
  high-entropy — check whether neighboring bytes form a coherent field.)
- **Constants / padding** — never change. Flagged as `constant`. A candidate
  field that includes a constant byte inflates its length and produces a
  tell-tale huge offset or an `x/256`-style scale; reject those.

## Multiplexing (OBD2)

OBD2 responses reuse one CAN ID (`0x7E8`) for many PIDs by putting the PID
number in byte 2 — a form of multiplexing. `extract_obd2.py` handles this
directly for mode-01 PIDs; `reference/obd2.dbc` expresses it with DBC
`SG_MUL_VAL_` extended multiplexing for external tools.
