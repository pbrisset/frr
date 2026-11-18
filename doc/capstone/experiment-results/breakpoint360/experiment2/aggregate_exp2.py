#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
"""Audit the full Experiment 2 matrix, summarize health and render figures."""

import csv
import json
import sys
import tarfile
from collections import defaultdict
from pathlib import Path
from statistics import mean
from tempfile import TemporaryDirectory

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from compare import compare

ROOT = Path(__file__).resolve().parent
DEVICES = ("leaf1", "leaf2", "leaf3", "leaf4", "leaf5", "spine1", "spine2")
DAEMONS = ("bgpd", "zebra")
SCHEDULES = ("synchronized", "staggered")
COLORS = {"synchronized": "#126e78", "staggered": "#ac553a"}
MARKERS = {10: "o", 30: "D"}
ARCHIVE = ROOT.parents[4] / "team-handoff/guna/mobility_tools.tar.gz"


def read_csv(path):
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def read_json(path):
    return json.loads(path.read_text())


def write_csv(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def cpu_metrics(samples, start, end):
    """Integrate aggregate daemon CPU over observed intervals in the capture window."""
    cpu_seconds = dict.fromkeys(DAEMONS, 0.0)
    peaks = dict.fromkeys(DAEMONS, 0.0)
    prev = float(samples[0]["t"])
    assert prev <= start
    observed = 0.0
    intervals = 0
    for row in samples[1:]:
        now = float(row["t"])
        assert now > prev
        overlap = max(0.0, min(now, end) - max(prev, start))
        prev = now
        if not overlap:
            continue
        observed += overlap
        intervals += 1
        for daemon in DAEMONS:
            cpus = [float(row[f"{device}_{daemon}_cpu_pct"]) for device in DEVICES]
            assert all(value >= 0 for value in cpus)
            total = sum(cpus)
            cpu_seconds[daemon] += total * overlap / 100
            peaks[daemon] = max(peaks[daemon], total)
    assert intervals > 0 and observed >= 0.95 * (end - start)
    result = {
        "health_sample_intervals": intervals,
        "health_observed_s": observed,
        "capture_window_s": end - start,
    }
    for daemon in DAEMONS:
        result[f"{daemon}_cpu_seconds"] = cpu_seconds[daemon]
        result[f"{daemon}_mean_cpu_pct_one_core"] = 100 * cpu_seconds[daemon] / observed
        result[f"{daemon}_peak_cpu_pct_one_core"] = peaks[daemon]
    return result


def robot_route_volume(point, meta, decoder):
    """Count robot Type-2 NLRIs separately from the fixed fabric endpoints."""
    robot_macs = {f"02:52:00:00:00:{index:02x}" for index in range(meta["config"]["robots"])}
    robot_nlris, other_nlris = 0, 0
    start, end = (meta["t"][key] for key in ("moves_start", "end"))
    for capture in (point / "bgp_capture").glob("*.pcap"):
        messages, _ = decoder.read_messages(capture)
        for timestamp, _, raw in messages:
            if not start <= timestamp < end:
                continue
            message = decoder.parse_message(raw)
            if message["type"] != "UPDATE":
                continue
            for action in ("adv", "wd"):
                for route in message[action]:
                    if route.get("family") != "evpn" or route.get("rt") != 2:
                        continue
                    if route.get("mac") in robot_macs:
                        robot_nlris += 1
                    else:
                        other_nlris += 1
    return robot_nlris, other_nlris


def load_trial(point, dwell, seed, schedule, decoder):
    meta = read_json(point / "meta.json")
    summary = read_json(point / "exp2_summary.json")
    config = meta["config"]
    assert config["robots"] == 64 and config["duration_s"] == 6 * dwell
    assert config["dwell_s"] == dwell and config["seed"] == seed
    assert config["gap_ms"] == 0 and config["robot"] == "repeat" and config["count"]
    assert config["sync"] == (schedule == "synchronized")
    assert not config["dad"] and config["adv_interval"] is None
    assert set(meta["dad_state"].values()) == {"Disable"}
    assert meta["warmup_routes"] == 64
    assert summary["capture_links"] == 10 and not summary["capture_gaps"]
    assert not summary["counter_mismatches"]
    assert len(list((point / "bgp_capture").glob("*.pcap"))) == 10
    for name in ("counts.csv", "timeline.csv", "health.csv", "leaf5_final_correctness.csv"):
        assert (point / name).stat().st_size > 0
    assert "2 passed," in (point / "pytest.log").read_text()
    assert len(read_csv(point / "counts.csv")) == len(DEVICES) * 2

    captures = [r for r in read_csv(point / "type2_counts.csv") if r["direction"] == "tx"]
    assert sorted(r["switch"] for r in captures) == sorted(DEVICES)
    adv = sum(int(r["type2_adv"]) for r in captures)
    wd = sum(int(r["type2_wd"]) for r in captures)
    all_updates = sum(int(r["bgp_updates"]) for r in captures)
    type2_updates = sum(int(r["type2_updates"]) for r in captures)
    assert adv + wd == summary["type2_nlris"]
    assert all_updates == summary["bgp_updates"] and type2_updates == summary["type2_bgp_updates"]
    assert summary["attached_moves"] == 384
    robot_nlris, other_nlris = robot_route_volume(point, meta, decoder)
    assert robot_nlris + other_nlris == adv + wd

    forwards = read_csv(point / "leaf5_final_correctness.csv")
    assert len(forwards) == summary["final_leaf5_checked"] == 64
    assert sum(r["correct"] == "True" for r in forwards) == summary["final_leaf5_correct"]
    attaches = defaultdict(list)
    for move in read_csv(point / "truth.csv"):
        if int(move["move"]) > 0:
            attaches[int(move["robot"])].append(float(move["t_attached"]))
    assert len(attaches) == 64 and all(len(values) == 6 for values in attaches.values())
    spacings = [after - before for values in attaches.values()
                for before, after in zip(values, values[1:])]
    assert min(spacings) > 0
    t = meta["t"]
    cpu = cpu_metrics(read_csv(point / "health.csv"), t["moves_start"], t["end"])
    return {
        "schedule": schedule, "dwell_s": dwell, "seed": seed,
        "completed_moves": summary["attached_moves"],
        "bgp_updates": all_updates, "type2_bgp_updates": type2_updates,
        "type2_advertisements": adv, "type2_withdrawals": wd, "type2_nlris": adv + wd,
        "robot_type2_nlris": robot_nlris, "non_robot_type2_nlris": other_nlris,
        "type2_nlris_per_update": (adv + wd) / all_updates,
        "type2_nlris_per_type2_update": (adv + wd) / type2_updates,
        "min_inter_move_s": min(spacings),
        "max_inter_move_s": max(spacings),
        **cpu,
        "capture_gaps": summary["capture_gaps"],
        "quiet_bgp_updates": summary["quiet_bgp_updates"],
        "leaf5_final_correct": summary["final_leaf5_correct"],
        "leaf5_final_checked": summary["final_leaf5_checked"],
        "moves_seen_at_correct_vtep": summary["moves_seen_at_correct_vtep"],
        "probe_loss_pct": summary["probe_loss_pct"],
        "warmup_all_reachable": meta["warmup_all_reachable"],
    }


def reduction(sync, stagger):
    return 100 * (1 - sync / stagger)


def pair_rows(rows):
    by_key = {(r["dwell_s"], r["seed"], r["schedule"]): r for r in rows}
    pairs = []
    for dwell in (10, 30):
        for seed in (1, 2, 3):
            sync, spread = (by_key[dwell, seed, mode] for mode in SCHEDULES)
            delta = {
                "dwell_s": dwell, "seed": seed,
                "update_reduction_pct": reduction(sync["bgp_updates"], spread["bgp_updates"]),
                "nlri_change_pct": 100 * (sync["type2_nlris"] / spread["type2_nlris"] - 1),
                "robot_nlri_change_pct": 100 * (
                    sync["robot_type2_nlris"] / spread["robot_type2_nlris"] - 1
                ),
            }
            for daemon in DAEMONS:
                key = f"{daemon}_cpu_seconds"
                delta[f"{daemon}_cpu_reduction_pct"] = (
                    reduction(sync[key], spread[key]) if spread[key] else None
                )
            pairs.append(delta)
    return pairs


def render(rows, pairs):
    graphs = ROOT / "graphs"
    graphs.mkdir(exist_ok=True)
    plt.rcParams.update({
        "font.size": 11, "axes.titleweight": "bold", "axes.axisbelow": True,
        "axes.spines.right": False, "axes.spines.top": False,
        "axes.labelcolor": "#203047", "figure.facecolor": "white",
    })

    def save(fig, name):
        fig.savefig(graphs / f"{name}.png", dpi=185)
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(8.8, 5.2), layout="constrained")
    for dwell in (10, 30):
        for schedule in SCHEDULES:
            chosen = [r for r in rows if (r["dwell_s"], r["schedule"]) == (dwell, schedule)]
            ax.scatter([r["bgp_updates"] for r in chosen], [r["type2_nlris"] for r in chosen],
                       color=COLORS[schedule], marker=MARKERS[dwell], s=66, alpha=0.88,
                       label=f"{schedule.capitalize()}, {dwell} s (n=3)")
    ax.set(title="BGP messages and EVPN Type-2 route volume",
           xlabel="BGP UPDATE messages per trial", ylabel="EVPN Type-2 NLRIs per trial")
    ax.set_xlim(left=0)
    ax.set_ylim(0, max(row["type2_nlris"] for row in rows) * 1.14)
    ax.grid(alpha=0.2)
    ax.legend(fontsize=9, ncol=2)
    save(fig, "01_updates_vs_nlris")

    fig, ax = plt.subplots(figsize=(8.8, 5.2), layout="constrained")
    for i, schedule in enumerate(SCHEDULES):
        for j, dwell in enumerate((10, 30)):
            values = [r["type2_nlris_per_update"] for r in rows
                      if (r["schedule"], r["dwell_s"]) == (schedule, dwell)]
            x = j + (i - 0.5) * 0.27
            avg = mean(values)
            ax.bar(x, avg, width=0.22, color=COLORS[schedule],
                   label=f"{schedule.capitalize()} (n=3 per dwell)" if j == 0 else None,
                   alpha=0.8)
            ax.errorbar(x, avg, yerr=[[avg - min(values)], [max(values) - avg]],
                        fmt="none", ecolor="#19263a", capsize=4, zorder=3)
            ax.scatter([x - 0.035, x, x + 0.035], values, s=18, color="#19263a", zorder=4)
    ax.set_xticks([0, 1], ["10 s", "30 s"])
    ax.set(title="EVPN packing by schedule and dwell", xlabel="Nominal per-robot dwell",
           ylabel="Average Type-2 NLRIs per BGP UPDATE")
    ax.grid(axis="y", alpha=0.2)
    ax.legend(fontsize=9)
    save(fig, "02_packing_ratio")

    fig, axes = plt.subplots(1, 2, figsize=(9.7, 4.8), sharex=True, layout="constrained")
    for ax, daemon in zip(axes, DAEMONS):
        for dwell in (10, 30):
            key = f"{daemon}_cpu_reduction_pct"
            chosen = [p for p in pairs if p["dwell_s"] == dwell and p[key] is not None]
            ax.scatter([p["update_reduction_pct"] for p in chosen], [p[key] for p in chosen],
                       marker=MARKERS[dwell], s=58, color={10: "#126e78", 30: "#785095"}[dwell],
                       label=f"{dwell} s dwell (n={len(chosen)} pairs)")
        ax.axhline(0, linewidth=0.8, color="#526079")
        ax.set(title=f"{daemon} CPU", ylabel="CPU seconds reduction (%)")
        ax.grid(alpha=0.2)
        ax.legend(fontsize=8)
    fig.suptitle("CPU versus wire message reduction in matched runs", fontweight="bold")
    fig.supxlabel("BGP UPDATE message reduction (%)")
    save(fig, "03_cpu_vs_packing")


def main():
    trials = ROOT / "trials"
    initial_status = (trials / "matrix.exit").read_text().strip()
    if initial_status != "0":
        assert (trials / "matrix-retry.exit").read_text().strip() == "0"
    assert len(list(trials.glob("*/*/exp2_summary.json"))) == 12
    compare(trials)
    with TemporaryDirectory() as temp:
        with tarfile.open(ARCHIVE) as archive:
            with archive.extractfile("bgp_pcap.py") as script:
                (Path(temp) / "bgp_pcap.py").write_bytes(script.read())
        sys.path.insert(0, temp)
        import bgp_pcap

        rows = [
            load_trial(trials / f"dwell-{dwell}s" / f"{mode}-seed-{seed}",
                       dwell, seed, mode, bgp_pcap)
            for dwell in (10, 30) for seed in (1, 2, 3) for mode in SCHEDULES
        ]
    pairs = pair_rows(rows)
    write_csv(ROOT / "results_exp2.csv", rows)
    write_csv(ROOT / "paired_exp2.csv", pairs)
    render(rows, pairs)


if __name__ == "__main__":
    main()
