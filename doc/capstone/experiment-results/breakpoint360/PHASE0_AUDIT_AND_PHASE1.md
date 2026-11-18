# breakpoint360 — Phase 0 audit and Phase 1 workload generator

Status: **Phase 0 and Phase 1 complete; stopped for review before Phase 2.**
Date: 2026-11-12/13. Branch `dev/guna-experiments` at `585ecd991f` (uncommitted
working tree). The completed roadmap360 code and 120-trial results were not
modified; breakpoint360 imports roadmap360 modules instead of editing them.

> Scope note: two instructions arrived for this phase. The first said to run all
> phases (0–7) unattended; the second, later one gave the full plan and limited
> this step to Phase 0 + Phase 1 with a stop for review. The later, narrower
> instruction was followed. No Phase 2–7 experiment was run. The only runs were
> five Phase 1 smoke trials that validate the generator.

Research question (sponsor): does more robot mobility make the EVPN control
plane fall behind and leave stale or missing *forwarding state*? At what
measured workload, where in the pipeline, and is it saturation or the known
NOARP issue?

---

## Phase 0 — Repository audit

### 0.1 Reusable roadmap360 code

| roadmap360 module | What it does | breakpoint360 use |
|---|---|---|
| `fleet.py` | Address plan (≤1000 robots, 5 subnets/VNIs 1000–1004), radio model (robot `br0` + one pre-wired veth per leaf, one radio up), extra-VNI dataplane/FRR config, leaf netns attach, bulk create/destroy | **Reused as-is** by `workload.py` and the Phase 1 driver |
| `fleet_agent.py` | Single-process robot driver: `setns`, rtnetlink `Rtnl.setlink`, GARP frame, 1 Hz telemetry | **Reused**: warm-up runs it unchanged; `bp_agent.py` imports `setns`, `Rtnl`, `nl_attr`, `garp` |
| `nlmon.py` | Event-driven rtnetlink listener (link, neigh, AF_BRIDGE FDB, IPv4 route, nexthop) with drop counters | `attrs()` reused in `bp_agent.py`; whole listener → Phase 2 kernel FDB/route layer and Phase 3 `PENDING_INSTALL` timing |
| `cpusample.py` | /proc zebra/bgpd ticks per node + host `/proc/stat` | Phase 2 control-plane vs emulator saturation |
| `ktrace.py` | kprobes on `__neigh_update` / `arp_process` | Phase 2/3: attributes relearn failures to NOARP rejections (needed for question 4) |
| `normalize.py` (`wire_bgp`) | tshark decode of BGP UPDATEs into per-NLRI actions (rt, rd, mac, ip) | Phase 2: UPDATE messages vs EVPN NLRIs |
| `analyze_360_roadmap.py` | Milestones T_mac, T_withdraw_recv, T_ip, T_leaf5_fib, ECMP fallback | Phase 3/5 definitions carried over |
| `sink.py`, `probe.py` | Telemetry sink; controller ICMP prober | sink reused now; prober = secondary data-plane check in Phase 3 |
| `run_360_suite.py` | Resumable suite runner, netns hygiene, retry, block randomisation | Pattern for the Phase 4 runner |
| `test_roadmap360.py` | Fresh-topology trial, warm-up, teardown wait for async netns deletion | Setup/warm-up/teardown copied into `test_breakpoint360.py` |

### 0.2 How EVPN routes cross this topology (verified)

Config (`tests/topotests/bgp_evpn_capstone/*/frr.conf`): leaves AS 65011–65015, spines AS 65000,
eBGP unnumbered leaf↔spine, `l2vpn evpn` activated everywhere. Spines have no VNIs, no RT import,
no `attribute-unchanged`. Leaves: `advertise-all-vni`; leaf1–4 carry VNI 1000 (+1001–1004 for the fleet),
leaf5 carries VNI 2000; every leaf imports L3VNI 500 (VRF500, symmetric IRB, `advertise ipv4 unicast`).

Code:
* `bgpd/bgp_updgrp_packet.c:595-606` — for AFI_L2VPN the next hop is rewritten only when it is
  0.0.0.0, so spines relay RT-2 with the **originating VTEP** as next hop.
* No route-target retention filter for EVPN (the retain check at `bgp_route.c:6309-6313` is
  `SAFI_MPLS_VPN` only), so
  spines keep and re-advertise every RT-2 without importing it.
* `bgp_route.c:5392` `process_queue->spec.hold = 50` ms: bgpd batches route processing at each hop
  (roadmap360 measured ≈50 ms per hop: leaf → spine → leaf).
