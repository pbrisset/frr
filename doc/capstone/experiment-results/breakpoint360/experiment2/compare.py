#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
"""Verify matched placements and moves across schedule pairs."""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


def compare(root):
    pairs = defaultdict(dict)
    for summary_file in sorted(root.glob("*/*/exp2_summary.json")):
        point = summary_file.parent
        summary = json.loads(summary_file.read_text())
        mode = "synchronized" if summary["sync"] else "staggered"
        with (point / "truth.csv").open(newline="") as stream:
            truth = list(csv.DictReader(stream))
        sequence = sorted((int(row["robot"]), int(row["move"]), row["src"], row["dst"])
                          for row in truth)
        first = [float(row["t_attached"]) for row in truth if row["move"] == "1"]
        assert len(first) == 64
        summary["first_round_attach_span_s"] = round(max(first) - min(first), 3)
        summary["bgp_updates_per_move"] = summary["bgp_updates"] / summary["attached_moves"]
        summary["type2_nlris_per_move"] = summary["type2_nlris"] / summary["attached_moves"]
        summary["schedule"] = mode
        if any(summary[key] for key in ("capture_gaps", "counter_mismatches")):
            raise ValueError(f"Capture audit failed for {point}")
        del summary["counter_mismatches"]
        key = (summary["dwell_s"], summary["seed"])
        assert mode not in pairs[key]
        pairs[key][mode] = (sequence, summary)

    if not pairs:
        raise ValueError(f"No completed pairs in {root}")
    rows = []
    for key, modes in sorted(pairs.items()):
        assert modes.keys() == {"synchronized", "staggered"}, f"Incomplete pair: {key}"
        (burst, burst_stats), (spread, spread_stats) = (
            modes["synchronized"], modes["staggered"]
        )
        assert burst == spread, f"Schedules used different routes: {key}"
        assert burst_stats["first_round_attach_span_s"] < spread_stats["first_round_attach_span_s"]
        rows.extend((burst_stats, spread_stats))

    dest = root / "comparison.csv"
    with dest.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(dest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    compare(parser.parse_args().results)
