# SPDX-License-Identifier: ISC
"""Unit tests for the breakpoint360 Phase 1 workload generator (no root, no topology).

python3 -m unittest discover -s tests/topotests/bgp_evpn_capstone/breakpoint360 -p 'test_*unit.py'
"""
import json
import os
import struct
import sys
import tempfile
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workload  # noqa: E402
import ledger  # noqa: E402
import bp_agent  # noqa: E402


class PlannerTest(unittest.TestCase):
    def test_staggered_uniform_spacing_and_count(self):
        w = workload.plan(robots=64, rate=8, duration_s=10)
        self.assertEqual(len(w['moves']), 80)
        times = [m['at_s'] for m in w['moves']]
        self.assertEqual(times[:3], [0.0, 0.125, 0.25])
        self.assertTrue(all(b > a for a, b in zip(times, times[1:])))
        self.assertAlmostEqual(w['stats']['planned_rate'], 8.0)

    def test_synchronized_same_count_batched(self):
        s = workload.plan(robots=64, rate=16, duration_s=10, mode='staggered')
        y = workload.plan(robots=64, rate=16, duration_s=10, mode='synchronized', sync_period_s=1.0)
        self.assertEqual(len(s['moves']), len(y['moves']))
        per_batch = Counter(m['at_s'] for m in y['moves'])
        self.assertEqual(sorted(per_batch), [float(k) for k in range(10)])
        self.assertEqual(set(per_batch.values()), {16})
        for t in per_batch:  # distinct robots in a batch
            robots = [m['robot'] for m in y['moves'] if m['at_s'] == t]
            self.assertEqual(len(robots), len(set(robots)))
        self.assertEqual(y['stats']['batches'], 10)

    def test_fractional_rate_synchronized_preserves_mean(self):
        y = workload.plan(robots=64, rate=2.5, duration_s=10, mode='synchronized')
        self.assertEqual(len(y['moves']), 25)
        sizes = Counter(m['at_s'] for m in y['moves']).values()
        self.assertTrue(set(sizes) <= {2, 3})

    def test_active_pct_and_hosting_leaves(self):
        w = workload.plan(robots=100, active_pct=25, hosting_leaves=3, rate=4, duration_s=10)
        active = [r for r in w['robots'] if r['active']]
        self.assertEqual(len(active), 25)
        homes = Counter(r['home'] for r in active)
        self.assertEqual(set(homes), {'leaf1', 'leaf2', 'leaf3'})
        self.assertLessEqual(max(homes.values()) - min(homes.values()), 1)  # stratified
        for r in w['robots']:
            self.assertIn(r['home'], ['leaf1', 'leaf2', 'leaf3'])
            self.assertEqual(r['ports'], ['leaf1', 'leaf2', 'leaf3'] if r['active'] else [r['home']])
        moving = {m['robot'] for m in w['moves']}
        self.assertTrue(moving <= {r['name'] for r in active})
        self.assertEqual(w['stats']['veth_pairs'], 25 * 3 + 75)

    def test_moves_are_inter_leaf_and_chain(self):
        w = workload.plan(robots=40, rate=10, duration_s=10, hosting_leaves=4, seed=7)
        where = {r['name']: r['home'] for r in w['robots']}
        for m in w['moves']:
            self.assertEqual(m['planned_src'], where[m['robot']])
            self.assertNotEqual(m['dst'], m['planned_src'])
            where[m['robot']] = m['dst']
        self.assertEqual(where, w['stats']['planned_final'])

    def test_two_leaves_ping_pong(self):
        w = workload.plan(robots=10, rate=4, duration_s=5, hosting_leaves=2)
        for m in w['moves']:
            self.assertEqual({m['planned_src'], m['dst']}, {'leaf1', 'leaf2'})

    def test_dad_cap_enforced_and_overridable(self):
        with self.assertRaises(workload.WorkloadError):
            workload.plan(robots=10, rate=10, duration_s=5)  # 50 moves / 10 robots = 5 > 4
        w = workload.plan(robots=10, rate=10, duration_s=5, allow_dad_exceed=True)
        self.assertFalse(w['stats']['dad_safe'])
        ok = workload.plan(robots=10, rate=8, duration_s=5)  # 40 / 10 = 4
        self.assertTrue(ok['stats']['dad_safe'])
        self.assertEqual(ok['stats']['max_moves_per_robot_planned'], 4)

    def test_cyclic_order_spreads_moves(self):
        w = workload.plan(robots=32, rate=16, duration_s=8)
        self.assertAlmostEqual(w['stats']['min_same_robot_gap_s'], 2.0)

    def test_deterministic_by_seed(self):
        a = workload.plan(robots=50, active_pct=50, rate=5, duration_s=10, seed=3)
        b = workload.plan(robots=50, active_pct=50, rate=5, duration_s=10, seed=3)
        c = workload.plan(robots=50, active_pct=50, rate=5, duration_s=10, seed=4)
        self.assertEqual(a['moves'], b['moves'])
        self.assertNotEqual(a['moves'], c['moves'])

    def test_poisson_arrivals(self):
        w = workload.plan(robots=200, rate=32, duration_s=10, arrival='poisson', seed=2)
        self.assertTrue(200 < len(w['moves']) < 440)
        self.assertTrue(all(0 <= m['at_s'] < 10 for m in w['moves']))

    def test_invalid(self):
        for bad in (dict(hosting_leaves=1, rate=1), dict(mode='burst'), dict(robots=1001), dict(bogus=1),
                    dict(active_pct=0, rate=1)):
            with self.assertRaises(workload.WorkloadError):
                workload.plan(**bad)
        self.assertEqual(workload.plan(rate=0)['moves'], [])

    def test_agent_spec_rebase(self):
        w = workload.plan(robots=8, rate=2, duration_s=2)
        spec = workload.agent_spec(w, {'leaf1': '/run/netns/x'}, lead_s=3, post_s=5)
        json.dumps(spec)
        self.assertNotIn('at_ns', spec['moves'][0])
        workload.rebase(spec, 7_000_000_000)
        self.assertEqual(spec['t0_ns'], 10_000_000_000)
        self.assertEqual(spec['stop_ns'], 17_000_000_000)
        self.assertEqual([m['at_ns'] for m in spec['moves']], [10_000_000_000, 10_500_000_000,
                                                               11_000_000_000, 11_500_000_000])
        self.assertNotIn('at_ns', w['moves'][0])  # plan itself untouched


