# SPDX-License-Identifier: ISC
"""Serial, break-before-make mobility for the dynamic Capstone fabric."""

import csv
import json
import os
import platform
import random
import time
from collections import Counter

ROBOT_IP = "45.0.0.50"
ROBOT_MAC = "02:52:00:00:00:00"
GATEWAY = "45.0.0.1"
QUIET_SECONDS = 10
STABLE_SECONDS = 2
COOLDOWN_SECONDS = 15
MOVE_TIMEOUT = 25
ANNOUNCE = f"ping -c 4 -i 0.25 -W 1 -q {GATEWAY}"
TRUTH_FIELDS = (
    "move", "src", "dst", "t_start", "t_detached", "t_attached", "t_stable",
    "t_end", "status", "error",
)


def build_radios(topology, tgen):
    """Extend robot1 across the existing APs, each with its own radio."""
    for index in range(2, len(topology.robot_leaves) + 1):
        tgen.add_link(
            tgen.gears["robot1"],
            tgen.gears[f"ap{index}"],
            f"w{index}",
            f"ap{index}-mobile",
        )


def _dump(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _prepare_robot(topology, tgen, radios):
    robot = tgen.net["robot1"]
    for index in range(2, len(topology.robot_leaves) + 1):
        tgen.net[f"robot{index}"].cmd_raises(f"ip link set w{index} down")
    robot.cmd_raises(
        "set -e\n"
        "sysctl -qw net.ipv6.conf.all.disable_ipv6=1\n"
        "ip -4 addr flush dev w1\n"
        + "\n".join(
            f"ip link set {radio} down\n"
            f"ip link set {radio} address {ROBOT_MAC}\n"
            f"ip addr add {ROBOT_IP}/24 dev {radio}"
            for radio in radios.values()
        )
    )
    return robot


def _attach(robot, radio):
    robot.cmd_raises(
        f"set -e\nip link set {radio} up\n"
        f"ip route replace default via {GATEWAY} dev {radio}\n"
        f"ip neigh flush to {GATEWAY} dev {radio}"
    )
    attached = time.time()
    robot.cmd_raises(ANNOUNCE)
    return attached


def _location(topology, tgen, leaf):
    """Verify MAC ownership on robot leaves and the controller's installed /32."""
    vtep = topology.loopbacks[leaf]
    for name in topology.robot_leaves:
        router = tgen.gears[name]
        entry = router.vtysh_cmd(
            f"show evpn mac vni 1000 mac {ROBOT_MAC} json", isjson=True
        ).get(ROBOT_MAC, {})
        if name == leaf:
            if entry.get("type") != "local" or entry.get("intf") != "eth-ap":
                return f"{name}: expected local MAC on eth-ap, got {entry}"
        elif entry.get("type") != "remote" or entry.get("remoteVtep") != vtep:
            return f"{name}: expected remote MAC via {vtep}, got {entry}"

    prefix = f"{ROBOT_IP}/32"
    entries = tgen.gears[topology.controller_leaf].vtysh_cmd(
        f"show ip route vrf VRF500 {prefix} json", isjson=True
    ).get(prefix, [])
    selected = {
        nexthop.get("ip")
        for entry in entries if entry.get("protocol") == "bgp" and entry.get("installed")
        for nexthop in entry.get("nexthops", []) if nexthop.get("active")
    }
    if selected != {vtep}:
        return f"{topology.controller_leaf}: expected installed {prefix} via {vtep}, got {entries}"
    return None


def _wait_stable(topology, tgen, leaf):
    deadline = time.monotonic() + MOVE_TIMEOUT
    good_since = None
    error = None
    while time.monotonic() < deadline:
        error = _location(topology, tgen, leaf)
        now = time.monotonic()
        if error:
            good_since = None
        elif good_since is None:
            good_since = now
        elif now - good_since >= STABLE_SECONDS:
            return None
        time.sleep(0.2)
    return error or f"{leaf}: stable observation window did not complete"


def _peer_stats(topology, tgen):
    states = {}
    for switch, router in tgen.routers().items():
        output = router.vtysh_cmd("show bgp neighbors json", isjson=True)
        for interface, peer in topology.peers(switch).items():
            state = output[interface]
            stats = state["messageStats"]
            states[f"{switch}>{peer}"] = {
                "tx": stats["updatesSent"],
                "rx": stats["updatesRecv"],
                "established": state.get("connectionsEstablished"),
                "dropped": state.get("connectionsDropped"),
                "state": state["bgpState"],
            }
    return states


def _audit_capture(topology, counter, outdir, meta, before, after):
    expected = {
        f"{leaf}_{interface}.pcap"
        for leaf in topology.leaves for interface in topology.peers(leaf)
    }
    files = {file.name: file for file in (outdir / "bgp_capture").glob("*.pcap")}
    if files.keys() != expected:
        raise RuntimeError(f"Unexpected capture coverage: {sorted(files.keys() ^ expected)}")
    updates = Counter()
    quiet_updates = 0
    gaps = 0
    mismatches = []
    start, end = meta["t"]["quiet_start"], meta["t"]["end"]
    for name, path in sorted(files.items()):
        leaf, interface = name.removesuffix(".pcap").split("_", 1)
        spine = topology.peers(leaf)[interface]
        messages, stats = counter.bgp_pcap.read_messages(path)
        gaps += stats["gaps"]
        for timestamp, outgoing, raw in messages:
            if not start <= timestamp < end:
                continue
            decoded = counter.bgp_pcap.parse_message(raw)
            if decoded["type"] == "UPDATE":
                if "error" in decoded:
                    mismatches.append([name, "decode", decoded["error"]])
                sender, receiver = (leaf, spine) if outgoing else (spine, leaf)
                updates[f"{sender}>{receiver}"] += 1
                quiet_updates += timestamp < meta["t"]["moves_start"]

    for session, initial in before.items():
        source, target = session.split(">")
        reverse = f"{target}>{source}"
        tx = after[session]["tx"] - initial["tx"]
        rx = after[reverse]["rx"] - before[reverse]["rx"]
        if tx != updates[session] or rx != updates[session]:
            mismatches.append([session, "pcap/FRR tx/FRR rx", updates[session], tx, rx])
        final = after[session]
        if (
            initial["state"] != "Established"
            or final["state"] != "Established"
            or initial["established"] != final["established"]
            or initial["dropped"] != final["dropped"]
        ):
            mismatches.append([session, "session changed", initial, final])
    return {
        "capture_gaps": gaps,
        "capture_links": len(files),
        "quiet_bgp_updates": quiet_updates,
        "capture_mismatches": mismatches,
        "capture_updates_by_session": dict(updates),
    }


def measure_moves(topology, tgen, counter, outdir, seed, move_count):
    """Write ground truth, captures, config and independent session audit."""
    if move_count < 1:
        raise ValueError("At least one move is required")
    outdir.mkdir(parents=True)
    leaves = topology.robot_leaves
    rng = random.Random(seed)
    radios = {leaf: f"w{i}" for i, leaf in enumerate(leaves, 1)}
    start_leaf = rng.choice(leaves)
    destinations = [start_leaf]
    for _ in range(move_count):
        destinations.append(rng.choice([leaf for leaf in leaves if leaf != destinations[-1]]))

    configs = outdir / "configs"
    configs.mkdir()
    for name, router in tgen.routers().items():
        config = router.vtysh_cmd("show running-config")
        (configs / f"{name}.conf").write_text(config)
        assert "sender-as-path-loop-detection" not in config, name
        assert "advertisement-interval" not in config, name
        if name in topology.leaves:
            assert "Duplicate address detection: Disable" in router.vtysh_cmd("show evpn"), name
    meta = {
        "leaf_count": len(topology.leaves),
        "spine_count": len(topology.spines),
        "controller_leaf": topology.controller_leaf,
        "vtep": topology.loopbacks,
        "spines": topology.spines,
        "seed": seed,
        "planned_sequence": destinations,
        "planned_moves": move_count,
        "robot_ip": ROBOT_IP,
        "robot_mac": ROBOT_MAC,
        "settings": {
            "mobility": "break-before-make",
            "announce": ANNOUNCE,
            "flush_gateway_neighbor_on_attach": True,
            "dad": "disabled",
            "advertisement_interval": "datacenter default",
            "sender_as_path_loop_detection": "baseline default",
            "quiet_s": QUIET_SECONDS,
            "stability_s": STABLE_SECONDS,
            "cooldown_s": COOLDOWN_SECONDS,
        },
        "runtime": {
            "linux": platform.release(),
            "architecture": platform.machine(),
            "cpus": os.cpu_count(),
            "memory": open("/proc/meminfo").readline().strip(),
            "frr": tgen.gears[topology.controller_leaf].vtysh_cmd("show version").splitlines()[0],
        },
        "status": "incomplete",
        "t": {},
    }
    _dump(outdir / "meta.json", meta)
    robot = _prepare_robot(topology, tgen, radios)
    _attach(robot, radios[start_leaf])
    error = _wait_stable(topology, tgen, start_leaf)
    assert error is None, f"Initial route validation: {error}"
    meta["initial_location_verified"] = start_leaf

    cap = counter.start(tgen, str(outdir))
    completed = 0
    attempted = 0
    try:
        before = _peer_stats(topology, tgen)
        meta["t"]["quiet_start"] = time.time()
        time.sleep(QUIET_SECONDS)
        meta["t"]["moves_start"] = time.time()
        with (outdir / "truth.csv").open("w", newline="") as truth:
            writer = csv.DictWriter(truth, fieldnames=TRUTH_FIELDS)
            writer.writeheader()
            for index, (src, dst) in enumerate(zip(destinations, destinations[1:]), 1):
                row = {"move": index, "src": src, "dst": dst, "t_start": time.time()}
                attempted += 1
                try:
                    robot.cmd_raises(f"ip link set {radios[src]} down")
                    row["t_detached"] = time.time()
                    row["t_attached"] = _attach(robot, radios[dst])
                    error = _wait_stable(topology, tgen, dst)
                    if error is None:
                        row["t_stable"] = time.time()
                except Exception as exc:
                    error = str(exc)
                row.update(t_end=time.time(), status="failed" if error else "completed", error=error)
                writer.writerow(row)
                truth.flush()
                if error:
                    break
                completed += 1
        meta["t"]["moves_end"] = time.time()
        time.sleep(COOLDOWN_SECONDS)
        meta["t"]["end"] = time.time()
        after = _peer_stats(topology, tgen)
    finally:
        counter.stop(cap)
        _dump(outdir / "meta.json", meta)

    meta["attempted_moves"] = attempted
    meta["completed_moves"] = completed
    meta["peer_stats_before"], meta["peer_stats_after"] = before, after
    meta.update(_audit_capture(topology, counter, outdir, meta, before, after))
    if (
        completed == move_count
        and not meta["capture_gaps"]
        and not meta["capture_mismatches"]
        and not meta["quiet_bgp_updates"]
    ):
        meta["status"] = "valid"
    _dump(outdir / "meta.json", meta)
    counter.summarize(str(outdir), moves=completed, quiet=True)
    return meta
