#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
"""Multi-target timestamped ICMP prober (run in the controller namespace).

Each target gets its own ICMP identifier; the payload carries the
CLOCK_MONOTONIC send time so packets can be matched in captures.  One JSON
line per probe: send/receive monotonic ns, success, rtt, timeout.
"""
import argparse
import json
import select
import socket
import struct
import time


def checksum(data):
    if len(data) % 2:
        data += b'\0'
    total = sum(struct.unpack('!%dH' % (len(data) // 2), data))
    while total >> 16:
        total = (total & 0xffff) + (total >> 16)
    return (~total) & 0xffff


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('output')
    parser.add_argument('--target', action='append', required=True, help='ip:identifier')
    parser.add_argument('--rate', type=float, default=10.0, help='per-target probes/s')
    parser.add_argument('--duration', type=float, required=True)
    parser.add_argument('--timeout', type=float, default=1.0)
    args = parser.parse_args()
    targets = []
    for spec in args.target:
        ip, ident = spec.split(':')
        targets.append((ip, int(ident)))
    assert 0 < args.rate <= 100 and args.duration * args.rate < 65000
    sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
    sock.setblocking(False)
    period = int(1e9 / args.rate)
    start = time.monotonic_ns()
    stop = start + int(args.duration * 1e9)
    phase = {t: start + i * period // max(1, len(targets)) for i, t in enumerate(targets)}
    seqs = {t: 0 for t in targets}
    by_ident = {ident: ip for ip, ident in targets}
    pending = {}
    with open(args.output, 'x', buffering=1 << 16) as log:
        def record(key, sent, received=None, error=None):
            ip, ident, seq = key
            log.write(json.dumps(dict(ip=ip, ident=ident, sequence=seq, send_ns=sent,
                                      receive_ns=received, success=received is not None,
                                      rtt_ms=(received - sent) / 1e6 if received else None,
                                      error=error), separators=(',', ':')) + '\n')
        log.write(json.dumps(dict(kind='start', start_ns=start, rate=args.rate,
                                  targets=targets, timeout_s=args.timeout)) + '\n')
        while True:
            now = time.monotonic_ns()
            if now >= stop and not pending:
                break
            if now < stop:
                for target in targets:
                    if now >= phase[target]:
                        ip, ident = target
                        seqs[target] = (seqs[target] % 65535) + 1
                        seq = seqs[target]
                        payload = b'R360' + struct.pack('!Q', now)
                        hdr = struct.pack('!BBHHH', 8, 0, 0, ident, seq)
                        pkt = struct.pack('!BBHHH', 8, 0, checksum(hdr + payload), ident, seq) + payload
                        try:
                            sock.sendto(pkt, (ip, 0))
                            pending[(ip, ident, seq)] = now
                        except OSError as exc:
                            record((ip, ident, seq), now, error=str(exc))
                        phase[target] = max(phase[target] + period, now + period // 2)
            wait = min([max(0, phase[t] - time.monotonic_ns()) for t in targets] + [5_000_000]) / 1e9
            ready, _, _ = select.select([sock], [], [], wait)
            if ready:
                while True:
                    try:
                        data, source = sock.recvfrom(65535)
                    except BlockingIOError:
                        break
                    received = time.monotonic_ns()
                    off = (data[0] & 15) * 4
                    kind, _, _, ident, seq = struct.unpack('!BBHHH', data[off:off + 8])
                    key = (source[0], ident, seq)
                    if kind == 0 and by_ident.get(ident) == source[0] and key in pending:
                        record(key, pending.pop(key), received)
            now = time.monotonic_ns()
            for key, sent in list(pending.items()):
                if now - sent >= args.timeout * 1e9:
                    record(key, pending.pop(key), error='timeout')
        log.write(json.dumps(dict(kind='end', end_ns=time.monotonic_ns())) + '\n')


if __name__ == '__main__':
    main()
