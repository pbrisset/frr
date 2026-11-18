#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
"""Collect the Phase 1 smoke trials into phase1-smoke-summary.csv (stdlib only)."""
import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
NOTES = {
    'smoke-s64': 'agent rev A (select loop, serial radio ops, no nice)',
    'smoke-y64b': 'agent rev B (pipelined batches); rev A run smoke-y64 had batch spread 17-38 ms, not kept',
    'smoke-s1000': 'agent rev C (poll loop); first attempt with rev B crashed: select() fd >= 1024',
    'smoke-s1000b': 'agent rev D (nice -10, loop-stall accounting) = current code',
    'smoke-y1000': 'agent rev D = current code',
}
COLS = ['trial', 'robots', 'active_robots', 'hosting_leaves', 'mode', 'requested_rate', 'duration_s',
        'planned_moves', 'completed_moves', 'achieved_rate_window', 'achieved_rate_overall', 'min_1s_bin',
        'max_1s_bin', 'batch_start_lag_p50_ms', 'batch_start_lag_p95_ms', 'batch_start_lag_max_ms',
        'batch_issue_spread_p50_ms', 'batch_issue_spread_max_ms', 'attach_ack_p50_ms', 'attach_ack_max_ms',
        'completion_latency_p50_ms', 'completion_latency_p95_ms', 'completion_latency_max_ms',
        'confirmed_by_bridge_forwarding', 'timeouts', 'generator_ok', 'generator_flags', 'ledger_robots',
        'ledger_kernel_mismatches', 'veth_pairs', 'fleet_build_s', 'fleet_learn_s', 'teardown_s',
        'arp_offsets_ms', 'notes']


def row(trial):
    s = json.loads((trial / 'agent-summary.json').read_text())
    m = json.loads((trial / 'manifest.json').read_text())
    p, w = m['plan_stats'], m['config']['workload']
    bins = s['completed_per_bin'][:int(s['requested_duration_s'])]
    return dict(
        trial=trial.name, robots=p['robots'], active_robots=p['active_robots'],
        hosting_leaves=len(p['hosting_leaves']), mode=s['mode'], requested_rate=s['requested_rate'],
        duration_s=s['requested_duration_s'], planned_moves=s['planned_moves'],
        completed_moves=s['completed_moves'], achieved_rate_window=round(s['achieved_rate_window'], 3),
        achieved_rate_overall=round(s['achieved_rate_overall'], 3), min_1s_bin=min(bins), max_1s_bin=max(bins),
        batch_start_lag_p50_ms=s.get('batch_start_lag_ms', s['schedule_lag_ms']).get('p50'),
        batch_start_lag_p95_ms=s.get('batch_start_lag_ms', s['schedule_lag_ms']).get('p95'),
        batch_start_lag_max_ms=s.get('batch_start_lag_ms', s['schedule_lag_ms']).get('max'),
        batch_issue_spread_p50_ms=s.get('batch_issue_spread_ms', {}).get('p50'),
        batch_issue_spread_max_ms=s.get('batch_issue_spread_ms', {}).get('max'),
        attach_ack_p50_ms=s['attach_ack_ms']['p50'], attach_ack_max_ms=s['attach_ack_ms']['max'],
        completion_latency_p50_ms=s['completion_latency_ms']['p50'],
        completion_latency_p95_ms=s['completion_latency_ms']['p95'],
        completion_latency_max_ms=s['completion_latency_ms']['max'],
        confirmed_by_bridge_forwarding=s['confirm_sources'].get('bridge_forwarding', 0), timeouts=s['timeouts'],
        generator_ok=s['generator_ok'], generator_flags='; '.join(s['generator_flags']),
        ledger_robots=m['ledger_validation']['robots'], ledger_kernel_mismatches=m['ledger_validation']['mismatches'],
        veth_pairs=p['veth_pairs'], fleet_build_s=m['fleet_build_s'], fleet_learn_s=m['fleet_learnt']['waited_s'],
        teardown_s=m['fleet_teardown_wait_s'], arp_offsets_ms=','.join(map(str, w['arp_offsets_ms'])),
        notes=NOTES.get(trial.name, ''))


def main():
    rows = [row(t) for t in sorted(HERE.iterdir()) if (t / 'agent-summary.json').exists()]
    for r in rows:
        for k, v in r.items():
            if isinstance(v, float):
                r[k] = round(v, 3)
    with open(HERE / 'phase1-smoke-summary.csv', 'w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLS)
        writer.writeheader()
        writer.writerows(rows)
    for r in rows:
        print(r['trial'], r['mode'], r['requested_rate'], r['achieved_rate_overall'], r['completed_moves'],
              r['ledger_kernel_mismatches'], r['generator_flags'] or 'ok')


if __name__ == '__main__':
    main()
