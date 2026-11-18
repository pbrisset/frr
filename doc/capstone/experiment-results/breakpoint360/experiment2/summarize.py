#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
"""Produce Type-2 packing counts and check leaf5's kernel forwarding state."""

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import analyze_mobility as mobility_analysis
import bgp_pcap

LEAVES = tuple(f"leaf{i}" for i in range(1, 6))
SPINES = ("spine1", "spine2")


def write_csv(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def count_type2(point, meta):
    expected = {f"{leaf}_eth-{spine}.pcap" for leaf in LEAVES for spine in SPINES}
    files = {p.name: p for p in (point / "bgp_capture").glob("*.pcap")}
    assert files.keys() == expected, f"Unexpected capture coverage: {files.keys() ^ expected}"
    counts = defaultdict(Counter)
    audit = defaultdict(Counter)
    gaps = 0
    quiet_updates = 0
    window = meta["t"]
    for name, path in sorted(files.items()):
        leaf, spine = path.stem.split("_eth-")
        messages, stats = bgp_pcap.read_messages(path)
        gaps += stats["gaps"]
        for timestamp, outgoing, raw in messages:
            if not window["quiet_start"] <= timestamp < window["end"]:
                continue
            msg = bgp_pcap.parse_message(raw)
            if msg["type"] != "UPDATE":
                continue
            assert "error" not in msg, (name, msg)
            sender, receiver = (leaf, spine) if outgoing else (spine, leaf)
            ends = ((sender, "tx"), (receiver, "rx"))
            for end in ends:
                audit[end]["msgs"] += 1
            if timestamp < window["moves_start"]:
                quiet_updates += 1
                continue
            routes = {
                action: sum(route.get("family") == "evpn" and route.get("rt") == 2
                            for route in msg[action])
                for action in ("adv", "wd")
            }
            for end in ends:
                stats = counts[end]
                stats["bgp_updates"] += 1
                stats["type2_updates"] += bool(sum(routes.values()))
                stats["type2_adv"] += routes["adv"]
                stats["type2_wd"] += routes["wd"]

    rows, mismatches = [], []
    for node in (*LEAVES, *SPINES):
        for direction in ("rx", "tx"):
            stats = counts[node, direction]
            key = f"upd_{direction}"
            cli = meta["updates_after"][node][key] - meta["updates_before"][node][key]
            captured = audit[node, direction]["msgs"]
            if cli != captured:
                mismatches.append(dict(node=node, direction=direction, frr=cli,
                                       captured=captured))
            type2 = stats["type2_adv"] + stats["type2_wd"]
            rows.append(dict(
                switch=node, direction=direction, **{
                    key: stats[key] for key in ("bgp_updates", "type2_updates", "type2_adv", "type2_wd")
                },
                type2_nlris=type2,
                type2_nlris_per_type2_update=(
                    type2 / stats["type2_updates"] if stats["type2_updates"] else None
                ),
            ))
    write_csv(point / "type2_counts.csv", rows)
    tx = [row for row in rows if row["direction"] == "tx"]
    nlris = sum(row["type2_nlris"] for row in tx)
    updates = sum(row["type2_updates"] for row in tx)
    return dict(
        capture_links=len(files), capture_gaps=gaps, quiet_bgp_updates=quiet_updates,
        counter_mismatches=mismatches, bgp_updates=sum(row["bgp_updates"] for row in tx),
        type2_bgp_updates=updates, type2_nlris=nlris,
        type2_nlris_per_type2_update=nlris / updates if updates else None,
    )


def check_final_fib(point):
    snapshot = json.loads((point / "leaf5_final_fib.json").read_text())
    routes = {route["dst"].removesuffix("/32"): route for route in snapshot["routes"]
              if "/" not in route["dst"] or route["dst"].endswith("/32")}
    objects = {entry["id"]: entry for entry in snapshot["nexthops"]}

    def gateways(entry):
        if "gateway" in entry:
            return {entry["gateway"]}
        if "nhid" in entry:
            return gateways(objects.get(entry["nhid"], {}))
        if "group" in entry:
            return set().union(*(gateways(objects.get(member["id"], {}))
                                 for member in entry["group"]))
        return set().union(*(gateways(member) for member in entry.get("nexthops", [])))

    with (point / "truth.csv").open(newline="") as stream:
        final = {}
        for row in csv.DictReader(stream):
            final[int(row["robot"])] = row["dst"]
    ips = (point / "targets.txt").read_text().splitlines()
    rows = []
    for i, ip in enumerate(ips):
        want = mobility_analysis.VTEP[final[i]]
        actual = gateways(routes.get(ip, {}))
        rows.append(dict(robot=i, ip=ip, expected_vtep=want,
                         actual_vteps=" ".join(sorted(actual)), correct=actual == {want}))
    write_csv(point / "leaf5_final_correctness.csv", rows)
    return sum(row["correct"] for row in rows), len(rows)


def main(point):
    meta = json.loads((point / "meta.json").read_text())
    summary, moves = mobility_analysis.summarize_point(str(point))
    config = meta["config"]
    rounds = int(config["duration_s"] / config["dwell_s"])
    counts = Counter(row["robot"] for row in moves)
    assert set(counts) == set(range(config["robots"])) and set(counts.values()) == {rounds}
    assert all(row["in_measure"] and row["src"] != row["dst"] for row in moves)
    correct, total = check_final_fib(point)
    wire = count_type2(point, meta)
    result = dict(
        sync=config["sync"], dwell_s=config["dwell_s"], seed=config["seed"],
        attached_moves=len(moves), final_leaf5_correct=correct, final_leaf5_checked=total,
        moves_seen_at_correct_vtep=sum(row["belief_ms"] is not None for row in moves),
        probe_loss_pct=summary["probe_loss_pct"], **wire,
    )
    (point / "exp2_summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    assert not wire["capture_gaps"] and not wire["counter_mismatches"], result
    assert wire["type2_nlris"] > 0, result


if __name__ == "__main__":
    main(Path(sys.argv[1]))
