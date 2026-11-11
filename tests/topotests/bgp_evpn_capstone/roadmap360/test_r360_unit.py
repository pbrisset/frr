#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
"""Unit tests for the 360-roadmap parsers and analysis (no topology needed).

python3 -m unittest discover -s tests/topotests/bgp_evpn_capstone/roadmap360 -p 'test_*unit.py'
"""
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_360_roadmap as an  # noqa: E402
import ktrace  # noqa: E402
import nlmon  # noqa: E402
import normalize  # noqa: E402

KTRACE = """\
          python3-1234    [002] d.s1. 100.000100: arp_rx: (arp_process+0x0/0x6f0) op=256 sha={0x0,0x0,0x0,0x0,0x0,0x11} sip=184549421 tip=16777261 dev="br1000" inum=42
          python3-1234    [002] d.s1. 100.000110: nupd: (__neigh_update+0x0/0xa70) new=4 uflags=1 old=64 nflags=16 fam=2 key=184549421 dev="br1000" inum=42 cur={0x0,0x0,0x0,0x0,0x0,0x11} nmac={0x0,0x0,0x0,0x0,0x0,0x11}
          python3-1234    [002] ..s1. 100.000112: nupd_ret: (neigh_event_ns+0x7c/0x100 <- __neigh_update) ret=-1
 zebra_dplane-77      [001] d.... 100.050000: nupd: (__neigh_update+0x0/0xa70) new=32 uflags=129 old=64 nflags=16 fam=2 key=184549421 dev="br1000" inum=42 cur={0x0,0x0,0x0,0x0,0x0,0x11} nmac=(fault)
 zebra_dplane-77      [001] ..... 100.050004: nupd_ret: (neigh_update+0x20/0x58 <- __neigh_update) ret=0
"""


class KtraceTest(unittest.TestCase):
    def test_parse_pairs_entry_and_return(self):
        recs = list(ktrace.parse(KTRACE.splitlines(), {'42': 'leaf2'}))
        arp = [r for r in recs if r['event'] == 'arp_rx'][0]
        self.assertEqual((arp['op'], arp['sip'], arp['tip'], arp['node']), ('request', '45.0.0.11', '45.0.0.1', 'leaf2'))
        self.assertEqual(arp['sha'], '00:00:00:00:00:11')
        upd = [r for r in recs if r['event'] == 'nupd']
        self.assertEqual(len(upd), 2)
        self.assertEqual(upd[0]['ip'], '45.0.0.11')
        self.assertEqual(upd[0]['old_state'], ['NOARP'])
        self.assertEqual(upd[0]['new_state'], ['STALE'])
        self.assertTrue(upd[0]['rejected'] and upd[0]['ext_learned'])
        self.assertEqual(upd[0]['update_flags'], ['OVERRIDE'])
        self.assertEqual(upd[1]['update_flags'], ['OVERRIDE', 'ADMIN'])
        self.assertFalse(upd[1]['rejected'])
        self.assertEqual(upd[0]['mono_ns'], 100000110000)


class NetlinkDecodeTest(unittest.TestCase):
    def test_bridge_fdb_and_neigh(self):
        dec = nlmon.Decoder()
        dec.names = {5: 'eth-host11', 9: 'br1000'}

        def attr(kind, value):
            return struct.pack('=HH', 4 + len(value), kind) + value + b'\0' * ((4 - len(value) % 4) % 4)
        neigh = struct.pack('=BBHiHBB', 2, 0, 0, 9, 0x40, 0x10, 1) + attr(1, bytes([45, 0, 0, 11])) + attr(2, bytes(5) + b'\x11')
        msg = struct.pack('=IHHII', 16 + len(neigh), 28, 0, 1, 0) + neigh
        rec = dec.decode(msg, 1, 2, 'event')[0]
        self.assertEqual((rec['msg'], rec['ip'], rec['mac'], rec['state'], rec['flags']),
                         ('NEWNEIGH', '45.0.0.11', '00:00:00:00:00:11', ['NOARP'], ['extern_learn']))
        fdb = struct.pack('=BBHiHBB', 7, 0, 0, 5, 0x02, 0x00, 0) + attr(2, bytes(5) + b'\x11') + attr(9, struct.pack('=I', 9))
        msg = struct.pack('=IHHII', 16 + len(fdb), 28, 0, 1, 0) + fdb
        rec = dec.decode(msg, 1, 2, 'event')[0]
        self.assertEqual((rec['family'], rec['ifname'], rec['master'], rec['mac']), ('bridge', 'eth-host11', 'br1000', '00:00:00:00:00:11'))


