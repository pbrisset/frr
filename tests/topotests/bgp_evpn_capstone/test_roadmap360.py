#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
"""Opt-in 360-degree EVPN mobility trial (one fresh topology per invocation).

Observation inside the window is event-driven only: rtnetlink listeners,
kernel kprobes (__neigh_update, arp_process), FRR debug logs, packet
captures, controller probes and /proc CPU counters.  vtysh/ip CLI commands
run only before the window (preconditions) and after it (final state).

Environment (all optional except R360_RUN and R360_OUT):
  R360_TEST_ID, R360_WS          labels
  R360_PATTERN   none|immediate|delayed|burst|ping|garp
  R360_MODE      bbm|mbb          R360_OVERLAP_MS 100|250|500
  R360_FLEET     none|baseline|low64|mob250|scale1000|roam64-sync|roam64-staggered
  R360_PRE_S (6) R360_POST_S (20) R360_PROBE_RATE (10) R360_SEED (0)
"""
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest
from lib import topotest
from lib.topogen import Topogen, get_topogen
from bgp_evpn_capstone import test_evpn_capstone as fabric
from bgp_evpn_capstone.roadmap360 import fleet, ktrace

RUN = os.environ.get('R360_RUN') == '1'
pytestmark = [pytest.mark.bgpd, pytest.mark.evpn,
              pytest.mark.skipif(not RUN, reason='opt-in experiment')]
TOOLS = Path(__file__).resolve().parent / 'roadmap360'
FLEET_SIZE = {'none': 0, 'baseline': 0, 'low64': 64, 'mob250': 250, 'scale1000': 1000,
              'roam64-sync': 64, 'roam64-staggered': 64}
DEBUGS = ['log timestamp precision 6', 'debug zebra events', 'debug zebra vxlan',
          'debug bgp zebra', 'debug bgp updates in', 'debug bgp updates out']
ROBOT, ROBOT_IP, ROBOT_MAC, GW = 'host11', '45.0.0.11', '00:00:00:00:00:11', '45.0.0.1'
SRC, DST = 'leaf1', 'leaf2'
CAPTURE_FILTER = ('tcp port 179 or arp or icmp or icmp6 or (udp port 4789 and '
                  '(udp[28:2] = 0x0806 or udp[28:2] = 0x86dd or (udp[28:2] = 0x0800 and udp[39] = 1)))')


def cfg():
    env = os.environ
    c = dict(test_id=env.get('R360_TEST_ID', 'adhoc'), ws=env.get('R360_WS', 'X'),
             pattern=env.get('R360_PATTERN', 'none'), mode=env.get('R360_MODE', 'bbm'),
             overlap_ms=int(env.get('R360_OVERLAP_MS', '0')), fleet=env.get('R360_FLEET', 'none'),
             seed=int(env.get('R360_SEED', '0')), pre_s=float(env.get('R360_PRE_S', '6')),
             post_s=float(env.get('R360_POST_S', '20')),
             probe_rate=float(env.get('R360_PROBE_RATE', '10')))
    assert c['pattern'] in ('none', 'immediate', 'delayed', 'burst', 'ping', 'garp')
    assert c['mode'] in ('bbm', 'mbb') and c['fleet'] in FLEET_SIZE
    assert c['mode'] == 'bbm' or c['overlap_ms'] in (100, 250, 500)
    assert c['pre_s'] >= 6 or c['fleet'] != 'mob250'
    return c


def setup_module(mod):
    if not RUN:
        return
    c = cfg()
    extra = c['fleet'] != 'none'
    tgen = Topogen(fabric.build_topo, mod.__name__)
    tgen.start_topology()
    for leaf, spec in fabric.LEAVES.items():
        fabric._setup_leaf_dataplane(tgen, leaf, spec)
        if extra and leaf in fleet.ACCESS_LEAVES:
            tgen.net[leaf].cmd_raises(fleet.extra_vni_dataplane(spec['lo']))
    for spec in fabric.LEAVES.values():
        for host, (_, mac) in spec['hosts'].items():
            tgen.net[host].cmd_raises('ip link set dev eth0 address {}'.format(mac))
    for rname, router in tgen.routers().items():
        conf = (Path(fabric.CWD) / rname / 'frr.conf').read_text()
        head = 'frr defaults datacenter\n'
        assert conf.startswith(head)
        conf = head + '\n'.join(DEBUGS) + '\n' + conf[len(head):]
        if extra and rname in fleet.ACCESS_LEAVES:
            conf = fleet.extra_vni_frr(conf, fabric.LEAVES[rname]['lo'])
        path = Path(tgen.logdir) / '{}-r360.conf'.format(rname)
        path.write_text(conf)
        router.load_frr_config(str(path))
    tgen.start_router()


