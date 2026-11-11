#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
"""Controller-side UDP telemetry sink: one compact line per received datagram."""
import argparse
import signal
import socket
import struct
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('output')
    parser.add_argument('--port', type=int, default=5005)
    args = parser.parse_args()
    running = [True]

    def stop(*_):
        running[0] = False
    signal.signal(signal.SIGTERM, stop)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
    sock.bind(('0.0.0.0', args.port))
    sock.settimeout(0.2)
    with open(args.output, 'x', buffering=1 << 16) as out:
        out.write('# robot_index seq send_mono_ns recv_mono_ns src_ip\n')
        while running[0]:
            try:
                data, source = sock.recvfrom(2048)
            except socket.timeout:
                continue
            if len(data) >= 16:
                index, seq, sent = struct.unpack_from('!IIQ', data)
                out.write('%d %d %d %d %s\n' % (index, seq, sent, time.monotonic_ns(), source[0]))


if __name__ == '__main__':
    main()
