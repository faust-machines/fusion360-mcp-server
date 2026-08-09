#!/usr/bin/env python
# /// script
# requires-python = ">=3.10"
# dependencies = ["flask"]
# ///
"""A tiny web app to capture a human-provided reference signal.

Reference method #3 from the workflow: when you have no digital reference but
can *observe* the quantity (turning a gauge, watching a needle), open this
app, start your CAN capture, and mirror the real value with the slider. Two
kinds of input are recorded, both timestamped on the wall clock:

* **Live tracking** — drag the slider to follow the real value (first pass).
* **Anchors** — set the slider to a known value and click "Sample 2s" to hold
  it steady while the CAN capture sees that exact level (second pass; far less
  noisy than hand-tracking).

Output is a ``t,value`` CSV. Because timestamps are wall-clock epoch seconds,
correlate.py / bitsearch.py recover the offset to the CAN log automatically
via their lag search — just start both captures at roughly the same moment.

Usage
-----
    uv run reference_slider.py --out ref_human.csv \
        --min 0 --max 100 --unit % --label "gauge position"
Then open http://127.0.0.1:5001 and press "Finish & Save" when done.
"""

# The HTML/JS UI lives in one big template string; long lines there are fine.
# ruff: noqa: E501
from __future__ import annotations

import argparse
import time

from flask import Flask, jsonify, request

app = Flask(__name__)
STATE = {"points": [], "cfg": {}}

PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>Reference capture — {label}</title>
<style>
 body{{font-family:system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px}}
 h1{{font-size:1.2rem}} .val{{font-size:2.4rem;font-weight:700;text-align:center}}
 input[type=range]{{width:100%}} button{{padding:10px 14px;margin:4px;font-size:1rem}}
 .row{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
 #count{{color:#555}} .rec{{color:#c0392b}}
</style></head><body>
<h1>Reference capture — {label} [{unit}]</h1>
<div class=val><span id=cur>{mid}</span> {unit}</div>
<input id=sld type=range min="{min}" max="{max}" step="{step}" value="{mid}">
<div class=row>
 <button id=live>▶ Start live tracking</button>
 <button id=sample>● Sample 2s at current value</button>
 <button id=save>■ Finish &amp; Save</button>
</div>
<p id=count>0 points recorded</p>
<p id=msg></p>
<script>
const sld=document.getElementById('sld'),cur=document.getElementById('cur');
const cnt=document.getElementById('count'),msg=document.getElementById('msg');
let live=false,liveTimer=null,n=0;
sld.oninput=()=>cur.textContent=sld.value;
function post(u,b){{return fetch(u,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(b||{{}})}}).then(r=>r.json());}}
function tick(){{post('/point',{{value:parseFloat(sld.value)}}).then(d=>{{n=d.n;cnt.textContent=n+' points recorded';}});}}
document.getElementById('live').onclick=e=>{{
 live=!live;
 if(live){{e.target.textContent='⏸ Stop live tracking';e.target.classList.add('rec');liveTimer=setInterval(tick,50);}}
 else{{e.target.textContent='▶ Start live tracking';e.target.classList.remove('rec');clearInterval(liveTimer);}}
}};
document.getElementById('sample').onclick=()=>{{
 msg.textContent='sampling 2s at '+sld.value+' …';
 let end=Date.now()+2000,s=setInterval(()=>{{
   if(Date.now()>=end){{clearInterval(s);msg.textContent='anchor recorded at '+sld.value;return;}}
   tick();
 }},50);
}};
document.getElementById('save').onclick=()=>post('/save',{{}}).then(d=>{{msg.textContent='saved '+d.n+' points to '+d.path;}});
</script></body></html>"""


@app.route("/")
def index():
    c = STATE["cfg"]
    mid = (c["min"] + c["max"]) / 2
    step = (c["max"] - c["min"]) / 1000 or 1
    return PAGE.format(label=c["label"], unit=c["unit"], min=c["min"],
                       max=c["max"], mid=round(mid, 3), step=step)


@app.route("/point", methods=["POST"])
def point():
    v = float(request.get_json(force=True)["value"])
    STATE["points"].append((time.time(), v))
    return jsonify(n=len(STATE["points"]))


@app.route("/save", methods=["POST"])
def save():
    path = STATE["cfg"]["out"]
    pts = STATE["points"]
    with open(path, "w") as f:
        f.write("t,value\n")
        for t, v in pts:
            f.write(f"{t:.3f},{v}\n")
    print(f"[reference_slider] saved {len(pts)} points to {path}")
    return jsonify(n=len(pts), path=path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="ref_human.csv")
    ap.add_argument("--min", type=float, default=0.0)
    ap.add_argument("--max", type=float, default=100.0)
    ap.add_argument("--unit", default="%")
    ap.add_argument("--label", default="reference")
    ap.add_argument("--port", type=int, default=5001)
    args = ap.parse_args()
    STATE["cfg"] = {"out": args.out, "min": args.min, "max": args.max,
                    "unit": args.unit, "label": args.label}
    print(f"[reference_slider] open http://127.0.0.1:{args.port}  "
          f"-> writes {args.out} on 'Finish & Save'")
    app.run(host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
