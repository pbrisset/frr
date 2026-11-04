#!/usr/bin/env python3
"""Attach capture frame and TCP peer evidence to decoded NLRI rows."""
import csv, subprocess
from pathlib import Path
p=Path(__file__).resolve().parent
f=p/'bgp-nlri-transitions.csv'
with f.open() as h: rows=list(csv.DictReader(h)); fields=list(rows[0]) if rows else []
lookup={}
for d in sorted(p.glob('raw-*-0[12]')):
 for cap in d.glob('*.pcapng'):
  cmd=['tshark','-n','-r',str(cap),'-Y','bgp.type == 2','-T','fields']
  for fld in ('frame.time_epoch','frame.number','ipv6.src','ipv6.dst','ip.src','ip.dst','tcp.srcport','tcp.dstport','tcp.analysis.retransmission'):
   cmd += ['-e',fld]
  result=subprocess.run(cmd,capture_output=True,text=True,check=True)
  for line in result.stdout.splitlines():
   a=line.split('\t'); a += ['']*(9-len(a)); lookup[(d.name,cap.stem,a[0])]=a[1:]
for row in rows:
 data=lookup.get((row['run'],row['device'],row['realtime_epoch_s']),['UNKNOWN']*8)
 for name,value in zip(['frame','ipv6_src','ipv6_dst','ipv4_src','ipv4_dst','tcp_srcport','tcp_dstport','tcp_retransmission_flag'],data): row[name]=value
with f.open('w',newline='') as h:
 w=csv.DictWriter(h,fieldnames=fields+['frame','ipv6_src','ipv6_dst','ipv4_src','ipv4_dst','tcp_srcport','tcp_dstport','tcp_retransmission_flag']);w.writeheader();w.writerows(rows)