class LedgerTest(unittest.TestCase):
    EVENTS = [
        dict(ev='meta', mono_ns=0),
        dict(ev='init', robot='f0', mono_ns=100, leaf='leaf1'),
        dict(ev='detach', robot='f0', mono_ns=1000, leaf='leaf1', move=0, err=0),
        dict(ev='attach', robot='f0', mono_ns=1100, leaf='leaf2', move=0, err=0),
        dict(ev='detach_confirmed', robot='f0', mono_ns=1150, leaf='leaf1', move=0),
        dict(ev='attach_confirmed', robot='f0', mono_ns=1300, leaf='leaf2', move=0, source='bridge_forwarding'),
        dict(ev='detach', robot='f0', mono_ns=5000, leaf='leaf2', move=1, err=0),
        dict(ev='attach', robot='f0', mono_ns=5100, leaf='leaf3', move=1, err=0),
        dict(ev='attach_confirmed', robot='f0', mono_ns=5200, leaf='leaf1', move=0),  # stale: ignored
        dict(ev='attach_timeout', robot='f0', mono_ns=9000, leaf='leaf3', move=1),
        dict(ev='final', mono_ns=9999),
    ]

    def load(self):
        with tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False) as handle:
            for ev in self.EVENTS:
                handle.write(json.dumps(ev) + '\n')
        try:
            return ledger.Ledger.load(handle.name)
        finally:
            os.unlink(handle.name)

    def test_state_machine(self):
        led = self.load()
        self.assertIsNone(led.state_at('f0', 50))
        self.assertEqual(led.state_at('f0', 500)['leaf'], 'leaf1')
        self.assertEqual(led.state_at('f0', 1200)['state'], ledger.IN_TRANSIT)  # ACKed but not confirmed
        s = led.state_at('f0', 1300)
        self.assertEqual((s['state'], s['leaf'], s['move']), (ledger.ATTACHED, 'leaf2', 0))
        self.assertEqual(led.state_at('f0', 6000)['state'], ledger.IN_TRANSIT)
        self.assertEqual(len(led.ignored), 1)
        self.assertEqual(led.current('f0')['state'], ledger.UNCONFIRMED)
        self.assertEqual(led.where(), {})

    def test_attach_error_is_detached(self):
        led = ledger.Ledger()
        led.apply(dict(ev='init', robot='f1', mono_ns=1, leaf='leaf1'))
        led.apply(dict(ev='detach', robot='f1', mono_ns=2, leaf='leaf1', move=0, err=0))
        led.apply(dict(ev='attach', robot='f1', mono_ns=3, leaf='leaf2', move=0, err=-19))
        self.assertEqual(led.current('f1')['state'], ledger.DETACHED)