* zebra DAD (`zebra_evpn_mac.c:466-548` detection, `:2084-2087` remote update, `:2283-2290` local
  learn): the per-VTEP counter goes up only on real
  local→remote / remote→local transitions; duplicate at count ≥ `max-moves`. Fabric shows
  `Duplicate address detection: Enable`, `max-moves 5, time 180`.

Observed in smoke trial `smoke-s64` for one moved robot (MAC 02:36:00:00:00:32, leaf4 → leaf2, 2nd move):

| Node | RT-2 MAC route (RD 10.0.0.2:1000) | Next hop | AS path | Ext. community |
|---|---|---|---|---|
| leaf2 (new owner) | 1 local path, best | 10.0.0.2 | Local | MM:2 |
| spine1 | 1 path, best, advertised to **all 5 leaves incl. leaf2** | 10.0.0.2 | 65012 | MM:2 |
| leaf5 | 2 paths (one per spine), 1 best | 10.0.0.2 | 65000 65012 | MM:2 |
| leaf4 (old owner) | 2 paths (one per spine) | 10.0.0.2 | 65000 65012 | MM:2 |

The old-RD route (10.0.0.4:1000) was withdrawn at every node. This robot had **no** MAC+IP route and no
Leaf5 /32 at the end of the run: the known NOARP silent-robot gap (roadmap360) reproduced, see 0.4.

UPDATE-message counters (`show bgp neighbors json`, pre/post workload) show two amplification paths to
quantify in E1:
1. Each spine sends every UPDATE to all five leaves, **including the originator** (every leaf received the
   same count, e.g. 137 per spine in `smoke-s64`), where it is dropped by the AS-path loop check.
2. Every leaf re-advertises spine-learned EVPN routes to the *other* spine. Leaf5 originates no robot
   routes yet sent 274 UPDATEs in `smoke-s64` (≈20 % of all leaf UPDATEs). The other spine drops them
   (AS 65000 already in the path).

Indicative fabric-wide UPDATE messages per completed move (n=1 each, counters include IP relearns; **not**
an E1 result): 41.8 (64 robots, 8/s), 26.1–26.3 (1000 robots, 128/s staggered), **11.6** (1000 robots,
128/s synchronized). Synchronized moves pack more NLRIs per UPDATE, which is E3's question.

### 0.3 Measuring BGP UPDATE messages vs EVPN NLRIs

| Layer | Method | Gives | Caveats |
|---|---|---|---|
| Wire | dumpcap `tcp port 179` on every leaf↔spine link; tshark `bgp.type == 2`; per UPDATE count MP_REACH EVPN NLRIs and MP_UNREACH withdrawn NLRIs (`normalize.wire_bgp` already decodes rt/rd/mac/ip per NLRI) | UPDATE messages, NLRIs/UPDATE (packing), RT-2 MAC-only vs MAC+IP, advertise vs withdraw, per hop | Count each link once (one capture side), let tshark reassemble TCP, check dumpcap drop stats, ignore KEEPALIVEs. Capture cost grows with rate, so watch emulator CPU |
| bgpd counters | `show bgp neighbors json` → `messageStats.updatesSent/updatesRecv` per peer (verified) | Exact UPDATE message counts per session | No NLRI counts; `pfxRcd/pfxSnt` are table sizes, not churn |
| bgpd per-NLRI | `debug bgp updates in/out` (µs timestamps, roadmap360) | Per-prefix receive/send times per hop | Heavy at high rates: E1 only, not the rate sweep |
| bgpd queueing | Peer InQ/OutQ (`show bgp summary json`), `show work-queues`, `show event cpu` (bgp_process) at low sample rate | Accepted vs queued, backlog | Polling perturbs; sample sparsely and label it |
| zebra ZAPI | `show zebra client` (json: `macIp.add/delete`, `zserv.c:1263`) — verified row present | MAC-IP add/del ZAPI messages zebra→bgpd per leaf | Cumulative since start: take pre/post deltas |
| Kernel | `nlmon.py` FDB/neigh/route events (Leaf5 /32 install = T_leaf5_fib) | Installed forwarding state, timing | Userspace receipt time; check socket drops |

### 0.4 Constraints and risks found

* **NOARP confound is live in this workload.** A robot that only sends one GARP on attach can stay without a
  MAC+IP route because its ARP reached the new leaf before the old leaf's withdrawal (seen in `smoke-s64`).
  The generator therefore supports optional gateway re-ARP after attachment (`arp_offsets_ms`); with
  `250,1000` ms (`smoke-y64b`) the last-moved robot's MAC+IP route and Leaf5 /32 were present. Phase 3/4
  should run both behaviours and use `ktrace.py` so NOARP rejections are not counted as saturation.
* **DAD.** FRR flags a MAC duplicate at 5 moves/180 s. The planner caps moves per robot at 4 by default,
  which can never reach 5 at any VTEP (each move is one departure plus one arrival). For 128 moves/s × 10 s
  this needs ≥320 active robots.
