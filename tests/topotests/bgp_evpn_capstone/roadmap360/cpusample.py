#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
"""pidstat-equivalent CPU sampler for FRR daemons (reads /proc only, never vtysh).

Run in the root namespace.  Daemon processes are attributed to topology
nodes through their network-namespace inode (map supplied as JSON).
Cumulative utime+stime ticks are written every --interval seconds.
"""
import argparse
import json
import os
import signal
import time


def daemons(inum_map, names):
    found = {}
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        try:
            comm = open('/proc/%s/comm' % pid).read().strip()
            if comm not in names:
                continue
            inum = os.readlink('/proc/%s/ns/net' % pid).split('[')[1].rstrip(']')
        except (OSError, IndexError):
            continue
        node = inum_map.get(inum)
        if node:
            found[(node, comm)] = int(pid)
    return found


def ticks(pid):
    try:
        fields = open('/proc/%d/stat' % pid).read().rsplit(')', 1)[1].split()
        return int(fields[11]) + int(fields[12])
    except (OSError, IndexError, ValueError):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('inum_map')
    parser.add_argument('output')
    parser.add_argument('--interval', type=float, default=0.25)
    args = parser.parse_args()
    inum_map = json.load(open(args.inum_map))
    running = [True]

    def stop(*_):
        running[0] = False
    signal.signal(signal.SIGTERM, stop)
    procs = daemons(inum_map, {'zebra', 'bgpd'})
    hz = os.sysconf('SC_CLK_TCK')
    with open(args.output, 'x', buffering=1 << 16) as out:
        out.write(json.dumps(dict(kind='ready', clk_tck=hz, procs={'%s/%s' % k: v for k, v in procs.items()},
                                  mono_ns=time.monotonic_ns())) + '\n')
        while running[0]:
            now = time.monotonic_ns()
            sample = {'%s/%s' % k: ticks(pid) for k, pid in procs.items()}
            total = open('/proc/stat').readline().split()[1:]
            out.write(json.dumps(dict(kind='cpu', mono_ns=now, ticks=sample,
                                      host=[int(x) for x in total]), separators=(',', ':')) + '\n')
            time.sleep(args.interval)


if __name__ == '__main__':
    main()
