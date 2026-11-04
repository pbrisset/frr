#!/usr/bin/env python3
"""Summarize observed snapshots; no interpolation and no cross-clock ordering."""
import csv, json
from pathlib import Path
p=Path(__file__).resolve().parent
out={n:[] for n in ('neighbor-transitions','zebra-mac-ip-transitions','leaf5-forwarding-transitions','controller-probe-results','bgp-nlri-transitions','intervention-comparison')}
def j(raw):
 try: return json.loads(raw)
 except (ValueError,TypeError): return None
for d in sorted(p.glob('raw-*-0[12]')):
 ev=[json.loads(s) for s in (d/'events.jsonl').read_text().splitlines()]; move=next(x['observed_ns'] for x in ev if x['kind']=='move_command_issued'); run=d.name; treatment=run.split('-')[1]
 probes=[json.loads(s) for s in (d/'host11-probes.jsonl').read_text().splitlines()]; summ=json.loads((d/'reachability-summary.json').read_text())['host11']; post=[x for x in probes if x['send_ns']>=move]
 rate=(len(probes)-1)/((probes[-1]['send_ns']-probes[0]['send_ns'])/1e9)
 out['controller-probe-results'].append([run,len(probes),sum(x['success'] for x in probes),summ['lost'],round(rate,3),len(post),sum(not x['success'] for x in post),max((b['count'] for b in summ['bursts']),default=0),json.dumps(summ['bursts']),f'{run}/host11-probes.jsonl'])
 missing=[]; mismatch=[]; local_ip=[]
 for x in ev:
  kind=x['kind']; dev=x.get('device'); elapsed=round((x['observed_ns']-move)/1e6,3)
  if kind=='kernel_neigh' and dev in ('leaf1','leaf2','leaf3'):
   v=j(x['output']); v=next((n for n in v if n.get('dst')=='45.0.0.11'),None) if isinstance(v,list) else 'UNKNOWN'
   out['neighbor-transitions'].append([run,elapsed,dev,'kernel snapshot',json.dumps(v),x.get('start_ns'),x.get('end_ns'),f'{run}/events.jsonl'])
  elif kind=='zebra_neigh' and dev in ('leaf1','leaf2','leaf3'):
   v=j(x['output']); v=v.get('45.0.0.11') if isinstance(v,dict) else 'UNKNOWN'
   out['neighbor-transitions'].append([run,elapsed,dev,'Zebra snapshot',json.dumps(v),x.get('start_ns'),x.get('end_ns'),f'{run}/events.jsonl'])
   if dev=='leaf2' and elapsed>0 and isinstance(v,dict) and v.get('type')=='local': local_ip.append(elapsed)
  elif kind=='zebra_mac' and dev in ('leaf1','leaf2'):
   v=j(x['output']); v=v.get('macs',{}).get('00:00:00:00:00:11') if isinstance(v,dict) else 'UNKNOWN'
   out['zebra-mac-ip-transitions'].append([run,elapsed,dev,'MAC snapshot',json.dumps(v),f'{run}/events.jsonl'])
  elif kind=='forwarding' and dev=='leaf5' and 'ip -j route show vrf VRF500' in x['command']:
   v=j(x['output'].splitlines()[0]) if x['output'] else None
   if isinstance(v,list):
    r=next((n for n in v if n.get('dst') in ('45.0.0.11','45.0.0.11/32')),None); s=next((n for n in v if n.get('dst')=='45.0.0.0/24'),None)
    out['leaf5-forwarding-transitions'].append([run,elapsed,'route table snapshot',json.dumps(r),json.dumps(s),'UNKNOWN',f'{run}/events.jsonl'])
    if elapsed>0 and not r: missing.append(elapsed)
  elif kind=='forwarding_resolution':
   out['leaf5-forwarding-transitions'].append([run,elapsed,'sequential route/neighbor/FDB lookup','UNKNOWN','UNKNOWN',json.dumps(x['candidates']),f'{run}/events.jsonl'])
   if elapsed>0 and x.get('matched') is False: mismatch.append(elapsed)
 pkt=d/'packet-events.jsonl'
 if pkt.exists():
  for line in pkt.read_text().splitlines():
   x=json.loads(line)
   for action in ('announced','withdrawn'):
    for n in x.get(action,[]):
     if n.get('mac')==['00:00:00:00:00:11']:
      out['bgp-nlri-transitions'].append([run,x['device'],','.join(x['realtime_epoch']),x['direction'],action,','.join(n['route_type']),','.join(n['ip_length']),','.join(n['ip']), 'capture transmission; peer/retransmission not deduplicated',f'{run}/packet-events.jsonl'])
 out['intervention-comparison'].append([run,treatment,'0' if run.endswith('01') else '1',len(probes),summ['lost'],len(missing),len(mismatch),len(local_ip),'not observed' if not local_ip else min(local_ip),f'{run}/events.jsonl; {run}/host11-stimulus.log'])
headers={
'neighbor-transitions':['run','relative_ms','device','evidence_type','robot_neighbor_json_null_means_absent','command_start_monotonic_ns','command_end_monotonic_ns','evidence'],
'zebra-mac-ip-transitions':['run','relative_ms','device','evidence_type','robot_mac_json','evidence'],
'leaf5-forwarding-transitions':['run','relative_ms','evidence_type','robot_32_json_null_means_absent','subnet_24_json','resolved_vteps','evidence'],
'controller-probe-results':['run','sent','replies','timeouts_or_errors','realized_sends_per_sec','post_command_sends','post_command_losses','longest_failure_burst','burst_details','evidence'],
'bgp-nlri-transitions':['run','device','realtime_epoch_s','capture_direction','action','route_type','ip_length_bits','ip','limitations','evidence'],
'intervention-comparison':['run','treatment','seed','sent','lost','post_move_samples_missing_32','post_move_mismatching_lookup_samples','post_move_leaf2_local_ip_samples','first_local_ip_sample_ms','evidence']}
for name,rows in out.items():
 with (p/(name+'.csv')).open('w',newline='') as f:
  w=csv.writer(f); w.writerow(headers[name]);w.writerows(rows)
