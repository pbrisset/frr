#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
"""Opt-in breakpoint360 Phase 1 trial: run one mobility workload on a fresh fabric.

Phase 1 scope only: build the robot fleet, execute the planned workload with
bp_agent.py, and validate the attachment ledger against the kernel's actual
radio state.  No control-plane instrumentation yet (Phase 2); the only FRR
state read is a pre/post BGP message-counter snapshot and a post-run route
lookup for one moved robot, kept as Phase 0 audit evidence.

Environment (BP_RUN=1 and BP_OUT are required):
  BP_ROBOTS (64)  BP_ACTIVE_PCT (100)  BP_LEAVES (4)  BP_RATE (8)  BP_DURATION (10)
  BP_MODE staggered|synchronized (staggered)  BP_SYNC_PERIOD (1.0)  BP_ARRIVAL uniform|poisson
  BP_MAX_MOVES (4)  BP_SEED (1)  BP_TELEMETRY_HZ (1)  BP_ARP_OFFSETS_MS ('' e.g. '250,1000')
  BP_LEAD_S (3)  BP_POST_S (10)  BP_TEST_ID
"""
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

import pytest
from lib import topotest
from lib.topogen import Topogen, get_topogen
from bgp_evpn_capstone import test_evpn_capstone as fabric
from bgp_evpn_capstone.roadmap360 import fleet
from bgp_evpn_capstone.breakpoint360 import ledger, workload

RUN = os.environ.get('BP_RUN') == '1'
pytestmark = [pytest.mark.bgpd, pytest.mark.evpn, pytest.mark.skipif(not RUN, reason='opt-in experiment')]
HERE = Path(__file__).resolve().parent
TOOLS = HERE / 'breakpoint360'
R360 = HERE / 'roadmap360'
PORT_RE = re.compile(r'^f(\d+)l(\d)$')


def cfg():
    env = os.environ
    offsets = [int(x) for x in env.get('BP_ARP_OFFSETS_MS', '').split(',') if x.strip()]
    overrides = dict(robots=int(env.get('BP_ROBOTS', '64')), active_pct=float(env.get('BP_ACTIVE_PCT', '100')),
                     hosting_leaves=int(env.get('BP_LEAVES', '4')), rate=float(env.get('BP_RATE', '8')),
                     duration_s=float(env.get('BP_DURATION', '10')), mode=env.get('BP_MODE', 'staggered'),
                     arrival=env.get('BP_ARRIVAL', 'uniform'), sync_period_s=float(env.get('BP_SYNC_PERIOD', '1')),
                     max_moves_per_robot=int(env.get('BP_MAX_MOVES', '4')), seed=int(env.get('BP_SEED', '1')),
                     telemetry_hz=float(env.get('BP_TELEMETRY_HZ', '1')), arp_offsets_ms=offsets)
    return dict(test_id=env.get('BP_TEST_ID', 'adhoc'), workload=overrides,
                lead_s=float(env.get('BP_LEAD_S', '3')), post_s=float(env.get('BP_POST_S', '10')))


def setup_module(mod):
    if not RUN:
        return
    tgen = Topogen(fabric.build_topo, mod.__name__)
    tgen.start_topology()
    for leaf, spec in fabric.LEAVES.items():
        fabric._setup_leaf_dataplane(tgen, leaf, spec)
        if leaf in fleet.ACCESS_LEAVES:
            tgen.net[leaf].cmd_raises(fleet.extra_vni_dataplane(spec['lo']))
    for spec in fabric.LEAVES.values():
        for host, (_, mac) in spec['hosts'].items():
            tgen.net[host].cmd_raises('ip link set dev eth0 address {}'.format(mac))
    for rname, router in tgen.routers().items():
        conf = (Path(fabric.CWD) / rname / 'frr.conf').read_text()
        head = 'frr defaults datacenter\n'
        assert conf.startswith(head)
        conf = head + 'log timestamp precision 6\n' + conf[len(head):]
        if rname in fleet.ACCESS_LEAVES:
            conf = fleet.extra_vni_frr(conf, fabric.LEAVES[rname]['lo'])
        path = Path(tgen.logdir) / '{}-bp360.conf'.format(rname)
        path.write_text(conf)
        router.load_frr_config(str(path))
    tgen.start_router()


def teardown_module(mod):
    if RUN:
        get_topogen().stop_topology()