class RateTest(unittest.TestCase):
    @staticmethod
    def records(rate, duration, lag_ns=200_000, latency_ns=1_000_000, t0=1_000_000_000):
        out = []
        for k in range(int(rate * duration)):
            at = t0 + int(k * 1e9 / rate)
            out.append(dict(at_ns=at, start_ns=at + lag_ns, attach_ack_ns=at + lag_ns + 100_000,
                            attach_confirmed_ns=at + lag_ns + latency_ns, confirm_source='bridge_forwarding'))
        return out

    def test_on_target(self):
        r = ledger.achieved_rates(self.records(16, 10), 16, 1_000_000_000, 10)
        self.assertAlmostEqual(r['achieved_rate_window'], 16.0)
        self.assertAlmostEqual(r['achieved_rate_overall'], 16.0)
        self.assertEqual(r['completed_per_bin'], [16] * 10)
        self.assertTrue(r['generator_ok'], r['generator_flags'])
        self.assertAlmostEqual(r['schedule_lag_ms']['p50'], 0.2)
        self.assertAlmostEqual(r['completion_latency_ms']['p95'], 1.0)

    def test_falling_behind_is_flagged(self):
        recs = self.records(16, 10)
        for k, rec in enumerate(recs):  # generator falls 10 ms further behind per move
            shift = k * 10_000_000
            for key in ('start_ns', 'attach_ack_ns', 'attach_confirmed_ns'):
                rec[key] += shift
        r = ledger.achieved_rates(recs, 16, 1_000_000_000, 10)
        self.assertLess(r['achieved_rate_overall'], 16 * 0.95)
        self.assertFalse(r['generator_ok'])
        self.assertTrue(any('lag' in f for f in r['generator_flags']))

    def test_synchronized_batch_spread(self):
        t0 = 1_000_000_000
        tight, loose = [], []
        for k in range(32):
            base = dict(at_ns=t0, batch=0, attach_confirmed_ns=t0 + 400_000_000)
            tight.append(dict(base, start_ns=t0 + 1_000_000 + k * 100_000, attach_ack_ns=t0 + 9_000_000))
            loose.append(dict(base, start_ns=t0 + 1_000_000 + k * 10_000_000,
                              attach_ack_ns=t0 + 2_000_000 + k * 10_000_000))
        ok = ledger.achieved_rates(tight, 32, t0, 1, mode='synchronized')
        self.assertEqual(ok['batches'], 1)
        self.assertAlmostEqual(ok['batch_issue_spread_ms']['max'], 8.0)
        self.assertAlmostEqual(ok['batch_start_lag_ms']['max'], 1.0)
        self.assertTrue(ok['generator_ok'], ok['generator_flags'])
        bad = ledger.achieved_rates(loose, 32, t0, 1, mode='synchronized')
        self.assertTrue(any('spread' in f for f in bad['generator_flags']))

    def test_missing_completions(self):
        recs = self.records(8, 5)
        for rec in recs[::2]:
            del rec['attach_confirmed_ns']
            rec['attach_timeout'] = True
            rec.pop('confirm_source')
        r = ledger.achieved_rates(recs, 8, 1_000_000_000, 5)
        self.assertEqual(r['completed_moves'], 20)
        self.assertEqual(r['timeouts'], 20)
        self.assertFalse(r['generator_ok'])


def nlmsg(mtype, family, index, flags, attrs):
    body = struct.pack('=BBHiII', family, 0, 0, index, flags, 0)
    for kind, value in attrs:
        body += struct.pack('=HH', 4 + len(value), kind) + value + b'\0' * ((4 - len(value) % 4) % 4)
    return struct.pack('=IHHII', 16 + len(body), mtype, 0, 0, 0) + body


class LinkParseTest(unittest.TestCase):
    def test_bridge_port_forwarding(self):
        protinfo = struct.pack('=HH', 5, 1) + b'\x03' + b'\0' * 3
        data = nlmsg(16, 7, 42, 0x11043, [(3, b'f12l2\0'), (12 | 0x8000, protinfo)])
        data += nlmsg(16, 0, 42, 0x11043, [(3, b'f12l2\0'), (16, b'\x06')])
        data += nlmsg(16, 0, 43, 0x1003, [(3, b'f13l2\0'), (16, b'\x02')])
        links = bp_agent.parse_links(data)
        self.assertEqual([l['ifname'] for l in links], ['f12l2', 'f12l2', 'f13l2'])
        self.assertEqual([bp_agent.classify(l) for l in links], ['forwarding', 'oper_up', 'down'])

    def test_bridge_port_disabled(self):
        protinfo = struct.pack('=HH', 5, 1) + b'\x00' + b'\0' * 3
        link = bp_agent.parse_links(nlmsg(16, 7, 5, 0x1003, [(3, b'f1l1\0'), (12 | 0x8000, protinfo)]))[0]
        self.assertEqual(bp_agent.classify(link), 'down')

    def test_arp_request_frame(self):
        frame = bp_agent.arp_request('02:36:00:00:00:01', '45.0.0.50', '45.0.0.1')
        self.assertEqual(len(frame), 42)
        self.assertEqual(frame[12:14], b'\x08\x06')
        self.assertEqual(frame[20:22], b'\x00\x01')  # request
        self.assertEqual(frame[38:42], bytes([45, 0, 0, 1]))


if __name__ == '__main__':
    unittest.main()
