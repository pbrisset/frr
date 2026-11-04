# Reproduction guide

This package is a retrospective audit; no topology tests were run during consolidation. Reproduction needs a Linux host/VM, namespaces and privileged networking, an FRR build with Zebra/bgpd/vtysh, pytest and the repository Topotest dependencies; raw probes need `CAP_NET_RAW`, namespace/link operations `CAP_NET_ADMIN`, and packet analysis uses tshark/dumpcap. The recorded environment was the privileged `frr-dev` Colima/Docker container with the checkout at `/home/frr/frr`. Allocate unique output paths; run one topology at a time. Source and binary identity caveats in `GIT_PROVENANCE.md` apply. These tests modify ephemeral namespaces and can interact with other topology tests; do not run them concurrently.

From the repository root, the documented prior baseline and Phase 0 verification were:

```sh
docker exec frr-dev bash -lc 'cd /home/frr/frr/tests/topotests && ENABLE_LIVE_ROUTE_GRAPH=false sudo -E pytest -s bgp_evpn_capstone/test_evpn_capstone.py bgp_evpn_capstone/test_mobility_workload.py'
python3 -m unittest discover -s tests/topotests/bgp_evpn_capstone/phase0 -p 'test_*unit.py'
docker exec frr-dev bash -lc 'cd /home/frr/frr/tests/topotests && PHASE0_RUN=1 PHASE0_RESULTS=/tmp/new-phase0-01 ENABLE_LIVE_ROUTE_GRAPH=false sudo -E pytest -s bgp_evpn_capstone/test_phase0_measurement.py'
```

The documented Phase 1B one-run examples below require fresh unique paths; the original matrix used five fresh invocations for BBM, then each requested overlap 100, 250 and 500 ms, in that fixed order:

```sh
docker exec frr-dev bash -lc 'cd /home/frr/frr/tests/topotests && PHASE1B_RUN=1 PHASE1B_RESULTS=/tmp/new-phase1-bbm PHASE1B_MODE=break-before-make ENABLE_LIVE_ROUTE_GRAPH=false sudo -E pytest -s -x bgp_evpn_capstone/test_phase1_comparison.py'
docker exec frr-dev bash -lc 'cd /home/frr/frr/tests/topotests && PHASE1B_RUN=1 PHASE1B_RESULTS=/tmp/new-phase1-mbb250 PHASE1B_MODE=make-before-break PHASE1B_OVERLAP_MS=250 ENABLE_LIVE_ROUTE_GRAPH=false sudo -E pytest -s -x bgp_evpn_capstone/test_phase1_comparison.py'
```

The detailed original run commands, environment and analysis steps are in `../experiment-results/phase0-2026-11-02/report.md`, `../experiment-results/phase1-bbm-vs-mbb/README.md` and `report.md`, and the archived October report. The Week 5 exploratory and causal harnesses require temporary copies inside the Topotest tree and hard-code paths under `/home/frr/frr`; follow their respective `reproduction-commands.md` exactly, checking the temporary file does not already exist. The AP fleet study cannot be reproduced until its original harness, settings and artifacts are obtained. Do not substitute our fixed-eight-host harness for the missing AP workload.

Reconcile without new experiments:

```sh
python3 doc/capstone/research-consolidation/audit-evidence.py
```

The script rewrites `EXPERIMENT_MATRIX.csv` and `VALIDATION_CHECKS.csv`; run on a copy to preserve archive hashes. Its 105 raw-probe versus summary/aggregate comparisons and 20 Phase 1 route checks are described in the handoff. PCAP files can be read with `tshark -r <file> -Y 'icmp and icmp.ident == 42000 and icmp.seq == 44'`; use the exact run and frame references in causal `controller-packet-paths.csv`. Reanalysis does not require executing topology tests.

The archive preserves repository-relative paths. It includes relevant topology/configuration copies as evidence; it does not include the complete FRR source/build or installed FRR binaries. Acquire a matching source checkout and install a suitable build separately. No claim is made that the selected committed files prove historical byte-for-byte harness or binary identity.
