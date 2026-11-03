#!/usr/bin/env python3
"""Independent raw ICMP worker. Run inside controller network namespace as root."""
import argparse
import json
import select
import socket
import struct
import time


def checksum(data):
    if len(data) % 2:
        data += b'\0'
    words = struct.unpack('!%dH' % (len(data) // 2), data)
    total = sum(words)
    while total >> 16:
        total = (total & 65535) + (total >> 16)
    return (~total) & 65535


def run(ip, duration, rate, timeout, output, identifier):
    sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    sock.setblocking(False)
    start = time.monotonic_ns()
    stop = start + int(duration * 1e9)
    period = int(1e9 / rate)
    next_send, seq, pending = start, 0, {}
    with open(output, 'x', buffering=1) as log:
        def record(sequence, sent, received=None, error=None):
            log.write(json.dumps(dict(sequence=sequence, ip=ip, send_ns=sent,
                receive_ns=received, success=received is not None,
                rtt_ms=(received-sent)/1e6 if received else None,
                failure_observed_ns=time.monotonic_ns() if received is None else None,
                error=error)) + '\n')
        while time.monotonic_ns() < stop or pending:
            now = time.monotonic_ns()
            if now < stop and now >= next_send:
                seq += 1
                payload = b'PHASE0!!' + struct.pack('!Q', now)
                packet = struct.pack('!BBHHH', 8, 0, 0, identifier, seq) + payload
                packet = struct.pack('!BBHHH', 8, 0, checksum(packet), identifier, seq) + payload
                try:
                    sock.sendto(packet, (ip, 0))
                    pending[seq] = now
                except OSError as exc:
                    record(seq, now, error=str(exc))
                next_send = max(next_send + period, now + period)
            ready, _, _ = select.select([sock], [], [], min(0.01, 1/rate))
            if ready:
                data, source = sock.recvfrom(65535)
                offset = (data[0] & 15) * 4
                kind, _, _, ident, received_seq = struct.unpack('!BBHHH', data[offset:offset+8])
                if kind == 0 and source[0] == ip and ident == identifier and received_seq in pending:
                    record(received_seq, pending.pop(received_seq), time.monotonic_ns())
            now = time.monotonic_ns()
            for expired, sent in list(pending.items()):
                if now - sent >= timeout * 1e9:
                    record(expired, pending.pop(expired), error='timeout')
    sock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('ip')
    parser.add_argument('output')
    parser.add_argument('--duration', type=float, required=True)
    parser.add_argument('--rate', type=float, default=10)
    parser.add_argument('--timeout', type=float, default=1)
    parser.add_argument('--identifier', type=int, default=42000)
    args = parser.parse_args()
    if not (0 < args.rate <= 100 and 0 < args.duration <= 600 and args.rate * args.duration < 65535):
        parser.error('rate 0..100, duration 0..600, fewer than 65535 probes required')
    run(args.ip, args.duration, args.rate, args.timeout, args.output, args.identifier)
