#!/usr/bin/env python
# SPDX-License-Identifier: ISC
"""Repeatable EVPN host mobility workload with per-move CSV telemetry.

Run this module on its own: pytest -s test_mobility_workload.py
The topology and dataplane setup are shared with test_evpn_capstone.py.
"""

import csv
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lib.topogen import get_topogen
from lib.topolog import logger

from bgp_evpn_capstone import test_evpn_capstone as fabric


pytestmark = [pytest.mark.bgpd, pytest.mark.evpn]

ROBOT = "host11"
MAC = fabric.LEAVES["leaf1"]["hosts"][ROBOT][1]
VNI = fabric.LEAVES["leaf1"]["l2vni"]
POLL_INTERVAL = 0.1
MOVE_TIMEOUT = float(os.environ.get("MOBILITY_MOVE_TIMEOUT", "5"))
CSV_FIELDS = (
    "timestamp", "workload", "robot_id", "mac_address", "src_leaf", "dst_leaf",
    "move_num", "seq_num", "convergence_time_ms", "status", "dad_warning",
)
WORKLOADS = (
    ("reverse", ("leaf1", "leaf2", "leaf1")),
    ("multi_leaf", ("leaf1", "leaf3", "leaf4", "leaf1")),
)


def setup_module(module):
    fabric.setup_module(module)


def teardown_module(module):
    fabric.teardown_module(module)


def _mac_state(router, mac=MAC):
    """Read the exact MAC entry, including Zebra's mobility sequence."""
    output = router.vtysh_cmd(
        "show evpn mac vni {} mac {} json".format(VNI, mac), isjson=True
    )
    return output.get(mac, {}) if isinstance(output, dict) else {}


def _log_offsets(tgen):
    """Capture current Zebra log positions so old DAD messages are ignored."""
    paths = (
        Path(tgen.logdir) / leaf / "zebra.log"
        for leaf in ("leaf1", "leaf2", "leaf3", "leaf4")
    )
    return {path: path.stat().st_size for path in paths if path.exists()}


def _dad_warning_since(offsets, tgen, macs=(MAC,)):
    for path, start in offsets.items():
        if not path.exists():
            continue
        with path.open("r", errors="replace") as log:
            log.seek(start)
            if "detected as duplicate" in log.read():
                return True
    # The MAC duplicate flag is observable even when Zebra logs elsewhere.
    return any(
        _mac_state(tgen.gears[leaf], mac).get("isDuplicate", False)
        for leaf in ("leaf1", "leaf2", "leaf3", "leaf4") for mac in macs
    )


def _wait_for_move(tgen, source, target, previous_seq, started,
                   host=ROBOT, mac=MAC, original_leaf="leaf1"):
    """Wait for local ownership, higher sequence, and fabric convergence."""
    deadline = started + MOVE_TIMEOUT
    last_error = "no local MAC entry"
    while time.monotonic() < deadline:
        state = _mac_state(tgen.gears[target], mac)
        seq = state.get("localSequence")
        if state.get("type") != "local" or state.get("intf") != "eth-{}".format(host):
            last_error = "{} has not learned {} locally: {}".format(target, mac, state)
        elif not isinstance(seq, int) or seq != previous_seq + 1:
            last_error = "{} sequence {} expected {}".format(
                target, seq, previous_seq + 1
            )
        else:
            last_error = fabric._check_robot_convergence(
                tgen, host, original_leaf, target, old_leaf=source
            )
            if last_error is None:
                return seq, (time.monotonic() - started) * 1000, None
        time.sleep(POLL_INTERVAL)
    return -1, (time.monotonic() - started) * 1000, last_error


def _wait_for_remote_sequence(tgen, leaf, sequence):
    """Avoid moving again before the next leaf has learned the current owner."""
    deadline = time.monotonic() + MOVE_TIMEOUT
    while time.monotonic() < deadline:
        state = _mac_state(tgen.gears[leaf])
        if state.get("type") == "remote" and state.get("remoteSequence") == sequence:
            return
        time.sleep(POLL_INTERVAL)
    pytest.fail("{} did not learn host11 remote sequence {}".format(leaf, sequence))


def move_robot_interface(tgen, source, target, move_num, previous_seq, workload, writer):
    """Move the host-facing veth, measure convergence, and write one CSV row."""
    _wait_for_remote_sequence(tgen, target, previous_seq)
    offsets = _log_offsets(tgen)
    started = time.monotonic()
    logger.info("Move %d: %s %s -> %s", move_num, ROBOT, source, target)

    # The access port must actually cross Munet network namespaces.
    fabric._move_robot(tgen, ROBOT, source, target)
    # Host traffic starts bridge learning and IP/MAC advertisement.
    tgen.gears[ROBOT].cmd("ping -c 2 -W 1 -I eth0 {}".format(
        fabric.LEAVES["leaf1"]["gw"]
    ))

    seq, elapsed_ms, error = _wait_for_move(
        tgen, source, target, previous_seq, started
    )
    dad_warning = _dad_warning_since(offsets, tgen)
    status = "SUCCESS" if error is None else "FAILURE"
    writer.writerow((
        datetime.now(timezone.utc).isoformat(), workload, ROBOT, MAC, source, target,
        move_num, seq, "{:.2f}".format(elapsed_ms), status, dad_warning,
    ))
    logger.info("Move %d: %s, seq=%s, %.2f ms, DAD=%s", move_num,
                status, seq, elapsed_ms, dad_warning)
    assert error is None, "Move {} {} -> {}: {}".format(move_num, source, target, error)
    return seq


