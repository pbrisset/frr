# Cisco FRR EVPN capstone audit — October 20, 2026

Prepared for the Wednesday, October 21 sponsor meeting. Tests ran inside the `frr-dev` Linux container from `/home/frr/frr/tests/topotests`, one suite at a time. Times in the router logs and telemetry are UTC.

## Executive summary

The visualizer is reachable and renders both HTML pages. All 13 Topotests passed. `host11` advanced its MAC mobility sequence by one on every measured move, and all seven measured moves converged. **The workload CSV reports no DAD warning, but leaf1 Zebra logged duplicate MAC and MAC/IP detections when the fleet test returned `host11` to leaf1 after recording the move results.** The pass count and CSV alone therefore understate the DAD finding.

## Environment and visualizer

| Check | Result |
| --- | --- |
| Python 3 imports | `flask`, `flask_socketio`, and `requests` available in the container |
| Template paths | `visualizer_server.py` renders `templates/index.html` at `/` and `templates/route_chart.html` at `/route-chart`; both files exist |
| `GET /health` | HTTP 200, JSON `{"status":"ok"}`, 16 bytes |
| `GET /route-chart` | HTTP 200, HTML, 9,341 bytes; nonblank document |
| `GET /` | HTTP 200, HTML, 22,388 bytes; nonblank document |

These checks establish HTTP availability and template rendering; they do not measure browser-side Socket.IO updates or chart behavior.

### Host networking follow-up

On October 20, the launcher was updated to accept `--port` (default 5000) while retaining its `0.0.0.0` bind. A separate `--port 5001` instance returned HTTP 200 from `/health`. The active server was restarted as `python3 visualizer_server.py`; its output is in [visualizer_server.log](visualizer_server.log).

The container and Colima VM listened on port 5000, but macOS initially had no port 5000 listener despite Docker reporting `0.0.0.0:5000->5000/tcp`. A local SSH tunnel from macOS `127.0.0.1:5000` to the Colima VM's `127.0.0.1:5000` restored host access. Host curl then returned `{"status":"ok"}` from `/health` and HTTP 200 with 9,341 bytes from `/route-chart`. The [Chrome screenshot](visualizer_proof.png) confirms that the route chart rendered through the host URL. It shows zero samples because no Topotest was producing route events during capture. The tunnel is a running host process and must be recreated if Colima or the Mac session stops it.

## Test execution

| Order | Command | Assertions | Runtime | Complete stdout/stderr |
| --- | --- | ---: | ---: | --- |
| 1 | `sudo -E pytest -s bgp_evpn_capstone/test_evpn_capstone.py` | 11 passed, 0 failed | 59.40 s | [fabric suite](capstone_audit_evpn_2026-10-20.log) |
| 2 | `sudo -E pytest -s bgp_evpn_capstone/test_mobility_workload.py` | 2 passed, 0 failed | 38.53 s | [workload suite](capstone_audit_workload_2026-10-20.log) |

The workload run exported `MOBILITY_CSV_PATH=/tmp/capstone_audit_mobility_2026-10-20.csv` to preserve earlier telemetry. The fresh [per-move CSV](capstone_audit_mobility_2026-10-20.csv) and [complete Topotest/router log archive](capstone_audit_router_logs_2026-10-20.tar.gz) are retained here. The archive contains both suites' `exec.log` traces, including `vtysh` command output, and per-router FRR logs. No preexisting mobility script or telemetry file was replaced.

## Mobility and convergence

| Move | Host | Path | MAC sequence | Route visibility / convergence |
| ---: | --- | --- | ---: | ---: |
| 1 | host11 | leaf1 → leaf2 | 1 | 2,383.72 ms |
| 2 | host11 | leaf2 → leaf1 | 2 | 2,341.28 ms |
| 3 | host11 | leaf1 → leaf3 | 3 | 2,309.14 ms |
| 4 | host11 | leaf3 → leaf4 | 4 | 2,339.35 ms |
| 5 | host11 | leaf4 → leaf1 | 5 | 2,328.33 ms |
| 6, fleet | host11 | leaf1 → leaf2 | 6 | 3,400.29 ms |
| 7, fleet | host21 | leaf2 → leaf3 | 1 | 3,851.72 ms |

The workload asserts the target leaf's local MAC ownership and exact `previous_seq + 1`, then checks Type-2 best-route ownership, stale-route withdrawal, and VTEP reachability across the fabric. Thus the measured `host11` sequence progressed from baseline 0 through 6 without a missed increment. The fleet test restores both hosts afterward, but does not record or assert the return move's MAC sequence or post-restore route state.

The five sequential moves ranged from **2.309 to 2.384 s**, mean **2.340 s**. The October 14 [benchmark CSV](../mobility_telemetry.csv) ranged from **2.370 to 2.443 s**, mean **2.396 s**. This run's mean was **56 ms (2.3%) lower**; a single run is insufficient to establish a sustained improvement. Fleet moves took 3.400 s and 3.852 s, versus 3.509 s and 4.058 s in that benchmark. Timings start at the interface move and include traffic generation and polling until fabric convergence; they are not isolated BGP propagation latency. The two fleet interfaces are moved sequentially before traffic is generated.

## DAD and route-freeze finding

All seven CSV rows say `SUCCESS` and `dad_warning=False`; `vtysh` MAC JSON sampled during the measured moves showed `isDuplicate:false`. At **08:39:11 UTC**, after the fleet CSV rows at 08:39:08, leaf1 `zebra.log` reported `host11` MAC `00:00:00:00:00:11` **“detected as duplicate during local update”** with last VTEP `10.0.0.2`. At **08:39:12 UTC**, it also reported the MAC/IP `45.0.0.11` as duplicate, inherited from the MAC. These events followed the test's `finally` block returning `host11` from leaf2 to leaf1. See `bgp_evpn_capstone.test_mobility_workload/leaf1/zebra.log` in the log archive, lines 153 and 157.

The workload's DAD probe runs before that `finally` restoration; its `False` values do not cover these warnings. A search of the leaf FRR logs found no explicit route-freeze or suppression message. The test did not query `vtysh` MAC flags, Type-2 advertisements, or traffic after the return, so the duration and forwarding impact of any DAD hold are **unverified**. The suite passing does not resolve that gap.

## Questions for Shelly and the Cisco team

1. For repeated legitimate robot moves, should a return from leaf2 to leaf1 immediately after the fleet test trigger Zebra's duplicate MAC/IP detection? Which DAD threshold, timer, or mobility policy should we use, and what post-return `vtysh` state would confirm that route advertisement and forwarding have recovered?
2. What convergence target should we use for the sponsor acceptance test: approximately 2.34 s for isolated moves and 3.40–3.85 s for this two-robot fleet run, measured from interface move through Type-2 visibility and stale-route withdrawal? Should we also measure a separate traffic interruption window?
