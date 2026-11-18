#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
set -eu
rel=doc/capstone/experiment-results/breakpoint360/experiment2

container_trial() (
    set -eu
    unset MOB_ADV_INTERVAL MOB_DIAG
    export MOB_OUT="$1" MOB_DWELL="$2" MOB_SYNC="$3" MOB_DURATION="$(($2 * $4))" MOB_SEED="$5"
    export MOB_ROBOTS=64 MOB_ROBOT=repeat MOB_GAP_MS=0 MOB_DAD=0 MOB_COUNT=1
    export MOB_SUBNET=narrow MOB_PROBE_HZ=10 ENABLE_LIVE_ROUTE_GRAPH=false
    export PYTHONDONTWRITEBYTECODE=1
    local temp status=0
    temp=$(mktemp -d)
    trap 'rm -r -- "$temp"' EXIT
    tar -xzf ../../team-handoff/guna/mobility_tools.tar.gz -C "$temp" \
        mobility_scale.py mobility_prober.py route_counter.py bgp_pcap.py \
        test_mobility_scale.py analyze_mobility.py
    ln -s "$PWD/bgp_evpn_capstone/test_evpn_capstone.py" "$temp/"
    export PYTHONPATH="$temp:$EXP2_ROOT:$PWD"
    cat /sys/fs/cgroup/memory.events
    pytest -c pytest.ini -p conftest -p exp2_schedule -qx --tb=short "$temp/test_mobility_scale.py" \
        -k "test_fabric_baseline or test_mobility_experiment" || status=$?
    cat /sys/fs/cgroup/memory.events /proc/pressure/memory
    if [[ "$status" != 0 ]]; then exit "$status"; fi
    python3 "$EXP2_ROOT/summarize.py" "$MOB_OUT"
)

container_matrix() {
    local out dwell sync seed mode failures=0
    exec > "$EXP2_ROOT/trials/matrix.log" 2>&1
    trap 'printf "%s\n" "$?" > "$EXP2_ROOT/trials/matrix.exit"' EXIT
    for dwell in 10 30; do
        for seed in 1 2 3; do
            for sync in 1 0; do
                mode=staggered
                if [[ "$sync" == 1 ]]; then mode=synchronized; fi
                out="$EXP2_ROOT/trials/dwell-${dwell}s/$mode-seed-$seed"
                mkdir -p "$(dirname "$out")"
                mkdir "$out"
                echo "Starting $mode, dwell ${dwell}s, seed $seed"
                if bash "$EXP2_ROOT/reproduce.sh" _trial "$out" "$dwell" "$sync" 6 "$seed" \
                        > "$out/pytest.log" 2>&1; then
                    echo "Validated $out"
                else
                    echo "Failed $out"
                    failures=$((failures + 1))
                fi
            done
        done
    done
    (( failures == 0 ))
}

case "${1:-}" in
    _trial) shift; container_trial "$@"; exit ;;
    _matrix) container_matrix; exit ;;
esac

root=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
mount=${FRR_MOUNT:-/home/frr/frr}
container=${FRR_CONTAINER:-frr-dev}
seed=${MOB_SEED:-1}
[[ "$seed" =~ ^[0-9]+$ ]] || exit 2

run() {
    local group=$1 dwell=$2 sync=$3 rounds=$4 mode=staggered out
    if [[ "$sync" == 1 ]]; then mode=synchronized; fi
    out="$rel/$group/dwell-${dwell}s/$mode-seed-$seed"
    mkdir -p "$(dirname "$root/$out")"
    mkdir "$root/$out"
    echo "Starting $mode, dwell ${dwell}s, rounds $rounds, seed $seed"
    docker exec -u root -w "$mount/tests/topotests" \
        -e "EXP2_ROOT=$mount/$rel" "$container" \
        bash "$mount/$rel/reproduce.sh" _trial "$mount/$out" "$dwell" "$sync" "$rounds" "$seed" \
        > "$root/$out/pytest.log" 2>&1
    echo "Validated $out"
}

case "${1:-}" in
    smoke)
        run smoke 10 1 1
        run smoke 10 0 1
        ;;
    matrix)
        for dwell in 10 30; do
            for sync in 1 0; do run trials "$dwell" "$sync" 6; done
        done
        ;;
    matrix-all)
        mkdir "$root/$rel/trials"
        docker exec -d -u root -w "$mount/tests/topotests" \
            -e "EXP2_ROOT=$mount/$rel" "$container" bash "$mount/$rel/reproduce.sh" _matrix
        echo "Colima matrix started. Check $rel/trials/matrix.log and matrix.exit."
        ;;
    *) echo "Usage: $0 {smoke|matrix|matrix-all}" >&2; exit 2 ;;
esac
