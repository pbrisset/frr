# Teammate reconciliation checklist

- [ ] Obtain original AP report, exact harness (`test_mobility_scale.py`, `mobility_scale.py`, `mobility_prober.py`, `run_mobility_points.sh`, `analyze_mobility.py` if those are the actual names), configurations, scripts, datasets and commit/dirty state. Absence from our search is not proof they do not exist elsewhere.
- [ ] Map AP attachment, departure detection, old-port behavior, zero-gap/BBM/MBB, MAC/IP identity, simultaneous links and access learning. Do not equate it with our veth BBM or single-IP ARP-triggered MBB.
- [ ] Compare effective DAD settings and observation windows, including any disabled DAD, thresholds, hold/freeze and cleanup-time warnings.
- [ ] Match initial, repeated, delayed, gratuitous and gateway ARP, ICMP telemetry and naturally generated background traffic. Identify actual frames and realized offsets, not just commands.
- [ ] Separate controller-originated delivery, probe sampling/rate/timeout, application location state, Leaf5 forwarding agreement and per-move censoring.
- [ ] Count BGP UPDATE messages, announced/withdrawn NLRIs, unique keys, per-peer copies and retransmissions separately; assess queues and drain, session/hold timers and captures.
- [ ] Compare Leaf5 /32 versus Type-5 /24 ECMP, route-get source, RMAC/neighbor, VXLAN FDB, actual packet VTEPs and successful alternate paths. Record unsampled/unknown intervals.
- [ ] Compare kernel, `NUD_NOARP`/external neighbor flags, neighbor notification loss, Zebra MAC/IP ownership, mobility sequence and actual running binary/kernel provenance.
- [ ] For fleet tests use achieved moves/s, completed moves, burst shape, seeds, observer saturation, per-run convergence and time-to-drain; compare matched rates across schedules. Low average CPU or temporary queue occupancy is not a capacity result.
- [ ] Separate laptop/VM resource limits from switch capacity and obtain packet/capture loss and namespace/neighbor-table limits. Match hardware, clocks and sampling before cross-study quantitative claims.
- [ ] Resolve which claims are supported by original raw artifacts, which conflict with our observations, and which need a narrowly scoped independent test. Do not merge teammate claims into confirmed findings at this stage.