def test_repeatable_mobility_workload():
    """Run reverse and multi-leaf paths, ending with host11 on leaf1."""
    tgen = get_topogen()
    if tgen.routers_have_failure():
        pytest.skip(tgen.errors)

    csv_path = Path(os.environ.get(
        "MOBILITY_CSV_PATH", str(Path(tgen.logdir) / "mobility_telemetry.csv")
    ))
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    # Establish baseline local and remote MAC state before measuring moves.
    tgen.gears[ROBOT].cmd("ping -c 2 -W 1 -I eth0 {}".format(
        fabric.LEAVES["leaf1"]["gw"]
    ))
    baseline = _mac_state(tgen.gears["leaf1"])
    assert baseline.get("type") == "local", "Host11 is not local on leaf1: {}".format(
        baseline
    )
    previous_seq = baseline["localSequence"]
    for leaf in ("leaf2", "leaf3", "leaf4"):
        _wait_for_remote_sequence(tgen, leaf, previous_seq)

    move_num = 0
    with csv_path.open("w", newline="") as log:
        writer = csv.writer(log)
        writer.writerow(CSV_FIELDS)
        for workload, path in WORKLOADS:
            for source, target in zip(path, path[1:]):
                move_num += 1
                previous_seq = move_robot_interface(
                    tgen, source, target, move_num, previous_seq, workload, writer
                )
                log.flush()
    logger.info("Mobility telemetry: %s", csv_path)


def test_concurrent_fleet_workload(tgen):
    """Workloads D/E: move host11 and host21 across the fabric together."""
    if tgen.routers_have_failure():
        pytest.skip(tgen.errors)

    moves = (("host11", "leaf1", "leaf2"), ("host21", "leaf2", "leaf3"))
    csv_path = Path(os.environ.get(
        "MOBILITY_CSV_PATH", str(Path(tgen.logdir) / "mobility_telemetry.csv")
    ))
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    next_move_num = 1
    if csv_path.exists() and csv_path.stat().st_size:
        with csv_path.open(newline="") as log:
            next_move_num += sum(1 for _ in csv.DictReader(log))

    logger.info("--- WORKLOAD D/E: CONCURRENT MULTI-ROBOT MOBILITY ---")
    moved = []
    baseline = {}
    started = {}
    errors = []
    offsets = _log_offsets(tgen)
    try:
        for host, source, target in moves:
            mac = fabric.LEAVES[source]["hosts"][host][1]
            state = _mac_state(tgen.gears[source], mac)
            assert state.get("type") == "local", "{} is not local on {}: {}".format(
                host, source, state
            )
            baseline[host] = state["localSequence"]
            started[host] = time.monotonic()
            fabric._move_robot(tgen, host, source, target)
            moved.append((host, source, target))

        # Generate traffic only after both access ports have moved.
        for host, source, _ in moves:
            tgen.gears[host].cmd("ping -c 2 -W 1 -I eth0 {}".format(
                fabric.LEAVES[source]["gw"]
            ))

        with csv_path.open("a", newline="") as log:
            writer = csv.writer(log)
            if log.tell() == 0:
                writer.writerow(CSV_FIELDS)
            for move_num, (host, source, target) in enumerate(moves, next_move_num):
                mac = fabric.LEAVES[source]["hosts"][host][1]
                seq, elapsed_ms, error = _wait_for_move(
                    tgen, source, target, baseline[host], started[host],
                    host=host, mac=mac, original_leaf=source,
                )
                dad_warning = _dad_warning_since(
                    offsets, tgen,
                    macs=tuple(fabric.LEAVES[src]["hosts"][robot][1]
                               for robot, src, _ in moves),
                )
                writer.writerow((
                    datetime.now(timezone.utc).isoformat(), "concurrent_fleet",
                    host, mac, source, target, move_num, seq,
                    "{:.2f}".format(elapsed_ms),
                    "SUCCESS" if error is None else "FAILURE", dad_warning,
                ))
                log.flush()
                logger.info("Fleet move %d: %s %s -> %s, seq=%s, %.2f ms, DAD=%s",
                            move_num, host, source, target, seq, elapsed_ms, dad_warning)
                if error is not None:
                    errors.append("{}: {}".format(host, error))
    finally:
        for host, source, target in reversed(moved):
            fabric._move_robot(tgen, host, target, source)
            tgen.gears[host].cmd("ping -c 3 -W 1 -I eth0 {}".format(
                fabric.LEAVES[source]["gw"]
            ))

    assert not errors, "; ".join(errors)
    logger.info("--- MULTI-ROBOT FLEET MOBILITY SUCCESSFUL ---")
