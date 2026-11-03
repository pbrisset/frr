#!/usr/bin/env python3
"""Send one gratuitous ARP request from the temporary access interface."""
import socket
import struct
import sys

interface, mac, address = sys.argv[1:]
source = bytes.fromhex(mac.replace(':', ''))
ip = socket.inet_aton(address)
frame = (b'\xff' * 6 + source + struct.pack('!H', 0x0806)
         + struct.pack('!HHBBH', 1, 0x0800, 6, 4, 1)
         + source + ip + b'\x00' * 6 + ip)
with socket.socket(socket.AF_PACKET, socket.SOCK_RAW) as sock:
    sock.bind((interface, 0))
    sock.send(frame)
