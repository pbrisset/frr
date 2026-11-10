#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
"""Opt-in dual-attached host11 mobility smoke test."""
import json
import os
from pathlib import Path
import time

import pytest
from lib.topogen import get_topogen
from lib import topotest
from bgp_evpn_capstone import test_evpn_capstone as fabric
from bgp_evpn_capstone.phase1.scan_roam import validate_local_neighbor

pytestmark = [pytest.mark.bgpd, pytest.mark.evpn,
              pytest.mark.skipif(os.getenv('PHASE1_MBB') != '1', reason='opt-in')]
MAC = '00:00:00:00:00:11'
IP = '45.0.0.11'


def setup_module(module):
    if os.getenv('PHASE1_MBB') == '1':
        fabric.setup_module(module)


def teardown_module(module):
    if os.getenv('PHASE1_MBB') == '1':
        fabric.teardown_module(module)


def test_mbb_host11():
    tgen = get_topogen()
    host, old, new = (tgen.gears[n] for n in ('host11', 'leaf1', 'leaf2'))
    controller = tgen.gears['controller']
    overlap = int(os.getenv('PHASE1_OVERLAP_MS', '250'))
    assert overlap in (100, 250, 500)
    out = Path(os.environ['PHASE1_RESULTS'])
    out.mkdir(parents=True, exist_ok=False)
    events = []

    def event(kind, **data):
        events.append(dict(kind=kind, monotonic_ns=time.monotonic_ns(), **data))
        (out / 'events.json').write_text(json.dumps(events, indent=2))

    def snapshot(label):
        def leaf_snapshot(node, port):
            started = time.monotonic_ns()
            raw = node.cmd_raises(
                'ip -j link show dev {}; bridge -j fdb show; '
                'vtysh -c "show evpn mac vni 1000 json"; '
                'vtysh -c "show bgp l2vpn evpn route type macip json"'.format(port))
            neighbors = json.loads(node.cmd_raises('ip -j neigh show'))
            routes = json.loads(node.cmd_raises('ip -j route show table all'))
            return dict(raw=raw, neighbors=neighbors, routes=routes,
                        sample_start_ns=started, sample_end_ns=time.monotonic_ns())

        event(label, host=host.cmd('ip -j addr; ip -j route'),
              leaf1=leaf_snapshot(old, 'eth-host11'),
              leaf2=leaf_snapshot(new, 'mbb-leaf'))
        validate_local_neighbor(events[-1]['leaf2'], MAC, IP, 'mbb-leaf',
                                {'vni1000', 'vni500'})

    fabric.test_bgp_underlay_established()
    fabric.test_evpn_sessions_established()
    fabric._prime_host_adjacencies(tgen)
    assert not tgen.routers_have_failure(), tgen.errors
    try:
        host.cmd_raises('ip link add mbb-host type veth peer name mbb-leaf')
        host.cmd_raises('ip link set mbb-host address ' + MAC)
        host.cmd_raises('ip link set mbb-leaf netns {}'.format(new.net.pids[-1]))
        host.cmd_raises('ip link set mbb-host up')
        new.cmd_raises('ip link set mbb-leaf master br1000')
        new.cmd_raises('ip link set mbb-leaf up')
        start = time.monotonic_ns()
        event('new_attached', requested_overlap_ms=overlap)
        assert json.loads(old.cmd_raises('ip -j link show dev eth-host11'))[0]['master'] == 'br1000'
        assert json.loads(new.cmd_raises('ip -j link show dev mbb-leaf'))[0]['master'] == 'br1000'
        addresses = json.loads(host.cmd_raises('ip -j addr'))
        assert sum(a.get('local') == IP for i in addresses for a in i.get('addr_info', [])) == 1
        assert next(i for i in addresses if i['ifname'] == 'mbb-host')['address'] == MAC
        announce = Path(__file__).parent / 'phase1' / 'announce.py'
        host.cmd_raises('python3 {} mbb-host {} {}'.format(announce, MAC, IP))
        event('gratuitous_arp_sent')
        snapshot('overlap_snapshot')
        # Each field concatenates four JSON command outputs; the third is Zebra.
        text = events[-1]['leaf2']['raw']
        decoder = json.JSONDecoder()
        for _ in range(3):
            state, end = decoder.raw_decode(text.lstrip())
            text = text.lstrip()[end:]
        learned = state['macs'][MAC]
        assert learned['type'] == 'local' and learned['intf'] == 'mbb-leaf'
        time.sleep(max(0, (start + overlap * 1000000 - time.monotonic_ns()) / 1e9))
        old.cmd_raises('ip link set eth-host11 nomaster')
        event('old_detached', observed_overlap_ns=time.monotonic_ns() - start)
        host.cmd_raises('ip addr del {}/24 dev eth0'.format(IP))
        host.cmd_raises('ip addr add {}/24 dev mbb-host'.format(IP))
        host.cmd_raises('ip route replace default via 45.0.0.1 dev mbb-host')
        host.cmd_raises('ping -c 2 -W 1 45.0.0.1')
        def reachable():
            return None if '1 received' in controller.cmd('ping -c 1 -W 1 {}'.format(IP)) else 'unreachable'
        _, result = topotest.run_and_expect(reachable, None, count=30, wait=1)
        assert result is None, result
        event('controller_reachable')
        snapshot('final_snapshot')
        assert 'master' not in json.loads(old.cmd_raises('ip -j link show dev eth-host11'))[0]
        assert json.loads(new.cmd_raises('ip -j link show dev mbb-leaf'))[0]['master'] == 'br1000'
    finally:
        # Restore the one address and its default route even after a partial move.
        host.cmd('ip addr del {}/24 dev mbb-host 2>/dev/null'.format(IP))
        host.cmd('ip addr replace {}/24 dev eth0'.format(IP))
        host.cmd('ip route replace default via 45.0.0.1 dev eth0')
        host.cmd('ip link del mbb-host 2>/dev/null')
        old.cmd_raises('ip link set eth-host11 master br1000')
        old.cmd_raises('ip link set eth-host11 up')
        host.cmd('ping -c 2 -W 1 45.0.0.1')
        event('cleanup', host=host.cmd('ip -j addr; ip -j route'),
              leaf1=old.cmd('ip -j link show dev eth-host11; bridge -j fdb show'),
              leaf2=new.cmd('ip -j link show; bridge -j fdb show'))
        links = json.loads(host.cmd_raises('ip -j addr'))
        assert not any(i['ifname'] == 'mbb-host' for i in links)
        assert sum(a.get('local') == IP for i in links for a in i.get('addr_info', [])) == 1
        assert any(a.get('local') == IP for i in links if i['ifname'] == 'eth0' for a in i.get('addr_info', []))
        assert json.loads(old.cmd_raises('ip -j link show dev eth-host11'))[0]['master'] == 'br1000'
        assert 'mbb-leaf' not in new.cmd_raises('ip -o link show')
