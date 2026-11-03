#!/usr/bin/env python3
"""Materialize evidence tables and a two-clock SVG timeline; stdlib + tshark."""
import argparse
from collections import Counter
import html
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from measurement import decode, loss_summary


def analyze(out):
    events = [json.loads(line) for line in (out/'events.jsonl').read_text().splitlines()]
    def write(name, rows):
        (out/name).write_text(''.join(json.dumps(r)+'\n' for r in rows))
    write('forwarding-snapshots.jsonl', [e for e in events if e['kind'] in ('forwarding', 'forwarding_resolution', 'kernel_state', 'attachment_snapshot', 'old_attachment_cleanup')])
    counters = []
    for e in events:
        if e['kind'] == 'control_plane' and 'neighbors json' in e['command']:
            counters.append(dict(device=e['device'], start_ns=e['start_ns'], end_ns=e['end_ns'], neighbors=decode(e['output']), error=e['error']))
    write('device-counters.jsonl', counters)
    route_counts = []
    def paths(value):
        if isinstance(value, dict):
            if 'routeType' in value and 'valid' in value:
                yield value
            for child in value.values():
                yield from paths(child)
        elif isinstance(value, list):
            for child in value:
                yield from paths(child)
    for e in events:
        if e['kind'] == 'control_plane' and 'route type' in e['command']:
            entries = list(paths(decode(e['output'])))
            counts_by_type = Counter(('rt2_mac_ip' if 'ip' in entry or entry.get('ipLen', 0) else 'rt2_mac_only') if entry['routeType'] == 2 else 'rt'+str(entry['routeType']) for entry in entries)
            route_counts.append(dict(device=e['device'], start_ns=e['start_ns'], end_ns=e['end_ns'], path_counts=dict(counts_by_type), note='Path counts, including both spine paths; raw route keys retained separately'))
    write('route-counts.jsonl', route_counts)
    write('route-snapshots.jsonl', [e for e in events if e['kind'] in ('control_plane', 'zebra_mac')])
    probes = {}
    resolutions = [e for e in events if e['kind'] == 'forwarding_resolution']
    for path in out.glob('*-probes.jsonl'):
        robot = path.name.split('-')[0]
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        probes[robot] = loss_summary(rows)
        for row in rows:
            prior = [e for e in resolutions if e['robot'] == robot and e['observed_ns'] <= row['send_ns']]
            row['prior_forwarding_sample'] = prior[-1] if prior else None
            row['forwarding_at_send'] = 'unknown; prior sample is contextual only'
        write(robot+'-reachability.jsonl', rows)
    (out/'reachability-summary.json').write_text(json.dumps(probes, indent=2))
    packets, counts = [], {}
    for capture in sorted(out.glob('*.pcapng')):
        result = subprocess.run(['tshark', '-n', '-r', str(capture), '-Y', 'bgp or icmp or arp', '-T', 'pdml'], capture_output=True)
        if result.returncode:
            counts[capture.stem] = {'error': result.stderr.decode(errors='replace')}
            continue
        root = ET.fromstring(result.stdout)
        counter = Counter()
        for packet in root.findall('packet'):
            def values(name, scope=packet):
                return [f.get('show') for f in scope.iter('field') if f.get('name') == name]
            timestamp = values('frame.time_epoch')
            direction = values('sll.pkttype') or values('sll2.pkttype')
            direction = 'sent' if direction == ['4'] else 'received' if direction == ['0'] else 'unknown'
            for bgp in packet.findall("proto[@name='bgp']"):
                types = values('bgp.type', bgp)
                for kind in types:
                    counter[direction+'_message_type_'+kind] += 1
                announced, withdrawn = [], []
                for field in bgp.iter('field'):
                    name = field.get('name', '')
                    if name not in ('bgp.update.path_attribute.mp_reach_nlri', 'bgp.update.path_attribute.mp_unreach_nlri'):
                        continue
                    for nlri in field.iter('field'):
                        if nlri.get('name') == 'bgp.evpn.nlri':
                            rt = values('bgp.evpn.nlri.rt', nlri)
                            iplen = values('bgp.evpn.nlri.iplen', nlri)
                            item = dict(route_type=rt, ip_length=iplen, mac=values('bgp.evpn.nlri.mac_addr', nlri), ip=values('bgp.evpn.nlri.ip.addr', nlri))
                            target = withdrawn if 'unreach' in name else announced
                            target.append(item)
                            counter[direction+('_withdrawn' if 'unreach' in name else '_announced')+'_rt'+','.join(rt)+'_iplen'+','.join(iplen)] += 1
                packets.append(dict(device=capture.stem, realtime_epoch=timestamp, direction=direction,
                                    bgp_types=types, announced=announced, withdrawn=withdrawn))
        counts[capture.stem] = dict(counter)
    write('packet-events.jsonl', packets)
    (out/'packet-counts.json').write_text(json.dumps(counts, indent=2))
    moves = [e for e in events if e['kind'] == 'move_command_issued']
    if not moves:
        return
    zero = moves[0]['observed_ns']
    marks = []
    for e in events:
        if e['kind'] in ('move_command_issued', 'old_attachment_disconnected', 'new_attachment_connected'):
            marks.append(((e['observed_ns']-zero)/1e6, e['kind'], '#275dad'))
        if e['kind'] == 'forwarding_resolution':
            marks.append(((e['observed_ns']-zero)/1e6, 'leaf5 VTEP '+str(e['candidates'])+' match='+str(e['matched']), '#157f56'))
    for path in out.glob('*-probes.jsonl'):
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        recovering = False
        for row in sorted(rows, key=lambda r: r['sequence']):
            if row['success'] and recovering:
                marks.append(((row['receive_ns']-zero)/1e6, 'first reply after loss: seq '+str(row['sequence']), '#b03c42'))
                recovering = False
            if not row['success']:
                recovering = True
            if not row['success'] or abs(row['send_ns']-zero) < 200000000:
                marks.append(((row['send_ns']-zero)/1e6, 'probe seq '+str(row['sequence'])+' '+('reply RTT %.3f ms' % row['rtt_ms'] if row['success'] else 'failed (timeout observed later)'), '#b03c42'))
    marks.sort()
    marks = [m for m in marks if -2500 <= m[0] <= 6000]
    lines = ['<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="%d">' % (160+len(marks)*27), '<rect width="100%" height="100%" fill="white"/>', '<g font-family="monospace" font-size="13" fill="#17243b">', '<text x="20" y="28">One robot move: direct harness events and sampled routed forwarding</text>', '<text x="20" y="52">Time = monotonic milliseconds relative to move-command observation; dots are observations</text>']
    for i, (ms, label, color) in enumerate(marks):
        y = 90+i*27
        x = 150+(ms+2500)/8500*350
        lines += [f'<text x="20" y="{y}">{ms:9.3f} ms</text>', f'<circle cx="{x:.1f}" cy="{y-4}" r="4" fill="{color}"/>', f'<text x="540" y="{y}">{html.escape(label)}</text>']
    lines += ['<text x="20" y="%d">BGP packets use realtime: see packet-events.jsonl; no inferred cross-clock ordering.</text>' % (120+len(marks)*27), '</g></svg>']
    packet_marks = [p for p in packets if p['device'] in ('leaf1', 'leaf2', 'leaf5') and any(n['mac'] == ['00:00:00:00:00:11'] for n in p['announced']+p['withdrawn'])]
    packet_lines = ['<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="%d">' % (100+len(packet_marks)*24), '<rect width="100%" height="100%" fill="white"/>', '<g font-family="monospace" font-size="12">', '<text x="20" y="24">BGP capture clock: realtime epoch seconds; separate from monotonic timeline</text>']
    for i, packet in enumerate(packet_marks):
        label = str(packet['realtime_epoch'])+' '+packet['device']+' '+packet['direction']+' UPDATE: '+str(len(packet['announced']))+' announced, '+str(len(packet['withdrawn']))+' withdrawn NLRIs'
        packet_lines.append('<text x="20" y="%d">%s</text>' % (55+i*24, html.escape(label)))
    packet_lines += ['</g></svg>']
    (out/'timeline-bgp.svg').write_text('\n'.join(packet_lines))
    (out/'timeline.html').write_text('<!doctype html><meta charset="utf-8"><title>Phase 0 move timeline</title><h1>Robot move observations</h1><p>Attachment, forwarding and probes share the verified monotonic clock. BGP capture timestamps use realtime. The panels do not establish cross-clock causal ordering.</p><object data="timeline.svg" type="image/svg+xml" width="1100" height="%d"></object><object data="timeline-bgp.svg" type="image/svg+xml" width="1100" height="%d"></object>' % (160+len(marks)*27, 100+len(packet_marks)*24))
    (out/'timeline.svg').write_text('\n'.join(lines))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('results', type=Path)
    analyze(parser.parse_args().results)
