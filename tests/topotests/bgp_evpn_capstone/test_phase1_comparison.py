#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
"""Opt-in BBM/MBB measurement using the Phase 0 collectors."""
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import time

import pytest
from lib.topogen import get_topogen
from bgp_evpn_capstone import test_evpn_capstone as fabric
from bgp_evpn_capstone.phase0.measurement import Recorder, decode, forwarding_vteps, loss_summary

pytestmark = [pytest.mark.bgpd, pytest.mark.evpn,
              pytest.mark.skipif(os.environ.get('PHASE1B_RUN') != '1', reason='opt-in experiment')]


def setup_module(module):
    if os.environ.get('PHASE1B_RUN') == '1':
        fabric.setup_module(module)


def teardown_module(module):
    if os.environ.get('PHASE1B_RUN') == '1':
        fabric.teardown_module(module)


def test_comparison_mobility():
    tgen = get_topogen()
    assert not tgen.routers_have_failure(), tgen.errors
    out = Path(os.environ['PHASE1B_RESULTS']).resolve()
    out.mkdir(parents=True, exist_ok=False)
    rec = Recorder(out)
    robots = os.environ.get('PHASE1B_ROBOTS', 'host11').split(',')
    specs = {host: (leaf, spec, values) for leaf, spec in fabric.LEAVES.items()
             if leaf != 'leaf5' for host, values in spec['hosts'].items()}
    assert robots and len(set(robots)) == len(robots) and set(robots) <= set(specs)
    duration = float(os.environ.get('PHASE1B_DURATION', '12'))
    interval = float(os.environ.get('PHASE1B_MOVE_INTERVAL', '4'))
    disconnect = float(os.environ.get('PHASE1B_DISCONNECT', '0'))
    rate = float(os.environ.get('PHASE1B_PROBE_RATE', '10'))
    sample = float(os.environ.get('PHASE1B_SAMPLE_INTERVAL', '0.5'))
    repetitions = int(os.environ.get('PHASE1B_REPETITIONS', '1'))
    synchronized = os.environ.get('PHASE1B_SCHEDULE', 'synchronized')
    seed = int(os.environ.get('PHASE1B_SEED', '0'))
    assert 0 < duration <= 600 and 0 < interval and 0 <= disconnect < interval
    assert 0 < rate <= 100 and duration * rate < 65535 and sample >= 0.1
    assert repetitions > 0 and synchronized in ('synchronized', 'staggered')
    mode = os.environ.get('PHASE1B_MODE', 'break-before-make')
    assert mode in ('break-before-make', 'make-before-break')
    overlap = int(os.environ.get('PHASE1B_OVERLAP_MS', '250'))
    assert overlap in (100, 250, 500)
    assert robots == ['host11'] and repetitions == 1
    assert os.environ.get('PHASE1B_DAD', 'baseline') == 'baseline', (
        'Phase 0 records baseline DAD; configuration sweeps require a separate reviewed fixture')
    assert int(os.environ.get('PHASE1B_ACCESS_LEAVES', '4')) == 4
    assert int(os.environ.get('PHASE1B_SPINES', '2')) == 2
    manifest = dict(schema=1, parameters={k: v for k, v in os.environ.items() if k.startswith('PHASE1B_')},
        effective=dict(robots=robots, duration=duration, move_interval=interval,
            disconnect=disconnect, probe_rate=rate, sample_interval=sample,
            repetitions=repetitions, seed=seed, schedule=synchronized,
            mode=mode, overlap_ms=overlap if mode == 'make-before-break' else 0, access_leaves=4, spines=2, dad='baseline'),
        clock=dict(clock='CLOCK_MONOTONIC', boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            time_namespace=Path('/proc/self/timens_offsets').read_text() if Path('/proc/self/timens_offsets').exists() else None,
            note='Command observations bracket execution; snapshots are sequential, not atomic. PCAP uses realtime; no cross-clock causal ordering.'),
        revision=subprocess.getoutput('git rev-parse HEAD'),
        dirty=subprocess.getoutput('git status --short'), started_wall_ns=time.time_ns(),
        started_monotonic_ns=time.monotonic_ns(), tgen_logdir=str(tgen.logdir))
    (out/'manifest.json').write_text(json.dumps(manifest, indent=2))
    for name, node in tgen.routers().items():
        rec.command(node, 'vtysh -c "show version"', 'environment')
        rec.command(node, 'vtysh -c "show running-config"', 'configuration')
        rec.command(node, 'vtysh -c "show evpn"', 'dad_configuration')
    rec.command(tgen.gears['leaf5'], 'uname -a; cat /etc/os-release; lscpu; cat /proc/meminfo; cat /proc/self/limits; cat /proc/pressure/cpu; cat /proc/pressure/memory; cat /sys/fs/cgroup/cpu.max /sys/fs/cgroup/cpu.stat /sys/fs/cgroup/memory.max; lsns; ps -eo pid,comm,pcpu,rss; readlink /proc/self/ns/time; cat /proc/self/timens_offsets', 'environment')
    fabric.test_bgp_underlay_established()
    fabric.test_evpn_sessions_established()
    fabric._prime_host_adjacencies(tgen)
    ownership = {h: specs[h][0] for h in robots}
    processes, files, moved = [], [], []
    mbb_created = False
    worker = str(Path(__file__).parent/'phase0'/'probe.py')

    def snapshot():
        for name, node in tgen.routers().items():
            for command in ('show bgp neighbors json', 'show bgp l2vpn evpn route type macip json',
                            'show bgp l2vpn evpn route type prefix json'):
                rec.command(node, 'vtysh -c "{}"'.format(command), 'control_plane')
        for leaf in fabric.LEAVES:
            node = tgen.gears[leaf]
            rec.command(node, 'vtysh -c "show evpn mac vni 1000 json"', 'zebra_mac')
            rec.command(node, 'bridge -j fdb show; ip -j neigh show vrf VRF500', 'kernel_state')
        node = tgen.gears['leaf5']
        neigh = decode(rec.command(node, 'ip -j neigh show vrf VRF500', 'forwarding')['output'])
        fdb = decode(rec.command(node, 'bridge -j fdb show', 'forwarding')['output'])
        rec.command(node, 'ip -j route show vrf VRF500; ip -j nexthop show; ip -d -j link show type vxlan; ip -j route show', 'forwarding')
        for h in robots:
            ip = specs[h][2][0].split('/')[0]
            route = decode(rec.command(node, 'ip -j route get {} vrf VRF500 from 20.0.0.51 iif br2000'.format(ip), 'forwarding')['output'])
            vteps = forwarding_vteps(route, neigh, fdb)
            rec.event('forwarding_resolution', robot=h, actual_leaf=ownership[h],
                actual_vtep=fabric.LEAVES[ownership[h]]['lo'] if ownership[h] else None,
                candidates=vteps, matched=(vteps == [fabric.LEAVES[ownership[h]]['lo']]) if ownership[h] and vteps else None,
                evidence='inferred route-get -> neighbor RMAC -> vni500 FDB; sequential snapshots')
            for leaf in fabric.LEAVES:
                rec.command(tgen.gears[leaf], 'ip -j link show dev eth-{}'.format(h), 'attachment_snapshot')
        rec.command(node, 'cat /proc/pressure/cpu /sys/fs/cgroup/cpu.stat; ps -eo pid,comm,pcpu,rss', 'resources')

    try:
        for name in list(tgen.routers()) + robots + ['controller']:
            node = tgen.net[name]
            rec.command(node, 'readlink /proc/self/ns/time; cat /proc/self/timens_offsets', 'clock_check')
            handle = (out/(name+'-capture.log')).open('w')
            files.append(handle)
            capture = node.popen(['tshark', '-n', '-i', 'any', '-f', 'tcp port 179 or udp port 4789 or icmp or arp',
                                  '-w', str(out/(name+'.pcapng'))], stdout=handle, stderr=handle)
            processes.append(capture)
        time.sleep(1)
        for process in processes:
            rec.event('capture_status', pid=process.pid, running=process.poll() is None)
            assert process.poll() is None, 'capture failed to start'
        for index, h in enumerate(robots):
            processes.append(tgen.net['controller'].popen(['python3', worker,
                specs[h][2][0].split('/')[0], str(out/(h+'-probes.jsonl')),
                '--duration', str(duration), '--rate', str(rate), '--identifier', str(42000+index)]))
        start = time.monotonic()
        rng = random.Random(seed)
        order = robots[:]
        rng.shuffle(order)
        schedule = sorted((interval*(r+1) + (i*interval/len(order) if synchronized == 'staggered' else 0), h)
                          for r in range(repetitions) for i, h in enumerate(order))
        assert max(t for t, _ in schedule) + disconnect + 1 < duration, 'duration too short for requested moves'
        next_sample = start
        while time.monotonic() - start < duration:
            elapsed = time.monotonic() - start
            while schedule and schedule[0][0] <= elapsed:
                requested, h = schedule.pop(0)
                source = ownership[h]
                target = 'leaf2' if source == 'leaf1' else 'leaf1'
                port = 'eth-'+h
                old, new = tgen.gears[source], tgen.gears[target]
                rec.event('move_command_issued', robot=h, source=source, target=target,
                          scheduled_ns=int((start+requested)*1e9), semantics='serialized commands; synchronized means common scheduled deadline')
                if mode == 'break-before-make':
                    old.cmd_raises('ip link set {} nomaster'.format(port))
                    ownership[h] = None
                    moved.append(h)
                    rec.event('old_attachment_disconnected', robot=h, leaf=source, interface=port, evidence='command completion')
                    if disconnect:
                        time.sleep(disconnect)
                    old.cmd_raises('ip link set {} netns {}'.format(port, new.net.pids[-1]))
                    new.cmd_raises('ip link set {} master br1000'.format(port))
                    new.cmd_raises('ip link set {} up'.format(port))
                    rec.event('new_attachment_connected', robot=h, leaf=target, interface=port, evidence='command completion')
                else:
                    host = tgen.gears[h]
                    host.cmd_raises('ip link add mbb-host type veth peer name mbb-leaf')
                    mbb_created = True
                    rec.event('new_interface_created')
                    host.cmd_raises('ip link set mbb-host address {}'.format(specs[h][2][1]))
                    host.cmd_raises('ip link set mbb-leaf netns {}'.format(new.net.pids[-1]))
                    host.cmd_raises('ip link set mbb-host up')
                    new.cmd_raises('ip link set mbb-leaf master br1000')
                    new.cmd_raises('ip link set mbb-leaf up')
                    attached = time.monotonic_ns()
                    rec.event('new_attachment_connected', robot=h, leaf=target, interface='mbb-leaf', evidence='command completion')
                    rec.command(old, 'ip -j link show dev eth-host11', 'overlap_attachment')
                    rec.command(new, 'ip -j link show dev mbb-leaf', 'overlap_attachment')
                    worker_arp = str(Path(__file__).parent/'phase1'/'announce.py')
                    host.cmd_raises('python3 {} mbb-host {} {}'.format(worker_arp, specs[h][2][1], specs[h][2][0].split('/')[0]))
                    rec.event('gratuitous_arp_sent', evidence='command completion')
                    state = rec.command(new, 'vtysh -c "show evpn mac vni 1000 json"', 'overlap_zebra')
                    parsed = decode(state['output']) or {}
                    row = parsed.get('macs', {}).get(specs[h][2][1], {})
                    if row.get('type') == 'local' and row.get('intf') == 'mbb-leaf':
                        rec.event('new_leaf_mac_confirmed', state=row, evidence='sampled Zebra')
                    else:
                        raise AssertionError('leaf2 MAC learning not confirmed during overlap')
                    time.sleep(max(0, (attached + overlap * 1000000 - time.monotonic_ns()) / 1e9))
                    old.cmd_raises('ip link set eth-host11 nomaster')
                    rec.event('old_attachment_disconnected', robot=h, leaf=source, observed_overlap_ns=time.monotonic_ns()-attached, evidence='command completion')
                    host.cmd_raises('ip addr del 45.0.0.11/24 dev eth0')
                    host.cmd_raises('ip addr add 45.0.0.11/24 dev mbb-host')
                    host.cmd_raises('ip route replace default via 45.0.0.1 dev mbb-host')
                    rec.event('ip_route_transfer_completed', evidence='command completion')
                ownership[h] = target
                final_port = 'mbb-leaf' if mode == 'make-before-break' else port
                final_link = json.loads(new.cmd_raises('ip -j link show dev ' + final_port))[0]
                assert final_link.get('master') == 'br1000', final_link
                rec.event('final_attachment_verified', leaf=target, interface=final_port)
                rec.command(old, 'ip -j link show; bridge -j fdb show', 'old_attachment_cleanup')
                handle = (out/(h+'-stimulus.log')).open('a')
                files.append(handle)
                processes.append(tgen.net[h].popen(['ping', '-c', '2', '-W', '1', specs[h][1]['gw']], stdout=handle, stderr=handle))
                elapsed = time.monotonic()-start
            if time.monotonic() >= next_sample:
                snapshot()
                next_sample = time.monotonic() + sample
            time.sleep(0.01)
        for process in processes:
            if process.args and 'probe.py' in str(process.args):
                assert process.wait(timeout=5) == 0
        snapshot()
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        for handle in files:
            handle.close()
        if mbb_created:
            host = tgen.gears['host11']
            host.cmd('ip addr del 45.0.0.11/24 dev mbb-host 2>/dev/null')
            host.cmd('ip addr replace 45.0.0.11/24 dev eth0')
            host.cmd('ip route replace default via 45.0.0.1 dev eth0')
            host.cmd('ip link del mbb-host 2>/dev/null')
            tgen.gears['leaf1'].cmd_raises('ip link set eth-host11 master br1000')
            tgen.gears['leaf1'].cmd_raises('ip link set eth-host11 up')
            rec.command(host, 'ip -j addr; ip -j route', 'cleanup_host')
            rec.command(tgen.gears['leaf1'], 'ip -j link show dev eth-host11', 'cleanup_leaf1')
            rec.command(tgen.gears['leaf2'], 'ip -j link show; bridge -j fdb show', 'cleanup_leaf2')
        for h in set(moved):
            source = ownership[h]
            if source and source != specs[h][0]:
                fabric._move_robot(tgen, h, source, specs[h][0])
        for h in set(moved) | ({'host11'} if mbb_created else set()):
            host = tgen.gears[h]
            links = json.loads(host.cmd_raises('ip -j addr'))
            assert sum(a.get('local') == '45.0.0.11' for i in links for a in i.get('addr_info', [])) == 1
            assert any(a.get('local') == '45.0.0.11' for i in links if i['ifname'] == 'eth0' for a in i.get('addr_info', []))
            assert not any(i['ifname'] == 'mbb-host' for i in links)
            assert json.loads(tgen.gears['leaf1'].cmd_raises('ip -j link show dev eth-host11'))[0].get('master') == 'br1000'
            assert 'mbb-leaf' not in tgen.gears['leaf2'].cmd_raises('ip -o link show')
            rec.event('cleanup_verified')
        for name in tgen.routers():
            path = Path(tgen.logdir)/name
            if path.exists():
                shutil.copytree(path, out/'router-logs'/name, dirs_exist_ok=True)
        rec.event('run_finished')
        rec.log.close()
    summary = {}
    for h in robots:
        rows = [json.loads(line) for line in (out/(h+'-probes.jsonl')).read_text().splitlines()]
        summary[h] = loss_summary(rows)
        assert rows and any(r['success'] for r in rows), 'no successful controller probes'
    (out/'reachability-summary.json').write_text(json.dumps(summary, indent=2))
    events = [json.loads(line) for line in (out/'events.jsonl').read_text().splitlines()]
    for h in robots:
        assert any(e['kind'] == 'forwarding_resolution' and e['robot'] == h and e['actual_leaf'] != specs[h][0] and e['matched'] for e in events), 'moved forwarding path not resolved'
        for leaf in ('leaf1', 'leaf2'):
            samples = [decode(e['output']) for e in events if e['kind'] == 'zebra_mac' and e['device'] == leaf]
            assert any(specs[h][2][1] in json.dumps(state) for state in samples), 'robot absent from Zebra samples'