def teardown_module(mod):
    if RUN:
        get_topogen().stop_topology()


def _inum(pid):
    return os.readlink('/proc/{}/ns/net'.format(pid)).split('[')[1].rstrip(']')


def _clock_pairs(samples=2000):
    """Bracket CLOCK_REALTIME between two CLOCK_MONOTONIC reads; keep tightest."""
    best = None
    for _ in range(samples):
        m1 = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
        r = time.clock_gettime_ns(time.CLOCK_REALTIME)
        m2 = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
        if best is None or m2 - m1 < best[2] - best[0]:
            best = (m1, r, m2)
    m1, r, m2 = best
    return dict(realtime_ns=r, mono_mid_ns=(m1 + m2) // 2, bracket_ns=m2 - m1,
                offset_ns=r - (m1 + m2) // 2)


def test_roadmap360_trial():
    tgen = get_topogen()
    assert not tgen.routers_have_failure(), tgen.errors
    c = cfg()
    out = Path(os.environ['R360_OUT']).resolve()
    out.mkdir(parents=True, exist_ok=False)
    events = (out / 'harness-events.jsonl').open('x', buffering=1)

    def event(kind, **data):
        rec = dict(kind=kind, mono_ns=time.monotonic_ns(), **data)
        events.write(json.dumps(rec) + '\n')
        return rec

    manifest = dict(schema='r360-1', config=c, started_wall_ns=time.time_ns(),
                    revision=subprocess.getoutput('git -C {} rev-parse HEAD'.format(fabric.CWD)),
                    dirty=subprocess.getoutput('git -C {} status --short'.format(fabric.CWD)),
                    kernel=os.uname().release, robot=dict(name=ROBOT, ip=ROBOT_IP, mac=ROBOT_MAC,
                                                          src=SRC, dst=DST, gw=GW),
                    debugs=DEBUGS, capture_filter=CAPTURE_FILTER, tgen_logdir=str(tgen.logdir))

    # ---- preconditions (pre-window CLI is allowed) ------------------------
    fabric.test_bgp_underlay_established()
    fabric.test_evpn_sessions_established()
    fabric._prime_host_adjacencies(tgen)
    pre = {}
    for name, router in tgen.routers().items():
        pre[name] = dict(version=router.vtysh_cmd('show version'),
                         evpn=router.vtysh_cmd('show evpn'),
                         debugging=router.vtysh_cmd('show debugging'))
    manifest['pre_cli'] = pre

    def leaf5_has_robot():
        out5 = tgen.gears['leaf5'].cmd('ip -j route show vrf VRF500 {}'.format(ROBOT_IP))
        n2 = tgen.gears[DST].cmd('ip -j neigh show {} dev br1000'.format(ROBOT_IP))
        ok = ROBOT_IP in out5 and 'extern_learn' in n2 and 'NOARP' in n2
        return None if ok else 'leaf5 /32 or leaf2 remote NOARP neighbour missing'
    ok, why = topotest.run_and_expect(leaf5_has_robot, None, count=60, wait=1)
    manifest['precondition'] = dict(ok=ok, detail=why,
                                     leaf2_neigh=tgen.gears[DST].cmd('ip -d -j neigh show {}'.format(ROBOT_IP)),
                                     leaf5_route=tgen.gears['leaf5'].cmd('ip -j route show vrf VRF500'))
    assert ok, why

    processes, handles = [], []
    robots = []
    nfleet = FLEET_SIZE[c['fleet']]
    leaf_paths = {}
    mbb_created = False
    ktrace_status = None
    try:
        # ---- fleet construction (pre-window) ------------------------------
        if nfleet:
            t = time.monotonic()
            leaf_paths = fleet.attach_leaves(tgen, fleet.ACCESS_LEAVES)
            robots = fleet.assign_ports(fleet.robot_plan(nfleet), c['fleet'])
            fleet.create(robots)
            manifest['fleet_build_s'] = time.monotonic() - t
            sink = tgen.net['controller'].popen(['python3', str(TOOLS / 'sink.py'), str(out / 'telemetry-sink.txt')])
            processes.append(sink)
            warm = dict(controller_ip='20.0.0.51', telemetry_hz=1.0, leaves=leaf_paths,
                        robots=robots, moves=[], start_ns=time.monotonic_ns(),
                        stop_ns=time.monotonic_ns() + int(3600e9))
            (out / 'fleet-spec-static.json').write_text(json.dumps(warm))
            agent_log = (out / 'fleet-agent.log').open('w')
            handles.append(agent_log)
            # Telemetry-only agent first: robots ARP for their gateway and are learnt.
            agent = subprocess.Popen(['python3', str(TOOLS / 'fleet_agent.py'), str(out / 'fleet-spec-static.json'),
                                      str(out / 'fleet-agent-warmup.jsonl')], stdout=agent_log, stderr=agent_log)

            (out / 'fleet-ips.txt').write_text(''.join(r['ip'] + '\n' for r in robots))

            def fleet_learnt():
                # Count inside the node: a 1000-route listing through the munet shell is not relied upon.
                raw = tgen.gears['leaf5'].cmd("ip -o route show vrf VRF500 | awk '{{print $1}}' | "
                                              "grep -cxFf {}".format(out / 'fleet-ips.txt'))
                try:
                    return int(raw.strip().splitlines()[-1])
                except (ValueError, IndexError):
                    return 0
            deadline = time.monotonic() + 240
            have = 0
            while time.monotonic() < deadline:
                have = fleet_learnt()
                if have >= nfleet:
                    break
                time.sleep(2)
            manifest['fleet_learnt'] = dict(have=have, want=nfleet, waited_s=time.monotonic() - t)
            agent.send_signal(signal.SIGTERM)
            agent.wait(timeout=10)
            assert have >= int(0.98 * nfleet), 'fleet not learnt at leaf5: {}/{}'.format(have, nfleet)
            time.sleep(2)

        # ---- namespace map, calibration, robot conditioning ---------------
        inum_map = {}
        for name, node in tgen.net.hosts.items():
            try:
                inum_map[_inum(node.pid)] = name
            except (OSError, AttributeError, TypeError):
                pass
        for r in robots:
            try:
                inum_map[str(os.stat(r['netns_path']).st_ino)] = r['name']
            except OSError:
                pass
        inum_map[_inum(os.getpid())] = 'root'
        (out / 'netns-map.json').write_text(json.dumps(inum_map))
        manifest['clock_start'] = _clock_pairs()
        tgen.gears[ROBOT].cmd('ping -c 1 -W 1 {}'.format(GW))
        manifest['robot_neigh_at_start'] = tgen.gears[ROBOT].cmd('ip -s -j neigh show')

        # ---- start event-driven observers ---------------------------------
        ktrace_status = ktrace.setup()
        manifest['ktrace'] = ktrace_status
        nl_nodes = list(tgen.routers()) + [ROBOT, 'controller']
        for name in nl_nodes:
            processes.append(tgen.net[name].popen(['python3', str(TOOLS / 'nlmon.py'),
                                                   str(out / 'nl-{}.jsonl'.format(name)), '--node', name]))
        cap_nodes = ['spine1', 'spine2', 'leaf1', 'leaf2', 'leaf3', 'leaf4', 'leaf5', ROBOT, 'controller']
        for name in cap_nodes:
            node = tgen.net[name]
            links = json.loads(node.cmd_raises('ip -j link show'))
            wanted = []
            for link in links:
                ifname = link['ifname']
                if ifname == 'lo' or ifname.startswith('eth-host') or ifname.startswith('f') \
                        or ifname.startswith('VRF') or ifname.startswith('mbb'):
                    continue
                if ifname[-4:] in ('1001', '1002', '1003', '1004'):
                    continue
                wanted.append(ifname)
            log = (out / 'cap-{}.log'.format(name)).open('w')
            handles.append(log)
            cmd = ['dumpcap', '-q', '-B', '32', '-f', CAPTURE_FILTER, '-w', str(out / 'cap-{}.pcapng'.format(name))]
            for ifname in wanted:
                cmd[1:1] = ['-i', ifname]
            processes.append(node.popen(cmd, stdout=log, stderr=log))
            event('capture_started', node=name, interfaces=wanted)
        cpu = subprocess.Popen(['python3', str(TOOLS / 'cpusample.py'), str(out / 'netns-map.json'),
                                str(out / 'cpu.jsonl')])
        processes.append(cpu)
        time.sleep(2.0)
        for proc in processes:
            if proc.poll() is not None:
                raise AssertionError('observer exited early: {}'.format(proc.args))
        event('observers_ready')

        # ---- probes, stimulus, fleet agent ---------------------------------
        total = c['pre_s'] + c['post_s']
        targets = ['{}:{}'.format(ROBOT_IP, 42000)]
        if c['fleet'].startswith('roam64'):
            targets += ['{}:{}'.format(r['ip'], 43000 + r['index']) for r in robots[:64]]
        probe_rate = c['probe_rate'] if len(targets) == 1 else 5.0
        prober = tgen.net['controller'].popen(['python3', str(TOOLS / 'probe.py'), str(out / 'probes.jsonl'),
                                               '--rate', str(probe_rate), '--duration', str(total)]
                                              + sum([['--target', t] for t in targets], []))
        processes.append(prober)
        manifest['probe'] = dict(targets=targets, rate=probe_rate, duration_s=total)
        stim_log = (out / 'stimulus.jsonl').open('w')
        handles.append(stim_log)
        stim = tgen.net[ROBOT].popen(['python3', str(TOOLS / 'stimulus.py'), c['pattern'], '--mac', ROBOT_MAC,
                                      '--ip', ROBOT_IP, '--gw', GW], stdin=subprocess.PIPE,
                                     stdout=stim_log, stderr=stim_log, text=True)
        processes.append(stim)
        t_start = time.monotonic_ns()
        t0_planned = t_start + int(c['pre_s'] * 1e9)
        if nfleet:
            moves = fleet.schedule(robots, c['fleet'], t0_planned, c['seed'])
            spec = dict(controller_ip='20.0.0.51', telemetry_hz=1.0, leaves=leaf_paths, robots=robots,
                        moves=moves, start_ns=t_start, stop_ns=t0_planned + int(c['post_s'] * 1e9))
            (out / 'fleet-spec.json').write_text(json.dumps(spec))
            agent_log = (out / 'fleet-agent.log').open('a')
            handles.append(agent_log)
            processes.append(subprocess.Popen(['python3', str(TOOLS / 'fleet_agent.py'), str(out / 'fleet-spec.json'),
                                               str(out / 'fleet-agent.jsonl')], stdout=agent_log, stderr=agent_log))
            manifest['fleet'] = dict(profile=c['fleet'], robots=nfleet, moves=len(moves))
        while time.monotonic_ns() < t0_planned:
            time.sleep(min(0.01, max(0, (t0_planned - time.monotonic_ns()) / 1e9)))

        # ---- the roam (T0) -------------------------------------------------
        old, new, host = tgen.gears[SRC], tgen.gears[DST], tgen.gears[ROBOT]
        port = 'eth-' + ROBOT
        t0 = time.monotonic_ns()
        event('T0', mode=c['mode'], overlap_ms=c['overlap_ms'])
        if c['mode'] == 'bbm':
            old.cmd_raises('ip link set {} nomaster'.format(port))
            event('old_detached', leaf=SRC, iface=port)
            old.cmd_raises('ip link set {} netns {}'.format(port, new.net.pids[-1]))
            new.cmd_raises('ip link set {} master br1000'.format(port))
            new.cmd_raises('ip link set {} up'.format(port))
            attach = event('new_attached', leaf=DST, iface=port)
            stim.stdin.write('eth0 {}\n'.format(attach['mono_ns']))
            stim.stdin.flush()
        else:
            host.cmd_raises('ip link add mbb-host type veth peer name mbb-leaf')
            mbb_created = True
            host.cmd_raises('ip link set mbb-host address {}'.format(ROBOT_MAC))
            host.cmd_raises('ip link set mbb-leaf netns {}'.format(new.net.pids[-1]))
            host.cmd_raises('ip link set mbb-host up')
            new.cmd_raises('ip link set mbb-leaf master br1000')
            new.cmd_raises('ip link set mbb-leaf up')
            attach = event('new_attached', leaf=DST, iface='mbb-leaf')
            stim.stdin.write('mbb-host {}\n'.format(attach['mono_ns']))
            stim.stdin.flush()
            time.sleep(max(0, (attach['mono_ns'] + c['overlap_ms'] * 1_000_000 - time.monotonic_ns()) / 1e9))
            old.cmd_raises('ip link set {} nomaster'.format(port))
            event('old_detached', leaf=SRC, iface=port,
                  overlap_ns=time.monotonic_ns() - attach['mono_ns'])
            host.cmd_raises('ip addr del {}/24 dev eth0'.format(ROBOT_IP))
            host.cmd_raises('ip addr add {}/24 dev mbb-host'.format(ROBOT_IP))
            host.cmd_raises('ip route replace default via {} dev mbb-host'.format(GW))
            event('ip_transferred', iface='mbb-host')
        stim.stdin.close()

        # ---- observation window: no CLI, no polling ------------------------
        end = t0 + int(c['post_s'] * 1e9)
        while time.monotonic_ns() < end:
            time.sleep(0.05)
        event('window_closed')
        prober.wait(timeout=10)
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT if 'dumpcap' in str(proc.args) else signal.SIGTERM)
        for proc in processes:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        for handle in handles:
            handle.close()
        if ktrace_status is not None:
            manifest['ktrace_stats'] = ktrace.collect(str(out / 'ktrace.txt'))
            ktrace.teardown()
        manifest['clock_end'] = _clock_pairs()
        # ---- post-window state (CLI allowed again) -------------------------
        post = {}
        for name in ('leaf1', 'leaf2', 'leaf3', 'leaf5'):
            router = tgen.gears[name]
            post[name] = dict(mac=router.vtysh_cmd('show evpn mac vni 1000 mac {}'.format(ROBOT_MAC)),
                              neigh=router.cmd('ip -d neigh show {}'.format(ROBOT_IP)),
                              fdb=router.cmd('bridge fdb show | grep -i {}'.format(ROBOT_MAC)),
                              route=router.cmd('ip route show vrf VRF500 {}'.format(ROBOT_IP)),
                              dup=router.vtysh_cmd('show evpn mac vni all duplicate'))
        manifest['post_cli'] = post
        if robots:
            fleet.destroy(robots)
            fleet.detach_leaves(fleet.ACCESS_LEAVES)
            # Namespace destruction is asynchronous and holds RTNL; stopping FRR while ~1000 robot
            # namespaces are still being torn down left zebra blocked in netlink sendmsg() at exit
            # (topotest then SIGBUSes it).  Wait until the leaves' robot ports are gone.
            t_wait = time.monotonic()
            while time.monotonic() - t_wait < 240:
                left = 0
                for leaf in fleet.ACCESS_LEAVES:
                    raw = tgen.gears[leaf].cmd("ip -o link show | grep -c ': f[0-9]' || true")
                    try:
                        left += int(raw.strip().splitlines()[-1])
                    except (ValueError, IndexError):
                        left += 1
                if left == 0:
                    break
                time.sleep(2)
            time.sleep(3)
            manifest['fleet_teardown_wait_s'] = round(time.monotonic() - t_wait, 1)
        if mbb_created:
            host = tgen.gears[ROBOT]
            host.cmd('ip link del mbb-host 2>/dev/null')
        for name in tgen.routers():
            path = Path(tgen.logdir) / name
            if path.exists():
                shutil.copytree(path, out / 'router-logs' / name, dirs_exist_ok=True)
        events.close()
        manifest['finished_wall_ns'] = time.time_ns()
        (out / 'manifest.json').write_text(json.dumps(manifest, indent=1, default=str))
    rows = [json.loads(l) for l in (out / 'probes.jsonl').read_text().splitlines()]
    assert any(r.get('success') for r in rows), 'no successful controller probe at all'
