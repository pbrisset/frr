#!/usr/bin/env python3
"""RTMGRP_NEIGH listener, retaining every message and socket drop notification."""
import json, os, signal, socket, struct, time
s=socket.socket(socket.AF_NETLINK,socket.SOCK_RAW,socket.NETLINK_ROUTE);s.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4194304)
for opt in (35,40):
 try:s.setsockopt(socket.SOL_SOCKET,opt,1)
 except OSError:pass
s.bind((0,4)); portid=s.getsockname()[0];s.settimeout(.2);running=True

def stop(signum,frame):
 global running
 running=False
signal.signal(signal.SIGTERM,stop)
def sockstats():
 try:
  lines=open('/proc/net/netlink').read().splitlines()
  return {'header':lines[0], 'matching_rows':[line for line in lines[1:] if line.split()[2]==str(portid)]}
 except Exception as e:return {'error':str(e)}
print(json.dumps({'kind':'ready','portid':portid,'netlink_stats':sockstats(),'pid':os.getpid(),'rcvbuf':s.getsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF),'wall_ns':time.time_ns(),'mono_ns':time.monotonic_ns()}),flush=True)
while running:
 try:
  data,anc,flags,addr=s.recvmsg(262144,1024); wall=time.time_ns();mono=time.monotonic_ns(); pos=0; ancillary=[{'level':l,'type':t,'hex':v.hex()} for l,t,v in anc]
  while pos+16<=len(data):
   length,typ,nflags,seq,pid=struct.unpack_from('=IHHII',data,pos)
   if length<16 or pos+length>len(data): break
   payload=data[pos+16:pos+length]; r={'kind':'netlink','wall_ns':wall,'mono_ns':mono,'message_type':typ,'nlmsg_flags':nflags,'sequence':seq,'pid':pid,'msg_flags':flags,'ancillary':ancillary,'payload_hex':payload.hex()}
   if typ in (28,29) and len(payload)>=12:
    fam,_,_,idx,state,ndflags,ndtype=struct.unpack_from('=BBHiHBB',payload);r.update(family=fam,ifindex=idx,nud_state=state,ndm_flags=ndflags,ndm_type=ndtype); off=12; attrs={}
    while off+4<=len(payload):
     alen,atype=struct.unpack_from('=HH',payload,off)
     if alen<4 or off+alen>len(payload):break
     val=payload[off+4:off+alen]; attrs[str(atype)]=val.hex();off+=(alen+3)&~3
    r['attributes']=attrs
    if '1' in attrs and fam in (2,10):
     try:r['ip']=socket.inet_ntop(socket.AF_INET if fam==2 else socket.AF_INET6,bytes.fromhex(attrs['1']))
     except OSError:pass
   print(json.dumps(r),flush=True);pos+=(length+3)&~3
 except socket.timeout: continue
 except OSError as e: print(json.dumps({'kind':'socket_error','error':str(e),'errno':e.errno,'wall_ns':time.time_ns(),'mono_ns':time.monotonic_ns()}),flush=True)

print(json.dumps({'kind':'final','portid':portid,'netlink_stats':sockstats(),'wall_ns':time.time_ns(),'mono_ns':time.monotonic_ns()}),flush=True)
