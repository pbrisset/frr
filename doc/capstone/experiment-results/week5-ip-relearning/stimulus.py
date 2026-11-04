#!/usr/bin/env python3
"""Emit a controlled gratuitous ARP or telemetry, recording monotonic and wall clocks."""
import json, socket, struct, subprocess, sys, time
case = sys.argv[1]
start = time.monotonic()
offsets = {'none': [], 'immediate': [0], 'repeated': [0, .25, .5, .75], 'delayed': [3], 'telemetry': [0, 1, 2, 3, 4, 5], 'gateway': [3] }[case]
for offset in offsets:
    time.sleep(max(0, start + offset - time.monotonic()))
    record = {'case': case, 'requested_offset_s': offset, 'wall_ns': time.time_ns(), 'monotonic_ns': time.monotonic_ns()}
    if case in ('telemetry', 'gateway'):
        result = subprocess.run(['ping', '-c', '1', '-W', '1', '45.0.0.1' if case == 'gateway' else '20.0.0.51'], capture_output=True, text=True)
        record.update(returncode=result.returncode, output=result.stdout, stderr=result.stderr)
    else:
        source = bytes.fromhex('000000000011'); ip = socket.inet_aton('45.0.0.11')
        frame = b'\xff'*6 + source + struct.pack('!H', 0x0806) + struct.pack('!HHBBH', 1, 0x0800, 6, 4, 1) + source + ip + b'\0'*6 + ip
        with socket.socket(socket.AF_PACKET, socket.SOCK_RAW) as sock:
            sock.bind(('eth0', 0)); record['bytes_sent'] = sock.send(frame)
    print(json.dumps(record), flush=True)
