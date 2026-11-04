import csv,json,pathlib,collections,statistics
root=pathlib.Path('doc/capstone/experiment-results/phase1-bbm-vs-mbb')
def read(n):
 with (root/n).open() as f:return list(csv.DictReader(f))
runs=read('runs.csv'); zebra=read('zebra-transitions.csv'); bgp=read('bgp-robot-nlri.csv'); routes=read('route-transitions.csv'); fw=read('forwarding-transitions.csv'); msgs=read('bgp-messages.csv')
issues=[]; matrix=[]; idx=[]
for r in runs:
 name=r['run']; d=root/'raw'/name
 events=[json.loads(x) for x in (d/'events.jsonl').read_text().splitlines()]; p=[json.loads(x) for x in (d/'host11-probes.jsonl').read_text().splitlines()]; move=next(x for x in events if x['kind']=='move_command_issued'); t=move['observed_ns']; c=json.loads((d/'packet-counts.json').read_text()); probes=sum(not x['success'] for x in p)
 if int(r['lost'])!=probes or int(r['sent'])!=len(p):issues.append(name+' probe counts disagree')
 z=[x for x in zebra if x['run']==name and x['device'] in ('leaf1','leaf2')]; maxseq=max((int(x[k]) for x in z for k in ('localSequence','remoteSequence') if x[k]),default=None); maxdad=max((int(x['detectionCount']) for x in z if x['detectionCount']),default=None); dup=collections.Counter(x['isDuplicate'] for x in z)
 f=[x for x in fw if x['run']==name and x['matched']=='False']; rs=[x for x in routes if x['run']==name and float(x['elapsed_ms'])>=0 and x['host32']=='False'];
 if bool(f)!=(int(r['sampled_mismatches'])>0):issues.append(name+' mismatch summary disagree')
 if bool(rs)!=(r['sampled_host32_absent']=='True'):issues.append(name+' route summary disagree')
 n=[x for x in bgp if x['run']==name and x['device']=='leaf2' and x['direction']=='sent' and x['action']=='withdrawn']; w=collections.Counter(x['ip_length'] for x in n); updates=c.get('leaf2',{}).get('sent_message_type_2','unknown');
 final=any(x['kind']=='final_attachment_verified' for x in events); match=any(x['kind']=='forwarding_resolution' and x.get('actual_leaf')=='leaf2' and x.get('matched') is True for x in events); cleanup=any(x['kind']=='cleanup_verified' for x in events)
 if not (final and match and cleanup):issues.append(name+' validation incomplete')
 matrix.append((name,r,len(p)-probes,maxseq,maxdad,dup,len(rs),len(f),updates,w))
 idx.append((name,t, next(x['observed_ns'] for x in events if x['kind']=='old_attachment_disconnected'),[x['sequence'] for x in p if not x['success']], rs[0]['elapsed_ms'] if rs else None, f[0]['elapsed_ms'] if f else None))
print('trials',len(matrix),'issues',issues)
print('duplicates',collections.Counter(k for _,_,_,_,_,dups,*_ in matrix for k in dups));print('seq3',[x[0] for x in matrix if x[3]==3])
pathlib.Path('/private/tmp/phase1_audit.json').write_text(json.dumps(dict(matrix=matrix,index=idx,issues=issues),indent=2))
