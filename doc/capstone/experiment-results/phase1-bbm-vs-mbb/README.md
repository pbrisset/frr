# Phase 1A: dual-attachment smoke test

## Design

```
                     leaf1 br1000 <-- eth-host11 <-- eth0 --+
                                                            | host11 namespace
                     leaf2 br1000 <-- mbb-leaf <-- mbb-host-+
```

This is one host namespace, not a second robot. The existing eth0 retains the only 45.0.0.11/24 assignment and default route during overlap. The new interface has the same MAC, no IP address, and is not bridged to eth0. A single raw gratuitous ARP request with the robot MAC/IP is sent through the new interface to cause source learning. Ordinary IP traffic remains on eth0 during overlap; after detaching leaf1 the address and default route move to mbb-host. This is a controlled access-port experiment, not seamless IP multipath or a wireless roam. No host bridge connects the two access links, avoiding a layer-2 loop. The two same-MAC interfaces can still induce legitimate mobility/DAD events; none are suppressed. Linux may accept traffic arriving on either interface while the IP remains on eth0; concurrent production traffic is not modeled.

The overlap parameter (100, 250, or 500 ms) is a minimum: synchronous checks, event recording and scheduling can extend it. Attach and detach times are userspace command-completion observations, not exact kernel timestamps. The snapshot commands execute sequentially and can lengthen the overlap; simultaneous control-plane state cannot be established from them alone.

## Validation

Host branch `dev/guna-experiments`, commit `689379cf254e3b80dc3e889a2f29a3f156704736`. Privileged frr-dev Linux container; no FRR or router configuration changes. Tests were run sequentially:

```sh
docker exec frr-dev bash -lc 'cd /home/frr/frr/tests/topotests && ENABLE_LIVE_ROUTE_GRAPH=false sudo -E pytest -s bgp_evpn_capstone/test_evpn_capstone.py bgp_evpn_capstone/test_mobility_workload.py'
python3 -m unittest discover -s tests/topotests/bgp_evpn_capstone/phase0 -p 'test_*unit.py'
docker exec frr-dev bash -lc 'cd /home/frr/frr/tests/topotests && PHASE1_MBB=1 PHASE1_OVERLAP_MS=250 PHASE1_RESULTS=/tmp/phase1a-smoke-03 ENABLE_LIVE_ROUTE_GRAPH=false sudo -E pytest -s -x bgp_evpn_capstone/test_phase1_mbb.py'
```

Baseline 13 passed in 114.38 seconds, helper tests 3 passed, final smoke 1 passed in 28.28 seconds. Use a new results directory on each run. The earlier attempt-01 failed because the test called a nonexistent `cmd_status` method; cleanup executed. Smoke-02 passed before the final added Zebra and cleanup assertions. Both attempts' evidence is retained. The event files contain raw command outputs and monotonic timestamps, not packet captures.

## Observations: smoke-03

The event log records new attachment at 25692266123034 ns, gratuitous ARP at 25692284514540 ns, and old attachment removal at 25692528387584 ns. The reported elapsed overlap was 262.265 ms, including command-completion and event-recording overhead. The test asserted both ports were attached to their respective bridges before removal, that the new interface had the original MAC, and that the IP occurred exactly once in the host namespace.

In sequential overlap snapshots leaf1 showed local MAC sequence 0 and leaf2 showed local MAC sequence 1, detectionCount 1 and isDuplicate false. Leaf2's BGP table contained the old leaf1 MAC-only and MAC/IP entries and the new leaf2 MAC-only entry; this does not establish simultaneous wire advertisements. In the final snapshot both MAC-only and MAC/IP entries were under leaf2's RD, and leaf1 showed the MAC remote via leaf2, sequence 1. No duplicate flag was observed in these samples; transients between samples are unknown.

The robot successfully pinged its gateway after transfer, and a controller-originated ping succeeded after the handoff. This is a connectivity check, not a continuous loss or outage measurement. Cleanup moved the single IP and default route back to eth0, deleted the temporary veth pair, reattached leaf1 and sent gateway traffic. Assertions verified the address, original attachment and absence of temporary interfaces; cleanup bridge FDB snapshots are retained. They do not prove all distributed EVPN state had converged before topology teardown.

## Files and limitations

- `tests/topotests/bgp_evpn_capstone/test_phase1_mbb.py`: opt-in topology test, snapshots and cleanup.
- `tests/topotests/bgp_evpn_capstone/phase1/announce.py`: raw ARP stimulus.
- `attempt-01/events.json`, `smoke-02/events.json`, `smoke-03/events.json`: per-attempt evidence.

Only 250 ms was executed; 100 and 500 ms are accepted but not experimentally validated. The test is not yet integrated into the Phase 0 continuous probe or PCAP harness, and the full comparison matrix was not run. No exact BGP, Zebra, kernel or packet transition times are measured. The control-plane snapshots and isolated ping do not justify any claim about packet loss, superiority, or production wireless handoff behavior.
