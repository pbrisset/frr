# Phase 1B: BBM and ARP-triggered MBB comparison

Twenty fresh-topology matrix trials passed the checks described below: five each of BBM, MBB-100, MBB-250 and MBB-500. These are small emulation samples, not production performance results. The trials ran sequentially in that fixed order; order and time effects are not randomized. This report does not claim that MBB is better.

| Method | Probe losses, per run | Median losses | Sampled host /32 absent | Sampled wrong leaf5 VTEP | Observed interval range (ms) |
|---|---|---:|---:|---:|---:|
| BBM | 0, 0, 0, 1, 1 | 0 | 0/5 | 0/5 | 13.74–19.79 disconnected |
| MBB 100 | 0, 0, 0, 0, 0 | 0 | 4/5 | 4/5 | 109.83–122.10 overlap |
| MBB 250 | 0, 0, 0, 1, 0 | 0 | 0/5 | 0/5 | 261.67–271.06 overlap |
| MBB 500 | 0, 1, 1, 0, 3 | 1 | 5/5 | 5/5 | 513.55–529.05 overlap |

There were 113–116 probes per run, requested at 10 Hz. The longest observed consecutive loss burst was three, in MBB-500-5. The five-run median loss does not establish a difference in reliability. Successful RTT medians per run ranged 0.143–0.209 ms across all runs, while individual maximum RTTs reached approximately 1 second. Probe timeouts are not outage durations; see `reachability.csv` and `runs.csv` for per-run data and first successful probe after detachment. A probe sent earlier can also return later. No lost probe has been assigned a cause.

The /24 route was present in sampled route tables, including when /32 was absent. A leaf5 lookup resolving through leaf3 instead of the actual leaf2 was sampled in four MBB-100 runs and all five MBB-500 runs. Lookup, neighbor and FDB queries were sequential, so this is an inferred sampled mismatch, not an atomic forwarding observation. The matrix BBM and MBB-250 samples did not catch a missing /32 or mismatch; this does not establish that neither occurred between samples. The revised BBM and MBB-250 pilots each sampled a leaf3 mismatch, independently of the matrix, and had respectively zero and one lost probe. A missing /32 plus available /24 is consistent with fallback; the sampled route lookup is not evidence that a particular lost probe took that path.

## EVPN and Zebra

In every matrix run the initial sampled Zebra state was leaf1 local and leaf2 remote. Leaf2 was sampled local with sequence 1 and detectionCount 1 in every run. Its sampled local sequence/detection count reached 3/3 in three MBB-250 and all five MBB-500 runs; all five BBM and five MBB-100 runs sampled a maximum of 1/1. The duplicate flag was false in every captured robot MAC sample. Detection counts are DAD-related move counts, not evidence that a duplicate condition triggered. Sampling can miss intermediate states.

The captures show method-dependent advertisement order and extra activity in examples, but do not supply a universal ordering. For example, in `phase1b-bbm-1` the captured leaf1 MAC-only/MAC-IP withdrawal preceded leaf2's MAC-only announcement on the capture clock; in `phase1b-mbb100-1` leaf2's MAC-only announcement preceded leaf1's captured withdrawal. In `phase1b-mbb500-1` the capture contains several robot MAC-only announcements and withdrawals. In the five BBM runs, decoded leaf2 sent counts for the robot were consistently two MAC-only and two MAC/IP announced NLRIs, and two of each withdrawn; in the MBB-500 runs the corresponding MAC-only announcement count was six, while MAC/IP announcements were four. Counts include separate peer transmissions and may include reassembly/capture artifacts; they are not unique routes. `bgp-messages.csv` contains message counts including UPDATE and KEEPALIVE, `bgp-robot-nlri.csv` contains robot NLRIs by device and capture timestamp, and per-run `packet-events.jsonl` retains message-level records. The recorded BGP realtime clock was not calibrated to monotonic events, so packet ordering cannot be precisely aligned to attachment or IP transfer.

All sampled BGP neighbor states were Established; brief transitions between samples remain possible. Session and Zebra samples are in `sessions.csv` and `zebra-transitions.csv`.

## Method and validity

Both modes reuse the same Phase 0 12-second controller probe, 10 Hz requested rate, snapshots, seven router and two endpoint packet captures, and baseline DAD configuration (enabled, 5 moves/180 seconds). The test kept the existing two-spine/five-leaf topology, host11 identity and controller. The MBB stimulus uses the Phase 1A second veth and gratuitous ARP; the new interface has no configured robot IP during overlap. The original interface remains the normal IP egress until after detachment, when the address and default route move. Physical overlap therefore does not imply two configured normal IP egress paths. Linux weak-host receive behavior was not separately measured. The timing of IP/default-route transfer relative to probe loss cannot establish causation here.

The original pilots passed but lacked explicit cleanup assertions; they were retained. The revised BBM and MBB-250 pilots passed with these checks. All 20 matrix trials had nine capture processes alive at startup, probes before the move and successful probes after final attachment, initial leaf1-local/leaf2-remote Zebra samples, a matching post-move forwarding sample and verified basic cleanup. Each had a fresh test topology and a manifest. The checks establish an experiment was executed and reached its final state; they do not prove every packet was captured, exact state-transition timing, or distributed convergence after cleanup. The existing Phase 0 helper tests passed 3/3 before this phase; the Phase 1A baseline passed 13/13.

## Reproduction

From the repository root, with the existing privileged `frr-dev` container running, use a **new** result path for each invocation and do not run topologies concurrently:

```sh
docker exec frr-dev bash -lc 'cd /home/frr/frr/tests/topotests && PHASE1B_RUN=1 PHASE1B_RESULTS=/tmp/new-bbm-01 PHASE1B_MODE=break-before-make ENABLE_LIVE_ROUTE_GRAPH=false sudo -E pytest -s -x bgp_evpn_capstone/test_phase1_comparison.py'
docker exec frr-dev bash -lc 'cd /home/frr/frr/tests/topotests && PHASE1B_RUN=1 PHASE1B_RESULTS=/tmp/new-mbb-01 PHASE1B_MODE=make-before-break PHASE1B_OVERLAP_MS=250 ENABLE_LIVE_ROUTE_GRAPH=false sudo -E pytest -s -x bgp_evpn_capstone/test_phase1_comparison.py'
docker exec frr-dev sudo python3 /home/frr/frr/tests/topotests/bgp_evpn_capstone/phase0/analyze.py /tmp/new-mbb-01
```

For the matrix use five fresh invocations per setting, in the order BBM, 100, 250, 500 and record each output. The actual run names and raw files are listed in `runs.csv` and `raw/`. Each raw directory contains its manifest, `events.jsonl`, probe records, capture logs, pcapng files, router logs, decoded packet events and per-run timeline. The first two pilots are `phase1b-pilot-*`, and revised pilots are `phase1b-pilot2-*`; pilots are excluded from matrix summaries. `comparison.csv`, `route-transitions.csv`, `forwarding-transitions.csv`, `reachability.csv`, `zebra-transitions.csv`, `sessions.csv`, and `bgp-messages.csv` provide aggregate views. See `plots/` and per-run timelines for visualizations. Phase 1A's README and smoke evidence remain intact.
