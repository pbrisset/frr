#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
"""Post-roam robot stimulus worker (run in the robot namespace).

Started before the roam; blocks until the harness writes an anchor line
``<iface> <CLOCK_MONOTONIC ns>`` on stdin (written right after the new
attachment completes), then emits the pattern at anchor + offset.

Patterns (offsets in seconds from the anchor):
  none       -
  immediate  gateway ARP request @0
  delayed    gateway ARP request @0.3
  burst      gateway ARP request @0, 0.25, 0.5, 0.75
  ping       unicast ICMP echo to gateway @0, 0.25, 0.5, 0.75 (same timing as burst)
  garp       gratuitous ARP (sender == target) @0
"""
import argparse
import json
import select
import socket
import struct
import sys
import time

SCHEDULES = {'none': [], 'immediate': [0.0], 'delayed': [0.3],
             'burst': [0.0, 0.25, 0.5, 0.75], 'ping': [0.0, 0.25, 0.5, 0.75], 'garp': [0.0]}


def arp_frame(mac, ip, target):
    src = bytes.fromhex(mac.replace(':', ''))
    return (b'\xff' * 6 + src + struct.pack('!H', 0x0806)
            + struct.pack('!HHBBH', 1, 0x0800, 6, 4, 1)
            + src + socket.inet_aton(ip) + b'\0' * 6 + socket.inet_aton(target))


def checksum(data):
    total = sum(struct.unpack('!%dH' % (len(data) // 2), data))
    while total >> 16:
        total = (total & 0xffff) + (total >> 16)
    return (~total) & 0xffff


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('pattern', choices=sorted(SCHEDULES))
    parser.add_argument('--mac', required=True)
    parser.add_argument('--ip', required=True)
    parser.add_argument('--gw', required=True)
    args = parser.parse_args()
    out = sys.stdout
    out.write(json.dumps(dict(kind='ready', pattern=args.pattern, mono_ns=time.monotonic_ns())) + '\n')
    out.flush()
    line = sys.stdin.readline().split()
    if not line:
        return
    iface, anchor = line[0], int(line[1])
    out.write(json.dumps(dict(kind='anchor', iface=iface, anchor_ns=anchor,
                              received_ns=time.monotonic_ns())) + '\n')
    out.flush()
    icmp = None
    if args.pattern == 'ping':
        icmp = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    for index, offset in enumerate(SCHEDULES[args.pattern]):
        delay = (anchor + int(offset * 1e9) - time.monotonic_ns()) / 1e9
        if delay > 0:
            time.sleep(delay)
        rec = dict(kind='send', pattern=args.pattern, index=index, offset_s=offset, iface=iface)
        if args.pattern == 'ping':
            body = struct.pack('!Q', time.monotonic_ns())
            hdr = struct.pack('!BBHHH', 8, 0, 0, 43210, index + 1)
            pkt = struct.pack('!BBHHH', 8, 0, checksum(hdr + body), 43210, index + 1) + body
            rec['before_ns'] = time.monotonic_ns()
            rec['before_wall_ns'] = time.time_ns()
            icmp.sendto(pkt, (args.gw, 0))
            rec['after_ns'] = time.monotonic_ns()
            rec['packet'] = 'icmp-echo-gateway'
        else:
            target = args.ip if args.pattern == 'garp' else args.gw
            with socket.socket(socket.AF_PACKET, socket.SOCK_RAW) as sock:
                sock.bind((iface, 0))
                rec['before_ns'] = time.monotonic_ns()
                rec['before_wall_ns'] = time.time_ns()
                sock.send(arp_frame(args.mac, args.ip, target))
                rec['after_ns'] = time.monotonic_ns()
            rec['packet'] = 'garp' if args.pattern == 'garp' else 'arp-request-gateway'
        out.write(json.dumps(rec) + '\n')
        out.flush()
    if icmp is not None:
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            ready, _, _ = select.select([icmp], [], [], 0.1)
            if ready:
                data, source = icmp.recvfrom(2048)
                off = (data[0] & 15) * 4
                kind, _, _, ident, seq = struct.unpack('!BBHHH', data[off:off + 8])
                if kind == 0 and ident == 43210:
                    out.write(json.dumps(dict(kind='ping_reply', index=seq - 1, source=source[0],
                                              mono_ns=time.monotonic_ns())) + '\n')
                    out.flush()
    out.write(json.dumps(dict(kind='done', mono_ns=time.monotonic_ns())) + '\n')


if __name__ == '__main__':
    main()
