#!/usr/bin/env python3
"""Create small static SVGs from observed per-run CSV data."""
import csv
import html
import sys
from pathlib import Path

root=Path(sys.argv[1]); plot=root/'plots'; plot.mkdir(exist_ok=True)
def read(name):
    with (root/name).open() as f:
        return list(csv.DictReader(f))
runs=read('runs.csv')
lines=['<svg xmlns="http://www.w3.org/2000/svg" width="960" height="%s">' % (80+len(runs)*25), '<rect width="100%" height="100%" fill="white"/>', '<g font-family="sans-serif" font-size="13" fill="#222">', '<text x="10" y="25">Per-run observed probe losses (not outage duration)</text>']
for i,r in enumerate(runs):
    y=55+i*25; lost=int(r['lost'])
    lines.append('<text x="10" y="%d">%s</text>'%(y,html.escape(r['run'])))
    lines.append('<rect x="180" y="%d" width="%d" height="14" fill="#3273a8"/>'%(y-12,lost*35))
    lines.append('<text x="%d" y="%d">%d / %s</text>'%(190+lost*35,y,lost,r['sent']))
lines.append('</g></svg>'); (plot/'probe-loss.svg').write_text('\n'.join(lines))
import json
lines=['<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="%s">'%(90+len(runs)*35),'<rect width="100%" height="100%" fill="white"/><g font-family="sans-serif" font-size="11">','<text x="10" y="20">Handoff observations: milliseconds relative to move command (not continuous states)</text>','<text x="10" y="38">Blue: new attached; red: old detached; orange: IP transfer; black cross: sampled leaf5 mismatch</text>']
for i,r in enumerate(runs):
    d=root/'raw'/r['run']; ev=[json.loads(x) for x in (d/'events.jsonl').read_text().splitlines()]; t=next(e['observed_ns'] for e in ev if e['kind']=='move_command_issued'); y=65+i*35
    lines.append('<text x="10" y="%s">%s</text>'%(y,html.escape(r['run'])))
    lines.append('<line x1="190" y1="%s" x2="1000" y2="%s" stroke="#bbb"/>'%(y-4,y-4))
    for e in ev:
        delta=(e['observed_ns']-t)/1e6
        if -100<=delta<=3000:
            x=216+(delta/3000)*760
            color={'new_attachment_connected':'#145da0','old_attachment_disconnected':'#cb2e2e','ip_route_transfer_completed':'#dd8b00'}.get(e['kind'])
            if e['kind']=='forwarding_resolution' and e['matched'] is False: color='#111'
            if color: lines.append('<circle cx="%.1f" cy="%s" r="4" fill="%s"><title>%s %.1f ms</title></circle>'%(x,y-4,color,e['kind'],delta))
lines.append('</g></svg>'); (plot/'comparative-timeline.svg').write_text('\n'.join(lines))
