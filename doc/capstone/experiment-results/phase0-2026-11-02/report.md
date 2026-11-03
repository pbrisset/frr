**Readiness: PARTIALLY READY**

The existing baseline passed 13/13 tests in 102.38s before instrumentation was executed. The fresh instrumented leaf1→leaf2 move passed 1/1 in 41.00s, including topology startup/teardown. Three independent helper tests pass. FRR source, existing tests and router configurations were preserved. No large-scale workload was run and no bottleneck was established.

The results are in [verified-move](verified-move/manifest.json), with [raw events](verified-move/events.jsonl), [reachability](verified-move/host11-reachability.jsonl), [summary](verified-move/reachability-summary.json), [device counters](verified-move/device-counters.jsonl), [route/path counts](verified-move/route-counts.jsonl), [forwarding snapshots](verified-move/forwarding-snapshots.jsonl), [packet counts](verified-move/packet-counts.json), seven router PCAPs plus host/controller PCAPs, and router logs. [Timeline](verified-move/timeline.html) displays attachment, forwarding and reachability on the monotonic clock and BGP packet updates in a separate realtime panel. Individual diagrams: [local events](verified-move/timeline.svg), [BGP UPDATEs](verified-move/timeline-bgp.svg).

**Observed facts**

| View | Before move | After move |
| --- | --- | --- |
| Harness/link attachment | host11 leaf1 eth-host11/br1000 | veth transferred into leaf2 eth-host11/br1000; absent from leaf1 link snapshots |
| Zebra | leaf1 local sequence 0; leaf2 remote10.0.0.1 | leaf2 local sequence 1, detectionCount 1, duplicate=false; leaf1 remote10.0.0.2 sequence 1 |
| BGP at leaf5 | MAC-only and MAC/IP Type-2 routes under RD10.0.0.1:1000 | Both under RD10.0.0.2:1000, two spine paths each; old RD no longer owns the robot in final snapshot |
| leaf5 routed kernel forwarding | 45.0.0.11 lookup → br500 gateway10.0.0.1 → neighbor RMAC → vni500 FDB10.0.0.1 | Lookup → br500 gateway10.0.0.2 → neighbor RMAC → vni500 FDB10.0.0.2 |
| Controller ICMP | Successful controller-originated probes | Delivery recovered; 116 sends total, 1 timeout, 115 replies |

Times below are monotonic observations relative to the move-command event, rounded to three decimals. They are not exact internal transition times.

| Observation | Relative ms |
| --- | ---: |
| Move command issued | 0.000 |
| Old bridge detach command completed | 21.659 |
| New attachment commands completed | 46.170 |
| Intermediate leaf5 lookup resolved VTEP10.0.0.3, attachment10.0.0.2 | 1056.633 |
| First sampled leaf5 resolution matching10.0.0.2 | 2529.410 |

The intermediate kernel table snapshot lacks robot45.0.0.11's /32 and retains the45.0.0.0/24 ECMP route across leaves1–4. The source-specific route lookup returned10.0.0.3 and its RMAC/FDB resolved to that VTEP. This is a sampled forwarding mismatch, not proof that every packet used leaf3.

Probe sequence 41 timed out. Its send-to-next-successful-reply observation was 1020.546ms; this includes sampling, delivery/queueing and reply timing and is **not** an exact outage duration. Successful RTTs ranged 0.098–913.090ms. Requested 10Hz yielded about 9.604Hz over the observed send span. Snapshots ran approximately 1.4s apart despite a 0.5s requested pause, because command execution adds overhead. This cadence cannot resolve sub-second control-plane transitions.

Captures decode per leaf 6 sent/6 received UPDATE messages and 2 announced MAC-only plus 2 announced MAC/IP NLRIs in each direction, with 2 withdrawals of each kind in each direction. Leaf1/2/3/5 show 10 sent/10 received KEEPALIVEs; leaf4 shows 8/8. Spine1 shows 15/15 UPDATEs and 23/23 KEEPALIVEs; spine2 shows 15/15 and 24/24 respectively. These are decoded capture-window observations per location and direction, not unique fabric routes or redundant-advertisement findings. See packet-counts.json for NLRI breakdown and raw packet evidence. Type-5 routes remain represented in route snapshots; no decoded Type-5 wire change was counted.