class LogRegexTest(unittest.TestCase):
    def match(self, patterns, text):
        for etype, regex, names in patterns:
            hit = regex.search(text)
            if hit:
                return etype, dict(zip(names, hit.groups()))
        return None, None

    def test_zebra_lines(self):
        e, f = self.match(normalize.Z_PATTERNS, 'Send MACIP Add f None  state 1 MAC 00:00:00:00:00:11 IP (null) seq 1 L2-VNI 1000 ESI - to bgp')
        self.assertEqual((e, f['action'], f['ip'], f['seq']), ('zapi_macip_send', 'Add', '(null)', '1'))
        e, f = self.match(normalize.Z_PATTERNS, 'Recv MACIP DEL VNI 1000 MAC 00:00:00:00:00:11 IP 45.0.0.11 Remote VTEP 10.0.0.1 from bgp')
        self.assertEqual((e, f['action'], f['ip'], f['vtep']), ('zapi_macip_recv', 'DEL', '45.0.0.11', '10.0.0.1'))
        e, f = self.match(normalize.Z_PATTERNS, 'Recv MACIP ADD VNI 1000 MAC 00:00:00:00:00:11 flags 0x0 seq 1 VTEP 10.0.0.2 ESI - from bgp')
        self.assertEqual((e, f['ip'], f['seq'], f['vtep']), ('zapi_macip_recv', None, '1', '10.0.0.2'))

    def test_bgp_lines(self):
        e, f = self.match(normalize.B_PATTERNS, 'u2:s2 send UPDATE RD 10.0.0.1:1000 [2]:[0]:[48]:[00:00:00:00:00:11]:[32]:[45.0.0.11] l2vpn evpn -- unreachable')
        self.assertEqual(e, 'bgp_send')
        self.assertIn('unreachable', f['tail'])
        e, f = self.match(normalize.B_PATTERNS, 'eth-spine1(spine1) rcvd UPDATE about RD 10.0.0.1:1000 [2]:[0]:[48]:[00:00:00:00:00:11]:[32]:[45.0.0.11] label 0 l2vpn evpn -- withdrawn')
        self.assertEqual((e, f['peer']), ('bgp_rcvd_withdraw', 'spine1'))
        e, f = self.match(normalize.B_PATTERNS, 'eth-spine2(spine2) rcvd RD 10.0.0.2:1000 [2]:[0]:[48]:[00:00:00:00:00:11]:[32]:[45.0.0.11] label 1000/500 l2vpn evpn')
        self.assertEqual((e, f['rd']), ('bgp_rcvd', '10.0.0.2:1000'))
        p = normalize.EVPN_PFX.search('[2]:[0]:[48]:[00:00:00:00:00:11]:[32]:[45.0.0.11]')
        self.assertEqual(p.groups(), ('00:00:00:00:00:11', '45.0.0.11'))
        self.assertEqual(normalize.rd_text('00:01:0a:00:00:01:03:e8'), '10.0.0.1:1000')


def ev(t_ms, node, layer, etype, mac=None, ip=None, flags=None, **details):
    details.setdefault('mono_ns', int(t_ms * 1e6))
    return dict(timestamp_ns=int(t_ms * 1e6), node=node, layer=layer, event_type=etype, mac=mac, ip=ip,
                flags=flags or [], details=details)


