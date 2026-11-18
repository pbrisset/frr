#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
"""breakpoint360 Phase 1: mobility workload executor (container root namespace, root).

Input : agent spec JSON from workload.agent_spec() (robots, moves with absolute
        CLOCK_MONOTONIC due times, hosting-leaf namespace paths).
Output (in the output directory):
  ledger.jsonl        authoritative attachment ledger (see ledger.py)
  moves.jsonl         one record per requested move: due, start, ACKs, confirmation
  agent-summary.json  requested vs achieved rate, lag, completion latency, monitor health

Per robot it opens (inside the robot namespace via setns) a UDP socket for
1 Hz telemetry, an AF_PACKET socket on br0 (GARP / gateway ARP) and, for
mobile robots, an rtnetlink socket used to toggle radio ports.  Per hosting
leaf it opens an RTMGRP_LINK listener inside the leaf namespace; a move's
attachment is complete when the leaf-side bridge port reports FORWARDING
(AF_BRIDGE RTM_NEWLINK, IFLA_BRPORT_STATE=3).  Only then is the ledger
updated to ATTACHED and the GARP sent (a frame sent before the port forwards
would be dropped by the leaf bridge).

Reused unchanged from roadmap360: fleet_agent.setns / Rtnl / garp, nlmon.attrs.
"""
import argparse
import heapq
import json
import os
import resource
import select
import signal
import socket
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(HERE, '..', 'roadmap360'))
import fleet_agent as fa  # noqa: E402  (roadmap360)
import nlmon  # noqa: E402  (roadmap360)
import ledger as ledger_mod  # noqa: E402
import workload  # noqa: E402

RTMGRP_LINK = 0x1
SO_RCVBUFFORCE = 33
AF_BRIDGE = 7
IFLA_IFNAME, IFLA_PROTINFO, IFLA_OPERSTATE = 3, 12, 16
IFLA_BRPORT_STATE = 1
BR_STATE_DISABLED, BR_STATE_FORWARDING = 0, 3
IF_OPER_UP = 6
IFF_LOWER_UP = 0x10000
PRIO_MOVE, PRIO_ARP, PRIO_TX, PRIO_CHECK = 0, 1, 2, 3


def parse_links(data):
    """Decode RTM_NEWLINK/RTM_DELLINK messages (generic and AF_BRIDGE port notifications)."""
    out, pos = [], 0
    while pos + 16 <= len(data):
        length, mtype, _, _, _ = struct.unpack_from('=IHHII', data, pos)
        if length < 16 or pos + length > len(data):
            break
        payload = data[pos + 16:pos + length]
        pos += (length + 3) & ~3
        if mtype not in (16, 17) or len(payload) < 16:
            continue
        family, _, _, index, flags, _ = struct.unpack_from('=BBHiII', payload)
        a = nlmon.attrs(payload, 16)
        brstate = None
        if IFLA_PROTINFO in a:
            inner = nlmon.attrs(a[IFLA_PROTINFO], 0)
            if IFLA_BRPORT_STATE in inner and inner[IFLA_BRPORT_STATE]:
                brstate = inner[IFLA_BRPORT_STATE][0]
        out.append(dict(msg='NEWLINK' if mtype == 16 else 'DELLINK', family=family, ifindex=index,
                        ifname=a.get(IFLA_IFNAME, b'').rstrip(b'\0').decode(errors='replace') or None,
                        flags=flags, operstate=a[IFLA_OPERSTATE][0] if a.get(IFLA_OPERSTATE) else None,
                        brstate=brstate))
    return out


def classify(link):
    """'forwarding' | 'oper_up' | 'down' | None for one decoded link message."""
    if link['msg'] == 'DELLINK':
        return 'down'
    if link['family'] == AF_BRIDGE and link['brstate'] is not None:
        if link['brstate'] == BR_STATE_FORWARDING:
            return 'forwarding'
        if link['brstate'] == BR_STATE_DISABLED:
            return 'down'
        return None
    if link['family'] == 0:
        if link['flags'] & IFF_LOWER_UP and link['operstate'] == IF_OPER_UP:
            return 'oper_up'
        if not link['flags'] & IFF_LOWER_UP:
            return 'down'
    return None