def bgp_message_stats(tgen):
    """Per router, per peer UPDATE/total message counters (show bgp neighbors json)."""
    out = {}
    for name, router in tgen.routers().items():
        data = router.vtysh_cmd('show bgp neighbors json', isjson=True)
        out[name] = {peer: dict((k, v.get('messageStats', {}).get(k)) for k in
                                ('updatesSent', 'updatesRecv', 'totalSent', 'totalRecv'))
                     for peer, v in data.items() if isinstance(v, dict)}
    return out


def radio_state(tgen, leaves):
    """robot index -> leaves whose leaf-side radio port is operationally up (kernel truth)."""
    up = {}
    for leaf in leaves:
        for link in json.loads(tgen.gears[leaf].cmd_raises('ip -j link show')):
            m = PORT_RE.match(link.get('ifname', ''))
            if m and link.get('operstate') == 'UP' and 'LOWER_UP' in link.get('flags', []):
                up.setdefault(int(m.group(1)), []).append(leaf)
    return up


def test_breakpoint360_workload():
    tgen = get_topogen()
    assert not tgen.routers_have_failure(), tgen.errors
    c = cfg()
    out = Path(os.environ['BP_OUT']).resolve()
    out.mkdir(parents=True, exist_ok=False)
    manifest = dict(schema='bp360-phase1-trial-1', test_id=c['test_id'], config=c, started_wall_ns=time.time_ns(),
                    revision=subprocess.getoutput('git -C {} rev-parse HEAD'.format(fabric.CWD)),
                    dirty=subprocess.getoutput('git -C {} status --short'.format(fabric.CWD)),
                    kernel=os.uname().release)
    w = workload.plan(**c['workload'])
    (out / 'workload-plan.json').write_text(json.dumps(w, indent=1))
    manifest['plan_stats'] = {k: v for k, v in w['stats'].items() if k != 'planned_final'}

    fabric.test_bgp_underlay_established()
    fabric.test_evpn_sessions_established()
    fabric._prime_host_adjacencies(tgen)
    manifest['frr_version'] = tgen.gears['leaf1'].vtysh_cmd('show version').splitlines()[:1]
    manifest['dad'] = [l.strip() for l in tgen.gears['leaf1'].vtysh_cmd('show evpn').splitlines()
                       if 'uplicate' in l or 'max-moves' in l]

    procs, handles = [], []
    robots = w['robots']
    hosting = w['stats']['hosting_leaves']
    try:
        # ---- fleet build + warm-up (reuses roadmap360 fleet and fleet_agent) --------
        t = time.monotonic()
        leaf_paths = fleet.attach_leaves(tgen, fleet.ACCESS_LEAVES)
        fleet.create(robots)
        manifest['fleet_build_s'] = round(time.monotonic() - t, 2)
        sink = tgen.net['controller'].popen(['python3', str(R360 / 'sink.py'), str(out / 'telemetry-sink.txt')])
        procs.append(sink)
        warm = dict(controller_ip='20.0.0.51', telemetry_hz=1.0, leaves=leaf_paths, robots=robots, moves=[],
                    start_ns=time.monotonic_ns(), stop_ns=time.monotonic_ns() + int(3600e9))
        (out / 'warmup-spec.json').write_text(json.dumps(warm))
        log = (out / 'warmup-agent.log').open('w')
        handles.append(log)
        warm_agent = subprocess.Popen(['python3', str(R360 / 'fleet_agent.py'), str(out / 'warmup-spec.json'),
                                       str(out / 'warmup-agent.jsonl')], stdout=log, stderr=log)
        (out / 'fleet-ips.txt').write_text(''.join(r['ip'] + '\n' for r in robots))
        deadline, have = time.monotonic() + 240, 0
        while time.monotonic() < deadline:
            raw = tgen.gears['leaf5'].cmd("ip -o route show vrf VRF500 | awk '{{print $1}}' | grep -cxFf {}"
                                          .format(out / 'fleet-ips.txt'))
            try:
                have = int(raw.strip().splitlines()[-1])
            except (ValueError, IndexError):
                have = 0
            if have >= len(robots):
                break
            time.sleep(2)
        warm_agent.send_signal(signal.SIGTERM)
        warm_agent.wait(timeout=30)
        manifest['fleet_learnt'] = dict(have=have, want=len(robots), waited_s=round(time.monotonic() - t, 1))
        assert have >= int(0.98 * len(robots)), 'fleet not learnt at leaf5: {}/{}'.format(have, len(robots))
        time.sleep(2)

        # ---- workload ---------------------------------------------------------
        manifest['bgp_pre'] = bgp_message_stats(tgen)
        spec = workload.agent_spec(w, {leaf: leaf_paths[leaf] for leaf in hosting}, lead_s=c['lead_s'],
                                   post_s=c['post_s'])
        (out / 'agent-spec.json').write_text(json.dumps(spec))
        log = (out / 'agent.log').open('w')
        handles.append(log)
        agent = subprocess.Popen(['python3', str(TOOLS / 'bp_agent.py'), str(out / 'agent-spec.json'), str(out)],
                                 stdout=log, stderr=log)
        procs.append(agent)
        budget = 120 + c['lead_s'] + w['spec']['duration_s'] + c['post_s'] + len(robots) / 50.0
        try:
            rc = agent.wait(timeout=budget)
        except subprocess.TimeoutExpired:
            agent.send_signal(signal.SIGTERM)
            rc = agent.wait(timeout=30)
            manifest['agent_timeout'] = True
        manifest['agent_rc'] = rc
        if rc != 0 or not (out / 'agent-summary.json').exists():
            raise AssertionError('bp_agent failed (rc={}): {}'.format(
                rc, (out / 'agent.log').read_text()[-1500:]))
        manifest['bgp_post'] = bgp_message_stats(tgen)

        # ---- ledger validation against kernel radio state ---------------------
        led = ledger.Ledger.load(str(out / 'ledger.jsonl'))
        final = led.final()
        kernel = radio_state(tgen, hosting)
        mismatches = []
        for r in robots:
            truth = kernel.get(r['index'], [])
            entry = final.get(r['name'])
            got = entry['leaf'] if entry and entry['state'] == ledger.ATTACHED else None
            if len(truth) != 1 or truth[0] != got:
                mismatches.append(dict(robot=r['name'], kernel=truth, ledger=entry))
        states = {}
        for entry in final.values():
            states[entry['state']] = states.get(entry['state'], 0) + 1
        manifest['ledger_validation'] = dict(robots=len(robots), ledger_states=states,
                                             mismatches=len(mismatches), examples=mismatches[:10],
                                             ignored_confirmations=len(led.ignored),
                                             planned_final_equal=sum(1 for r in robots if final[r['name']]['leaf'] ==
                                                                     w['stats']['planned_final'][r['name']]))
        # ---- Phase 0 evidence: how one move's RT-2 looks at spine and leaves ---
        moved = [json.loads(l) for l in (out / 'moves.jsonl').read_text().splitlines() if l.strip()]
        moved = [m for m in moved if m.get('attach_confirmed_ns')]
        if moved:
            m = moved[-1]
            vni = m['vni']
            rd_new = '{}:{}'.format(workload.VTEP[m['dst']], vni)
            rd_old = '{}:{}'.format(workload.VTEP[m['src']], vni)
            flow = dict(move=m)
            for node in ('spine1', 'leaf5', m['dst'], m['src']):
                router = tgen.gears[node]
                flow[node] = dict(
                    new=router.vtysh_cmd('show bgp l2vpn evpn route rd {} mac {} json'.format(rd_new, m['mac']),
                                         isjson=True),
                    new_macip=router.vtysh_cmd('show bgp l2vpn evpn route rd {} mac {} ip {} json'.format(
                        rd_new, m['mac'], m['ip']), isjson=True),
                    old=router.vtysh_cmd('show bgp l2vpn evpn route rd {} mac {} json'.format(rd_old, m['mac']),
                                         isjson=True))
            flow['leaf5_vrf_bgp'] = tgen.gears['leaf5'].vtysh_cmd(
                'show bgp vrf VRF500 ipv4 unicast {}/32 json'.format(m['ip']), isjson=True)
            flow['leaf5_kernel'] = tgen.gears['leaf5'].cmd('ip route show table 500 {}'.format(m['ip']))
            flow['zebra_client_leaf1'] = tgen.gears['leaf1'].vtysh_cmd('show zebra client')
            (out / 'route-flow-evidence.json').write_text(json.dumps(flow, indent=1))
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
        for proc in procs:
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        for handle in handles:
            handle.close()
        fleet.destroy(robots)
        fleet.detach_leaves(fleet.ACCESS_LEAVES)
        t_wait = time.monotonic()
        while time.monotonic() - t_wait < 300:  # see test_roadmap360: wait for async netns teardown
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
        manifest['finished_wall_ns'] = time.time_ns()
        (out / 'manifest.json').write_text(json.dumps(manifest, indent=1, default=str))
    summary = json.loads((out / 'agent-summary.json').read_text())
    assert summary['planned_moves'] == len(w['moves'])
    assert manifest['ledger_validation']['mismatches'] == 0, manifest['ledger_validation']['examples']
