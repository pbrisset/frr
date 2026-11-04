# Reproduction commands

Use the privileged Linux `frr-dev` container, no parallel topology runs, and a unique output path for each trial. The test must be placed under `tests/topotests` so pytest loads its topotest configuration. Check the destination does not exist; never overwrite an existing user's file. The host checkout is mounted in the container at `/home/frr/frr`.

```sh
cd '/Users/gunabhiram/College/Capstone project/frr'
test ! -e tests/topotests/bgp_evpn_capstone/test_week5_causal_temporary.py || exit 1
cp doc/capstone/experiment-results/week5-ip-relearning-causal/test_causal.py tests/topotests/bgp_evpn_capstone/test_week5_causal_temporary.py
docker exec frr-dev bash -lc 'cd /home/frr/frr/tests/topotests && PHASE0_RUN=1 PHASE0_SEED=1 PHASE0_RESULTS=/tmp/new-causal-none-01 CAUSAL_CASE=none ENABLE_LIVE_ROUTE_GRAPH=false sudo -E pytest -s -x bgp_evpn_capstone/test_week5_causal_temporary.py'
docker cp frr-dev:/tmp/new-causal-none-01 /tmp/new-causal-none-01
# After teardown and all trials, remove only the temporary copy created above.
python3 -c "from pathlib import Path; Path('tests/topotests/bgp_evpn_capstone/test_week5_causal_temporary.py').unlink()"
```

Repeat with `CAUSAL_CASE=immediate`, `delayed`, and `repeated`, and unique `PHASE0_RESULTS` paths. Offsets are measured from worker startup; actual send brackets are in `host11-stimulus.log`. The recorded matrix ran iteration 1–5, condition order none/immediate/delayed/repeated, except immediate-01 was the standalone pilot and seed 0. The other runs used their iteration number as seed. Seed does not randomize a single robot. Preserve pytest stdout and captures. Do not flush neighbors or modify DAD.

The separate direct-port validation used `test_access_validation.py`, copied as `test_week5_access_temporary.py`, `CAUSAL_CASE=immediate`, and `neighbor_monitor_checked.py`; its 200 ms wait makes it a distinct instrumentation run. It captured per-socket `/proc/net/netlink` counters at listener startup and termination. The matched runs use `test_causal.py` and `neighbor_monitor.py`.

Raw captures can be reanalyzed on a **copy** so archived hashes remain valid:

```sh
docker exec frr-dev sudo python3 /home/frr/frr/tests/topotests/bgp_evpn_capstone/phase0/analyze.py /path/to/copied/raw-run
docker exec frr-dev sudo python3 /home/frr/frr/doc/capstone/experiment-results/week5-ip-relearning-causal/summarize.py
```

`summarize.py` rewrites CSVs in its own directory; run a copied package when preserving this package. Example packet inspection: `tshark -r <pcap> -Y 'icmp and icmp.ident == 42000 and icmp.seq == 44'` and filter ARP on sender/target `45.0.0.11`. `controller-packet-paths.csv` gives exact run and frame references. Monitor logs, capture counters, raw PCAP and original events are retained; no cross-clock interval is claimed without calibration.

The repository ignores `*.pcapng` and `*.log`; a normal `git add` omits those essential raw artifacts. After reviewing the package and existing staged work, a user who intends to include all evidence can use `git add -f doc/capstone/experiment-results/week5-ip-relearning-causal/`, then inspect `git diff --cached --stat` and `git diff --cached --check`. This audit did not stage or commit anything.