def send_setlink(rtnl, name, up):
    """Non-blocking half of roadmap360 fleet_agent.Rtnl.setlink (same message)."""
    rtnl.seq += 1
    body = struct.pack('=BBHiII', 0, 0, 0, 0, fa.IFF_UP if up else 0, fa.IFF_UP) + \
        fa.nl_attr(fa.IFLA_IFNAME, name.encode() + b'\0')
    rtnl.sock.send(struct.pack('=IHHII', 16 + len(body), 16, 0x1 | 0x4, rtnl.seq, 0) + body)
    return rtnl.seq


def wait_ack(rtnl, seq):
    while True:
        data = rtnl.sock.recv(65536)
        _, kind, _, got, _ = struct.unpack_from('=IHHII', data)
        if kind == 2 and got == seq:
            return struct.unpack_from('=i', data, 16)[0]


def arp_request(mac, ip, target):
    src = bytes.fromhex(mac.replace(':', ''))
    return (b'\xff' * 6 + src + struct.pack('!H', 0x0806) + struct.pack('!HHBBH', 1, 0x0800, 6, 4, 1)
            + src + socket.inet_aton(ip) + b'\0' * 6 + socket.inet_aton(target))


class LeafMonitor:
    """RTMGRP_LINK listener bound inside one leaf namespace."""

    def __init__(self, leaf, path, root_fd):
        fd = os.open(path, os.O_RDONLY)
        fa.setns(fd)
        try:
            self.sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 0)
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, SO_RCVBUFFORCE, 8 << 20)
            except OSError:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
            self.sock.bind((0, RTMGRP_LINK))
            self.sock.setblocking(False)
        finally:
            fa.setns(root_fd)
            os.close(fd)
        self.leaf = leaf
        self.messages = 0
        self.overruns = 0

    def drain(self):
        links = []
        while True:
            try:
                data = self.sock.recv(1 << 20)
            except BlockingIOError:
                break
            except OSError:  # ENOBUFS: socket overrun, events lost
                self.overruns += 1
                continue
            now = time.monotonic_ns()
            for link in parse_links(data):
                self.messages += 1
                links.append((now, link))
        return links


