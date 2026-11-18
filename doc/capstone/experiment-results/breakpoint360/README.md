# breakpoint360 — EVPN mobility breaking-point investigation

Question: does more robot mobility make the EVPN control plane fall behind and
leave stale or missing forwarding state? If so: at which measured workload, at
which pipeline stage, and is it control-plane saturation or the known NOARP
relearning gap (roadmap360)?

| Phase | Status | Output |
|---|---|---|
| 0 Repository audit | done | [`PHASE0_AUDIT_AND_PHASE1.md`](PHASE0_AUDIT_AND_PHASE1.md) §0 |
| 1 Core workload generator | done, awaiting review | same file §Phase 1, [`phase1-smoke/`](phase1-smoke/) |
| 2 Control-plane instrumentation | not started | – |
| 3 Forwarding correctness oracle | not started | – |
| 4 Experiments E1–E4 | not started | – |
| 5–7 Breaking-point definition, statistics, outputs | not started | – |

Code: `tests/topotests/bgp_evpn_capstone/breakpoint360/` (`workload.py`,
`bp_agent.py`, `ledger.py`, `test_bp_unit.py`) and the opt-in trial
`tests/topotests/bgp_evpn_capstone/test_breakpoint360.py`. It reuses
roadmap360's fleet, fleet agent and netlink helpers by import; roadmap360 code
and results are unchanged.

Unit tests (inside `frr-dev`): `python3 -m unittest discover -s
tests/topotests/bgp_evpn_capstone/breakpoint360 -p 'test_*unit.py'`.

Smoke evidence keeps each trial's ledger, move log and plan as small
`*.jsonl.gz` / `*.json.gz` files (tracked) next to the JSON summaries. Plain
`*.jsonl` and `*.log` (e.g. `agent.log`) are git-ignored by the
repository-wide patterns.
