# Control-plane walkthrough

The representative evidence is **Phase 0 host11 leaf1 → leaf2**, not the missing AP experiment. All relative times below are command or sampled observations on the monotonic clock. Packet capture uses realtime with no calibrated mapping to these events. See `../phase0-2026-11-02/report.md`, `verified-move/events.jsonl`, packet events, forwarding snapshots and PCAPs.

| Stage | Evidence and interpretation |
|---|---|
| 1. Attachment | Command issued 0 ms; old bridge detach command completed +21.659 ms; new attachment commands completed +46.170 ms. Command completion bounds observations; exact kernel transitions UNKNOWN. |
| 2. Bridge learning | veth transferred to leaf2 `br1000`; old link absent in sampled leaf1 state. Exact FDB deletion/learn notifications and their times UNKNOWN. |
| 3. Zebra MAC | Before: leaf1 local seq 0, leaf2 remote via 10.0.0.1. After: leaf2 local seq 1, detectionCount 1, duplicate false; leaf1 remote via 10.0.0.2. Sequential snapshots, not continuous or atomic. |
| 4. Zebra IP neighbor | Exact per-device neighbor deletion, learn and timestamps UNKNOWN. Successful later host route is consistent with eventual MAC/IP availability, not proof of its mechanism. |
| 5. Mobility sequence | Sampled 0→1 at new leaf; exact increment event and route-wide ordering UNKNOWN. |
| 6. Type-2 origination | Leaf5 before/after RIB snapshots contain MAC-only and MAC/IP routes under old/new RD respectively; packet capture contains both announcement and withdrawal NLRIs. Specific internal bgpd origin/withdrawal times UNKNOWN. |
| 7–8. Spine distribution | Captures at both spines and leaves contain UPDATEs, and final leaf5 has two spine paths. Counts are captured per-device/direction, not evidence of exact enqueue or per-NLRI propagation across both peers without further correlation. |
| 9. Remote selection | Final leaf5 RIB has two paths for each route. Exact selection event and selection during the interval UNKNOWN. |
| 10. Zebra/kernel | Final source-specific route lookup, neighbor RMAC, and FDB resolve leaf2 10.0.0.2. Queries sequential; exact install event UNKNOWN. |
| 11–12. /32 and /24 | Intermediate sample at +1056.633 ms: /32 absent, /24 ECMP present and lookup/RMAC/FDB resolution via leaf3 10.0.0.3; first matching sample +2529.410 ms. These bound no precise withdrawal/reinstallation time; route, lookup and FDB were separate queries. Type-5 subnet present; route use by a given packet UNKNOWN. |
| 13. Delivery | 116 controller ICMP sends, 115 replies, one timeout. Send-to-next-successful-reply 1020.546 ms is not outage duration. Packet loss location and causal relation to sampled mismatch UNKNOWN. |

Per-device capture clocks, decoded NLRI records and Phase 1 sample indices are in original artifacts; clocks cannot be exactly joined. Phase 1 BBM-1 capture recorded old leaf robot withdrawal before new leaf MAC-only announcement; MBB-100-1 recorded the reverse on capture clock. These are examples, not universal ordering or proof of internal event timings. Phase 0 packet counts report six UPDATEs sent and six received per leaf within that capture window, and 15 each direction per spine; per-peer duplication and capture behavior preclude interpreting these as unique changes or fleet rates.