* **Emulator.** One 4-vCPU / 8 GiB Colima VM runs 7 routers (14 FRR daemons) plus up to 1000 robot
  namespaces on one kernel. Every netlink configuration change in every namespace takes the same RTNL
  lock. At 128 moves/s a radio-up ACK sometimes waited up to 99 ms and the generator loop stalled
  up to 290 ms (transient; see Phase 1). Emulator saturation must be measured, not assumed away.
* **Kernel neighbour table.** ~1000 robot namespaces overflow the default global IPv4 neighbour limits.
  Set at runtime in the VM (as roadmap360 did; reverts on VM restart):
  `colima ssh -- sudo sysctl -w net.ipv4.neigh.default.gc_thresh1=4096 net.ipv4.neigh.default.gc_thresh2=8192 net.ipv4.neigh.default.gc_thresh3=16384`
  (restore: `128 / 512 / 1024`). **Currently set to the raised values.**
* Address plan max 1000 robots. veth pairs = active × hosting leaves + static. Teardown of 2200 veths takes ~70 s.

### 0.5 Workload generator plan (as implemented)

1. **Planner** (`workload.py`, pure): reuse the fleet address plan; home leaf = `hosting[i % L]`; active robots
   stratified by home leaf; mobile robots wired to every hosting leaf, static robots to home only. Due times
   are `k / rate` (or Poisson) inside the duration. Synchronized mode quantises those same times down to
   `sync_period_s` boundaries, so count and mean rate stay identical and only packing changes. Robots move in
   a fixed shuffled cycle (largest same-robot spacing, distinct robots per batch). Destination is random among
   wired leaves ≠ current. Enforce the DAD cap; seeded and deterministic.
2. **Executor** (`bp_agent.py`, root): one process; per robot UDP + AF_PACKET + rtnetlink socket (reused
   roadmap360 primitives); per hosting leaf an RTMGRP_LINK listener. Each move is BBM: old radio down, then new
   radio up. A batch is pipelined across robots. The attachment completes when the **leaf-side bridge port
   reports FORWARDING**; then GARP is sent and optional re-ARPs are scheduled.
3. **Ledger** (`ledger.py`): append-only `ledger.jsonl`; robot is ATTACHED only from `attach_confirmed` until the
   next detach (IN_TRANSIT between; UNCONFIRMED on timeout); stale confirmations ignored; `state_at(robot, t)`
   for the Phase 3 oracle.
4. **Rates**: requested (spec) vs issued vs **achieved = attachments actually completed** per requested window
   and over the whole span, 1-s bins, batch start lag, synchronized batch issue spread, completion latency. The
   generator flags itself if achieved < 95 % of requested, batch start lag p95 > 100 ms, or synchronized spread
   p95 > 25 % of the period, and records its own loop stalls and context switches.

---

## Phase 1 — Core workload generator

### Files

| Path | Role |
|---|---|
| `tests/topotests/bgp_evpn_capstone/breakpoint360/workload.py` | Planner: robots, active %, hosting leaves, rate, staggered/synchronized, uniform/Poisson, DAD cap, `agent_spec()` / `rebase()` |
| `tests/topotests/bgp_evpn_capstone/breakpoint360/bp_agent.py` | Executor + leaf-side attachment confirmation + achieved-rate summary |
| `tests/topotests/bgp_evpn_capstone/breakpoint360/ledger.py` | Authoritative attachment ledger, `achieved_rates()` |
| `tests/topotests/bgp_evpn_capstone/breakpoint360/test_bp_unit.py` | 21 unit tests (no root) |
| `tests/topotests/bgp_evpn_capstone/test_breakpoint360.py` | Opt-in topotest: fresh fabric → fleet → workload → ledger vs kernel check |
| `doc/capstone/experiment-results/breakpoint360/phase1-smoke/` | Smoke evidence, `phase1-smoke-summary.csv`, `summarize_smoke.py` |

Parameters (env `BP_*` in the driver, keyword args to `workload.plan()`): `robots`, `active_pct`,
`hosting_leaves` (2–4), `rate` (requested aggregate inter-leaf moves/s), `duration_s`,
`mode` = `staggered|synchronized`, `sync_period_s`, `arrival` = `uniform|poisson`,
`max_moves_per_robot` (4), `allow_dad_exceed`, `seed`, `telemetry_hz`, `garp`, `arp_offsets_ms`,
`attach_timeout_ms`.

### Validation