class Agent:
    def __init__(self, spec, outdir):
        self.spec = spec
        self.outdir = outdir
        self.running = True
        self.root_fd = os.open('/proc/self/ns/net', os.O_RDONLY)
        self.ledger = open(os.path.join(outdir, 'ledger.jsonl'), 'x', buffering=1 << 16)
        self.moves_out = open(os.path.join(outdir, 'moves.jsonl'), 'x', buffering=1 << 16)
        self.robots = {}
        self.monitors = {}
        self.pend_attach = {}   # (leaf, ifname) -> record
        self.pend_detach = {}   # (leaf, ifname) -> record
        self.records = []
        self.heap = []
        self.counters = dict(telemetry_sent=0, telemetry_errors=0, garp_sent=0, arp_sent=0, arp_errors=0,
                             arp_superseded=0, link_events_matched=0, confirm_ignored=0)

    # -- setup ---------------------------------------------------------------
    def open_robots(self):
        for robot in self.spec['robots']:
            fd = os.open(robot['netns_path'], os.O_RDONLY)
            fa.setns(fd)
            try:
                udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                udp.setblocking(False)
                udp.bind((robot['ip'], 0))
                raw = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
                raw.bind(('br0', 0))
                rtnl = fa.Rtnl() if len(robot.get('ports', [])) > 1 else None
            finally:
                fa.setns(self.root_fd)
                os.close(fd)
            self.robots[robot['name']] = dict(robot, udp=udp, raw=raw, rtnl=rtnl, gen=0, seq=0)

    def open_monitors(self):
        for leaf, path in self.spec['leaves'].items():
            self.monitors[leaf] = LeafMonitor(leaf, path, self.root_fd)

    # -- logging ---------------------------------------------------------------
    def ledger_event(self, ev, robot, t_ns, **extra):
        self.ledger.write(json.dumps(dict(ev=ev, robot=robot, mono_ns=t_ns, **extra), separators=(',', ':')) + '\n')

    def finish(self, rec):
        if rec.get('_done'):
            return
        rec['_done'] = True
        self.moves_out.write(json.dumps({k: v for k, v in rec.items() if not k.startswith('_')},
                                        separators=(',', ':')) + '\n')

    # -- link monitor handling ------------------------------------------------------
    def poll_monitors(self):
        for leaf, mon in self.monitors.items():
            for t, link in mon.drain():
                verdict = classify(link)
                if verdict is None or not link['ifname']:
                    continue
                key = (leaf, link['ifname'])
                rec = self.pend_attach.get(key)
                if rec is not None:
                    if verdict == 'oper_up' and 'oper_up_ns' not in rec:
                        rec['oper_up_ns'] = t
                    elif verdict == 'forwarding':
                        self.counters['link_events_matched'] += 1
                        self.confirm(rec, t, 'bridge_forwarding')
                rec = self.pend_detach.get(key)
                if rec is not None and verdict == 'down':
                    self.pend_detach.pop(key)
                    rec['detach_confirmed_ns'] = t
                    self.ledger_event('detach_confirmed', rec['robot'], t, leaf=leaf, move=rec['seq'])

    def confirm(self, rec, t, source):
        robot = self.robots[rec['robot']]
        self.pend_attach.pop((rec['dst'], rec['_port']), None)
        if robot['gen'] != rec['_gen']:
            self.counters['confirm_ignored'] += 1
            rec['superseded'] = True
            self.finish(rec)
            return
        rec['attach_confirmed_ns'] = t
        rec['confirm_source'] = source
        self.ledger_event('attach_confirmed', rec['robot'], t, leaf=rec['dst'], move=rec['seq'], source=source)
        now = time.monotonic_ns()
        if self.spec.get('garp', True):
            try:
                robot['raw'].send(fa.garp(robot['mac'], robot['ip']))
                rec['garp_ns'] = time.monotonic_ns()
                self.counters['garp_sent'] += 1
            except OSError as exc:
                rec['garp_error'] = str(exc)
        for offset in self.spec.get('arp_offsets_ms', []):
            heapq.heappush(self.heap, (now + int(offset) * 1_000_000, PRIO_ARP, 'arp', rec['robot'], rec['_gen']))
        self.finish(rec)

    # -- actions -----------------------------------------------------------------
    def do_moves(self, batch):
        """Execute moves that share one due time.  The radio operations are
        pipelined across robots (all detach requests, then their ACKs, then all
        attach requests, then their ACKs), so a synchronized batch is limited by
        the kernel's RTNL serialisation rather than by per-request round trips.
        Each robot still breaks before it makes."""
        live = []
        for move in batch:
            name = move['robot']
            robot = self.robots[name]
            src, dst = robot['leaf'], move['dst']
            rec = dict(seq=move['seq'], batch=move.get('batch'), robot=name, mac=robot['mac'], ip=robot['ip'],
                       vni=robot['vni'], src=src, planned_src=move.get('planned_src'), dst=dst,
                       at_ns=move['at_ns'], batch_size=len(batch))
            self.records.append(rec)
            if robot['rtnl'] is None or dst == src or dst not in robot['ports']:
                rec['start_ns'] = time.monotonic_ns()
                rec['skipped'] = 'static robot' if robot['rtnl'] is None else \
                    ('already at destination' if dst == src else 'destination not wired')
                self.finish(rec)
                continue
            robot['gen'] += 1
            rec['_gen'] = robot['gen']
            # stale confirmation guard: a pending attach of this robot belongs to an older move
            for key, old in list(self.pend_attach.items()):
                if old['robot'] == name:
                    self.pend_attach.pop(key)
                    old['superseded'] = True
                    self.finish(old)
            rec['_port'] = 'f{}l{}'.format(robot['index'], dst[-1])
            live.append((rec, robot))
        for rec, robot in live:
            rec['start_ns'] = time.monotonic_ns()
            rec['_dseq'] = send_setlink(robot['rtnl'], 'p' + rec['src'][-1], False)
        for rec, robot in live:
            rec['detach_err'] = wait_ack(robot['rtnl'], rec['_dseq'])
            rec['detach_ack_ns'] = time.monotonic_ns()
            self.ledger_event('detach', rec['robot'], rec['detach_ack_ns'], leaf=rec['src'], move=rec['seq'],
                              err=rec['detach_err'])
            if rec['detach_err'] == 0:
                self.pend_detach[(rec['src'], 'f{}l{}'.format(robot['index'], rec['src'][-1]))] = rec
        for rec, robot in live:
            rec['_aseq'] = send_setlink(robot['rtnl'], 'p' + rec['dst'][-1], True)
        for rec, robot in live:
            rec['attach_err'] = wait_ack(robot['rtnl'], rec['_aseq'])
            rec['attach_ack_ns'] = time.monotonic_ns()
            self.ledger_event('attach', rec['robot'], rec['attach_ack_ns'], leaf=rec['dst'], move=rec['seq'],
                              err=rec['attach_err'])
            if rec['attach_err'] != 0:
                self.finish(rec)
                continue
            robot['leaf'] = rec['dst']
            self.pend_attach[(rec['dst'], rec['_port'])] = rec
            heapq.heappush(self.heap, (rec['attach_ack_ns'] + int(self.spec['attach_timeout_ms']) * 1_000_000,
                                       PRIO_CHECK, 'check', rec['robot'], rec['seq']))

    def do_check(self, name, seq):
        for key, rec in list(self.pend_attach.items()):
            if rec['robot'] == name and rec['seq'] == seq:
                if 'oper_up_ns' in rec:
                    self.confirm(rec, rec['oper_up_ns'], 'oper_up')
                else:
                    self.pend_attach.pop(key)
                    rec['attach_timeout'] = True
                    self.ledger_event('attach_timeout', name, time.monotonic_ns(), leaf=rec['dst'], move=seq)
                    self.finish(rec)

    def do_arp(self, name, gen):
        robot = self.robots[name]
        if robot['gen'] != gen:
            self.counters['arp_superseded'] += 1
            return
        try:
            robot['raw'].send(arp_request(robot['mac'], robot['ip'], robot['gw']))
            self.counters['arp_sent'] += 1
        except OSError:
            self.counters['arp_errors'] += 1

    def do_tx(self, name, when, period, controller):
        robot = self.robots[name]
        robot['seq'] += 1
        try:
            robot['udp'].sendto(struct.pack('!IIQ', robot['index'], robot['seq'], time.monotonic_ns()) + b'T' * 40,
                                controller)
            self.counters['telemetry_sent'] += 1
        except OSError:
            self.counters['telemetry_errors'] += 1
        heapq.heappush(self.heap, (when + period, PRIO_TX, 'tx', name, None))

    # -- main loop ----------------------------------------------------------------
    def run(self):
        spec = self.spec
        if 'lead_s' in spec:
            workload.rebase(spec, time.monotonic_ns())
        meta = dict(ev='meta', mono_ns=time.monotonic_ns(), t0_ns=spec['t0_ns'], stop_ns=spec['stop_ns'],
                    robots=len(self.robots), moves=len(spec['moves']), leaves=sorted(self.monitors))
        self.ledger.write(json.dumps(meta) + '\n')
        start = time.monotonic_ns()
        for name, robot in sorted(self.robots.items()):
            self.ledger_event('init', name, start, leaf=robot['leaf'], source='fleet.create')
        hz = float(spec.get('telemetry_hz', 1.0))
        period = int(1e9 / hz) if hz > 0 else None
        t_start = int(spec.get('start_ns') or start)
        names = sorted(self.robots)
        if period:
            for index, name in enumerate(names):
                heapq.heappush(self.heap, (t_start + index * period // len(names), PRIO_TX, 'tx', name, None))
        for move in spec['moves']:
            heapq.heappush(self.heap, (int(move['at_ns']), PRIO_MOVE, 'move', move['seq'], move))
        controller = (spec['controller_ip'], int(spec.get('controller_port', 5005)))
        stop_ns = int(spec['stop_ns'])
        poller = select.poll()  # select() cannot take fds >= 1024 (3 sockets per robot)
        for mon in self.monitors.values():
            poller.register(mon.sock.fileno(), select.POLLIN)
        last_flush = time.monotonic_ns()
        last_iter = time.monotonic_ns()
        stalls = []  # (ms after T0, gap ms): the agent itself did not run for > 20 ms
        while self.running and self.heap:
            when, _, action, key, data = self.heap[0]
            if when >= stop_ns:
                break
            now = time.monotonic_ns()
            if now - last_iter > 20_000_000:
                stalls.append(((last_iter - spec['t0_ns']) / 1e6, (now - last_iter) / 1e6))
            last_iter = now
            if when > now:
                wait_ms = int((when - now) / 1e6)  # floor: never sleep past the due time
                if wait_ms < 1 or poller.poll(min(wait_ms, 50)):
                    self.poll_monitors()
                continue
            heapq.heappop(self.heap)
            if action == 'move':
                batch, seen = [data], {data['robot']}
                while self.heap and self.heap[0][2] == 'move' and self.heap[0][0] == when \
                        and self.heap[0][4]['robot'] not in seen:
                    nxt = heapq.heappop(self.heap)[4]
                    batch.append(nxt)
                    seen.add(nxt['robot'])
                self.do_moves(batch)
            elif action == 'tx':
                self.do_tx(key, when, period, controller)
            elif action == 'arp':
                self.do_arp(key, data)
            elif action == 'check':
                self.do_check(key, data)
            self.poll_monitors()
            if now - last_flush > 500_000_000:
                self.ledger.flush()
                self.moves_out.flush()
                last_flush = now
        self.poll_monitors()
        for rec in list(self.pend_attach.values()):
            if 'oper_up_ns' in rec:
                self.confirm(rec, rec['oper_up_ns'], 'oper_up')
            else:
                rec['unresolved_at_stop'] = True
                self.finish(rec)
        self.pend_attach.clear()
        final_leaf = {n: r['leaf'] for n, r in self.robots.items()}
        self.ledger.write(json.dumps(dict(ev='final', mono_ns=time.monotonic_ns(), radio_leaf=final_leaf)) + '\n')
        self.ledger.close()
        self.moves_out.close()
        summary = ledger_mod.achieved_rates(self.records, spec['requested_rate'], spec['t0_ns'], spec['duration_s'],
                                            planned=len(spec['moves']), mode=spec.get('mode', 'staggered'),
                                            sync_period_s=spec.get('sync_period_s', 1.0))
        usage = resource.getrusage(resource.RUSAGE_SELF)
        summary['agent_health'] = dict(
            nice=os.nice(0), cpu_user_s=usage.ru_utime, cpu_sys_s=usage.ru_stime,
            voluntary_ctxsw=usage.ru_nvcsw, involuntary_ctxsw=usage.ru_nivcsw, loop_stalls_over_20ms=len(stalls),
            worst_stalls=[dict(at_ms_after_t0=round(a, 1), gap_ms=round(g, 1))
                          for a, g in sorted(stalls, key=lambda s: -s[1])[:10]])
        summary.update(t0_ns=spec['t0_ns'], stop_ns=spec['stop_ns'], mode=spec.get('mode'),
                       sync_period_s=spec.get('sync_period_s'),
                       interrupted=not self.running, counters=self.counters,
                       monitors={l: dict(messages=m.messages, overruns=m.overruns) for l, m in self.monitors.items()},
                       superseded=sum(1 for r in self.records if r.get('superseded')),
                       unresolved_at_stop=sum(1 for r in self.records if r.get('unresolved_at_stop')))
        if any(m.overruns for m in self.monitors.values()):
            summary['generator_flags'].append('leaf link monitor overrun (confirmations may be missing)')
            summary['generator_ok'] = False
        with open(os.path.join(self.outdir, 'agent-summary.json'), 'w') as handle:
            json.dump(summary, handle, indent=1)
        return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('spec')
    parser.add_argument('outdir')
    parser.add_argument('--nice', type=int, default=-10,
                        help='niceness increment so FRR load cannot starve the schedule (0 = unchanged)')
    args = parser.parse_args()
    spec = json.load(open(args.spec))
    resource.setrlimit(resource.RLIMIT_NOFILE, (65536, 65536))
    if args.nice:
        try:
            os.nice(args.nice)
        except OSError:
            pass
    os.makedirs(args.outdir, exist_ok=True)
    agent = Agent(spec, args.outdir)

    def stop(*_):
        agent.running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    agent.open_robots()
    agent.open_monitors()
    with open(os.path.join(args.outdir, 'agent-ready.json'), 'w') as handle:
        json.dump(dict(mono_ns=time.monotonic_ns(), robots=len(agent.robots), monitors=sorted(agent.monitors)), handle)
    agent.run()


if __name__ == '__main__':
    main()
