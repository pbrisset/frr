# Experiment 2 — Synchronized vs. staggered mobility

Each trial starts a fresh 5-leaf, 2-spine fabric, with 64 robots roaming among
leaf1–leaf4 and the controller on leaf5. The runner stages the original handoff
tools in the container and reuses the checked-in fixed-topology baseline.
Duplicate address detection is disabled, robot gateway announcements use
`repeat`, and BGP keeps the baseline advertisement interval and sender-side
AS-path defaults.

A seed fixes the same initial placements and per-robot source/destination
sequence for both schedules. Each round gives every robot one move.
`MOB_SYNC=1` dispatches all 64 moves together using the archived worker pool.
`MOB_SYNC=0` spreads starts evenly over the same dwell interval and assigns
the phase to robots in a seeded order. This gives both schedules the same move
count and nominal per-robot dwell. The warmup has at least one dwell of quiet
control before movement, and each trial has a 15-second cooldown. The archived
attachment procedure uses break-before-make.

## Run

Use the privileged Colima `frr-dev` container with the checkout mounted at
`/home/frr/frr` and the handoff archive at
`team-handoff/guna/mobility_tools.tar.gz`. The runner creates a new output
directory per schedule and refuses to overwrite evidence. Set `MOB_SEED`
for a new paired repetition.

```bash
out=doc/capstone/experiment-results/breakpoint360/experiment2
bash "$out/reproduce.sh" smoke                     # One round, 10 s dwell
bash "$out/reproduce.sh" matrix-all                # Seeds 1–3, both schedules and dwells
tail -f "$out/trials/matrix.log"                    # Follow the detached Colima run
cat "$out/trials/matrix.exit"                      # 0 after all 12 trials pass
python3 "$out/compare.py" "$out/trials"
```

Each matrix trial has six rounds. The runner starts the tests serially inside
the same container, with a fresh topology per trial. The detached run continues
if the host terminal disconnects. For a single-seed matrix in a fresh results
directory, use `MOB_SEED=1 bash "$out/reproduce.sh" matrix`.

## Evidence per trial

- `counts.csv` and `timeline.csv` contain shared-counter EVPN data.
  `type2_counts.csv` gives each switch's RX and TX BGP UPDATE messages,
  Type-2 advertisements and withdrawals, and Type-2 NLRIs per UPDATE
  carrying Type-2 routes. BGP counts use decoded messages over TCP.
- `bgp_capture/` contains all 10 leaf-side captures. The post-run check
  reconciles per-device capture counts with FRR UPDATE counters and records
  reassembly gaps and quiet-window traffic.
- `truth.csv` records actual attach times and source/destination leaves.
  `belief_leaf5.log` and `moves.csv` capture whether leaf5 selected the correct
  VTEP after each move. `leaf5_final_fib.json` and
  `leaf5_final_correctness.csv` independently check that each final Linux /32
  resolves exclusively to the expected VTEP. Controller probe results are
  preserved separately in `probes.csv`.
- `exp2_summary.json` combines capture and forwarding results.
  `compare.py` checks that both modes used identical move sequences and writes
  `comparison.csv`, including the observed first-round attachment span.

## 10-second smoke result

One round at seed 1. Both modes ran the same 64 inter-leaf moves.

| Schedule | Attachment span | BGP UPDATE messages | Type-2 NLRIs | Type-2 NLRIs per UPDATE | Final leaf5 /32s correct |
|---|---:|---:|---:|---:|---:|
| Synchronized | 0.244 s | 810 | 5,140 | 6.35 | 64/64 |
| Staggered | 9.841 s | 3,898 | 5,220 | 1.34 | 64/64 |

Both runs captured all 10 links, had zero reassembly gaps and quiet-window
UPDATEs, and matched FRR's per-device UPDATE counters. All 64 moves in each run
were observed at the expected VTEP in the leaf5 route monitor. The smoke pair
checks workload delivery and instrumentation. The differences in message and
NLRI totals are single-round observations. [Smoke comparison CSV](smoke/comparison.csv)
