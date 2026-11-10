#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
"""Inspect phase1 events.json snapshots for a local FDB/neighbor mismatch.

The original smoke captures concatenate JSON outputs from link, bridge FDB,
EVPN MAC, and BGP commands. Newer captures can include nonempty ip -j neigh
and ip -j route outputs, or use explicit {fdb, neighbors, routes} fields.
Empty tables must be labeled to distinguish them from other empty outputs.
"""
import argparse
import ipaddress
import json
import sys


def decode_stream(value):
    if not isinstance(value, str):
        return [value]
    decoder = json.JSONDecoder()
    result = []
    while value.strip():
        value = value.lstrip()
        item, end = decoder.raw_decode(value)
        result.append(item)
        value = value[end:]
    return result


def tables(value):
    """Extract only recognizable or explicitly named tables; missing != empty."""
    result = {}
    for item in decode_stream(value):
        if isinstance(item, dict):
            if 'raw' in item:
                result.update(tables(item['raw']))
            for name in ('fdb', 'neighbors', 'routes'):
                if name in item and isinstance(item[name], list):
                    result[name] = item[name]
        elif isinstance(item, list) and item and all(isinstance(x, dict) for x in item):
            if any('mac' in x and 'ifname' in x for x in item):
                result['fdb'] = item
            elif any('dst' in x and ('lladdr' in x or 'state' in x) for x in item):
                result['neighbors'] = item
            elif any('dst' in x and ('gateway' in x or 'dev' in x or 'nexthops' in x) for x in item):
                result['routes'] = item
    return result


def validate_local_neighbor(snapshot, mac, ip, port, tunnels):
    """Fail when a local FDB entry coexists with a tunnel-bound NOARP neighbor."""
    state = tables(snapshot)
    if 'fdb' not in state or 'neighbors' not in state:
        raise AssertionError('FDB or neighbor table was not captured')
    local = any(entry.get('mac', '').lower() == mac.lower()
                and entry.get('ifname') == port and entry.get('master')
                and 'self' not in entry.get('flags', [])
                for entry in state['fdb'])
    for neighbor in state['neighbors']:
        if (local and neighbor.get('dst') == str(ip)
                and neighbor.get('dev') in tunnels
                and 'extern_learn' in neighbor
                and 'NOARP' in neighbor.get('state', [])):
            raise AssertionError(
                f'Local MAC {mac} on {port}, but neighbor {ip} is '
                f'extern_learn NOARP on tunnel {neighbor.get("dev")}')


def matches_route(route, ip):
    try:
        return ip in ipaddress.ip_network(route.get('dst', 'default').replace('default', '0.0.0.0/0'), strict=False)
    except ValueError:
        return False


def scan(events, leaf, mac, ip, port, tunnel):
    start = None
    last_match = None
    count = 0
    for event in events:
        kind = event.get('kind', '')
        if kind in ('new_attached', 'roam', 'move', 'move_complete'):
            start = event.get('monotonic_ns')
            last_match = None
            print(f"Move marker: {kind}, monotonic_ns={start}")
            continue
        if kind == 'cleanup':
            break
        if start is None or leaf not in event:
            continue
        count += 1
        state = tables(event[leaf])
        timestamp = event.get('monotonic_ns')
        delta = (timestamp - start) / 1e6 if isinstance(timestamp, int) else None
        print(f"\n{kind} at +{delta:.3f} ms" if delta is not None else f"\n{kind} (time unknown)")
        fdb = state.get('fdb')
        local = [] if fdb is None else [x for x in fdb if x.get('mac', '').lower() == mac and x.get('ifname') == port and x.get('master') and 'self' not in x.get('flags', [])]
        print('  FDB:', 'not captured' if fdb is None else ('local on ' + port if local else 'no local entry on ' + port))
        neighs = state.get('neighbors')
        entries = [] if neighs is None else [x for x in neighs if x.get('dst') == str(ip)]
        print('  Neighbor:', 'not captured' if neighs is None else json.dumps(entries) if entries else 'no matching entry')
        routes = state.get('routes')
        matching = [] if routes is None else [x for x in routes if matches_route(x, ip)]
        print('  Routes:', 'not captured' if routes is None else json.dumps(matching) if matching else 'no matching route in captured table')
        flagged = [x for x in entries if 'extern_learn' in x and 'NOARP' in x.get('state', [])]
        remote_neigh = [x for x in flagged if x.get('dev') in tunnel]
        remote_routes = [x for x in matching if x.get('dev') in tunnel or any(n.get('dev') in tunnel for n in x.get('nexthops', []))]
        if local and flagged:
            print('  OBSERVED: local FDB and externally learned NOARP neighbor coexist.')
        if local and (remote_neigh or remote_routes):
            print('  SUSPECT: local FDB and a tunnel-facing neighbor or matching route coexist; route selection and packet loss are not established.')
            if last_match is not None and isinstance(timestamp, int):
                print(f'  Matching observations span {(timestamp - last_match) / 1e6:.3f} ms; intermediate state is unobserved.')
            if last_match is None:
                last_match = timestamp
        else:
            last_match = None
        if local and neighs is not None and not entries:
            print('  OBSERVED: local FDB with no matching neighbor in the captured table; this alone does not prove a black hole.')
        if local and routes is not None and not matching:
            print('  OBSERVED: local FDB with no matching route in this captured table; other routing tables or rules may apply.')
    if start is None:
        print('No recognized move marker found; cannot establish event order.')
    elif not count:
        print(f'No post-move snapshots for {leaf} before cleanup.')
    print('\nSnapshots are samples, not continuous proof of duration or packet loss. '
          'Bridge SVI neighbors (e.g. br1000) are not VXLAN interfaces; '
          'use route lookup and packet captures to verify forwarding and loss.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events', help='path to events.json')
    parser.add_argument('--ip', required=True, type=ipaddress.ip_address, help='robot IP address')
    parser.add_argument('--mac', required=True, help='robot MAC address')
    parser.add_argument('--leaf', required=True, help='new local leaf field in events.json')
    parser.add_argument('--port', required=True, help='new local bridge access port')
    parser.add_argument('--tunnel', action='append', default=[], help='VXLAN interface name; repeat as needed')
    args = parser.parse_args()
    try:
        with open(args.events, encoding='utf-8') as source:
            events = json.load(source)
        if not isinstance(events, list) or not all(isinstance(e, dict) for e in events):
            raise ValueError('expected a JSON array of event objects')
        scan(events, args.leaf, args.mac.lower(), args.ip, args.port, set(args.tunnel))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
