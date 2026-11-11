#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
"""Normalise one trial's raw observations into ``telemetry-events.jsonl``.

Record schema (one JSON object per line, sorted by time)::

  {timestamp_ns, node, layer, event_type, mac, ip, flags, details}

``timestamp_ns`` is CLOCK_MONOTONIC relative to T0 (the first roam command;
negative = before the roam).  ``details.mono_ns`` keeps the absolute value.
Realtime sources (FRR logs, pcapng) are mapped with the offset measured at
start and end of the trial (``details.clock`` notes the mapping).

Layers: harness, kernel (rtnetlink), ktrace (kprobes), zebra, bgpd (debug
logs), wire (pcap), probe (controller ICMP), cpu, fleet.
Requires tshark for the wire layer (run inside the frr-dev container).
"""
import argparse
import calendar
import gzip
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ktrace  # noqa: E402

TS = re.compile(r'^(\d{4})/(\d\d)/(\d\d) (\d\d):(\d\d):(\d\d)\.(\d{6}) (ZEBRA|BGP): \[[^\]]+\] (.*)$')
EVPN_PFX = re.compile(r'\[2\]:\[\d+\]:\[48\]:\[([0-9a-f:]{17})\](?::\[(?:32|128)\]:\[([0-9a-f.:]+)\])?')
Z_PATTERNS = [
    ('zapi_macip_send', re.compile(r'Send MACIP (Add|Del) f (.*?) state (\d+) MAC (\S+) IP (\S+) seq (\d+) L2-VNI (\d+)'),
     ('action', 'macip_flags', 'state', 'mac', 'ip', 'seq', 'vni')),
    ('zapi_macip_recv', re.compile(r'Recv MACIP (ADD|DEL) VNI (\d+) MAC (\S+?)(?: IP (\S+))?(?: flags (\S+))?(?: seq (\d+))? (?:Remote )?VTEP (\S+)'),
     ('action', 'vni', 'mac', 'ip', 'macip_flags', 'seq', 'vtep')),
    ('zebra_local_mac', re.compile(r'(ADD|UPD) MAC (\S+) intf (\S+?)\((\d+)\) VID (\d+) -> VNI (\d+)(.*)'),
     ('action', 'mac', 'intf', 'ifindex', 'vid', 'vni', 'extra')),
    ('zebra_local_mac_del', re.compile(r'DEL MAC (\S+) VNI (\d+) seq (\d+) flags (\S+)'),
     ('mac', 'vni', 'seq', 'mac_flags')),
    ('zebra_neigh_update', re.compile(r'Add/Update neighbor (\S+) MAC (\S+) intf (\S+?)\((\d+)\) state (0x[0-9a-f]+) (.*?)-> L2-VNI (\d+)'),
     ('ip', 'mac', 'intf', 'ifindex', 'state', 'extra', 'vni')),
    ('zebra_neigh_delete', re.compile(r'Del neighbor (\S+) intf (\S+?)\((\d+)\) -> L2-VNI (\d+)'),
     ('ip', 'intf', 'ifindex', 'vni')),
    ('zebra_dp_remote_mac', re.compile(r'dp(Add|Del) remote MAC (\S+)'), ('action', 'mac')),
    ('zebra_dad', re.compile(r'(.*duplicate.*)', re.I), ('text',)),
]
B_PATTERNS = [
    ('bgp_send_update_msg', re.compile(r'u(\d+):s(\d+) send UPDATE (?:\(withdraw\) )?len (\d+).*?numpfx (\d+)'),
     ('ugrp', 'subgrp', 'len', 'numpfx')),
    ('bgp_send', re.compile(r'u(\d+):s(\d+) send UPDATE (?:RD (\S+) )?(\[\S+\])(.*)$'),
     ('ugrp', 'subgrp', 'rd', 'prefix', 'tail')),
    ('bgp_rcvd_withdraw', re.compile(r'(\S+?)\((\S+)\) rcvd UPDATE about (?:RD (\S+) )?(\[\S+\]).*-- withdrawn'),
     ('peer_if', 'peer', 'rd', 'prefix')),
    ('bgp_rcvd_denied', re.compile(r'(\S+?)\((\S+)\) rcvd UPDATE about (?:RD (\S+) )?(\[\S+\]).*-- DENIED due to: (.*)'),
     ('peer_if', 'peer', 'rd', 'prefix', 'reason')),
    ('bgp_rcvd', re.compile(r'(\S+?)\((\S+)\) rcvd (?:RD (\S+) )?(\[\S+\])'), ('peer_if', 'peer', 'rd', 'prefix')),
    ('bgp_zapi_recv', re.compile(r'\d+:Recv MACIP (Add|Del) f (\S+) MAC (\S+) IP (\S+) VNI (\d+) seq (\d+) state (\d+)'),
     ('action', 'macip_flags', 'mac', 'ip', 'vni', 'seq', 'state')),
    ('bgp_zapi_tx', re.compile(r'Tx (ADD|DEL) MACIP, VNI (\d+) MAC (\S+) IP (\S+) flags (\S+) seq (\d+) remote VTEP (\S+)'),
     ('action', 'vni', 'mac', 'ip', 'macip_flags', 'seq', 'vtep')),
    ('bgp_rib_tx', re.compile(r'Tx route (add|delete) VRF (\S+) \(table id \d+\) (\S+)'), ('action', 'vrf', 'prefix')),
]


