#!/usr/bin/env python3
"""Extract observed packets and events; missing samples remain unknown."""
import csv,json,re,subprocess
from pathlib import Path
p=Path(__file__).resolve().parent
names=['intervention-trials','kernel-zebra-event-trace','arp-capture-analysis','leaf5-forwarding-transitions','bgp-type2-transitions']
rows={n:[] for n in names}
def readj(x):
 try:return json.loads(x)
 except (ValueError,TypeError):return None
def tshark(path,display,fields):
 cmd=['tshark','-n','-r',str(path),'-Y',display,'-T','fields']
 for f in fields:cmd+=['-e',f]
 r=subprocess.run(cmd,capture_output=True,text=True)
 if r.returncode:return []
 return [x.split('\t') for x in r.stdout.splitlines()]
def arp(d,side):
 path=d/('leaf2-port.pcapng' if side=='leaf2-port' else side+'-access.pcapng'); fields=['frame.number','frame.time_epoch','eth.src','eth.dst','arp.opcode','arp.src.proto_ipv4','arp.dst.proto_ipv4']
 return tshark(path,'arp and (arp.src.proto_ipv4 == 45.0.0.11 or arp.dst.proto_ipv4 == 45.0.0.11)',fields) if path.exists() else []
def stats(d,side):
 path=d/('leaf2-port-capture.log' if side=='leaf2-port' else side+'-access-capture.log'); text=path.read_text(errors='replace') if path.exists() else ''
 hit=re.search(r"received/dropped on interface '[^']+': (\d+)/(\d+).*?pcap:(\d+)/dumpcap:(\d+)/flushed:(\d+)/ps_ifdrop:(\d+)",text)
 return list(hit.groups()) if hit else ['UNKNOWN']*6
