#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
"""Aggregate Experiment 1 raw captures and render measured-data plots."""

import argparse
import csv
import json
import sys
import tarfile
from bisect import bisect_right
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, stdev
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent
SUMMARY = (
    "leaf_count spine_count seed completed_moves mean_leaf_rx_per_move "
    "mean_spine_rx_per_move fabric_routes_per_move bgp_msgs_per_move capture_gaps status "
    "all_bgp_updates_per_move evpn_nlris_per_update route_advertisements "
    "route_withdrawals robot_nlris other_evpn_nlris robot_mac_only robot_mac_ip "
    "originated echo delivery readvertised updates_after_move_stable cooldown_bgp_updates"
).split()
ROLES = ("source", "destination", "uninvolved", "controller", "spine")
PATHS = ("originated", "echo", "delivery", "readvertised")


def write_csv(path, fields, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def classify(sender, receiver, origin, spines):
    if origin is None:
        return "other"
    if sender in spines:
        return "echo" if receiver == origin else "delivery"
    return "originated" if sender == origin else "readvertised"


def analyze_trial(trial, meta, decoder):
    truth = read_csv(trial / "truth.csv")
    moves = [row for row in truth if row["status"] == "completed"]
    starts = [float(row["t_start"]) for row in moves]
    num_moves = len(moves)
    assert num_moves == meta["completed_moves"]
    assert len(truth) == meta["planned_moves"]
    assert [moves[0]["src"], *(row["dst"] for row in moves)] == meta["planned_sequence"]
    prior_end = meta["t"]["moves_start"]
    for row in moves:
        times = [float(row[field]) for field in
                 ("t_start", "t_detached", "t_attached", "t_stable", "t_end")]
        assert prior_end <= times[0] and times == sorted(times)
        assert row["src"] != row["dst"]
        prior_end = times[-1]
    assert prior_end <= meta["t"]["moves_end"] <= meta["t"]["end"]

    spines = set(meta["spines"])
    leaves = set(meta["vtep"]) - spines
    vtep_to_leaf = {vtep: leaf for leaf, vtep in meta["vtep"].items() if leaf in leaves}
    sessions = defaultdict(Counter)
    devices = defaultdict(Counter)
    totals = Counter()
    for capture in sorted((trial / "bgp_capture").glob("*.pcap")):
        leaf, interface = capture.stem.split("_", 1)
        spine = interface.removeprefix("eth-")
        messages, stats = decoder.read_messages(capture)
        assert stats["gaps"] == 0, capture
        for timestamp, outgoing, raw in messages:
            if not meta["t"]["moves_start"] <= timestamp < meta["t"]["end"]:
                continue
            message = decoder.parse_message(raw)
            if message["type"] != "UPDATE":
                continue
            assert "error" not in message, (capture, message)
            index = bisect_right(starts, timestamp) - 1
            assert index >= 0, (capture, timestamp)
            sender, receiver = (leaf, spine) if outgoing else (spine, leaf)
            session = sessions[index, sender, receiver]
            session["all_updates"] += 1
            totals["updates_after_move_stable"] += timestamp >= float(moves[index]["t_stable"])
            totals["cooldown_bgp_updates"] += timestamp >= meta["t"]["moves_end"]
            for action, key in (("adv", "routes_adv"), ("wd", "routes_wd")):
                for route in message[action]:
                    if route["family"] != "evpn":
                        continue
                    assert "error" not in route, (capture, route)
                    session[key] += 1
                    totals[key] += 1
                    if route.get("rt") != 2 or route.get("mac") != meta["robot_mac"]:
                        session["other_evpn_nlris"] += 1
                        totals["other_evpn_nlris"] += 1
                        continue
                    origin = vtep_to_leaf.get(route["rd"].rsplit(":", 1)[0])
                    propagation = classify(sender, receiver, origin, spines)
                    session[propagation] += 1
                    totals[propagation] += 1
                    totals["robot_nlris"] += 1
                    totals["robot_mac_ip" if route.get("ip") else "robot_mac_only"] += 1
                    if propagation == "originated":
                        session[f"originated_{action}"] += 1
            if any(route["family"] == "evpn" for action in ("adv", "wd")
                   for route in message[action]):
                session["evpn_updates"] += 1

    identity = {key: meta[key] for key in ("leaf_count", "spine_count", "seed")}
    session_fields = (
        "routes_adv", "routes_wd", "evpn_updates", "all_updates",
        "other_evpn_nlris", *PATHS, "other", "originated_adv", "originated_wd",
    )
    session_rows = []
    for (index, sender, receiver), count in sorted(sessions.items()):
        for switch, direction in ((sender, "tx"), (receiver, "rx")):
            devices[index, switch, direction].update(count)
        session_rows.append(dict(
            identity, move=index + 1, sender=sender, receiver=receiver,
            **{field: count[field] for field in session_fields},
        ))
    device_rows = []
    for index, move in enumerate(moves):
        for switch in sorted(meta["vtep"]):
            if switch in spines:
                role = "spine"
            elif switch == meta["controller_leaf"]:
                role = "controller"
            elif switch == move["src"]:
                role = "source"
            elif switch == move["dst"]:
                role = "destination"
            else:
                role = "uninvolved"
            for direction in ("rx", "tx"):
                count = devices[index, switch, direction]
                device_rows.append(dict(
                    identity, move=index + 1, switch=switch, role_in_move=role,
                    direction=direction, routes=count["routes_adv"] + count["routes_wd"],
                    evpn_updates=count["evpn_updates"], all_updates=count["all_updates"],
                ))

    # Compare every switch and direction with the original shared counter.
    per_switch = Counter()
    for (_, switch, direction), count in devices.items():
        for field in ("routes_adv", "routes_wd", "evpn_updates"):
            per_switch[switch, direction, field] += count[field]
    for row in read_csv(trial / "counts.csv"):
        for field, original in (("routes_adv", "routes_adv"), ("routes_wd", "routes_wd"),
                                ("evpn_updates", "msgs")):
            assert per_switch[row["switch"], row["dir"], field] == int(row[original]), row

    evpn_updates = sum(row["evpn_updates"] for row in session_rows)
    all_updates = sum(row["all_updates"] for row in session_rows)
    routes = totals["routes_adv"] + totals["routes_wd"]
    rx = [row for row in device_rows if row["direction"] == "rx"]
    result = dict(
        identity, completed_moves=num_moves, status=meta["status"],
        capture_gaps=meta["capture_gaps"],
        mean_leaf_rx_per_move=mean(r["routes"] for r in rx if r["role_in_move"] != "spine"),
        mean_spine_rx_per_move=mean(r["routes"] for r in rx if r["role_in_move"] == "spine"),
        fabric_routes_per_move=routes / num_moves,
        bgp_msgs_per_move=evpn_updates / num_moves,
        all_bgp_updates_per_move=all_updates / num_moves,
        evpn_nlris_per_update=routes / evpn_updates if evpn_updates else None,
        route_advertisements=totals["routes_adv"], route_withdrawals=totals["routes_wd"],
        **{key: totals[key] for key in (
            "robot_nlris", "other_evpn_nlris", "robot_mac_only", "robot_mac_ip", *PATHS,
            "updates_after_move_stable", "cooldown_bgp_updates",
        )},
    )
    return result, device_rows, session_rows


def plot(results):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "font.size": 11, "axes.spines.top": False, "axes.spines.right": False,
        "axes.labelcolor": "#1b263b", "axes.titleweight": "bold",
        "figure.facecolor": "white", "axes.axisbelow": True,
    })
    grouped = defaultdict(list)
    for row in results:
        grouped[row["leaf_count"], row["spine_count"]].append(row)
    if not grouped:
        return
    colors = {2: "#147d82", 4: "#8e4c82"}
    leaf_colors = {5: "#147d82", 9: "#b55232", 17: "#615397"}
    graph_dir = ROOT / "graphs"
    graph_dir.mkdir(exist_ok=True)

    def create(title, xlabel, ylabel):
        fig, ax = plt.subplots(figsize=(8.9, 5.2), layout="constrained")
        ax.set(title=title, xlabel=xlabel, ylabel=ylabel)
        ax.grid(axis="both", alpha=0.17)
        return fig, ax

    def save(fig, filename):
        fig.savefig(graph_dir / f"{filename}.png", dpi=180)
        plt.close(fig)

    fig, ax = create("Leaf receive load by spine count", "Spines",
                     "EVPN NLRIs received per leaf per completed move")
    for i, leaves in enumerate(sorted({key[0] for key in grouped})):
        for (l, s), trials in sorted(grouped.items()):
            if l != leaves:
                continue
            values = [r["mean_leaf_rx_per_move"] for r in trials]
            ax.scatter([s + (i - 1) * 0.19] * len(values), values, s=55,
                       marker=("o", "s", "D")[i], color=leaf_colors[l],
                       label=f"{l}L/{s}S, n={len(values)}")
    ax.set_xticks(sorted({key[1] for key in grouped}))
    ax.legend(fontsize=8, ncol=2)
    save(fig, "01_leaf_rx_vs_spines")

    fig, ax = create("Spine receive load by leaf count", "Leaves",
                     "EVPN NLRIs received per spine per completed move")
    for (l, s), trials in sorted(grouped.items()):
        values = [r["mean_spine_rx_per_move"] for r in trials]
        ax.scatter([l + (-0.25 if s == 2 else 0.25)] * len(values), values,
                   s=55, color=colors[s], label=f"{l}L/{s}S, n={len(values)}")
    ax.set_xticks(sorted({key[0] for key in grouped}))
    ax.legend(fontsize=8, ncol=2)
    save(fig, "02_spine_rx_vs_leaves")

    labels = [f"{l}L/{s}S\nn={len(grouped[l, s])}" for l, s in grouped]
    positions = range(len(labels))
    fig, ax = create("Measured versus predicted receive load", "Fabric topology and trial count",
                     "EVPN NLRIs received per switch per completed move")
    for key, offset, color, title, dimension, formula in (
        ("mean_leaf_rx_per_move", -0.20, "#147d82", "Leaf", 1, "4S"),
        ("mean_spine_rx_per_move", 0.20, "#8e4c82", "Spine", 0, "4L"),
    ):
        xs = [x + offset for x in positions]
        ax.bar(xs, [mean(r[key] for r in rows) for rows in grouped.values()],
               width=0.32, color=color, label=f"{title} measured")
        ax.scatter(xs, [4 * shape[dimension] for shape in grouped],
                   marker="_", s=140, color="#14213d", zorder=4,
                   label=f"{title} prediction ({formula})")
    ax.set_xticks(list(positions), labels)
    ax.legend(fontsize=8, ncol=2)
    save(fig, "03_measured_vs_predicted")

    fig, ax = create("BGP UPDATEs and their EVPN payload",
                     "EVPN-carrying BGP UPDATE messages per completed move",
                     "EVPN NLRI transmissions per completed move")
    for (l, s), trials in sorted(grouped.items()):
        points = [(r["bgp_msgs_per_move"], r["fabric_routes_per_move"]) for r in trials]
        ax.scatter(*zip(*points), s=55, color=colors[s], label=f"{l}L/{s}S, n={len(points)}")
    ax.legend(fontsize=8, ncol=2)
    save(fig, "04_updates_vs_nlris")

    fig, ax = create("Fabric-wide EVPN transmission work", "Fabric topology and trial count",
                     "EVPN NLRI transmissions per completed move")
    ax.bar(list(positions), [mean(r["fabric_routes_per_move"] for r in rows)
                            for rows in grouped.values()], color="#147d82", width=0.62)
    ax.set_xticks(list(positions), labels)
    save(fig, "05_fabric_transmissions")

    fig, ax = create("Variation across repeated fabric runs", "Fabric topology and trial count",
                     "EVPN NLRI transmissions per completed move")
    for x, rows in enumerate(grouped.values()):
        values = [r["fabric_routes_per_move"] for r in rows]
        center = mean(values)
        ax.errorbar(x, center, yerr=[[center - min(values)], [max(values) - center]],
                    fmt="none", capsize=6, color="#1b263b")
        ax.scatter([x + (i - (len(values) - 1) / 2) * 0.07 for i in range(len(values))],
                   values, color="#b55232", s=37, zorder=3)
    ax.set_xticks(list(positions), labels)
    save(fig, "06_trial_variation")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    results, devices, sessions = [], [], []
    with TemporaryDirectory() as temp:
        script = Path(temp) / "bgp_pcap.py"
        with tarfile.open(args.archive) as archive:
            with archive.extractfile("bgp_pcap.py") as member:
                script.write_bytes(member.read())
        sys.path.insert(0, temp)
        import bgp_pcap

        trials = ROOT / "trials"
        for log in sorted(trials.glob("*/*.log")):
            trial = log.with_suffix("")
            leaves, spines = map(int, trial.parent.name.split("x"))
            identity = dict(leaf_count=leaves, spine_count=spines,
                            seed=int(trial.name.removeprefix("seed-")))
            metadata = trial / "meta.json"
            meta = json.loads(metadata.read_text()) if metadata.exists() else {}
            if meta.get("status") != "valid" or not (trial / "counts.csv").exists():
                results.append(dict(identity, completed_moves=meta.get("completed_moves"),
                                    capture_gaps=meta.get("capture_gaps"),
                                    status=meta.get("status", "no_metadata")))
                continue
            result, trial_devices, trial_sessions = analyze_trial(trial, meta, bgp_pcap)
            results.append(result)
            devices.extend(trial_devices)
            sessions.extend(trial_sessions)

    results.sort(key=lambda row: (row["spine_count"], row["leaf_count"], row["seed"]))
    write_csv(ROOT / "results.csv", SUMMARY, results)
    if not devices:
        return
    write_csv(ROOT / "per_move_devices.csv", list(devices[0]), devices)
    write_csv(ROOT / "per_move_sessions.csv", list(sessions[0]), sessions)
    role_summary = []
    for l, s in sorted({(r["leaf_count"], r["spine_count"]) for r in devices}):
        for role in ROLES:
            for direction in ("rx", "tx"):
                values = [r["routes"] for r in devices
                          if (r["leaf_count"], r["spine_count"], r["role_in_move"],
                              r["direction"]) == (l, s, role, direction)]
                if values:
                    role_summary.append(dict(leaf_count=l, spine_count=s, role=role,
                                             direction=direction, observations=len(values),
                                             mean=mean(values), minimum=min(values),
                                             maximum=max(values),
                                             sample_sd=stdev(values) if len(values) > 1 else 0))
    write_csv(ROOT / "role_summary.csv", list(role_summary[0]), role_summary)
    plot([row for row in results if row["status"] == "valid"])


if __name__ == "__main__":
    main()
