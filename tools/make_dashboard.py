#!/usr/bin/env python3
"""Generate the live progress dashboard (self-contained HTML fragment).

Reads output/state.db + output/logs/events.jsonl and writes a static page:
completion ring, stat tiles, throughput chart, source-domain and review-reason
bars, and a wall of recently accepted logos (inlined as data URIs).

Usage: python tools/make_dashboard.py [out_path]
"""
import base64
import io
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config          # noqa: E402
from pipeline.state import State     # noqa: E402

# validated palettes (dataviz six-checks: light on #fff, dark on #101B1E)
LIGHT = {"good": "#0B8A62", "warn": "#B7791F", "crit": "#B42318", "neutral": "#98A2A8"}
DARK = {"good": "#2FA383", "warn": "#BE8514", "crit": "#E5484D", "neutral": "#6E7F86"}

CSS = """
:root {
  --surface: #FBFDFC; --panel: #FFFFFF; --ink: #16282B; --ink-2: #4E6166;
  --ink-3: #7C8D91; --line: #E2EAEA; --accent: #0B8A62;
  --good: #0B8A62; --warn: #B7791F; --crit: #B42318; --neutral: #98A2A8;
  --good-soft: #E3F3EC; --warn-soft: #F7EEDD; --crit-soft: #F8E7E4; --neutral-soft: #EEF1F2;
  --tile: #F4F7F7;
}
@media (prefers-color-scheme: dark) {
  :root {
    --surface: #0C1517; --panel: #101B1E; --ink: #E6EFEF; --ink-2: #A7B8BB;
    --ink-3: #71838A; --line: #223136; --accent: #2FA383;
    --good: #2FA383; --warn: #BE8514; --crit: #E5484D; --neutral: #6E7F86;
    --good-soft: #14312A; --warn-soft: #33290F; --crit-soft: #3A1A1C; --neutral-soft: #1B272B;
    --tile: #16232699;
  }
}
:root[data-theme="dark"] {
  --surface: #0C1517; --panel: #101B1E; --ink: #E6EFEF; --ink-2: #A7B8BB;
  --ink-3: #71838A; --line: #223136; --accent: #2FA383;
  --good: #2FA383; --warn: #BE8514; --crit: #E5484D; --neutral: #6E7F86;
  --good-soft: #14312A; --warn-soft: #33290F; --crit-soft: #3A1A1C; --neutral-soft: #1B272B;
  --tile: #16232699;
}
:root[data-theme="light"] {
  --surface: #FBFDFC; --panel: #FFFFFF; --ink: #16282B; --ink-2: #4E6166;
  --ink-3: #7C8D91; --line: #E2EAEA; --accent: #0B8A62;
  --good: #0B8A62; --warn: #B7791F; --crit: #B42318; --neutral: #98A2A8;
  --good-soft: #E3F3EC; --warn-soft: #F7EEDD; --crit-soft: #F8E7E4; --neutral-soft: #EEF1F2;
  --tile: #F4F7F7;
}
* { box-sizing: border-box; }
body {
  background: var(--surface); color: var(--ink); margin: 0;
  font: 15px/1.5 -apple-system, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
}
.wrap { max-width: 1060px; margin: 0 auto; padding: 28px 20px 60px; }
.eyebrow { font-size: 11px; letter-spacing: .14em; font-weight: 700; color: var(--accent);
  text-transform: uppercase; display:flex; align-items:center; gap:8px; }
.pulse { width:8px; height:8px; border-radius:50%; background: var(--accent); }
@media (prefers-reduced-motion: no-preference) {
  .pulse { animation: pulse 1.6s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:.25 } }
}
h1 { font-size: 26px; margin: 6px 0 2px; letter-spacing: -.01em; text-wrap: balance; }
.sub { color: var(--ink-2); margin: 0 0 6px; }
.asof { color: var(--ink-3); font-size: 12.5px; }
.grid { display: grid; gap: 14px; }
.hero { grid-template-columns: 300px 1fr; margin-top: 22px; }
@media (max-width: 760px) { .hero { grid-template-columns: 1fr; } }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 18px; }
.tiles { grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
.tile { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; }
.tile .k { font-size: 11px; letter-spacing: .1em; text-transform: uppercase; font-weight: 700; color: var(--ink-3); }
.tile .v { font-size: 28px; font-weight: 750; letter-spacing: -.02em; font-variant-numeric: tabular-nums; margin-top: 2px; }
.tile .d { font-size: 12.5px; color: var(--ink-2); margin-top: 1px; }
.tile.good .v { color: var(--good); } .tile.warn .v { color: var(--warn); }
.tile.crit .v { color: var(--crit); } .tile.neutral .v { color: var(--ink-2); }
.ringbox { display:flex; flex-direction:column; align-items:center; gap: 12px; }
.legend { display:flex; flex-wrap:wrap; gap: 8px 14px; justify-content:center; font-size: 12.5px; color: var(--ink-2); }
.legend .sw { display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:5px; vertical-align: -1px; }
h2 { font-size: 15px; margin: 0 0 4px; }
.note { color: var(--ink-3); font-size: 12.5px; margin: 0 0 12px; }
.two { grid-template-columns: 1fr 1fr; margin-top: 14px; }
@media (max-width: 760px) { .two { grid-template-columns: 1fr; } }
.chart { width: 100%; height: auto; display: block; }
.bar-row:hover rect.bar { opacity: .82; }
.logo-wall { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 10px; }
.logo-tile { background: #FFFFFF; border: 1px solid var(--line); border-radius: 8px; padding: 8px;
  display:flex; flex-direction:column; align-items:center; gap:6px; }
.logo-tile img { width: 100%; aspect-ratio: 1; object-fit: contain; }
.logo-tile .id { font-size: 10.5px; color: var(--ink-3); font-variant-numeric: tabular-nums;
  max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
table.statuses { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
table.statuses td, table.statuses th { padding: 6px 8px; border-top: 1px solid var(--line); text-align: left; font-size: 13.5px; }
table.statuses th { color: var(--ink-3); font-size: 11px; text-transform: uppercase; letter-spacing: .08em; border-top: none; }
details { margin-top: 10px; } summary { cursor: pointer; color: var(--ink-2); font-size: 13px; }
.footer { margin-top: 26px; color: var(--ink-3); font-size: 12.5px; }
"""