Unit tests (container): `python3 -m unittest discover -s tests/topotests/bgp_evpn_capstone/breakpoint360 -p 'test_*unit.py'`
→ **21 OK** (roadmap360's 6 still OK). They cover spacing and counts, synchronized batches (distinct robots,
fractional rates), active % and stratification, hosting leaves, inter-leaf chaining, DAD cap, determinism,
Poisson, invalid specs, T0 rebase, the ledger state machine (stale confirmation, attach error), achieved-rate
maths (on target, falling behind, missing completions, batch spread), and netlink bridge-port parsing.

Smoke trials (fresh fabric each; `phase1-smoke/phase1-smoke-summary.csv`):

| Trial | Robots (active) | Leaves | Mode | Requested | Completed | Achieved (overall) | 1-s bins | Batch start lag p50 / p95 / max ms | Batch spread ms | Ledger vs kernel |
|---|---|---|---|---|---|---|---|---|---|---|
| smoke-s64 | 64 (32) | 4 | staggered | 8/s | 64/64 | 8.00/s | 8–8 | 1.0 / 4.7 / 7.2 | – | 0 mismatches / 64 |
| smoke-y64b (re-ARP 250,1000) | 64 (64) | 2 | synchronized 1 s | 16/s | 128/128 | 16.00/s | 16–16 | 2.9 / 5.7 / 6.9 | 18–20 | 0 / 64 |
| smoke-s1000 | 1000 (400) | 4 | staggered | 128/s | 1280/1280 | 128.00/s | 104–152 | 0.01 / **130.7** / 238 | – | 0 / 1000 |
| smoke-s1000b | 1000 (400) | 4 | staggered | 128/s | 1280/1280 | 128.00/s | 109–147 | 0.01 / 96.9 / **290** | – | 0 / 1000 |
| smoke-y1000 | 1000 (400) | 4 | synchronized 1 s | 128/s | 1280/1280 | 128.00/s | 128–128 | 0.26 / 0.65 / 0.8 | **123–151** | 0 / 1000 |

Every attachment in every trial was confirmed by the leaf bridge port reaching FORWARDING (0 timeouts). The
ledger matched the kernel's actual radio state for every robot in every trial.

What the smoke runs show about the generator (not about FRR):
* Up to 128 moves/s with 1000 robots it delivers the requested **average** rate exactly. At 128/s staggered it
  has rare transients where one radio ACK is held up to 99 ms and the schedule slips by up to 0.29 s, then
  catches up. `smoke-s1000` raised the lag flag (p95 130.7 ms). `smoke-s1000b` (agent at nice −10) passed
  the p95 test only narrowly (96.9 ms against the 100 ms limit). Phase 4 must report lag per trial and treat
  flagged trials as generator-limited.
* "Synchronized" in this emulator means a 128-move batch issues over ~123–151 ms (kernel-serialised
  ~1 ms per radio toggle, even when pipelined), not instantaneously. E3 must use the measured spread.
* Completion latency (radio-up request → leaf bridge FORWARDING) is ~1 ms median when staggered and ~104 ms
  median inside 128-move batches.

Fixes made during Phase 1 (all re-tested): synchronized batches were issued serially (spread 17–38 ms for
16 moves) → pipelined; `select()` failed on fds ≥ 1024 with 1000 robots → `poll()`; generator starvation
→ `nice -10` plus loop-stall and context-switch accounting.

Re-run a smoke trial (inside `frr-dev`, from `tests/topotests`, after the sysctl in 0.4):

```sh
sudo -E env BP_RUN=1 BP_OUT=/tmp/bp360/x BP_ROBOTS=1000 BP_ACTIVE_PCT=40 BP_LEAVES=4 BP_RATE=128 \
  BP_DURATION=10 BP_MODE=staggered ENABLE_LIVE_ROUTE_GRAPH=false \
  timeout --kill-after=60 900 python3 -m pytest -s -x -q bgp_evpn_capstone/test_breakpoint360.py
```

### Not done (by instruction) / open items for review

* Phases 2–7 not started. No control-plane instrumentation, no oracle, no experiment matrix, no graphs.
* Proposed Phase 2 entry point: add `nlmon` (all leaves), `cpusample` (daemons + host), pcaps on leaf↔spine
  links and pre/post `show zebra client json` to the existing driver. Then write the per-NLRI counter on top of
  `normalize.wire_bgp`.
* Decisions for review:
  1. Default robot behaviour for E2: silent (GARP only) vs re-ARP. Proposed: run both, because the
     difference isolates NOARP.
  2. Active % / duration per E2 rate under the DAD cap. Proposed: 1000 robots, active = max(10 %, rate × 10 s / 4).
  3. Mobile robots are wired to all hosting leaves; E4 (leaf-count amplification) varies hosting leaves
     2/3/4 while all four access leaves keep the VNIs. Confirm that is the intended definition.
  4. VM sysctl (neighbour table) stays raised for the study; OK?