class AnalysisTest(unittest.TestCase):
    M, I = '00:00:00:00:00:11', '45.0.0.11'

    def trial(self, events):
        meta = dict(timestamp_ns=None, node='meta', layer='meta', event_type='trial_meta', mac=None, ip=None, flags=[],
                    details=dict(config=dict(test_id='t1', ws='A', pattern='burst', mode='bbm', overlap_ms=0,
                                             fleet='none', pre_s=6, post_s=20),
                                 robot=dict(name='host11', ip=self.I, mac=self.M, src='leaf1', dst='leaf2'),
                                 clock=dict(drift_ns=0), ktrace=True, ktrace_stats={}))
        d = Path(tempfile.mkdtemp())
        p = d / 'telemetry-events.jsonl'
        p.write_text('\n'.join(json.dumps(x) for x in [meta] + events) + '\n')
        return an.Trial(p)

    def test_hypothesis_a_rejection_then_recovery(self):
        M, I = self.M, self.I
        events = [
            ev(0, 'harness', 'harness', 'T0'),
            ev(60, 'host11', 'harness', 'stimulus_send', mac=M, ip=I, packet='arp-request-gateway', index=0, offset_s=0),
            ev(61, 'leaf2', 'kernel', 'fdb_update', mac=M, flags=['REACHABLE', 'NONE'], ifname='eth-host11'),
            ev(61.5, 'leaf2', 'ktrace', 'arp_rx', mac=M, ip=I, flags=['request'], op='request', target_ip='45.0.0.1', cpu=1),
            ev(61.6, 'leaf2', 'ktrace', 'neigh_update_call', mac=M, ip=I, flags=['OVERRIDE'], old_state=['NOARP'],
               new_state=['STALE'], rejected=True, ret=-1, cpu=1),
            ev(62, 'leaf2', 'zebra', 'zapi_macip_send', mac=M, ip=None, flags=['add'], seq='1'),
            ev(80, 'leaf1', 'wire', 'bgp_nlri', mac=M, ip=I, flags=['withdraw', 'tx'], sender='leaf1'),
            ev(120, 'leaf2', 'ktrace', 'neigh_update_call', mac=M, ip=I, flags=['OVERRIDE', 'ADMIN'], old_state=['NOARP'],
               new_state=['FAILED'], rejected=False, ret=0, cpu=0),
            ev(121, 'leaf2', 'kernel', 'neigh_delete', ip=I, flags=['FAILED', 'NONE'], state=['FAILED']),
            ev(150, 'leaf5', 'kernel', 'route_delete', ip=I, prefix=I + '/32', gateway='10.0.0.1'),
            ev(310, 'leaf2', 'ktrace', 'arp_rx', mac=M, ip=I, flags=['request'], op='request', target_ip='45.0.0.1', cpu=3),
            ev(310.1, 'leaf2', 'ktrace', 'neigh_update_call', mac=M, ip=I, flags=['OVERRIDE'], old_state=['NONE'],
               new_state=['STALE'], rejected=False, ret=0, cpu=3),
            ev(310.2, 'leaf2', 'kernel', 'neigh_update', mac=M, ip=I, flags=['STALE', 'NONE'], state=['STALE']),
            ev(311, 'leaf2', 'zebra', 'zapi_macip_send', mac=M, ip=I, flags=['add'], seq='1'),
            ev(470, 'leaf5', 'kernel', 'route_add', ip=I, prefix=I + '/32', gateway='10.0.0.2'),
            ev(-100, 'controller', 'probe', 'probe_result', ip=I, success=True, receive_ns=int(-99e6), mono_ns=int(-100e6)),
            ev(100, 'controller', 'probe', 'probe_result', ip=I, success=False, receive_ns=None, mono_ns=int(100e6)),
            ev(500, 'controller', 'probe', 'probe_result', ip=I, success=True, receive_ns=int(501e6), mono_ns=int(500e6)),
        ]
        t = self.trial(events)
        res = t.analyse()
        m = res['m']
        self.assertEqual(ms_(m['T_mac']), 61)
        self.assertEqual(ms_(m['T_withdraw_recv']), 120)
        self.assertEqual(m['T_withdraw_recv_src'], 'ktrace')
        self.assertEqual(ms_(m['T_ip']), 310.1)  # kernel (kprobe) time of the accepted update
        self.assertEqual(m['T_ip_src'], 'ktrace')
        self.assertEqual(ms_(m['T_leaf5_fib']), 470)
        self.assertEqual(ms_(m['fallback_ns']), 320)
        self.assertEqual(res['arp']['blocked_before'], 1)
        self.assertIn('A', res['flags'])
        self.assertNotIn('B', res['flags'])
        self.assertEqual(res['order'], 'T_ip AFTER T_withdraw_recv')
        self.assertEqual(res['arp']['trigger'], 'robot-initiated request')
        self.assertEqual(ms_(res['d_unreachable']), 501)
        self.assertEqual(res['probes']['lost'], 1)
        self.assertEqual(res['dominant'], 'A')  # wait 160.1 ms > propagation 159.9 ms, rejection seen
        self.assertAlmostEqual(ms_(res['wait_ns']), 160.1)

    def test_percentile(self):
        self.assertEqual(an.pct([1, 2, 3, 4], 50), 2.5)
        self.assertEqual(an.pct([5], 99), 5)
        self.assertIsNone(an.pct([], 50))


def ms_(ns):
    return None if ns is None else round(ns / 1e6, 3)


if __name__ == '__main__':
    unittest.main()