def clean_ip(value):
    if value in (None, '(null)', '0.0.0.0', '::', ''):
        return None
    return value


class Run:
    def __init__(self, path):
        self.path = Path(path)
        self.manifest = json.loads((self.path / 'manifest.json').read_text())
        self.harness = [json.loads(l) for l in (self.path / 'harness-events.jsonl').read_text().splitlines() if l]
        self.t0 = next(e['mono_ns'] for e in self.harness if e['kind'] == 'T0')
        c0 = self.manifest['clock_start']['offset_ns']
        c1 = self.manifest.get('clock_end', {}).get('offset_ns', c0)
        self.offset = (c0 + c1) // 2
        self.clock = dict(offset_ns=self.offset, drift_ns=c1 - c0,
                          bracket_ns=max(self.manifest['clock_start']['bracket_ns'],
                                         self.manifest.get('clock_end', {}).get('bracket_ns', 0)))
        cfg = self.manifest['config']
        self.lo = self.t0 - int((cfg['pre_s'] + 3) * 1e9)
        self.hi = self.t0 + int((cfg['post_s'] + 3) * 1e9)
        self.out = []

    def add(self, mono, node, layer, event_type, mac=None, ip=None, flags=None, **details):
        details['mono_ns'] = mono
        self.out.append(dict(timestamp_ns=mono - self.t0, node=node, layer=layer, event_type=event_type,
                             mac=mac.lower() if isinstance(mac, str) else mac, ip=ip,
                             flags=flags or [], details=details))

    def real_to_mono(self, real_ns):
        return real_ns - self.offset

    # ------------------------------------------------------------------ harness
    def harness_events(self):
        for e in self.harness:
            data = {('target_' + k if k in ('node', 'layer', 'mac', 'ip', 'flags') else k): v
                    for k, v in e.items() if k not in ('kind', 'mono_ns')}
            self.add(e['mono_ns'], 'harness', 'harness', e['kind'], **data)
        stim = self.path / 'stimulus.jsonl'
        if stim.exists():
            robot = self.manifest['robot']
            for line in stim.read_text().splitlines():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get('kind') == 'send':
                    self.add(rec['before_ns'], robot['name'], 'harness', 'stimulus_send', mac=robot['mac'],
                             ip=robot['ip'], packet=rec['packet'], index=rec['index'], offset_s=rec['offset_s'],
                             iface=rec['iface'], after_ns=rec['after_ns'])
                elif rec.get('kind') == 'ping_reply':
                    self.add(rec['mono_ns'], robot['name'], 'harness', 'stimulus_ping_reply', index=rec['index'])

    # ------------------------------------------------------------------ kernel
    def kernel(self):
        for path in sorted(self.path.glob('nl-*.jsonl')):
            node = path.stem[3:]
            for line in path.read_text().splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                kind = r.get('kind')
                if kind in ('ready', 'final', 'socket_error', 'dump_timeout'):
                    self.add(r['mono_ns'], node, 'kernel', 'netlink_' + kind,
                             **{k: v for k, v in r.items() if k not in ('kind', 'mono_ns', 'node')})
                    continue
                if kind != 'netlink':
                    continue
                msg, src = r['msg'], r['src']
                suffix = '_snapshot' if src == 'dump' else ''
                if msg in ('NEWNEIGH', 'DELNEIGH'):
                    base = 'fdb' if r.get('family') == 'bridge' else 'neigh'
                    if base == 'neigh' and r.get('family') != 'inet':
                        continue
                    etype = base + ('_update' if msg == 'NEWNEIGH' else '_delete') + suffix
                    self.add(r['mono_ns'], node, 'kernel', etype, mac=r.get('mac'), ip=r.get('ip'),
                             flags=r.get('state', []) + r.get('flags', []), ifname=r.get('ifname'),
                             vtep=r.get('vtep'), master=r.get('master'), state=r.get('state'),
                             ntf=r.get('flags'), vni=r.get('vni'), protocol=r.get('protocol'))
                elif msg in ('NEWROUTE', 'DELROUTE'):
                    if r.get('table') != 500 and r.get('dst_len') != 32:
                        continue
                    etype = 'route_' + ('add' if msg == 'NEWROUTE' else 'delete') + suffix
                    self.add(r['mono_ns'], node, 'kernel', etype, ip=r.get('dst'),
                             prefix='{}/{}'.format(r.get('dst'), r.get('dst_len')), table=r.get('table'),
                             gateway=r.get('gateway'), nhid=r.get('nhid'), oif=r.get('oif'),
                             multipath=r.get('multipath'), protocol=r.get('protocol'))
                elif msg in ('NEWNEXTHOP', 'DELNEXTHOP'):
                    etype = 'nexthop_' + ('add' if msg == 'NEWNEXTHOP' else 'delete') + suffix
                    self.add(r['mono_ns'], node, 'kernel', etype, nhid=r.get('nhid'), gateway=r.get('gateway'),
                             oif=r.get('oif'), group=r.get('group'), fdb=r.get('fdb'))
                elif msg in ('NEWLINK', 'DELLINK'):
                    etype = 'link_' + ('change' if msg == 'NEWLINK' else 'delete') + suffix
                    self.add(r['mono_ns'], node, 'kernel', etype, mac=r.get('mac'), flags=r.get('flags'),
                             ifname=r.get('ifname'), master=r.get('master'), operstate=r.get('operstate'))

    # ------------------------------------------------------------------ ktrace
    def ktrace(self):
        path = self.path / 'ktrace.txt'
        if not path.exists():
            return
        inum = json.loads((self.path / 'netns-map.json').read_text())
        for rec in ktrace.parse(path.read_text().splitlines(), inum):
            node = rec.get('node') or 'inum:{}'.format(rec.get('inum'))
            if rec['event'] == 'nupd':
                self.add(rec['mono_ns'], node, 'ktrace', 'neigh_update_call', mac=rec.get('cur_mac'),
                         ip=rec['ip'], flags=rec['update_flags'], old_state=rec['old_state'],
                         new_state=rec['new_state'], ext_learned=rec['ext_learned'], ret=rec.get('ret'),
                         rejected=rec.get('rejected'), dev=rec.get('dev'), comm=rec['comm'], pid=rec['pid'],
                         new_mac=rec.get('new_mac'),
                         cpu=rec['cpu'], ret_mono_ns=rec.get('ret_mono_ns'))
            else:
                self.add(rec['mono_ns'], node, 'ktrace', 'arp_rx', mac=rec.get('sha'), ip=rec.get('sip'),
                         flags=[str(rec.get('op'))], op=rec.get('op'), target_ip=rec.get('tip'),
                         dev=rec.get('dev'), comm=rec['comm'], pid=rec['pid'], cpu=rec['cpu'])

    # ------------------------------------------------------------------ FRR logs
    def frr_logs(self):
        logs = sorted((self.path / 'router-logs').glob('*/*.log')) + sorted((self.path / 'router-logs').glob('*/*.log.gz'))
        for logfile in logs:
            node, daemon = logfile.parent.name, logfile.name.split('.')[0]
            if daemon not in ('zebra', 'bgpd'):
                continue
            patterns = Z_PATTERNS if daemon == 'zebra' else B_PATTERNS
            opener = gzip.open if logfile.suffix == '.gz' else open
            with opener(logfile, 'rt', errors='replace') as handle:
                for line in handle:
                    m = TS.match(line.rstrip('\n'))
                    if not m:
                        continue
                    y, mo, d, h, mi, s, us = (int(x) for x in m.groups()[:7])
                    real = calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0)) * 1_000_000_000 + us * 1000
                    mono = self.real_to_mono(real)
                    if not self.lo <= mono <= self.hi:
                        continue
                    text = m.group(9)
                    for etype, regex, names in patterns:
                        hit = regex.search(text)
                        if not hit:
                            continue
                        fields = dict(zip(names, hit.groups()))
                        mac, ip = fields.pop('mac', None), clean_ip(fields.pop('ip', None))
                        prefix = fields.get('prefix')
                        if prefix:
                            p = EVPN_PFX.search(prefix)
                            if p:
                                mac, ip = p.group(1), clean_ip(p.group(2))
                        flags = []
                        if etype == 'bgp_send':
                            flags = ['withdraw' if 'unreachable' in (fields.get('tail') or '') else 'announce']
                        if etype in ('bgp_rcvd_withdraw',):
                            flags = ['withdraw']
                        if etype in ('bgp_rcvd',):
                            flags = ['announce']
                        if 'action' in fields:
                            flags.append(fields['action'].lower())
                        if etype == 'zebra_neigh_update' and 'ext-learned' in (fields.get('extra') or ''):
                            flags.append('ext-learned')
                        self.add(mono, node, daemon, etype, mac=mac, ip=ip, flags=flags,
                                 clock='realtime log stamp mapped with trial offset', **fields)
                        break

    # ------------------------------------------------------------------ probes
    def probes(self):
        path = self.path / 'probes.jsonl'
        if not path.exists():
            return
        for line in path.read_text().splitlines():
            r = json.loads(line)
            if 'sequence' not in r:
                continue
            self.add(r['send_ns'], 'controller', 'probe', 'probe_result', ip=r['ip'],
                     flags=['ok' if r['success'] else (r.get('error') or 'lost')], ident=r['ident'],
                     seq=r['sequence'], success=r['success'], receive_ns=r['receive_ns'], rtt_ms=r['rtt_ms'])

    # ------------------------------------------------------------------ cpu / fleet
    def cpu(self):
        path = self.path / 'cpu.jsonl'
        if not path.exists():
            return
        rows = [json.loads(l) for l in path.read_text().splitlines() if l]
        hz = rows[0].get('clk_tck', 100) if rows else 100
        prev = None
        for r in rows[1:]:
            if prev is not None:
                dt = (r['mono_ns'] - prev['mono_ns']) / 1e9
                for key, value in r['ticks'].items():
                    before = prev['ticks'].get(key)
                    if value is None or before is None or dt <= 0:
                        continue
                    node, daemon = key.split('/')
                    self.add(r['mono_ns'], node, 'cpu', 'cpu_sample', daemon=daemon,
                             cpu_pct=100.0 * (value - before) / hz / dt)
            prev = r

    def fleet(self):
        for name in ('fleet-agent.jsonl',):
            path = self.path / name
            if not path.exists():
                continue
            for line in path.read_text().splitlines():
                r = json.loads(line)
                if r.get('kind') == 'move':
                    self.add(r['start_ns'], r['robot'], 'fleet', 'fleet_move', mac=r['mac'], ip=r['ip'],
                             src=r['src'], dst=r['dst'], scheduled_ns=r['scheduled_ns'],
                             detached_ns=r['detached_ns'], attached_ns=r['attached_ns'],
                             garp_ns=r.get('garp_ns'), move_err=r['move_err'], attach_err=r['attach_err'])
                elif r.get('kind') == 'final':
                    self.add(r['mono_ns'], 'fleet', 'fleet', 'fleet_final', telemetry_sent=r['telemetry_sent'],
                             telemetry_errors=r['telemetry_errors'])
        sink = self.path / 'telemetry-sink.txt'
        spec = self.path / 'fleet-spec.json'
        if sink.exists() and spec.exists():
            robots = {r['index']: r for r in json.loads(spec.read_text())['robots']}
            arrivals = {}
            for line in sink.read_text().splitlines():
                if line.startswith('#'):
                    continue
                parts = line.split()
                recv = int(parts[3])
                if self.lo <= recv <= self.hi:
                    arrivals.setdefault(int(parts[0]), []).append(recv)
            for index, times in arrivals.items():
                times.sort()
                r = robots.get(index, {})
                for a, b in zip(times, times[1:]):
                    if b - a > 1_500_000_000:
                        self.add(a, r.get('name', str(index)), 'fleet', 'telemetry_gap', mac=r.get('mac'),
                                 ip=r.get('ip'), gap_ms=(b - a) / 1e6, resumed_ns=b)
                self.add(times[0], r.get('name', str(index)), 'fleet', 'telemetry_summary', mac=r.get('mac'),
                         ip=r.get('ip'), received=len(times), first_ns=times[0], last_ns=times[-1])

    # ------------------------------------------------------------------ wire
    def node_macs(self):
        macs = {}
        for path in self.path.glob('nl-*.jsonl'):
            node = path.stem[3:]
            for line in path.read_text().splitlines():
                if '"NEWLINK"' not in line:
                    continue
                r = json.loads(line)
                if r.get('mac') and r.get('ifname'):
                    macs.setdefault(r['mac'], (node, r['ifname']))
        return macs

    def wire(self):
        macs = self.node_macs()
        idents = set()
        for target in self.manifest.get('probe', {}).get('targets', []):
            idents.add(target.split(':')[1])
        for pcap in sorted(self.path.glob('cap-*.pcapng*')):
            node = pcap.name[4:].split('.')[0]
            self.wire_packets(pcap, node, idents)
            if node.startswith(('leaf', 'spine')):
                self.wire_bgp(pcap, node, macs)

    def wire_packets(self, pcap, node, idents):
        fields = ['frame.time_epoch', 'frame.interface_name', 'eth.src', 'eth.dst', 'ip.src', 'ip.dst',
                  'vxlan.vni', 'icmp.type', 'icmp.ident', 'icmp.seq', 'arp.opcode', 'arp.src.hw_mac',
                  'arp.src.proto_ipv4', 'arp.dst.proto_ipv4', 'icmpv6.type', 'ipv6.src']
        cmd = ['tshark', '-n', '-r', str(pcap), '-Y', 'icmp or arp or icmpv6', '-T', 'fields',
               '-E', 'separator=\t', '-E', 'occurrence=a', '-E', 'aggregator=,']
        for f in fields:
            cmd += ['-e', f]
        res = subprocess.run(cmd, capture_output=True, text=True)
        for line in res.stdout.splitlines():
            cols = line.split('\t')
            if len(cols) < len(fields):
                continue
            row = dict(zip(fields, cols))
            sec, frac = row['frame.time_epoch'].split('.')
            real = int(sec) * 1_000_000_000 + int((frac + '000000000')[:9])
            mono = self.real_to_mono(real)
            iface = row['frame.interface_name']
            ips_src, ips_dst = row['ip.src'].split(','), row['ip.dst'].split(',')
            vxlan = bool(row['vxlan.vni'])
            # ip.src lists outer then inner for VXLAN-encapsulated IP; an encapsulated ARP has only the outer.
            outer = dict(outer_src=ips_src[0], outer_dst=ips_dst[0]) if vxlan and ips_src[0] else {}
            ethsrc = row['eth.src'].split(',')
            inner_mac = ethsrc[-1] if ethsrc and ethsrc[-1] else None
            if row['icmp.type']:
                ident = row['icmp.ident'].split(',')[0]
                if ident not in idents and ident != '43210':
                    continue
                itype = int(row['icmp.type'].split(',')[0])
                self.add(mono, node, 'wire', 'probe_pkt' if ident != '43210' else 'stimulus_ping_pkt',
                         ip=ips_dst[-1] if itype == 8 else ips_src[-1], mac=inner_mac,
                         flags=['echo-request' if itype == 8 else 'echo-reply' if itype == 0 else 'icmp-%d' % itype],
                         iface=iface, ident=int(ident), seq=int(row['icmp.seq'].split(',')[0] or 0),
                         inner_src=ips_src[-1], inner_dst=ips_dst[-1], vni=row['vxlan.vni'] or None, **outer)
            elif row['arp.opcode']:
                op = {'1': 'request', '2': 'reply'}.get(row['arp.opcode'], row['arp.opcode'])
                self.add(mono, node, 'wire', 'arp_pkt', mac=row['arp.src.hw_mac'], ip=row['arp.src.proto_ipv4'],
                         flags=[op], iface=iface, target_ip=row['arp.dst.proto_ipv4'],
                         eth_dst=row['eth.dst'].split(',')[-1], vni=row['vxlan.vni'] or None, **outer)
            elif row['icmpv6.type']:
                self.add(mono, node, 'wire', 'icmpv6_pkt', mac=inner_mac, ip=row['ipv6.src'].split(',')[-1],
                         flags=['icmpv6-%s' % row['icmpv6.type'].split(',')[-1]], iface=iface,
                         vni=row['vxlan.vni'] or None)

    def wire_bgp(self, pcap, node, macs):
        cmd = ['tshark', '-n', '-r', str(pcap), '-Y', 'bgp.type == 2', '-T', 'json', '-J', 'frame eth bgp']
        res = subprocess.run(cmd, capture_output=True, text=True)
        if not res.stdout.strip():
            return
        packets = json.loads(res.stdout, object_pairs_hook=lambda pairs: pairs)
        for packet in packets:
            layers = dict(packet).get('_source')
            layers = dict(layers).get('layers')
            frame = dict(dict(layers).get('frame', []))
            eth = dict(dict(layers).get('eth', []))
            sec, frac = frame.get('frame.time_epoch', '0.0').split('.')
            mono = self.real_to_mono(int(sec) * 1_000_000_000 + int((frac + '000000000')[:9]))
            iface = next((v for k, v in _iter_pairs(dict(layers).get('frame', [])) if k == 'frame.interface_name'), None)
            sender = macs.get(eth.get('eth.src'), (None, None))[0]
            receiver = macs.get(eth.get('eth.dst'), (None, None))[0]
            for index, (key, msg) in enumerate(p for p in layers if p[0] == 'bgp'):
                if not isinstance(msg, list) or dict(msg).get('bgp.type') != '2':
                    continue
                nlris, seq = [], None
                self._walk(msg, None, nlris)
                for k, v in _iter_pairs(msg):
                    if k == 'bgp.ext_com_evpn.mmac.seq':
                        seq = int(v)
                if not nlris:
                    continue
                reach = sum(1 for n in nlris if n['action'] == 'announce')
                self.add(mono, node, 'wire', 'bgp_update_msg', flags=['tx' if sender == node else 'rx'],
                         iface=iface, sender=sender, receiver=receiver, n_reach=reach,
                         n_unreach=len(nlris) - reach, msg_index=index)
                for n in nlris:
                    if n.get('rt') != '2':
                        continue
                    self.add(mono, node, 'wire', 'bgp_nlri', mac=n.get('mac'), ip=clean_ip(n.get('ip')),
                             flags=[n['action'], 'tx' if sender == node else 'rx'], iface=iface, sender=sender,
                             receiver=receiver, rd=rd_text(n.get('rd')),
                             mac_mobility_seq=seq if n['action'] == 'announce' else None)

    def _walk(self, tree, action, out):
        if not isinstance(tree, list):
            return
        keys = dict(tree)
        code = keys.get('bgp.update.path_attribute.type_code')
        if code == '14':
            action = 'announce'
        elif code == '15':
            action = 'withdraw'
        if 'bgp.evpn.nlri.rt' in keys:
            out.append(dict(action=action, rt=keys.get('bgp.evpn.nlri.rt'), rd=keys.get('bgp.evpn.nlri.rd'),
                            mac=keys.get('bgp.evpn.nlri.mac_addr'),
                            ip=keys.get('bgp.evpn.nlri.ip.addr') or keys.get('bgp.evpn.nlri.ipv6.addr')))
            return
        for _, value in tree:
            self._walk(value, action, out)

    def write(self):
        self.out.sort(key=lambda r: (r['timestamp_ns'], r['node'], r['event_type']))
        with open(self.path / 'telemetry-events.jsonl', 'w') as handle:
            handle.write(json.dumps(dict(timestamp_ns=None, node='meta', layer='meta', event_type='trial_meta',
                                         mac=None, ip=None, flags=[],
                                         details=dict(t0_mono_ns=self.t0, clock=self.clock,
                                                      config=self.manifest['config'],
                                                      robot=self.manifest['robot'],
                                                      ktrace=self.manifest.get('ktrace', {}).get('enabled'),
                                                      ktrace_stats=self.manifest.get('ktrace_stats'),
                                                      fleet=self.manifest.get('fleet'),
                                                      fleet_learnt=self.manifest.get('fleet_learnt'),
                                                      probe=self.manifest.get('probe'))),
                                    separators=(',', ':')) + '\n')
            for rec in self.out:
                handle.write(json.dumps(rec, separators=(',', ':')) + '\n')
        return len(self.out)


def rd_text(raw):
    """Render a type-1 RD ("00:01:0a:00:00:01:03:e8") as 10.0.0.1:1000."""
    try:
        b = bytes.fromhex(raw.replace(':', ''))
    except (AttributeError, ValueError):
        return raw
    if len(b) == 8 and b[:2] == b'\x00\x01':
        return '{}.{}.{}.{}:{}'.format(b[2], b[3], b[4], b[5], int.from_bytes(b[6:], 'big'))
    return raw


def _iter_pairs(tree):
    if isinstance(tree, list):
        for key, value in tree:
            if isinstance(value, list):
                yield from _iter_pairs(value)
            else:
                yield key, value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('run_dir', nargs='+')
    parser.add_argument('--no-wire', action='store_true')
    args = parser.parse_args()
    for path in args.run_dir:
        run = Run(path)
        run.harness_events()
        run.kernel()
        run.ktrace()
        run.frr_logs()
        run.probes()
        run.cpu()
        run.fleet()
        if not args.no_wire:
            run.wire()
        print('{}: {} events'.format(path, run.write()))


if __name__ == '__main__':
    main()
