#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
"""Background-fleet agent (runs in the container root namespace as root).

One process drives every background robot: it opens a UDP socket, an
AF_PACKET socket and an rtnetlink socket inside each robot namespace (setns),
sends 1 Hz telemetry to the controller with phases spread uniformly, and
executes a pre-computed move schedule (radio model, see fleet.py):

  robot : RTM_NEWLINK name=p<old leaf> flags=0     (old radio down: break)
  robot : RTM_NEWLINK name=p<new leaf> flags=IFF_UP (new radio up: make)
  robot : one gratuitous ARP on br0 (roaming-client announcement)

Every move is logged with CLOCK_MONOTONIC brackets.
"""
import argparse
import ctypes
import heapq
import json
import os
import resource
import signal
import socket
import struct
import time

CLONE_NEWNET = 0x40000000
libc = ctypes.CDLL('libc.so.6', use_errno=True)
IFF_UP = 0x1
IFLA_IFNAME = 3


def setns(fd):
    if libc.setns(fd, CLONE_NEWNET) != 0:
        raise OSError(ctypes.get_errno(), 'setns failed')


def nl_attr(kind, value):
    return struct.pack('=HH', 4 + len(value), kind) + value + b'\0' * ((4 - len(value) % 4) % 4)


class Rtnl:
    """rtnetlink socket bound inside one namespace (created while setns'd)."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 0)
        self.sock.bind((0, 0))
        self.sock.settimeout(2)
        self.seq = 0

    def setlink(self, name, up):
        self.seq += 1
        body = struct.pack('=BBHiII', 0, 0, 0, 0, IFF_UP if up else 0, IFF_UP) + nl_attr(IFLA_IFNAME, name.encode() + b'\0')
        self.sock.send(struct.pack('=IHHII', 16 + len(body), 16, 0x1 | 0x4, self.seq, 0) + body)
        while True:
            data = self.sock.recv(65536)
            _, kind, _, seq, _ = struct.unpack_from('=IHHII', data)
            if kind == 2 and seq == self.seq:
                return struct.unpack_from('=i', data, 16)[0]


def garp(mac, ip):
    src = bytes.fromhex(mac.replace(':', ''))
    addr = socket.inet_aton(ip)
    return (b'\xff' * 6 + src + struct.pack('!H', 0x0806) + struct.pack('!HHBBH', 1, 0x0800, 6, 4, 1)
            + src + addr + b'\0' * 6 + addr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('spec')
    parser.add_argument('output')
    args = parser.parse_args()
    spec = json.load(open(args.spec))
    resource.setrlimit(resource.RLIMIT_NOFILE, (65536, 65536))
    running = [True]

    def stop(*_):
        running[0] = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    root_fd = os.open('/proc/self/ns/net', os.O_RDONLY)
    robots = {}
    for robot in spec['robots']:
        fd = os.open(robot['netns_path'], os.O_RDONLY)
        setns(fd)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setblocking(False)
        udp.bind((robot['ip'], 0))
        raw = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
        raw.bind(('br0', 0))
        rtnl = Rtnl() if len(robot.get('ports', [])) > 1 else None
        setns(root_fd)
        os.close(fd)
        robots[robot['name']] = dict(robot, udp=udp, raw=raw, rtnl=rtnl, leaf=robot['leaf'], seq=0)
    out = open(args.output, 'x', buffering=1 << 16)

    def log(rec):
        out.write(json.dumps(rec, separators=(',', ':')) + '\n')

    log(dict(kind='ready', mono_ns=time.monotonic_ns(), robots=len(robots)))
    out.flush()
    hz = float(spec.get('telemetry_hz', 1.0))
    period = int(1e9 / hz) if hz > 0 else None
    start = int(spec.get('start_ns') or time.monotonic_ns())
    stop_ns = int(spec['stop_ns'])
    heap = []
    names = sorted(robots)
    if period:
        for index, name in enumerate(names):
            heapq.heappush(heap, (start + index * period // len(names), 1, 'tx', name, None))
    for move in spec.get('moves', []):
        heapq.heappush(heap, (int(move['at_ns']), 0, 'move', move['robot'], move['to']))
    controller = (spec['controller_ip'], int(spec.get('controller_port', 5005)))
    sent, errors = 0, 0
    last_flush = time.monotonic_ns()
    while running[0] and heap:
        when, _, action, name, target = heap[0]
        if when >= stop_ns:
            break
        now = time.monotonic_ns()
        if when > now:
            time.sleep(min((when - now) / 1e9, 0.05))
            continue
        heapq.heappop(heap)
        robot = robots[name]
        if action == 'tx':
            robot['seq'] += 1
            try:
                robot['udp'].sendto(struct.pack('!IIQ', robot['index'], robot['seq'], now) + b'T' * 40,
                                    controller)
                sent += 1
            except OSError:
                errors += 1
            heapq.heappush(heap, (when + period, 1, 'tx', name, None))
        else:
            rec = dict(kind='move', robot=name, mac=robot['mac'], ip=robot['ip'], src=robot['leaf'],
                       dst=target, scheduled_ns=when, start_ns=time.monotonic_ns())
            rec['move_err'] = robot['rtnl'].setlink('p' + robot['leaf'][-1], False)
            rec['detached_ns'] = time.monotonic_ns()
            rec['attach_err'] = robot['rtnl'].setlink('p' + target[-1], True)
            rec['attached_ns'] = time.monotonic_ns()
            if rec['move_err'] == 0:
                robot['leaf'] = target
            try:
                robot['raw'].send(garp(robot['mac'], robot['ip']))
                rec['garp_ns'] = time.monotonic_ns()
            except OSError as exc:
                rec['garp_error'] = str(exc)
            log(rec)
        if time.monotonic_ns() - last_flush > 500_000_000:
            out.flush()
            last_flush = time.monotonic_ns()
    log(dict(kind='final', mono_ns=time.monotonic_ns(), telemetry_sent=sent, telemetry_errors=errors,
             final_leaf={n: r['leaf'] for n, r in robots.items()}))
    out.close()


if __name__ == '__main__':
    main()