def ring_svg(counts, total):
    """Completion ring: one stroke segment per status, 2px gaps, % in center."""
    order = [("completed", "var(--good)"), ("manual_review", "var(--warn)"),
             ("failed", "var(--crit)"), ("pending", "var(--neutral)")]
    r, cx, cy, sw = 84, 110, 110, 22
    circ = 2 * math.pi * r
    gap = 2.5
    segs, offset = [], 0.0
    done = counts["completed"] + counts["manual_review"] + counts["failed"]
    for key, color in order:
        n = counts.get(key, 0) + (counts.get("processing", 0) if key == "pending" else 0)
        if n <= 0:
            continue
        frac = n / total
        length = max(frac * circ - gap, 0.8)
        segs.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" '
            f'stroke-width="{sw}" stroke-linecap="butt" '
            f'stroke-dasharray="{length:.2f} {circ - length:.2f}" '
            f'stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 {cx} {cy})">'
            f'<title>{key.replace("_", " ")}: {n:,} ({n/total*100:.1f}%)</title></circle>')
        offset += frac * circ
    pct = done / total * 100
    return f'''<svg class="chart" viewBox="0 0 220 220" role="img"
  aria-label="Progress ring: {pct:.1f} percent of rows processed" style="max-width:230px">
  <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="var(--line)" stroke-width="{sw}"/>
  {''.join(segs)}
  <text x="{cx}" y="{cy - 4}" text-anchor="middle" font-size="38" font-weight="750"
    fill="var(--ink)" style="font-variant-numeric:tabular-nums">{pct:.0f}%</text>
  <text x="{cx}" y="{cy + 22}" text-anchor="middle" font-size="12" fill="var(--ink-2)">processed</text>
</svg>'''


def area_chart(buckets):
    """Throughput: rows processed per 10-minute bucket."""
    if len(buckets) < 2:
        return '<p class="note">Not enough history yet.</p>'
    w, h, pad_l, pad_b, pad_t = 640, 170, 34, 22, 12
    xs = list(range(len(buckets)))
    ys = [n for _, n in buckets]
    ymax = max(max(ys), 1)
    def X(i): return pad_l + i / max(len(xs) - 1, 1) * (w - pad_l - 8)
    def Y(v): return pad_t + (1 - v / ymax) * (h - pad_t - pad_b)
    pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in zip(xs, ys))
    area = f"{pad_l},{Y(0):.1f} {pts} {X(len(xs)-1):.1f},{Y(0):.1f}"
    gridlines = "".join(
        f'<line x1="{pad_l}" y1="{Y(ymax*f):.1f}" x2="{w-8}" y2="{Y(ymax*f):.1f}" stroke="var(--line)" stroke-width="1"/>'
        f'<text x="{pad_l-6}" y="{Y(ymax*f)+4:.1f}" text-anchor="end" font-size="10" fill="var(--ink-3)" style="font-variant-numeric:tabular-nums">{int(ymax*f)}</text>'
        for f in (0.5, 1.0))
    hover = "".join(
        f'<g class="bar-row"><rect x="{X(i)-4:.1f}" y="{pad_t}" width="8" height="{h-pad_t-pad_b}" fill="transparent">'
        f'<title>{t}: {v} rows</title></rect></g>'
        for i, (t, v) in enumerate(buckets))
    lx, ly = X(len(xs) - 1), Y(ys[-1])
    labels = (f'<text x="{pad_l}" y="{h-6}" font-size="10" fill="var(--ink-3)">{buckets[0][0]}</text>'
              f'<text x="{w-8}" y="{h-6}" text-anchor="end" font-size="10" fill="var(--ink-3)">{buckets[-1][0]}</text>')
    return f'''<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="Rows processed per 10 minutes">
  {gridlines}
  <polygon points="{area}" fill="var(--accent)" opacity="0.14"/>
  <polyline points="{pts}" fill="none" stroke="var(--accent)" stroke-width="2"/>
  <circle cx="{lx:.1f}" cy="{ly:.1f}" r="4" fill="var(--accent)" stroke="var(--panel)" stroke-width="2"/>
  {labels}{hover}
</svg>'''


