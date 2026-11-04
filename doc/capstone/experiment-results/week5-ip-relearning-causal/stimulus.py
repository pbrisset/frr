#!/usr/bin/env python3
"""Broadcast gateway ARP requests on identical schedules; record clock brackets."""
import json, socket, struct, sys, time
case=sys.argv[1]; offsets={'none': [], 'immediate': [0], 'delayed': [3], 'repeated': [0, .25, .5, .75]}[case]
start=time.monotonic(); source=bytes.fromhex('000000000011'); ip=socket.inet_aton('45.0.0.11'); target=socket.inet_aton('45.0.0.1')
frame=b'\xff'*6+source+struct.pack('!H',0x0806)+struct.pack('!HHBBH',1,0x0800,6,4,1)+source+ip+b'\0'*6+target
for offset in offsets:
 time.sleep(max(0,start+offset-time.monotonic()))
 with socket.socket(socket.AF_PACKET,socket.SOCK_RAW) as sock:
  sock.bind(('eth0',0)); rec={'case':case,'offset_s':offset,'before_wall_ns':time.time_ns(),'before_mono_ns':time.monotonic_ns()}; rec['bytes_sent']=sock.send(frame); rec['after_mono_ns']=time.monotonic_ns();rec['after_wall_ns']=time.time_ns()
 print(json.dumps(rec),flush=True)
