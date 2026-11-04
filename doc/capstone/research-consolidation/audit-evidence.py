#!/usr/bin/env python3
"""Reconcile retained experiment records; no new test execution."""
from pathlib import Path
from datetime import datetime,timezone
import csv,json,collections
root=Path(__file__).resolve().parents[3]; p=root/'doc/capstone/research-consolidation'; base=root/'doc/capstone/experiment-results'
rows=[];checks=[]
def put(name,head,data):
 with (p/name).open('w',newline='') as f:w=csv.writer(f);w.writerow(head);w.writerows(data)
def iso(ns):return datetime.fromtimestamp(ns/1e9,timezone.utc).isoformat() if ns else 'UNKNOWN'
def rel(x):return str(x.relative_to(root))
def run(d,phase,method,questions,summaryrow=None):
 m=json.loads((d/'manifest.json').read_text());eff=m.get('effective',{});pr=d/'host11-probes.jsonl'
 probes=[json.loads(x) for x in pr.read_text().splitlines()] if pr.exists() else []
 s=json.loads((d/'reachability-summary.json').read_text()).get('host11',{}) if (d/'reachability-summary.json').exists() else {}
 sent=len(probes);lost=sum(not x.get('success',False) for x in probes);check=(sent==s.get('sent') and lost==s.get('lost')) if probes and s else False
 checks.append([phase,d.name,'probe JSON vs summary',sent,lost,s.get('sent','UNKNOWN'),s.get('lost','UNKNOWN'),'PASS' if check else 'UNAVAILABLE OR MISMATCH',rel(d)])
 if summaryrow:
  rs=summaryrow.get('sent',summaryrow.get('probe_sends'));rl=summaryrow.get('lost',summaryrow.get('probe_losses'))
  if rs is not None and rl is not None:checks.append([phase,d.name,'probe JSON vs aggregate CSV',sent,lost,rs,rl,'PASS' if str(sent)==str(rs) and str(lost)==str(rl) else 'MISMATCH',rel(d)])
 treatment=method; mode=eff.get('mode','break-before-make');overlap=eff.get('overlap_ms',0);seed=eff.get('seed','UNKNOWN')
 if phase.startswith('week5'):
  cm=json.loads((d/'causal-manifest.json').read_text()) if (d/'causal-manifest.json').exists() else {}
  seed=cm.get('seed',seed);treatment=cm.get('condition',method)
 results=f'{sent} sends / {sent-lost} replies / {lost} failures' if probes else 'UNKNOWN'
 rows.append([phase+':'+d.name,iso(m.get('started_wall_ns')),questions,m.get('revision','UNKNOWN')+'; dirty='+str(bool(m.get('dirty'))),rel(d/'manifest.json'),rel(d),mode,1,'host11 leaf1→leaf2','scheduled '+str(eff.get('move_interval','UNKNOWN'))+'s; requested overlap '+str(overlap)+'ms',str(eff.get('dad','baseline'))+'; enabled 5/180s reported','controller raw ICMP; requested '+str(eff.get('probe_rate','UNKNOWN'))+'Hz',1,seed,treatment,results,'see route, Zebra and packet records','snapshots non-atomic; realtime/monotonic separation; emulation; source-version caveats',rel(d),'see REPRODUCTION_GUIDE.md'])
 return check
phase0=base/'phase0-2026-11-02';run(phase0/'verified-move','phase0','BBM','Does measurement correlate attachment and controller forwarding?')
phase1=base/'phase1-bbm-vs-mbb'; agg={r['run']:r for r in csv.DictReader((phase1/'runs.csv').open())}
for d in sorted((phase1/'raw').iterdir()):
 if d.is_dir():run(d,'phase1b',d.name,'BBM versus ARP-triggered dual attachment',agg.get(d.name))
route=list(csv.DictReader((phase1/'route-transitions.csv').open()));forward=list(csv.DictReader((phase1/'forwarding-transitions.csv').open()))
for name,x in agg.items():
 missing=any(y['run']==name and float(y['elapsed_ms'])>0 and y['host32']=='False' for y in route)
 mismatch=any(y['run']==name and float(y['elapsed_ms'])>0 and y['matched']=='False' for y in forward)
 ok=(str(missing)==x['sampled_host32_absent'] and str(mismatch)==str(int(x['sampled_mismatches'])>0))
 checks.append(['phase1b',name,'route and lookup CSV vs runs.csv',str(missing),str(mismatch),x['sampled_host32_absent'],str(int(x['sampled_mismatches'])>0),'PASS' if ok else 'MISMATCH',rel(phase1)])
