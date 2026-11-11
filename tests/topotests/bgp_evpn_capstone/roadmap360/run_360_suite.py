#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
"""Non-interactive driver for the 360-roadmap experiment matrix.

Run as root inside the privileged frr-dev container::

  sudo python3 tests/topotests/bgp_evpn_capstone/roadmap360/run_360_suite.py \
      --results doc/capstone/experiment-results/roadmap360/results

Each trial is one fresh topology (one pytest invocation of
test_roadmap360.py).  Trials are executed sequentially, workstreams in the
order A, B, C, D; inside a workstream the conditions are block-randomised
(each replicate block contains every condition once, shuffled with a fixed
seed).  Completed trials (telemetry-events.jsonl present) are skipped, so the
suite is resumable.  A failed trial is retried once, then archived with a
FAILED marker.  Raw output is written to /tmp first and copied afterwards.
"""
import argparse
import gzip
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOPOTESTS = HERE.parent.parent
sys.path.insert(0, str(HERE))
import ktrace  # noqa: E402


def plan(reps_a=10, reps_b=5, reps_c=5, reps_d=10, seed=360):
    trials = []

    def block(ws, conditions, reps):
        for rep in range(1, reps + 1):
            order = conditions[:]
            random.Random('{}-{}-{}'.format(seed, ws, rep)).shuffle(order)
            for name, env in order:
                trials.append(dict(test_id='{}-{}-{:02d}'.format(ws, name, rep), ws=ws, rep=rep, env=env))
    block('A', [(p, dict(R360_PATTERN=p, R360_POST_S='30')) for p in ('none', 'immediate', 'delayed', 'burst', 'ping')],
          reps_a)
    block('B', [(f, dict(R360_PATTERN='burst', R360_FLEET=f)) for f in ('baseline', 'low64', 'mob250', 'scale1000')],
          reps_b)
    block('C', [(f, dict(R360_PATTERN='burst', R360_FLEET=f)) for f in ('roam64-sync', 'roam64-staggered')], reps_c)
    block('D', [('bbm', dict(R360_PATTERN='garp', R360_MODE='bbm', R360_POST_S='30'))] +
          [('mbb{}'.format(o), dict(R360_PATTERN='garp', R360_MODE='mbb', R360_OVERLAP_MS=str(o), R360_POST_S='30'))
           for o in (100, 250, 500)], reps_d)
    return trials


def hygiene():
    ktrace.teardown()
    names = subprocess.run(['ip', 'netns', 'list'], capture_output=True, text=True).stdout.split('\n')
    stale = [n.split()[0] for n in names if n.startswith('r360')]
    if stale:
        subprocess.run(['ip', '-batch', '-'], input=''.join('netns delete {}\n'.format(n) for n in stale),
                       text=True, capture_output=True)
    subprocess.run(['pkill', '-f', 'roadmap360/(nlmon|probe|sink|fleet_agent|cpusample|stimulus).py'],
                   capture_output=True)
    return len(stale)


def compress(directory):
    for path in list(directory.glob('cap-*.pcapng')) + list(directory.glob('router-logs/*/*.log')):
        with open(path, 'rb') as src, gzip.open(str(path) + '.gz', 'wb', compresslevel=3) as dst:
            shutil.copyfileobj(src, dst)
        path.unlink()


