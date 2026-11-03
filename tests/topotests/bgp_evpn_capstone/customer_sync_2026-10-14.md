# Cisco customer sync — EVPN mobility

**Wednesday, October 14, 2026**

## Test status

| Validation stage | Result | Runtime | Evidence |
| --- | ---: | ---: | --- |
| Stage 1 fabric baseline | 8/8 passed | 52.27 s | Team-provided baseline result |
| Single and multi-move baseline | 11/11 passed | 61.88 s | Team-provided mobility result |
| Sequential and concurrent workload suites | 2/2 passed | 40.90 s | Previous workload run |
| Instrumented workload rerun | 2/2 passed | 40.70 s | Container run on October 13 |

The instrumented run used:

```bash
cd /home/frr/frr/tests/topotests
sudo -E pytest -s bgp_evpn_capstone/test_mobility_workload.py
```

## Mobility telemetry

The [CSV performance output](mobility_telemetry.csv) from the final run contains five sequential host11 moves and two fleet moves. All seven moves succeeded, with no DAD warnings.

| Workload | Host | Move | MAC sequence | Convergence |
| --- | --- | --- | ---: | ---: |
| Concurrent fleet | host11 | leaf1 → leaf2 | 6 | 3,509.11 ms |
| Concurrent fleet | host21 | leaf2 → leaf3 | 1 | 4,057.56 ms |

The five sequential moves converged in 2,369.97–2,442.68 ms. Fleet timings are measured separately from the start of each host's interface move through fabric convergence. Both fleet interfaces move before traffic generation and convergence checks; the interface operations themselves are sequential within the same test iteration.

The fleet test checks local MAC ownership, sequence increment, Type-2 route convergence, and stale-route withdrawal across the fabric. It restores both hosts to their original leaves after measurement.
