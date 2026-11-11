# 360° EVPN host-mobility / IP-relearning suite

Automated, event-driven investigation of the EVPN IP relearning gap (remote
`extern_learn NOARP` neighbour vs. Leaf1 Type-2 withdrawal). Findings:
[`REPORT_360_FINDINGS.md`](REPORT_360_FINDINGS.md); per-run metrics:
[`runs_360.csv`](runs_360.csv).

## Layout

```
results/workstream_<A|B|C|D>/<test_id>/
  telemetry-events.jsonl   normalised trace {timestamp_ns, node, layer, event_type, mac, ip, flags, details}
  manifest.json            config, pre/post CLI state, clock calibration, kprobe status
  harness-events.jsonl     roam commands (T0, detach, attach, ...)
  nl-<node>.jsonl          raw rtnetlink events (neigh, bridge FDB, routes, nexthops, links)
  ktrace.txt               raw kprobe trace (__neigh_update entry/return, arp_process)
  cap-<node>.pcapng.gz     per-interface captures (BGP, VXLAN, ARP, ICMP, ICMPv6)
  probes.jsonl             controller ICMP probe results
  stimulus.jsonl           robot post-roam stimulus send times
  cpu.jsonl                zebra/bgpd CPU counters (/proc)
  router-logs/<node>/*.log.gz  FRR debug logs (µs timestamps)
  fleet-*.json(l), telemetry-sink.txt   background fleet (B/C only)
results/suite-plan.json, suite-status.jsonl, suite-environment.json
```

## Code

`tests/topotests/bgp_evpn_capstone/test_roadmap360.py` (one trial per
invocation) and `tests/topotests/bgp_evpn_capstone/roadmap360/`:
`nlmon.py`, `ktrace.py`, `probe.py`, `stimulus.py`, `fleet.py`,
`fleet_agent.py`, `sink.py`, `cpusample.py`, `normalize.py`,
`run_360_suite.py`, `analyze_360_roadmap.py`, `test_r360_unit.py`.

## Reproduce

Environment prerequisite for fleet runs (the IPv4 neighbour table is global
across network namespaces; defaults 128/512/1024 overflow with ~500+ robot
namespaces). Runtime-only, reverts when the Colima VM restarts:

```sh
colima ssh -- sudo sysctl -w net.ipv4.neigh.default.gc_thresh1=4096 \
    net.ipv4.neigh.default.gc_thresh2=8192 net.ipv4.neigh.default.gc_thresh3=16384
# restore: ... gc_thresh1=128 gc_thresh2=512 gc_thresh3=1024
```

Run the whole matrix (resumable; skips trials that already have a trace):

```sh
docker exec frr-dev bash -lc 'cd /home/frr/frr && sudo -E python3 \
  tests/topotests/bgp_evpn_capstone/roadmap360/run_360_suite.py \
  --results doc/capstone/experiment-results/roadmap360/results'
docker exec frr-dev bash -lc 'cd /home/frr/frr && python3 \
  tests/topotests/bgp_evpn_capstone/roadmap360/analyze_360_roadmap.py \
  doc/capstone/experiment-results/roadmap360/results'
```

Single trial: `R360_RUN=1 R360_OUT=/tmp/x R360_WS=A R360_PATTERN=burst sudo -E
python3 -m pytest -s bgp_evpn_capstone/test_roadmap360.py` from
`tests/topotests`. Unit tests: `python3 -m unittest discover -s
tests/topotests/bgp_evpn_capstone/roadmap360 -p 'test_*unit.py'`.