def run_trial(trial, scratch, timeout):
    out = scratch / trial['test_id']
    if out.exists():
        shutil.rmtree(out)
    env = dict(os.environ, R360_RUN='1', R360_OUT=str(out), R360_TEST_ID=trial['test_id'], R360_WS=trial['ws'],
               R360_SEED=str(trial['rep']), ENABLE_LIVE_ROUTE_GRAPH='false', **trial['env'])
    log = scratch / (trial['test_id'] + '.pytest.log')
    start = time.monotonic()
    with open(log, 'w') as handle:
        try:
            # Same invocation shape as the validated pilots: coreutils timeout gives pytest its own process
            # group; without it munet's namespace teardown stalled 2 x 30 s per trial.
            rc = subprocess.run(['timeout', '--kill-after=60', str(timeout), sys.executable, '-m', 'pytest', '-s', '-x',
                                 '-q', 'bgp_evpn_capstone/test_roadmap360.py'],
                                cwd=str(TOPOTESTS), env=env, stdout=handle, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, timeout=timeout + 120).returncode
        except subprocess.TimeoutExpired:
            rc = 'timeout'
    return out, log, rc, time.monotonic() - start


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--results', required=True)
    parser.add_argument('--scratch', default='/tmp/r360/raw')
    parser.add_argument('--only', default='', help='comma-separated workstreams to run (default all)')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--timeout', type=int, default=1500)
    args = parser.parse_args()
    results = Path(args.results).resolve()
    scratch = Path(args.scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)
    trials = plan()
    if args.only:
        trials = [t for t in trials if t['ws'] in args.only.split(',')]
    (results / 'suite-plan.json').write_text(json.dumps(trials, indent=1))
    env_file = results / 'suite-environment.json'
    envs = json.loads(env_file.read_text()) if env_file.exists() else []
    envs.append(dict(started=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                     vm_sysctl_note=os.environ.get('R360_VM_SYSCTL', ''),
                     kernel=os.uname().release, python=sys.version.split()[0],
                     frr=subprocess.run(['/usr/lib/frr/zebra', '--version'], capture_output=True,
                                        text=True).stdout.splitlines()[:1],
                     tshark=subprocess.run(['tshark', '--version'], capture_output=True,
                                           text=True).stdout.splitlines()[:1],
                     revision=subprocess.run(['git', '-C', str(HERE), 'rev-parse', 'HEAD'], capture_output=True,
                                             text=True).stdout.strip(),
                     nproc=os.cpu_count()))
    env_file.write_text(json.dumps(envs, indent=1))
    status = open(results / 'suite-status.jsonl', 'a', buffering=1)
    done = 0
    for index, trial in enumerate(trials, 1):
        dest = results / 'workstream_{}'.format(trial['ws']) / trial['test_id']
        if (dest / 'telemetry-events.jsonl').exists():
            continue
        if args.limit and done >= args.limit:
            break
        for attempt in (1, 2):
            stale = hygiene()
            out, log, rc, took = run_trial(trial, scratch, args.timeout)
            record = dict(test_id=trial['test_id'], index=index, total=len(trials), attempt=attempt, rc=rc,
                          seconds=round(took, 1), stale_cleaned=stale, wall=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
            ok = rc == 0 and (out / 'manifest.json').exists()
            if ok:
                norm = subprocess.run([sys.executable, str(HERE / 'normalize.py'), str(out)], capture_output=True, text=True)
                record['normalize'] = (norm.stdout + norm.stderr).strip()[-300:]
                ok = norm.returncode == 0
            record['status'] = 'ok' if ok else 'failed'
            status.write(json.dumps(record) + '\n')
            print(json.dumps(record), flush=True)
            if out.exists():
                shutil.move(str(log), str(out / 'pytest.log'))
            if not ok and attempt == 1:
                # keep the first attempt's evidence; run_trial() wipes the output directory on retry
                keep = scratch / (trial['test_id'] + '.attempt1')
                if keep.exists():
                    shutil.rmtree(keep)
                if out.exists():
                    shutil.copytree(out, keep, ignore=shutil.ignore_patterns('*.pcapng'))
                elif log.exists():
                    keep.mkdir()
                    shutil.copy(log, keep / 'pytest.log')
            if ok or attempt == 2:
                keep = scratch / (trial['test_id'] + '.attempt1')
                if keep.exists() and out.exists():
                    shutil.copytree(keep, out / 'attempt1', dirs_exist_ok=True)
                    shutil.rmtree(keep)
                if out.exists():
                    if not ok:
                        (out / 'FAILED').write_text(json.dumps(record))
                    compress(out)
                    if dest.exists():
                        shutil.rmtree(dest)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(out, dest)
                    shutil.rmtree(out)
                elif log.exists():
                    dest.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(log), str(dest / 'pytest.log'))
                    (dest / 'FAILED').write_text(json.dumps(record))
                break
        done += 1
    hygiene()
    status.close()


if __name__ == '__main__':
    main()
