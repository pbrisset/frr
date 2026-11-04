# Reproduction commands

Use the existing privileged `frr-dev` Linux container and sequential runs; each `/tmp` output must be new. The topotest pytest configuration requires the test file to be inside `tests/topotests`; the archived test and stimulus below are the experiment source. The temporary file must not already exist. Remove only the temporary copy created by this procedure, even if a run fails. The helper's source path is hard-coded for this checkout's container mount. The first five recorded runs used `stimulus-initial.py`; current `stimulus.py` adds the gateway treatment and leaves the earlier branches unchanged. No installed dependencies or router settings are changed.

```sh
cd '/Users/gunabhiram/College/Capstone project/frr'
test ! -e tests/topotests/bgp_evpn_capstone/test_week5_ip_relearning_temporary.py
cp doc/capstone/experiment-results/week5-ip-relearning/test_ip_relearning.py tests/topotests/bgp_evpn_capstone/test_week5_ip_relearning_temporary.py
# For each case use a unique path and wait for teardown before the next run.
docker exec frr-dev bash -lc 'cd /home/frr/frr/tests/topotests && PHASE0_RUN=1 PHASE0_SEED=0 PHASE0_RESULTS=/tmp/new-ip-none-01 IPCASE=none ENABLE_LIVE_ROUTE_GRAPH=false sudo -E pytest -s -x bgp_evpn_capstone/test_week5_ip_relearning_temporary.py'
docker cp frr-dev:/tmp/new-ip-none-01 /tmp/new-ip-none-01
# After all runs, remove only the temporary copy made above.
python3 -c "from pathlib import Path; Path('tests/topotests/bgp_evpn_capstone/test_week5_ip_relearning_temporary.py').unlink()"
```

Use `IPCASE=immediate`, `repeated`, `delayed`, `telemetry`, or `gateway` and unique results paths to run the corresponding treatment. Gateway is a diagnostic intervention, not a production fix. Run multiple fresh invocations; a seed has no meaningful effect with one scheduled robot. The original first five cases used seed 0; none/repeated/delayed/gateway second batch used seed 1. No new runs should be concurrent. If a command fails, still remove the temporary copy; do not delete any existing user's file.

For an already copied raw result in this package, the following uses the repository's Phase 0 decoder and then regenerates summary tables; it rewrites derived files, so work on a copy when preserving this package's recorded hashes:

```sh
docker exec frr-dev python3 /home/frr/frr/tests/topotests/bgp_evpn_capstone/phase0/analyze.py /home/frr/frr/doc/capstone/experiment-results/week5-ip-relearning/raw-none-01
python3 doc/capstone/experiment-results/week5-ip-relearning/summarize.py
docker exec frr-dev python3 /home/frr/frr/doc/capstone/experiment-results/week5-ip-relearning/add_bgp_peers.py
```

For packet examples inspect PCAPs with `tshark -r <file> -Y 'icmp and icmp.ident == 42000 and icmp.seq == 60'` or the ARP display filter `arp.src.proto_ipv4 == 45.0.0.11`; see `causal-chain.md` for run and frame numbers. Capture interface `any` duplicates frames. To falsify the specific stale-externally-learned explanation, instrument the kernel neighbor notification and access-port ARP on the same calibrated clock in a fresh run, show a local valid neighbor installed and an event reaching Zebra before withdrawal, yet the MAC/IP route still fails to originate without DAD hold; that result would contradict H2 as the required cause. Complement it with matched delayed gateway exchange and no-announcement controls. Record sysctls and check capture/netlink loss.
