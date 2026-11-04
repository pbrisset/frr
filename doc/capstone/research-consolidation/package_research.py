#!/usr/bin/env python3
"""Build and verify the evidence ZIP without overwriting original artifacts."""
from pathlib import Path
import collections,csv,hashlib,io,subprocess,zipfile
root=Path(__file__).resolve().parents[3];p=root/'doc/capstone/research-consolidation'
out=root/'Cisco_Capstone_Our_Research_Evidence_Through_Week5.zip'
if out.exists():raise SystemExit('Target exists; choose a new destination or preserve it before rebuilding: '+str(out))
roots=[root/'doc/capstone/experiment-results',root/'tests/topotests/bgp_evpn_capstone',p]
additional=['CAPSTONE.md','doc/capstone/README.md','doc/capstone/phase0-audit.md','doc/capstone/project_proposal.md','doc/capstone/student_cookbook.md','doc/capstone/project_topology.pdf','zebra/zebra_evpn_neigh.c','zebra/zebra_evpn_mac.c','zebra/zebra_vxlan.c','zebra/rt_netlink.c','bgpd/bgp_evpn.c']
paths=[]
for r in roots:
 for f in r.rglob('*'):
  if f.is_file() and not f.is_symlink() and '__pycache__' not in f.parts and f.name!='.DS_Store' and not f.name.endswith(('.pyc','.pyo')) and f.name!='ARTIFACT_MANIFEST.csv':paths.append(f)
paths += [root/x for x in additional if (root/x).is_file()]
paths=sorted(set(paths),key=lambda x:str(x.relative_to(root)))
names=[str(f.relative_to(root)) for f in paths]
tracked=set(subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0'))
result=subprocess.run(['git','check-ignore','-z','--stdin'],cwd=root,input=('\0'.join(names)+'\0').encode(),stdout=subprocess.PIPE,check=False)
if result.returncode not in (0,1):raise SystemExit('git check-ignore failed')
ignored=set(result.stdout.decode().split('\0'))
def status(n):return 'tracked (committed at audit start)' if n in tracked else 'ignored local' if n in ignored else 'untracked new or local'
def origin(n):
 if n.startswith('doc/capstone/experiment-results/'):return n.split('/')[3]
 if n.startswith('tests/topotests/bgp_evpn_capstone/'):return 'capstone topology, harness, configs or historical audit'
 if n.startswith('doc/capstone/research-consolidation/'):return 'new consolidation; historical unique ZIP members where marked'
 return 'project context or selected FRR source'
def sha(f):
 h=hashlib.sha256()
 with f.open('rb') as g:
  for block in iter(lambda:g.read(1024*1024),b''):h.update(block)
 return h.hexdigest()
manifest=p/'ARTIFACT_MANIFEST.csv'; rows=[]; hashes={}
for f,n in zip(paths,names):
 digest=sha(f);hashes[n]=digest;rows.append([n,f.stat().st_size,digest,origin(n),status(n),'INCLUDED'])
selfname=str(manifest.relative_to(root));rows.append([selfname,'SELF','SELF','new consolidation','untracked new','INCLUDED; hash in ARCHIVE_CONTENT_SHA256SUMS'])
old=root/'doc/capstone/review-packages/phase1-complete-review-package.zip'
if old.exists():rows.append([str(old.relative_to(root)),old.stat().st_size,sha(old),'historical review ZIP; 3095 duplicated members; 2 unique members preserved','ignored local','OMITTED AS REDUNDANT; original preserved'])
with manifest.open('w',newline='') as f:
 w=csv.writer(f);w.writerow(['repository_relative_path','size_bytes','sha256','origin','git_status_at_inventory','archive_disposition']);w.writerows(rows)
hashes[selfname]=sha(manifest);paths.append(manifest);names.append(selfname)
checksum=''.join(f'{hashes[n]}  {n}\n' for n in sorted(names))
with zipfile.ZipFile(out,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
 for f,n in sorted(zip(paths,names),key=lambda pair:pair[1]):z.write(f,n)
 z.writestr('ARCHIVE_CONTENT_SHA256SUMS',checksum)
with zipfile.ZipFile(out) as z:
 bad=z.testzip()
 if bad:raise SystemExit('ZIP CRC failure: '+bad)
 expected=set(names)|{'ARCHIVE_CONTENT_SHA256SUMS'}; actual=z.namelist()
 if set(actual)!=expected or len(actual)!=len(expected):raise SystemExit('ZIP member count/path mismatch')
 for n in names:
  h=hashlib.sha256()
  with z.open(n) as stream:
   for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
  if h.hexdigest()!=hashes[n]:raise SystemExit('ZIP hash mismatch: '+n)
 if z.read('ARCHIVE_CONTENT_SHA256SUMS').decode()!=checksum:raise SystemExit('Checksums mismatch')
counts=collections.Counter(status(n) for n in names if n!=selfname)
print('Included files:',len(names),'Git categories:',dict(counts))
print('Unique content hashes:',len(set(hashes.values())),'duplicate-path members:',len(hashes)-len(set(hashes.values())))
print('ZIP CRC, path set and every included SHA-256: PASS')
print('ZIP size bytes:',out.stat().st_size,'ZIP SHA-256:',sha(out))
