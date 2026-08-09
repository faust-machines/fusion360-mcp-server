---
name: can-reverse-engineering
description: >-
  Reverse-engineer proprietary CAN bus signals into a DBC file with the help
  of a correlated reference signal. Use when the user wants to decode unknown
  CAN data from a vehicle, truck, ship, or machine — turning raw CAN frames
  (CAN IDs + data bytes) into physical values like speed, RPM, temperature, or
  gauge position. Triggers on: "reverse engineer CAN", "decode CAN signal",
  "find the speed/rpm signal in this CAN log", "build a DBC from a trace",
  "what byte is X in this CAN data", CAN CSV traces, OBD2 references, or the
  CANsub / CANmod / SavvyCAN / python-can toolchains. Also covers capturing the
  reference signal (OBD2 from the same log, OCR of a dashboard video, or live
  human slider input).
---

# CAN bus reverse engineering

Turn an unknown ("proprietary") CAN signal into decode rules — start bit,
length, endianness, scale, offset — and write a DBC file. You cannot do this
from raw frames alone: you need a **reference signal** that closely tracks the
target and is recorded on the **same time base** as the CAN trace. Given that,
the scripts here survey the trace, rank candidate fields by correlation, search
bit fields, fit scale/offset, write the DBC, and plot the result for
verification.

**This is not magic.** Without a clean reference signal the exercise is
impossible — say so plainly instead of guessing. You are limited to the
signals you can get a good reference for.

## When to use / not use

Use it when the user has (or can record) a CAN trace **and** a reference for
the signal they want. If they only have raw frames and no way to obtain a
reference, explain the reference requirement first (see step 1) — don't fabricate
a decoding.

## The toolkit

All scripts are standalone and run with `uv run` (dependencies are declared
inline; no project setup needed). They live in `scripts/`:

| Script | Role |
|--------|------|
| `survey.py` | Compact per-id / per-byte map of a trace (timing, counters, constants, checksum-like bytes). |
| `extract_obd2.py` | Decode an OBD2 (mode-01) PID from the same log into a reference CSV. |
| `video_to_timeseries.py` | OCR a numeric dashboard readout from a video into a reference CSV. |
| `reference_slider.py` | Web app to capture a human-provided reference (live tracking + steady anchors). |
| `correlate.py` | Rank candidate byte-aligned fields across all ids against the reference. |
| `bitsearch.py` | Exhaustive bit-field search + scale/offset fit for one id; prints a **recommended** field. |
| `build_dbc.py` | Write a DBC from solved parameters (single signal via CLI, or many via a spec JSON). |
| `plot_signal.py` | Decode via the DBC and overlay against the reference (R² + scatter) to verify. |
| `make_synthetic.py` | Generate a synthetic trace with known encodings — for demos and the self-test. |

`scripts/canlog.py` and `scripts/refsig.py` are shared libraries (loaders, bit
extraction, lag-aware correlation). `reference/obd2.dbc` is a ready OBD2 DBC for
external tools (webCAN, cantools); `reference/` also holds primers on CAN signal
encoding and CSV formats — read them if a trace's layout or a big-endian field
is giving trouble.

## Workflow

### 1. Establish the reference signal

Ask what signal to reverse-engineer and which reference is available. In order
of preference:

1. **OBD2 from the same log** (best). If the trace contains OBD2 responses
   (reply id `0x7E8`..`0x7EF`), extract the matching PID:
   ```
   uv run scripts/extract_obd2.py LOG.csv --list          # see available PIDs
   uv run scripts/extract_obd2.py LOG.csv --pid 0x0D --out ref_speed.csv
   ```
   This shares the log's exact clock, so alignment is trivial.

2. **Vision / OCR** (e.g. electric cars with no OBD2). Film the dashboard,
   then:
   ```
   uv run scripts/video_to_timeseries.py drive.mp4 --preview           # find the ROI
   uv run scripts/video_to_timeseries.py drive.mp4 --roi X,Y,W,H \
       --out ref_speed.csv --sample-fps 5 --max-value 260
   ```
   Review the printed fail rate; `--max-value`/`--min-value` clamp misreads.

