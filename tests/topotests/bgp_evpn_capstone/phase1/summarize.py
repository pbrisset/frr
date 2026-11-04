#!/usr/bin/env python3
"""Summarize completed Phase 1B runs; empty fields mean unavailable."""
import csv
import json
import statistics
import sys
from pathlib import Path

root = Path(sys.argv[1])
rows, probes, forwards, routes, bgps = [], [], [], [], []
for directory in sorted((root / 'raw').glob('phase1b-*')):
    if 'pilot' in directory.name or not (directory / 'reachability-summary.json').exists():
        continue
    name = directory.name
    events = [json.loads(x) for x in (directory / 'events.jsonl').read_text().splitlines()]
    samples = [json.loads(x) for x in (directory / 'host11-probes.jsonl').read_text().splitlines()]
    move = next(e['observed_ns'] for e in events if e['kind'] == 'move_command_issued')
    detach = next(e for e in events if e['kind'] == 'old_attachment_disconnected')
    attach = next(e for e in events if e['kind'] == 'new_attachment_connected')
    summaries = json.loads((directory / 'reachability-summary.json').read_text())['host11']
    fs = [e for e in events if e['kind'] == 'forwarding_resolution']
    for e in fs:
        forwards.append(dict(run=name, elapsed_ms=(e['observed_ns']-move)/1e6, attachment=e['actual_leaf'], candidates=' '.join(e['candidates']), matched=e['matched']))
    for e in events:
        if e['kind'] == 'forwarding' and 'route show vrf' in e.get('command', ''):
            try:
                table, _ = json.JSONDecoder().raw_decode(e['output'].lstrip())
                destinations = {r.get('dst') for r in table}
                routes.append(dict(run=name, elapsed_ms=(e['end_ns']-move)/1e6, host32=('45.0.0.11' in destinations or '45.0.0.11/32' in destinations), subnet24='45.0.0.0/24' in destinations))
            except (ValueError, TypeError):
                routes.append(dict(run=name, elapsed_ms=(e['end_ns']-move)/1e6, host32='', subnet24=''))
    for p in samples:
        probes.append(dict(run=name, elapsed_ms=(p['send_ns']-move)/1e6, **p))
    counts_path = directory / 'packet-counts.json'
    if counts_path.exists():
        for device, counts in json.loads(counts_path.read_text()).items():
            for key, value in counts.items():
                bgps.append(dict(run=name, device=device, counter=key, value=value))
    rtt = [p['rtt_ms'] for p in samples if p['success']]
    after = [p for p in samples if p['send_ns'] >= detach['observed_ns'] and p['success']]
    prefix = name.split('-')[1]
    route_samples = [x for x in routes if x['run'] == name and x['elapsed_ms'] >= 0]
    rows.append(dict(run=name, method=prefix, sent=len(samples), lost=summaries['lost'], loss_fraction=summaries['lost']/len(samples), max_loss_burst=max((b['count'] for b in summaries['bursts']), default=0), rtt_median_ms=statistics.median(rtt) if rtt else '', rtt_min_ms=min(rtt) if rtt else '', rtt_max_ms=max(rtt) if rtt else '', first_success_after_detach_ms=(after[0]['receive_ns']-detach['observed_ns'])/1e6 if after else '', interval_ms=(detach.get('observed_overlap_ns')/1e6 if detach.get('observed_overlap_ns') is not None else (attach['observed_ns']-detach['observed_ns'])/1e6), interval_type='overlap_observed' if 'observed_overlap_ns' in detach else 'disconnection_observed', sampled_host32_absent=any(x['host32'] is False for x in route_samples), sampled_subnet24_present=any(x['subnet24'] is True for x in route_samples), sampled_mismatches=sum(e['matched'] is False for e in fs), cleanup_verified=any(e['kind']=='cleanup_verified' for e in events)))

def write(filename, records, fields):
    with (root/filename).open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader(); w.writerows(records)

write('runs.csv', rows, list(rows[0]) if rows else ['run'])
write('reachability.csv', probes, ['run','sequence','elapsed_ms','send_ns','receive_ns','success','rtt_ms','failure_observed_ns','error'])
write('forwarding-transitions.csv', forwards, ['run','elapsed_ms','attachment','candidates','matched'])
write('route-transitions.csv', routes, ['run','elapsed_ms','host32','subnet24'])
write('bgp-messages.csv', bgps, ['run','device','counter','value'])
comparison=[]
for method in sorted({r['method'] for r in rows}):
    group=[r for r in rows if r['method']==method]
    for metric in ('lost','loss_fraction','max_loss_burst','rtt_median_ms','interval_ms','sampled_mismatches'):
        vals=[r[metric] for r in group if isinstance(r[metric], (int,float))]
        if vals:
            comparison.append(dict(method=method, metric=metric, n=len(vals), minimum=min(vals), median=statistics.median(vals), maximum=max(vals)))
write('comparison.csv', comparison, ['method','metric','n','minimum','median','maximum'])