for folder,phase in [('week5-ip-relearning','week5-ip'),('week5-ip-relearning-causal','week5-causal')]:
 q=base/folder;file='intervention-comparison.csv' if phase=='week5-ip' else 'intervention-trials.csv';a={r['run']:r for r in csv.DictReader((q/file).open())}
 for d in sorted(q.glob('raw-*')):run(d,phase,d.name.split('-')[1],'Does neighbor learning and traffic affect forwarding?',a.get(d.name))
for smoke in ('smoke-02','smoke-03'):
 d=phase1/smoke; rows.append(['phase1a:'+smoke,'2026-11-03 (recorded report/commit; exact UTC unknown)','Validate dual attachment and identity','689379cf...; execution-time source not independently attested','tests/topotests/bgp_evpn_capstone/test_phase1_mbb.py',rel(d),'MBB',1,'host11 leaf1→leaf2','250ms requested','baseline enabled 5 moves/180s','isolated ping; no continuous probe',1,'UNKNOWN','gratuitous ARP through new interface','smoke passed per report; overlap and cleanup events retained','new leaf MAC local; single robot IPv4 retained until transfer','no independent pytest log or PCAP; does not prove seamless IP continuity',rel(d),'see REPRODUCTION_GUIDE.md'])
for name,path,date in [('historical-oct13','tests/topotests/bgp_evpn_capstone/mobility_telemetry.csv','2026-10-13'),('historical-oct20','tests/topotests/bgp_evpn_capstone/audit_2026-10-20/capstone_audit_mobility_2026-10-20.csv','2026-10-20')]:
 rs=list(csv.DictReader((root/path).open()));rows.append([name,date,'Fabric route convergence and short two-robot workload','execution-time SHA UNKNOWN; see report','tests/topotests/bgp_evpn_capstone/test_mobility_workload.py',path,'single-veth move; fleet operations serialized','1 and 2','host11 various; host21 leaf2→leaf3','5 sequential then 2 fleet moves','default; CSV misses post-return DAD warnings','gateway stimulus/polling; no continuous controller probe',1,'UNKNOWN','historical workload',f'{len(rs)} recorded move rows; not independent topology trials','20 Oct 2.309–2.384s sequential; 3.400/3.852s two-host','polling and workload time included; not outage; post-return DAD',path,'tests/topotests/bgp_evpn_capstone/audit_2026-10-20/REPORT.md'])
for name,phase,path,reason in [
 ('attempt-01','phase0','doc/capstone/experiment-results/phase0-2026-11-02/attempt-01','FAILED: forwarding parser expected dev rather than ifname; excluded from verified result'),
 ('attempt-01','phase1a','doc/capstone/experiment-results/phase1-bbm-vs-mbb/attempt-01','FAILED: nonexistent cmd_status; retained event record; not a successful run')]:
 rows.append([phase+':'+name,'2026-11-02/03; exact unknown','Instrumentation / harness pilot','UNKNOWN','see retained source and report',path,'BBM' if phase=='phase0' else 'MBB',1,'host11 leaf1→leaf2','UNKNOWN','baseline','UNKNOWN',0,'UNKNOWN','failed pilot',reason,'not included in aggregate statistics','failed experiment; partial artifacts only',path,'see REPRODUCTION_GUIDE.md'])
put('EXPERIMENT_MATRIX.csv',['experiment_id','date_utc','research_question','source_revision','harness_or_manifest','configuration_or_raw_dir','mobility_model','moving_robots','source_destination','schedule_or_overlap','dad','probe_method_and_rate','independent_trials','seed','treatment','actual_result','key_observations','limitations','raw_evidence','reproduction'],rows)
put('VALIDATION_CHECKS.csv',['phase','run','check','raw_sends','raw_failures','reported_sends','reported_failures','status','evidence'],checks)
print('matrix rows',len(rows),'validation checks',len(checks),'nonpass',sum(x[-2]!='PASS' for x in checks))