3. **Human input** (last resort). Observe the quantity and mirror it:
   ```
   uv run scripts/reference_slider.py --out ref_human.csv \
       --min 0 --max 100 --unit % --label "gauge position"
   ```
   Start the CAN capture and the slider together. Do a **live-tracking** pass,
   then several steady **2s anchors** at known values (much less noisy).

The reference is a `t,value` CSV. Time bases need not be perfectly aligned —
`correlate.py`/`bitsearch.py` recover a constant lag automatically. But the two
recordings must overlap in wall-clock time.

### 2. Survey the trace

```
uv run scripts/survey.py LOG.csv --json survey.json
```
Note which ids update at a plausible rate, and which bytes are **constants**,
**counters**, or **high-entropy** (checksum/random). The target lives in bytes
that *vary*. (An expected quirk: the low byte of a fast signal often shows as
`high_entropy` — that's fine, it's still part of the signal.)

### 3. Rank candidate fields

```
uv run scripts/correlate.py LOG.csv --ref ref_speed.csv --json cands.json
```
This scores byte-aligned candidates across every id by best time-lagged |r|.
The top rows point you at the id (and rough region) carrying the signal.

### 4. Bit-search the winning id

```
uv run scripts/bitsearch.py LOG.csv --ref ref_speed.csv --id 0x3E9 \
    --name vehicle_speed --json result.json
```
It tries every little-endian field plus byte-aligned big-endian, signed and
unsigned, fits `physical = raw*scale + offset`, and prints a **recommended**
field: the maximal run of varying bytes read in whichever endianness correlates
best.

**Choosing the final field.** Correlation and R² often tie across several
lengths (a quantized reference can't reveal unexercised high bits). Resolve it
with priors, in this order:
- Prefer the **recommended** byte-aligned field — it's usually right.
- Prefer a **round scale** (`0.01`, `0.25`, `0.1`, `1`…) and an **offset near 0**
  (or a sensible one like `-40` for temperatures).
- Prefer **byte-aligned** start/length unless the data clearly says otherwise.
- Watch out for candidates that swallow a constant byte (huge offset or a
  scale like `x/256`) — reject them.

### 5. Write and verify the DBC

```
uv run scripts/build_dbc.py --out decoding_output/<app>/<signal>/signal.dbc \
    --id 0x3E9 --name vehicle_speed --start-bit 0 --length 16 \
    --byte-order little_endian --scale 0.01 --offset 0 --unit km/h

uv run scripts/plot_signal.py LOG.csv \
    --dbc decoding_output/<app>/<signal>/signal.dbc --ref ref_speed.csv \
    --out decoding_output/<app>/<signal>/verify.png
```
A tight overlay and near-1 R² in the plot confirms the signal. If R² is poor,
go back to step 4 with the next candidate.

### 6. Organize outputs

Write results under `decoding_output/<application>/<signal>/`, where
`<application>` comes from the user's context (e.g. `mercedes_e350`) and
`<signal>` is the signal name. Put the DBC, the verification plot, and the
bitsearch/correlation JSON there.

## Extending to related signals

Once one signal is solved, adjacent varying bytes often encode sibling signals
the same way (the video's eight-gauge case). Hypothesize their fields from the
first result, assemble a **spec JSON** with all messages/signals, and build one
combined DBC:
```
uv run scripts/build_dbc.py --spec signals.json --out combined.dbc
```
See `build_dbc.py`'s docstring for the spec schema. Verify each with
`plot_signal.py --name <signal>`.

## Conventions & limits

- **Units:** OBD2 speed is km/h, RPM is rpm, temps °C. DBC scale/offset map raw
  → physical as `raw*scale + offset`.
- **Endianness:** big-endian fields are supported **byte-aligned only** (the
  common case). For a non-aligned Motorola field, decode manually and note it.
- **Self-test:** to sanity-check the whole chain end-to-end without hardware:
  ```
  uv run scripts/make_synthetic.py --out synth
  uv run scripts/extract_obd2.py synth.csv --pid 0x0D --out ref.csv
  uv run scripts/bitsearch.py synth.csv --ref ref.csv --id 0x3E9 --name speed
  ```
  The recommended field should be `start=0 length=16 little_endian` with
  `scale≈0.01` (ground truth in `synth.groundtruth.json`).
- Keep context small: pass the JSON summaries around, not raw frames.
