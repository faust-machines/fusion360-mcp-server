# can-reverse-engineering (Claude Code skill)

An AI-assisted workflow for reverse-engineering proprietary CAN bus signals
into a DBC file, using a correlated **reference signal** (OBD2, an OCR'd
dashboard video, or live human input). Inspired by the CANsub/CANmod +
python-can workflow, but hardware-agnostic: it works on any CAN log CSV.

The idea: an LLM alone can't decode raw CAN frames, and shouldn't try to read
millions of them. Instead, deterministic Python scripts do the statistics
(survey → correlate → bit-search → scale/offset fit → DBC → verify) and hand
Claude compact summaries to reason over.

## Quick start (no hardware — uses synthetic data)

```bash
cd .claude/skills/can-reverse-engineering/scripts

# 1. generate a trace with known encodings + OBD2 references
uv run make_synthetic.py --out synth

# 2. pull the OBD2 speed reference out of the same log
uv run extract_obd2.py synth.csv --pid 0x0D --out ref_speed.csv

# 3. find which id/field carries speed
uv run correlate.py synth.csv --ref ref_speed.csv

# 4. bit-search + fit scale/offset (recommends start=0 len=16 LE scale≈0.01)
uv run bitsearch.py synth.csv --ref ref_speed.csv --id 0x3E9 --name vehicle_speed

# 5. write and verify the DBC
uv run build_dbc.py --out speed.dbc --id 0x3E9 --name vehicle_speed \
    --start-bit 0 --length 16 --byte-order little_endian --scale 0.01 --unit km/h
uv run plot_signal.py synth.csv --dbc speed.dbc --ref ref_speed.csv --out speed.png
```

Ground truth for the synthetic trace is in `synth.groundtruth.json` (a
little-endian speed signal and a big-endian RPM signal).

## What's here

```
SKILL.md                  # instructions Claude follows (the entry point)
scripts/
  canlog.py               # shared: CSV loading, bit extraction, lag correlation
  refsig.py               # shared: reference-signal CSV loader
  survey.py               # per-id/per-byte map of a trace
  extract_obd2.py         # OBD2 mode-01 PID -> reference CSV
  video_to_timeseries.py  # OCR a dashboard video -> reference CSV
  reference_slider.py     # web app: human-input reference capture
  correlate.py            # rank candidate fields vs reference
  bitsearch.py            # exhaustive bit-field search + scale/offset fit
  build_dbc.py            # write a DBC (single signal or spec JSON)
  plot_signal.py          # decode via DBC and verify against reference
  make_synthetic.py       # synthetic trace generator / self-test
reference/
  obd2.dbc                # OBD2 mode-01 DBC subset (for webCAN/cantools)
  can_signal_encoding.md  # primer: start bit, endianness, scale/offset, traps
  csv_formats.md          # supported log CSV layouts
```

## Requirements

Just [`uv`](https://docs.astral.sh/uv/). Every script declares its own
dependencies inline (PEP 723), so `uv run <script>` installs what it needs on
first use — numpy/pandas for the core, cantools for DBC I/O, matplotlib for
plots, opencv/easyocr for video OCR, flask for the slider app. The OCR backend
(`easyocr`, or `tesseract` via `pytesseract`) downloads a model on first run.

## Notes

- Big-endian fields are searched/emitted **byte-aligned only** — the common
  case. See `reference/can_signal_encoding.md`.
- This is an aid, not magic: no clean reference signal → no reverse
  engineering. The scripts are honest about correlation quality (R²), so a bad
  match is visible rather than hidden.