for d in sorted(p.glob('raw-*')):
 if not (d/'events.jsonl').exists():continue
 ev=[json.loads(s) for s in (d/'events.jsonl').read_text().splitlines()];move=next((x['observed_ns'] for x in ev if x['kind']=='move_command_issued'),None)
 if move is None:continue
 run=d.name; case=run.split('-')[1]; probes=[json.loads(s) for s in (d/'host11-probes.jsonl').read_text().splitlines()]; sent=len(probes); loss=sum(not x['success'] for x in probes); actual=(sent-1)/((probes[-1]['send_ns']-probes[0]['send_ns'])/1e9) if sent>1 else 0
 worker=[]
 for line in (d/'host11-stimulus.log').read_text().splitlines():
  x=readj(line)
  if isinstance(x,dict):worker.append(x)
 for side in ('host11','leaf2','leaf2-port'):
  st=stats(d,side)
  for a in arp(d,side):
   a+=['']*(7-len(a)); n,ts,src,dst,op,spa,tpa=a; delta=[]
   for w in worker:
    lo=w['before_wall_ns']/1e9; hi=w['after_wall_ns']/1e9
    if lo-0.001<=float(ts)<=hi+0.005 and spa=='45.0.0.11' and tpa=='45.0.0.1' and op=='1':delta.append(w['offset_s'])
   classification='worker timestamp match candidate' if delta else 'background or unmatched; no worker attribution'
   rows['arp-capture-analysis'].append([run,side,n,ts,src,dst,op,spa,tpa,classification,json.dumps(delta),*st,f'{run}/'+('leaf2-port.pcapng' if side=='leaf2-port' else side+'-access.pcapng'),f'{run}/'+('leaf2-port-capture.log' if side=='leaf2-port' else side+'-access-capture.log')])
 for x in ev:
  k=x['kind']; elapsed=round((x['observed_ns']-move)/1e6,3);dev=x.get('device','')
  if dev=='leaf2' and k in ('kernel_neigh','zebra_neigh','pre_stimulus_kernel_neigh','pre_stimulus_zebra_neigh','zebra_mac'):
   v=readj(x.get('output',''))
   if 'kernel_neigh' in k:v=next((z for z in v if z.get('dst')=='45.0.0.11'),None) if isinstance(v,list) else 'UNKNOWN'
   elif 'zebra_neigh' in k:v=v.get('45.0.0.11') if isinstance(v,dict) else 'UNKNOWN'
   else:v=v.get('macs',{}).get('00:00:00:00:00:11') if isinstance(v,dict) else 'UNKNOWN'
   rows['kernel-zebra-event-trace'].append([run,k,'monotonic',x.get('start_ns'),x['observed_ns'],elapsed,json.dumps(v),f'{run}/events.jsonl'])
  if k=='forwarding' and dev=='leaf5' and 'ip -j route show vrf VRF500' in x['command']:
   v=readj(x['output'].splitlines()[0]) if x['output'] else None
   if isinstance(v,list):
    r=next((z for z in v if z.get('dst') in ('45.0.0.11','45.0.0.11/32')),None);s=next((z for z in v if z.get('dst')=='45.0.0.0/24'),None)
    rows['leaf5-forwarding-transitions'].append([run,elapsed,'route snapshot',json.dumps(r),json.dumps(s),'UNKNOWN',f'{run}/events.jsonl'])
  if k=='forwarding_resolution':rows['leaf5-forwarding-transitions'].append([run,elapsed,'sequential route/neighbor/FDB','','',json.dumps(x['candidates']),f'{run}/events.jsonl'])
 for line in (d/'leaf2-neighbor-events.jsonl').read_text().splitlines():
  n=readj(line)
  if isinstance(n,dict) and (n.get('ip')=='45.0.0.11' or n.get('kind') in ('ready','socket_error')):
   rows['kernel-zebra-event-trace'].append([run,'netlink '+str(n.get('message_type',n['kind'])),'userspace wall and monotonic receive',n.get('wall_ns'),n.get('mono_ns'),round((n.get('mono_ns',move)-move)/1e6,3),json.dumps(n),f'{run}/leaf2-neighbor-events.jsonl'])
 netlog=d/'leaf5-netlink.log'
 if netlog.exists():
  for line in netlog.read_text(errors='replace').splitlines():
   if '45.0.0.11' in line:rows['leaf5-forwarding-transitions'].append([run,'UNKNOWN','realtime netlink monitor',line,'','',f'{run}/leaf5-netlink.log'])
 packets=d/'packet-events.jsonl'
 bgp_new=0
 if packets.exists():
  for line in packets.read_text().splitlines():
   x=json.loads(line)
   for act in ('announced','withdrawn'):
    for n in x.get(act,[]):
     if n.get('mac')==['00:00:00:00:00:11']:
      if x['device']=='leaf2' and x['direction']=='sent' and act=='announced' and n.get('ip_length')==['32']: bgp_new += 1
      rows['bgp-type2-transitions'].append([run,x['device'],','.join(x['realtime_epoch']),x['direction'],act,','.join(n['ip_length']),','.join(n['ip']),'per-capture transmission; peers/retransmissions not deduplicated',f'{run}/packet-events.jsonl'])
 post=[x for x in probes if x['send_ns']>=move]; mismatch=sum(1 for x in ev if x['kind']=='forwarding_resolution' and x['observed_ns']>move and x.get('matched') is False)
 iplocal=sum(1 for x in ev if x['kind']=='zebra_neigh' and x.get('device')=='leaf2' and x['observed_ns']>move and isinstance(readj(x['output']),dict) and isinstance(readj(x['output']).get('45.0.0.11'),dict) and readj(x['output'])['45.0.0.11'].get('type')=='local')
 monitored=(d/'leaf2-neighbor-events.jsonl').read_text();errors=monitored.count('"kind": "socket_error"'); burst=json.loads((d/'reachability-summary.json').read_text())['host11']['bursts']
 route_after=[]
 for x in ev:
  if x['kind']=='forwarding' and x.get('device')=='leaf5' and x['observed_ns']>move and 'ip -j route show vrf VRF500' in x.get('command','') and x.get('output'):
   v=readj(x['output'].splitlines()[0])
   if isinstance(v,list): route_after.append(next((z for z in v if z.get('dst') in ('45.0.0.11','45.0.0.11/32')),None))
 newroute=any(isinstance(z,dict) and z.get('gateway')=='10.0.0.2' for z in route_after)
 missing32=any(z is None for z in route_after)
 recovery='NO LOSS' if not any(not q['success'] for q in post) else ('OBSERVED' if post and post[-1]['success'] else 'NOT OBSERVED (CENSORED)')
 rows['intervention-trials'].append([run,case,len(worker),sent,sent-loss,loss,round(actual,3),len(post),sum(not x['success'] for x in post),max((b['count'] for b in burst),default=0),iplocal,bgp_new,missing32,newroute,recovery,mismatch,*stats(d,'leaf2'),errors,'socket overrun cannot be excluded' if case!='access' else 'see checked socket final stats',f'{run}/events.jsonl; {run}/host11-stimulus.log'])
heads={
'intervention-trials':['run','condition','worker_sends','probe_sends','probe_replies','probe_losses','realized_probe_rate_hz','post_move_sends','post_move_losses','max_loss_burst','local_ip_samples','leaf2_sent_mac_ip_nlri_copies','sampled_absent_32','sampled_new_leaf_32','post_move_probe_recovery','mismatching_leaf5_samples','leaf2_capture_received','leaf2_capture_dropped','leaf2_pcap_drops','leaf2_dumpcap_drops','leaf2_flushed','leaf2_interface_drops','netlink_socket_errors','netlink_limit','evidence'],
'kernel-zebra-event-trace':['run','observation','clock','start_or_wall_ns','end_or_monotonic_ns','elapsed_monotonic_ms','value_json','evidence'],
'arp-capture-analysis':['run','capture_point','frame','wall_epoch_s','eth_src','eth_dst','arp_opcode','sender_ip','target_ip','classification','matched_offsets_s','received','dropped','pcap_dropped','dumpcap_dropped','flushed','interface_dropped','pcap','stats_log'],
'leaf5-forwarding-transitions':['run','elapsed_ms','observation','robot_32_or_event','subnet_24','candidate_vtep','evidence'],
'bgp-type2-transitions':['run','device','realtime_epoch_s','direction','action','ip_length','ip','limitation','evidence']}
for n in names:
 with (p/(n+'.csv')).open('w',newline='') as f:
  w=csv.writer(f);w.writerow(heads[n]);w.writerows(rows[n])