All checked namespaces share time namespace `time:[4026532501]` with zero monotonic offsets, consistent with the harness clock. PCAP realtime has no calibrated mapping to monotonic time, so its BGP panel does not establish ordering against harness events. No duplicate-address warning was found in copied router logs; sampled duplicate flags are false. No session/hold-timer failure was established.

Environment observations: macOS27.0.1 host, Colima/Docker Linux aarch64 VM, Ubuntu22.04.5 container, kernel 6.8.0-117-generic, 4 VM CPUs (Apple vendor), 8,113,628kB RAM and7,040,688kB available in the initial environment snapshot. Seven routers plus nine endpoints in the topology. Privileged container, Docker NanoCpus=0 and Memory=0 (no explicit Docker quota); cgroup cpu.max=`max 100000`, memory.max=`max`. Initial cgroup nr_throttled=0; initial CPU pressure some avg10=2.79, full=0.00. These facts do not establish overload or absence of observer impact. FRR10.8.0-dev-my-manual-build configure flags are retained in events; baseline DAD enabled, 5 moves/180s. Emulation timings must not be interpreted as production hardware timings.

**Hypotheses and unresolved questions**

The temporary /32 absence is consistent with fallback to the Type-5 subnet while MAC/IP learning/reorigination catches up. Packet-level path/drop attribution and synchronized internal-event evidence are still needed before assigning cause to the observed timeout or high RTT. The instrumented run does not identify an overload threshold, persistent stale state or an FRR defect.

[Coverage and limitations](coverage.md) lists unavailable timestamps, missing workload controls and readiness gaps. [Repository audit](../../phase0-audit.md) distinguishes implemented behavior, documentation claims and future work with source line references.

The first instrumentation attempt failed its forwarding-resolution assertion because the measurement parser expected bridge JSON `dev` instead of `ifname`. Its artifacts and failed log are retained in `attempt-01/` and `attempt-01.log`; its reachability numbers are not substituted for the verified run. The parser was corrected, covered by a unit test, and the experiment rerun in a fresh topology. Baseline log: [baseline.log](baseline.log). Successful instrumented log: [verified-move.log](verified-move.log).

**Exact reproduction commands**

Run these from the repository root on the host with the existing privileged `frr-dev` container running. Do not run topology tests concurrently. Each output directory must be new.

```bash
docker exec frr-dev bash -lc 'cd /home/frr/frr/tests/topotests && ENABLE_LIVE_ROUTE_GRAPH=false sudo -E pytest -s bgp_evpn_capstone/test_evpn_capstone.py bgp_evpn_capstone/test_mobility_workload.py'
python3 -m unittest discover -s tests/topotests/bgp_evpn_capstone/phase0 -p 'test_*unit.py'
docker exec frr-dev bash -lc 'cd /home/frr/frr/tests/topotests && PHASE0_RUN=1 PHASE0_RESULTS=/tmp/phase0-reproduce-01 ENABLE_LIVE_ROUTE_GRAPH=false sudo -E pytest -s bgp_evpn_capstone/test_phase0_measurement.py'
docker exec frr-dev sudo python3 /home/frr/frr/tests/topotests/bgp_evpn_capstone/phase0/analyze.py /tmp/phase0-reproduce-01
docker cp frr-dev:/tmp/phase0-reproduce-01 /tmp/phase0-reproduce-01
```

The successful recorded run used `/tmp/phase0-move-02` as PHASE0_RESULTS and otherwise the same defaults. Capture/analysis uses the container's existing tshark; raw probing needs root/CAP_NET_RAW and topology mobility needs namespace/CAP_NET_ADMIN permissions. No dependencies were installed. Independent repetitions require fresh pytest runs and unique result directories. Do not begin scale experiments before closing the gaps in coverage.md.