def bars(items, color_var, total_label="completed"):
    if not items:
        return '<p class="note">No data yet.</p>'
    w, rh, gap_y, pad_l = 640, 22, 8, 4
    vmax = max(n for _, n in items)
    h = len(items) * (rh + gap_y) + 6
    rows = []
    for i, (label, n) in enumerate(items):
        y = i * (rh + gap_y)
        bw = max(n / vmax * (w - 275), 3)
        lbl = (label[:34] + "…") if len(label) > 35 else label
        rows.append(
            f'<g class="bar-row"><text x="{pad_l}" y="{y+15}" font-size="12" fill="var(--ink-2)">{lbl}</text>'
            f'<rect class="bar" x="216" y="{y+2}" width="{bw:.1f}" height="{rh-6}" rx="4" fill="{color_var}">'
            f'<title>{label}: {n} {total_label}</title></rect>'
            f'<text x="{216+bw+7:.1f}" y="{y+15}" font-size="12" font-weight="650" fill="var(--ink)" '
            f'style="font-variant-numeric:tabular-nums">{n}</text></g>')
    return f'<svg class="chart" viewBox="0 0 {w} {h}" role="img">{"".join(rows)}</svg>'


def logo_thumb_data_uri(path):
    from PIL import Image
    img = Image.open(path).convert("RGBA")
    img.thumbnail((96, 96))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def main(out_path):
    st = State()
    counts = st.counts()
    rows = st.all_rows()
    total = counts["total"]
    done = counts["completed"] + counts["manual_review"] + counts["failed"]
    remaining = counts["pending"] + counts["processing"]

    # throughput buckets from the event log
    events = []
    ev_path = config.LOGS_DIR / "events.jsonl"
    if ev_path.exists():
        for line in ev_path.read_text().splitlines():
            try:
                events.append(json.loads(line))
            except Exception:
                pass
    buckets = Counter()  # 10-minute windows
    for e in events:
        ts = e.get("ts", "")
        if len(ts) >= 16:
            buckets[ts[:15] + "0"] += 1
    bucket_items = sorted(buckets.items())
    bucket_view = [(k[11:16], v) for k, v in bucket_items][-36:]

    # rate & ETA from the last 30 minutes of events
    now = datetime.now(timezone.utc)
    recent = [e for e in events
              if e.get("ts") and (now - datetime.fromisoformat(e["ts"])).total_seconds() < 1800]
    rate_hr = round(len(recent) * 2)
    eta_h = remaining / rate_hr if rate_hr else None
    eta_txt = (f"~{eta_h:.1f} h" if eta_h and eta_h > 0.05 else ("—" if remaining else "done"))
    running = len([e for e in events
                   if e.get("ts") and (now - datetime.fromisoformat(e["ts"])).total_seconds() < 300]) > 0

    completed_rows = [r for r in rows if r["status"] == "completed"]
    dom_counts = Counter(r["source_domain"] for r in completed_rows if r["source_domain"])
    top_domains = dom_counts.most_common(8)
    reason_counts = Counter()
    for r in rows:
        if r["status"] == "manual_review" and r["review_reason"]:
            key = r["review_reason"].split(" 0.")[0].split(" below")[0][:48]
            reason_counts[key] += 1
    top_reasons = reason_counts.most_common(8)

    recent_logos = sorted(completed_rows, key=lambda r: r["processed_at"] or "", reverse=True)[:24]
    wall = []
    for r in recent_logos:
        p = config.LOGOS_DIR / (r["filename"] or "")
        if not p.exists():
            continue
        try:
            uri = logo_thumb_data_uri(p)
        except Exception:
            continue
        wall.append(f'<div class="logo-tile"><img src="{uri}" alt="Logo for {r["account"]}">'
                    f'<span class="id">#{r["id"]} · {r["account"][:24]}</span></div>')

    gen_ts = now.strftime("%Y-%m-%d %H:%M UTC")
    status_note = "Pipeline running" if running else "Pipeline idle / between passes"
    pct_complete = counts["completed"] / total * 100

    html = f'''<title>Hospital Logo Pipeline — Live Tracker</title>
<style>{CSS}</style>
<div class="wrap">
  <div class="eyebrow"><span class="pulse"></span>{status_note} · live tracker</div>
  <h1>Hospital Logo Collection Pipeline</h1>
  <p class="sub">{total:,} hospital accounts → verified square PNG account icons</p>
  <p class="asof">Snapshot generated {gen_ts} · <span id="ago"></span> Refresh the page for the latest published snapshot.</p>

  <div class="grid hero">
    <div class="panel ringbox">
      {ring_svg(counts, total)}
      <div class="legend">
        <span><span class="sw" style="background:var(--good)"></span>Completed</span>
        <span><span class="sw" style="background:var(--warn)"></span>Manual review</span>
        <span><span class="sw" style="background:var(--crit)"></span>Failed</span>
        <span><span class="sw" style="background:var(--neutral)"></span>Pending</span>
      </div>
    </div>
    <div>
      <div class="grid tiles">
        <div class="tile good"><div class="k">Completed</div><div class="v">{counts["completed"]:,}</div><div class="d">{pct_complete:.1f}% of all rows</div></div>
        <div class="tile warn"><div class="k">Manual review</div><div class="v">{counts["manual_review"]:,}</div><div class="d">documented, needs a human</div></div>
        <div class="tile crit"><div class="k">Failed</div><div class="v">{counts["failed"]:,}</div><div class="d">errors after retries</div></div>
        <div class="tile neutral"><div class="k">Remaining</div><div class="v">{remaining:,}</div><div class="d">waiting in queue</div></div>
        <div class="tile"><div class="k">Rate</div><div class="v">{rate_hr:,}</div><div class="d">rows/hour (last 30 min)</div></div>
        <div class="tile"><div class="k">ETA</div><div class="v">{eta_txt}</div><div class="d">to finish current queue</div></div>
      </div>
      <div class="panel" style="margin-top:14px">
        <h2>Throughput</h2>
        <p class="note">Rows processed per 10-minute window (dips = tuning pauses between passes)</p>
        {area_chart(bucket_view)}
      </div>
    </div>
  </div>

  <div class="grid two">
    <div class="panel">
      <h2>Top logo sources</h2>
      <p class="note">Domains the accepted logos came from</p>
      {bars(top_domains, "var(--good)")}
    </div>
    <div class="panel">
      <h2>Why rows go to manual review</h2>
      <p class="note">Every unresolved row is documented — nothing is silently skipped</p>
      {bars(top_reasons, "var(--warn)", "rows")}
    </div>
  </div>

  <div class="panel" style="margin-top:14px">
    <h2>Freshly collected logos</h2>
    <p class="note">The {len(wall)} most recently accepted account icons (white tiles show true transparency)</p>
    <div class="logo-wall">{''.join(wall)}</div>
  </div>

  <details class="panel" style="margin-top:14px">
    <summary>Status table</summary>
    <table class="statuses">
      <tr><th>Status</th><th>Rows</th><th>Share</th></tr>
      <tr><td>Completed</td><td>{counts["completed"]:,}</td><td>{counts["completed"]/total*100:.1f}%</td></tr>
      <tr><td>Manual review</td><td>{counts["manual_review"]:,}</td><td>{counts["manual_review"]/total*100:.1f}%</td></tr>
      <tr><td>Failed</td><td>{counts["failed"]:,}</td><td>{counts["failed"]/total*100:.1f}%</td></tr>
      <tr><td>Pending / processing</td><td>{remaining:,}</td><td>{remaining/total*100:.1f}%</td></tr>
      <tr><td><strong>Total</strong></td><td><strong>{total:,}</strong></td><td>100%</td></tr>
    </table>
  </details>

  <p class="footer">Filenames follow File_&#123;ID&#125;_0_0_0_0_0_0.png · sources: official hospital sites,
  parent health systems, HIFLD, Wikidata/Wikimedia · accuracy &gt; auto-completion rate.</p>
</div>
<script>
  const gen = new Date("{now.isoformat()}");
  const el = document.getElementById("ago");
  function tick() {{
    const m = Math.max(0, Math.round((Date.now() - gen.getTime()) / 60000));
    el.textContent = m === 0 ? "(just now)." : `(${{m}} min ago).`;
  }}
  tick(); setInterval(tick, 30000);
</script>'''
    Path(out_path).write_text(html)
    print(f"dashboard written to {out_path} ({len(html)//1024} KB, {len(wall)} logos)")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else str(config.OUTPUT_DIR / "dashboard.html")
    main(out)
