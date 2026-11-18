#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
set -eu

root=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
container=${FRR_CONTAINER:-frr-dev}
mount=${FRR_MOUNT:-/home/frr/frr}
rel=doc/capstone/experiment-results/breakpoint360/experiment1
archive=team-handoff/guna/mobility_tools.tar.gz

run() {
    local size=$1 seed=$2 out="$rel/trials/$1/seed-$2"
    if [[ -e "$root/$out" || -e "$root/$out.log" ]]; then
        echo "Existing trial evidence at $out" >&2
        return 1
    fi
    mkdir -p "$root/$(dirname "$out")"
    echo "Starting $size seed $seed"
    docker exec -u root -w "$mount/tests/topotests" \
        -e "EVPN_EXP1_OUT=$mount/$out" -e "EVPN_EXP1_SEED=$seed" \
        -e "EVPN_TOOLS_ARCHIVE=$mount/$archive" \
        "$container" bash -lc '
            set -eu
            temp=$(mktemp -d)
            trap "rm -r -- \"$temp\"" EXIT
            tar -xzf "$EVPN_TOOLS_ARCHIVE" -C "$temp" route_counter.py bgp_pcap.py
            export PYTHONPATH="$temp:$PWD"
            export ENABLE_LIVE_ROUTE_GRAPH=false
            cat /sys/fs/cgroup/memory.events
            status=0
            pytest -q --tb=short bgp_evpn_capstone/test_evpn_fabric_size.py \
                -k "test_route_amplification and $(basename "$(dirname "$EVPN_EXP1_OUT")")" || status=$?
            cat /sys/fs/cgroup/memory.events /proc/pressure/memory
            exit "$status"
        ' > "$root/$out.log" 2>&1 || return $?
    echo "Validated $size seed $seed"
}

case "${1:-}" in
    calibration) run 5x2 1 ;;
    matrix)
        python3 -c 'import json,sys; assert json.load(open(sys.argv[1]))["status"] == "valid"' \
            "$root/$rel/trials/5x2/seed-1/meta.json"
        failures=0
        for size in 5x2 9x2 17x2 5x4 9x4 17x4; do
            for seed in 1 2 3; do
                out="$root/$rel/trials/$size/seed-$seed"
                if [[ -f "$out/counts.csv" && -f "$out/timeline.csv" ]] &&
                    python3 -c 'import json,sys; sys.exit(json.load(open(sys.argv[1]))["status"] != "valid")' "$out/meta.json" &&
                    grep -q '1 passed,' "$out.log"; then
                    echo "Preserving validated $size seed $seed"
                    continue
                fi
                run "$size" "$seed" || failures=$((failures + 1))
            done
        done
        (( failures == 0 ))
        ;;
    trial)
        case "${2:-}" in 5x2|9x2|17x2|5x4|9x4|17x4) ;; *) exit 2 ;; esac
        [[ "${3:-}" =~ ^[0-9]+$ ]] || exit 2
        run "$2" "$3"
        ;;
    *) echo "Usage: bash $0 {calibration|matrix|trial SIZE SEED}" >&2; exit 2 ;;
esac
